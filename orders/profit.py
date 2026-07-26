"""รายงานกำไรรายวัน — logic คำนวณ + cache (DailySummary).

หลักการ:
- กำไรขั้นต้น (gross_profit) = ยอดขาย − (จำนวนตัว × ต้นทุนตาม shirt_type ของ item)
- กำไรหลังหักแอด (net_after_ads) = gross_profit − ค่าแอดของเพจวันนั้น (DailyAdSpend)
- วันที่ผ่านไปแล้ว: คำนวณครั้งแรกแล้วเขียนลง DailySummary → อ่านจาก cache ตลอด
- วันปัจจุบัน/อนาคต: คำนวณสดเสมอ ไม่เขียน cache (ข้อมูลยังเปลี่ยน)
- แก้ order / ค่าแอด / ต้นทุนย้อนหลัง → เรียก invalidate_days() ลบแถว cache
  ของวันนั้นทิ้ง ให้คำนวณใหม่รอบหน้า (hook อยู่ใน views.py + admin.py)
- item เก่าที่ไม่ได้ระบุ shirt_type ('') → คิดต้นทุนแบบแขนสั้น (DEFAULT_SHIRT_TYPE)
  และนับจำนวนตัวเข้า defaulted_shirts เพื่อโชว์ badge เตือนในรายงาน
"""
import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from .models import DailyAdSpend, DailySummary, Order, ShirtCost

DEFAULT_SHIRT_TYPE = 'short'

# กันพังกรณีแถว ShirtCost หาย (โดนลบใน admin) — ตรงกับ seed ใน migration 0023
FALLBACK_COSTS = {
    'short': Decimal('50'),
    'long': Decimal('65'),
    'polo': Decimal('85'),
}

# key ตัวเลขของแถวรายงาน — ใช้ทั้งตอนเขียน cache และตอนรวมยอด (totals)
NUMERIC_KEYS = ('orders', 'shirts', 'revenue', 'shirt_cost', 'ad_spend',
                'gross_profit', 'net_after_ads', 'defaulted_shirts')


def shirt_cost_map():
    """{shirt_type: Decimal cost} จาก DB, เติม fallback ให้ครบทุกประเภทเสมอ."""
    costs = dict(FALLBACK_COSTS)
    for row in ShirtCost.objects.all():
        costs[row.shirt_type] = row.cost
    return costs


def _blank_row(day, page):
    return {
        'date': day, 'page': page, 'orders': 0, 'shirts': 0,
        'revenue': Decimal('0'), 'shirt_cost': Decimal('0'),
        'ad_spend': Decimal('0'), 'gross_profit': Decimal('0'),
        'net_after_ads': Decimal('0'), 'defaulted_shirts': 0,
    }


def compute_day(day):
    """คำนวณสดจากใบงานจริงของวันนั้น (นับตาม created_date) → list แถวต่อเพจ.
    เพจที่มีแต่ค่าแอดไม่มีออร์เดอร์ก็ขึ้นเป็นแถว (net ติดลบ) จะได้ไม่ตกหล่น."""
    costs = shirt_cost_map()
    default_cost = costs[DEFAULT_SHIRT_TYPE]
    pages = {}

    orders = (
        Order.objects.filter(created_date=day)
        .prefetch_related('items', 'items__variants')
    )
    for o in orders:
        row = pages.setdefault(o.source, _blank_row(day, o.source))
        row['orders'] += 1
        row['revenue'] += o.total_price or Decimal('0')
        for item in o.items.all():
            qty = item.total_qty
            if not qty:
                continue
            row['shirts'] += qty
            row['shirt_cost'] += qty * costs.get(item.shirt_type, default_cost)
            if not item.shirt_type:
                row['defaulted_shirts'] += qty

    for spend in DailyAdSpend.objects.filter(date=day):
        row = pages.setdefault(spend.page, _blank_row(day, spend.page))
        row['ad_spend'] += spend.amount

    rows = []
    for row in pages.values():
        row['gross_profit'] = row['revenue'] - row['shirt_cost']
        row['net_after_ads'] = row['gross_profit'] - row['ad_spend']
        rows.append(row)
    rows.sort(key=lambda r: r['revenue'], reverse=True)
    return rows


def _row_from_cache(c):
    return {'date': c.date, 'page': c.page,
            **{k: getattr(c, k) for k in NUMERIC_KEYS}}


def get_day_rows(day):
    """แถวกำไรของวัน: วันนี้/อนาคต = คำนวณสด (ไม่ cache), วันที่ผ่านแล้ว =
    อ่าน cache ถ้ามี ไม่งั้นคำนวณแล้วเขียน cache. วันว่าง (ไม่มีทั้งออร์เดอร์
    และค่าแอด) ไม่เขียน cache — คำนวณใหม่ก็ถูก (query เดียว)."""
    today = timezone.localdate()
    if day >= today:
        return compute_day(day)

    cached = list(DailySummary.objects.filter(date=day).order_by('-revenue'))
    if cached:
        return [_row_from_cache(c) for c in cached]

    rows = compute_day(day)
    if rows:
        DailySummary.objects.bulk_create(
            [DailySummary(**r) for r in rows], ignore_conflicts=True,
        )
    return rows


def invalidate_days(*days):
    """ลบ cache ของวันที่ระบุ (แก้ order/ค่าแอดย้อนหลัง) — คำนวณใหม่รอบหน้า."""
    real = {d for d in days if d}
    if real:
        DailySummary.objects.filter(date__in=real).delete()


def invalidate_all():
    """ล้าง cache ทั้งหมด — ใช้ตอนแก้ต้นทุนเสื้อ (กระทบทุกวันย้อนหลัง)."""
    DailySummary.objects.all().delete()


def totals(rows):
    """แถวรวมของ list แถวรายงาน (ใช้ทั้งรายวันและรายเดือน)."""
    t = _blank_row(None, 'รวม')
    for r in rows:
        for k in NUMERIC_KEYS:
            t[k] += r[k]
    return t


def get_month_data(year, month):
    """ข้อมูลรายเดือน: (day_rows, month_total, page_rows)
    - day_rows: ยอดรวมทุกเพจต่อวัน (เฉพาะวันที่ถึงแล้ว) — ใช้วาดกราฟ + ตาราง
    - month_total: รวมทั้งเดือน
    - page_rows: รวมทั้งเดือนแยกต่อเพจ (เรียงยอดขายมาก→น้อย)
    วันในอดีตถูก cache อัตโนมัติผ่าน get_day_rows ทีละวัน."""
    today = timezone.localdate()
    last_dom = calendar.monthrange(year, month)[1]
    day_rows, all_rows = [], []
    page_acc = {}

    d = date(year, month, 1)
    while d <= date(year, month, last_dom) and d <= today:
        rows = get_day_rows(d)
        all_rows.extend(rows)
        day_total = totals(rows)
        day_total['date'] = d
        day_rows.append(day_total)
        for r in rows:
            acc = page_acc.setdefault(r['page'], _blank_row(None, r['page']))
            for k in NUMERIC_KEYS:
                acc[k] += r[k]
        d += timedelta(days=1)

    page_rows = sorted(page_acc.values(), key=lambda r: r['revenue'], reverse=True)
    return day_rows, totals(all_rows), page_rows
