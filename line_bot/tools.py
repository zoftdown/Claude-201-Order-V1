"""
tools — ฟังก์ชัน "อ่านอย่างเดียว" ที่ LLM เรียกได้ตอนถาม-ตอบ (ขั้น 5)

ทุกฟังก์ชันคืน dict/list ธรรมดา (str/int/float/bool/None) ให้ json.dumps ได้ตรงๆ
ไม่มีการเขียน DB · ไม่มีเรื่องกำไร/ROI/ต้นทุน (ตั้งใจ — ขั้นนี้ยังไม่เปิด)

รายงานที่หน้าเว็บมีอยู่แล้ว reuse ฟังก์ชันเดิมใน orders.views ห้ามเขียน query ซ้ำ:
- daily_summary  → orders.views._daily_summary_context (หน้า "สรุปรายวัน")
- overdue_orders → orders.views._report_stuck_rows   (tab "ค้างเกิน 7 วัน")

TOOL_SCHEMAS = schema แบบ OpenAI function tools · run_tool(name, args) = dispatcher
(หา function ด้วย getattr ตอนเรียก เพื่อให้ tests patch ฟังก์ชันใน module นี้ได้)
"""
import json
import logging
import sys
from types import SimpleNamespace

from django.db.models import F
from django.utils import timezone
from django.utils.dateparse import parse_date

from orders.models import Order
from orders.views import STAGE_TIMELINE, _daily_summary_context, _report_stuck_rows

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 10
MAX_LIMIT = 30

# ขั้นปัจจุบัน (= ขั้นที่เสร็จล่าสุด แบบเดียวกับ Order.progress_label) เรียงจากไกลสุด → ใกล้สุด
# key ภาษาอังกฤษให้ LLM เลือก · (field ที่ต้องมีค่า) · label ไทยตรงกับ progress_label
# ป้ายฝั่งบอทของสถานะ awaiting_pickup_at: model/การ์ดสรุปรายวันใช้ "รอลูกค้ารับ" (มุมแผนกแพ็ค = รอคนมารับ)
# แต่ timeline หน้า detail + คำสั่งบอท "ลูกค้ารับ" ถือเป็นขั้นสุดท้ายที่ติ๊กแล้ว (progress_done=True)
# → 2026-10-15 6910-122 ติ๊กครบ 7 ขั้นแต่บอทตอบ "รอลูกค้ารับ" คนอ่านเข้าใจว่ายังไม่เสร็จ
# แปะทับเฉพาะฝั่งบอท (ไม่แตะ models/เว็บ): current_stage ยังมาจาก Order.progress_label แล้ว map ป้ายนี้
BOT_STAGE_LABELS = {'รอลูกค้ารับ': 'ลูกค้ารับแล้ว'}


def bot_stage_label(order):
    label = order.progress_label
    return BOT_STAGE_LABELS.get(label, label)


STAGE_KEYS = [
    ('shipped',          'shipped_at',         'ส่งแล้ว'),
    ('awaiting_pickup',  'awaiting_pickup_at', 'ลูกค้ารับแล้ว'),
    ('packed',           'packed_at',          'รีด+แพ็คแล้ว'),
    ('sewing',           'sent_to_tailors_at', 'ส่งเย็บแล้ว'),
    ('sorted',           'sort_done_at',       'คัดแล้ว'),
    ('cut',              'cut_done_at',        'ตัดแล้ว'),
    ('rolled',           'roll_done_at',       'โรลแล้ว'),
    ('printed',          'print_done_at',      'พิมพ์แล้ว'),
    ('not_started',      None,                 'ยังไม่เริ่ม'),
]
STAGE_KEY_SET = [k for k, _, _ in STAGE_KEYS]


# ---------------------------------------------------------------------------
# serializers
# ---------------------------------------------------------------------------

def _num(value):
    value = value or 0
    return int(value) if value == int(value) else float(value)


def _iso_date(d):
    return d.isoformat() if d else None


def _iso_dt(dt):
    return timezone.localtime(dt).strftime('%Y-%m-%d %H:%M') if dt else None


def _order_row(order):
    """แถวสรุปใบงาน (ใช้ทั้ง search_orders / daily_summary / overdue_orders)"""
    return {
        'order_number': order.order_number,
        'shirt_name': order.shirt_name or '',
        'customer_name': order.customer_name or '',
        'source': order.source or '',
        'created_date': _iso_date(order.created_date),
        'total_qty': order.total_qty,
        'total_price': _num(order.total_price),
        'deposit': _num(order.deposit),
        'balance': _num(order.remaining),
        'delivery_method': order.delivery_method or '',
        'customer_link': order.customer_link or '',        # ลิงก์ FB / เบอร์โทร — ตอบตรงจากฟิลด์ ห้ามย่อ URL
        'shipping_address': order.shipping_address or '',
        'current_stage': bot_stage_label(order),
        'job_done': bool(order.progress_done),  # ออกจากร้านแล้ว (ส่งแล้ว/ลูกค้ารับแล้ว) = จบงาน
        'special_note': order.special_note or '',
        'is_urgent': bool(order.is_urgent),
    }


def _variant_row(v):
    return {
        'collar': v.collar or '',
        'sleeve': v.sleeve or '',
        'color': v.color or '',
        'sizes': {s.get('label', ''): s.get('qty', 0) for s in v.sizes if isinstance(s, dict)},
        'qty': v.total_qty,
        'note': v.note or '',
    }


def _item_row(item):
    return {
        'shirt_type': item.get_shirt_type_display() if item.shirt_type else '',
        'design_doc_number': item.effective_design_doc or '',
        'variants': [_variant_row(v) for v in item.variants.all()],
        'qty': item.total_qty,
    }


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------

def _stage_filter(qs, stage):
    """กรองตามขั้นปัจจุบัน: field ของขั้นนั้นมีค่า + ทุกขั้นที่ไกลกว่าต้องยังว่าง"""
    higher = []
    for key, field, _label in STAGE_KEYS:
        if key == stage:
            if field:
                qs = qs.filter(**{f'{field}__isnull': False})
            for h in higher:
                qs = qs.filter(**{f'{h}__isnull': True})
            return qs
        higher.append(field)
    return qs


def search_orders(customer_name=None, shirt_name=None, order_number=None, source=None,
                  date_from=None, date_to=None, stage=None, has_balance=None,
                  limit=DEFAULT_LIMIT):
    """ค้นใบงานตามเงื่อนไข (ทุก arg optional, AND กัน) — ใหม่สุดก่อน
    คืน {'orders': [...≤limit], 'total_count': N, 'truncated': bool}"""
    qs = Order.objects.all()
    if customer_name:
        qs = qs.filter(customer_name__icontains=customer_name.strip())
    if shirt_name:
        qs = qs.filter(shirt_name__icontains=shirt_name.strip())
    if order_number:
        qs = qs.filter(order_number__icontains=order_number.strip())
    if source:
        qs = qs.filter(source__icontains=source.strip())
    if date_from:
        d = parse_date(str(date_from))
        if d:
            qs = qs.filter(created_date__gte=d)
    if date_to:
        d = parse_date(str(date_to))
        if d:
            qs = qs.filter(created_date__lte=d)
    if stage in STAGE_KEY_SET:
        qs = _stage_filter(qs, stage)
    if has_balance is True:
        qs = qs.filter(total_price__gt=F('deposit'))
    elif has_balance is False:
        qs = qs.filter(total_price__lte=F('deposit'))

    try:
        limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT

    qs = qs.order_by('-created_date', '-id').prefetch_related('items', 'items__variants')
    total = qs.count()
    rows = [_order_row(o) for o in qs[:limit]]
    return {'orders': rows, 'total_count': total, 'truncated': total > len(rows)}


def get_order(order_number):
    """รายละเอียดใบเดียว + รายการเสื้อ (ประเภท/สี/ไซส์) + ความคืบหน้าทุกขั้นพร้อมเวลา"""
    number = (order_number or '').strip()
    order = (Order.objects.filter(order_number=number)
             .prefetch_related('items', 'items__variants', 'tailors').first())
    if order is None:
        return {'found': False, 'order_number': number}

    progress = [{'stage': label, 'done_at': _iso_dt(getattr(order, field))}
                for _slug, label, field in STAGE_TIMELINE]
    progress.append({'stage': 'ลูกค้ารับ', 'done_at': _iso_dt(order.awaiting_pickup_at)})
    progress.append({'stage': 'ส่งแล้ว', 'done_at': _iso_dt(order.shipped_at)})

    data = _order_row(order)
    data.update({
        'found': True,
        'status': order.status,
        'waiting_confirm': bool(order.waiting_confirm),
        'production_place': order.production_place or '',
        'print_date': _iso_date(order.print_date),
        'designer_name': order.designer_name or '',
        'design_doc_number': order.design_doc_number or '',
        'fabric_spec': order.fabric_spec or '',
        'extra_note': order.extra_note or '',
        'created_by': order.created_by.username if order.created_by_id else '',
        # ชื่อ key ต้องไม่ชวนสับสนกับขั้นผลิต "พิมพ์" (print_done_at ใน progress) — 2026-10-15 LLM เคย
        # อ่าน printed_at (เวลาพิมพ์ใบงานกระดาษ A4) เป็น "พิมพ์ 09:18" ทั้งที่ current_stage = ยังไม่เริ่ม
        'sheet_printed_at': _iso_dt(order.printed_at),  # เวลาพิมพ์ใบงานกระดาษ ไม่ใช่ขั้นผลิต
        'needs_repair': bool(order.needs_repair),
        'tailors': [t.name for t in order.tailors.all()],
        'items': [_item_row(i) for i in order.items.all()],
        'progress': progress,
    })
    return data


def daily_summary(date=None):
    """สรุปใบงานของวัน (default วันนี้) — query เดียวกับหน้า "สรุปรายวัน"
    ผ่าน orders.views._daily_summary_context (รับ request → ส่ง shim ที่มีแค่ GET)"""
    request = SimpleNamespace(GET={'date': str(date or '')})
    ctx = _daily_summary_context(request)
    return {
        'date': _iso_date(ctx['day']),
        'order_count': ctx['order_count'],
        'total_qty': ctx['total_qty'],
        'orders': [_order_row(o) for o in ctx['orders']],
    }


def overdue_orders():
    """ใบที่ค้างเกิน 7 วันโดยยังไม่เริ่มผลิตเลย — query เดียวกับ tab "ค้างเกิน 7 วัน"
    (orders.views._report_stuck_rows) · ค้างนานสุดก่อน"""
    rows = _report_stuck_rows(sort='date_asc')
    out = []
    for r in rows:
        row = _order_row(r['order'])
        row['days_stuck'] = r['days_stuck']
        out.append(row)
    return {'count': len(out), 'orders': out}


# ---------------------------------------------------------------------------
# schema + dispatcher
# ---------------------------------------------------------------------------

_SOURCE_LIST = ', '.join(k for k, _ in Order.SOURCE_CHOICES)

TOOL_SCHEMAS = [
    {
        'type': 'function',
        'function': {
            'name': 'search_orders',
            'description': ('ค้นหาใบออร์เดอร์ตามเงื่อนไข (ทุกเงื่อนไข optional และ AND กัน) '
                            'คืนรายการสรุป ใหม่สุดก่อน พร้อม total_count ถ้าผลเกิน limit '
                            '(แต่ละใบมี customer_link = ลิงก์แชท/เบอร์ลูกค้า และ shipping_address)'),
            'parameters': {
                'type': 'object',
                'properties': {
                    'customer_name': {'type': 'string', 'description': 'ชื่อลูกค้า (ค้นบางส่วนได้)'},
                    'shirt_name': {'type': 'string', 'description': 'ชื่องาน/ชื่อเสื้อ (ค้นบางส่วนได้)'},
                    'order_number': {'type': 'string', 'description': 'เลขออร์เดอร์ เช่น 6910-116 (ค้นบางส่วนได้)'},
                    'source': {'type': 'string', 'description': 'แหล่งที่มา/เพจ: ' + _SOURCE_LIST},
                    'date_from': {'type': 'string', 'description': 'วันที่สร้างตั้งแต่ YYYY-MM-DD'},
                    'date_to': {'type': 'string', 'description': 'วันที่สร้างถึง YYYY-MM-DD'},
                    'stage': {
                        'type': 'string', 'enum': STAGE_KEY_SET,
                        'description': ('ขั้นผลิตปัจจุบัน: not_started=ยังไม่เริ่ม, printed=พิมพ์แล้ว, '
                                        'rolled=โรลแล้ว, cut=ตัดแล้ว, sorted=คัดแล้ว, sewing=ส่งเย็บแล้ว, '
                                        'packed=รีด+แพ็คแล้ว, awaiting_pickup=ลูกค้ารับแล้ว (จบงาน), shipped=ส่งแล้ว'),
                    },
                    'has_balance': {'type': 'boolean', 'description': 'true = ยังค้างชำระ (ยอดรวม > มัดจำ)'},
                    'limit': {'type': 'integer', 'description': 'จำนวนสูงสุด (default 10, max 30)'},
                },
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_order',
            'description': ('รายละเอียดใบออร์เดอร์ใบเดียว: รายการเสื้อ (ประเภท/สี/ไซส์) + ความคืบหน้าทุกขั้นพร้อมเวลา '
                            '(current_stage/progress = ขั้นผลิตจริง; sheet_printed_at = เวลาพิมพ์ใบงานกระดาษ ไม่ใช่ขั้นผลิต)'),
            'parameters': {
                'type': 'object',
                'properties': {
                    'order_number': {'type': 'string', 'description': 'เลขออร์เดอร์เต็ม เช่น 6910-116'},
                },
                'required': ['order_number'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'daily_summary',
            'description': 'สรุปใบงานที่สร้างในวันหนึ่ง (จำนวนใบ/จำนวนตัว/รายการ) เหมือนหน้า "สรุปรายวัน"',
            'parameters': {
                'type': 'object',
                'properties': {
                    'date': {'type': 'string', 'description': 'วันที่ YYYY-MM-DD (ไม่ระบุ = วันนี้)'},
                },
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'overdue_orders',
            'description': 'ใบที่ค้างเกิน 7 วันโดยยังไม่เริ่มผลิตเลย (เหมือน tab "ค้างเกิน 7 วัน")',
            'parameters': {'type': 'object', 'properties': {}},
        },
    },
]

TOOL_NAMES = [t['function']['name'] for t in TOOL_SCHEMAS]


def run_tool(name, args):
    """เรียก tool ตามชื่อ (เฉพาะใน TOOL_NAMES) ด้วย kwargs จาก LLM — ไม่ throw
    คืน dict เสมอ (พังเป็น {'error': ...}) ให้ LLM อ่านต่อได้"""
    if name not in TOOL_NAMES:
        return {'error': 'unknown tool %s' % name}
    if not isinstance(args, dict):
        args = {}
    func = getattr(sys.modules[__name__], name)
    try:
        result = func(**args)
    except TypeError as exc:  # arg ชื่อผิด/เกิน
        logger.warning('tool %s args ไม่ถูก %r: %s', name, args, exc)
        return {'error': 'bad arguments: %s' % exc}
    except Exception as exc:  # noqa: BLE001
        logger.exception('tool %s พัง (args=%r)', name, args)
        return {'error': str(exc)}
    # กันหลุด type ที่ json ไม่รู้จัก
    return json.loads(json.dumps(result, ensure_ascii=False, default=str))
