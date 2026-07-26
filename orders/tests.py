"""Regression tests — โฟกัสรายงานกำไรรายวัน (orders/profit.py + tab ใน /reports/)
และ flow เดิมที่ห้ามพัง (สร้างใบงาน, หน้า list, รายงานเดิม).

รัน: python manage.py test orders
"""
import tempfile
from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    DailyAdSpend, DailySummary, Order, OrderItem, ShirtCost, ShirtVariant,
)
from .profit import compute_day, get_day_rows, invalidate_days, totals


def make_order(day, source='หน้าร้าน', price='2000', items=()):
    """สร้างใบงานพร้อม item/variant แบบย่อ — items = [(shirt_type, qty), ...]"""
    order = Order.objects.create(
        created_date=day, source=source, customer_name='ทดสอบ',
        shirt_name='งานทดสอบ', total_price=Decimal(price),
    )
    for shirt_type, qty in items:
        item = OrderItem.objects.create(order=order, shirt_type=shirt_type)
        ShirtVariant.objects.create(
            item=item, collar='คอกลม', sleeve='แขนสั้น',
            sizes=[{'label': 'M', 'qty': qty}],
        )
    return order


class ShirtCostSeedTests(TestCase):
    def test_seed_costs_from_migration(self):
        """migration 0023 seed ต้นทุน short=50, long=65, polo=85"""
        costs = {c.shirt_type: c.cost for c in ShirtCost.objects.all()}
        self.assertEqual(costs, {
            'short': Decimal('50'), 'long': Decimal('65'), 'polo': Decimal('85'),
        })


class ProfitLogicTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.yesterday = self.today - timedelta(days=1)

    def test_gross_and_net_math(self):
        """กำไรขั้นต้น = ยอดขาย − ตัว×ต้นทุนตามประเภท; net = ขั้นต้น − ค่าแอด.
        item ไม่ระบุประเภท → คิดแบบ short + นับ defaulted_shirts"""
        make_order(self.yesterday, items=[('short', 10), ('', 5)], price='2000')
        make_order(self.yesterday, items=[('polo', 4)], price='1000',
                   source='เพจเสื้อเนินสูง')
        DailyAdSpend.objects.create(date=self.yesterday, page='หน้าร้าน',
                                    amount=Decimal('300'))

        rows = {r['page']: r for r in compute_day(self.yesterday)}

        shop = rows['หน้าร้าน']
        self.assertEqual(shop['shirts'], 15)
        self.assertEqual(shop['shirt_cost'], Decimal('750'))   # (10+5)×50
        self.assertEqual(shop['gross_profit'], Decimal('1250'))
        self.assertEqual(shop['ad_spend'], Decimal('300'))
        self.assertEqual(shop['net_after_ads'], Decimal('950'))
        self.assertEqual(shop['defaulted_shirts'], 5)

        page = rows['เพจเสื้อเนินสูง']
        self.assertEqual(page['shirt_cost'], Decimal('340'))   # 4×85
        self.assertEqual(page['gross_profit'], Decimal('660'))
        self.assertEqual(page['defaulted_shirts'], 0)

    def test_ad_spend_only_page_appears(self):
        """เพจที่มีแต่ค่าแอดไม่มีออร์เดอร์ ก็ต้องขึ้นแถว (net ติดลบ)"""
        DailyAdSpend.objects.create(date=self.yesterday, page='Tiktok',
                                    amount=Decimal('120'))
        rows = compute_day(self.yesterday)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['net_after_ads'], Decimal('-120'))

    def test_past_day_cached_today_not(self):
        """วันที่ผ่านแล้ว → เขียน cache รอบแรกแล้วอ่านจาก cache;
        วันนี้ → คำนวณสด ไม่เขียน cache"""
        make_order(self.yesterday, items=[('short', 2)])
        make_order(self.today, items=[('short', 2)])

        get_day_rows(self.yesterday)
        self.assertTrue(DailySummary.objects.filter(date=self.yesterday).exists())

        get_day_rows(self.today)
        self.assertFalse(DailySummary.objects.filter(date=self.today).exists())

    def test_cache_serves_stale_until_invalidated(self):
        """cache ค้างค่าเดิมจนกว่าจะ invalidate → คำนวณใหม่ได้ค่าล่าสุด"""
        order = make_order(self.yesterday, items=[('short', 2)], price='500')
        first = get_day_rows(self.yesterday)
        self.assertEqual(first[0]['revenue'], Decimal('500'))

        Order.objects.filter(pk=order.pk).update(total_price=Decimal('900'))
        cached = get_day_rows(self.yesterday)
        self.assertEqual(cached[0]['revenue'], Decimal('500'))  # ยังอ่านจาก cache

        invalidate_days(self.yesterday)
        fresh = get_day_rows(self.yesterday)
        self.assertEqual(fresh[0]['revenue'], Decimal('900'))

    def test_totals(self):
        make_order(self.yesterday, items=[('short', 1)], price='100')
        make_order(self.yesterday, items=[('long', 2)], price='300',
                   source='Shopee')
        t = totals(compute_day(self.yesterday))
        self.assertEqual(t['orders'], 2)
        self.assertEqual(t['shirts'], 3)
        self.assertEqual(t['revenue'], Decimal('400'))
        self.assertEqual(t['shirt_cost'], Decimal('180'))      # 50 + 2×65


class ProfitViewTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('boss', password='pw1234')
        self.client.force_login(self.admin)
        self.url = reverse('reports')

    def unlock(self, report='profit'):
        return self.client.post(f'{self.url}?report={report}',
                                {'stats_pin': settings.STATS_PIN})

    def test_profit_tab_locked_until_pin(self):
        resp = self.client.get(f'{self.url}?report=profit')
        self.assertTrue(resp.context['stats_locked'])
        self.unlock()
        resp = self.client.get(f'{self.url}?report=profit')
        self.assertNotIn('stats_locked', resp.context)
        self.assertIn('profit_rows', resp.context)

    def test_pin_unlocks_all_money_tabs(self):
        self.unlock(report='stats')
        for report in ('profit', 'profit_month', 'stats'):
            resp = self.client.get(f'{self.url}?report={report}')
            self.assertNotIn('stats_locked', resp.context, report)

    def test_ad_spend_form_saves_and_invalidates(self):
        day = timezone.localdate() - timedelta(days=1)
        make_order(day, items=[('short', 1)])
        get_day_rows(day)  # เขียน cache ก่อน
        self.unlock()

        resp = self.client.post(
            f'{self.url}?report=profit&date={day.isoformat()}',
            {'ad_spend_submit': '1', 'date': day.isoformat(),
             'ad__หน้าร้าน': '250', 'ad__Tiktok': ''},
        )
        self.assertEqual(resp.status_code, 302)
        spend = DailyAdSpend.objects.get(date=day, page='หน้าร้าน')
        self.assertEqual(spend.amount, Decimal('250'))
        self.assertFalse(DailyAdSpend.objects.filter(date=day, page='Tiktok').exists())
        # cache ของวันนั้นถูกล้าง → รอบหน้า net รวมค่าแอดใหม่
        self.assertFalse(DailySummary.objects.filter(date=day).exists())
        rows = get_day_rows(day)
        self.assertEqual(rows[0]['ad_spend'], Decimal('250'))

    def test_profit_month_renders(self):
        make_order(timezone.localdate(), items=[('short', 3)])
        self.unlock()
        resp = self.client.get(f'{self.url}?report=profit_month')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('pm_total', resp.context)


def tiny_png(name='design.png'):
    """รูป PNG 2×2 สำหรับอัปโหลดในเทสต์ (item ใหม่ต้องมีรูปถึงจะถูกสร้าง
    — พฤติกรรม formset เดิม)."""
    from PIL import Image
    buf = BytesIO()
    Image.new('RGB', (2, 2), 'red').save(buf, 'PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='order_test_media_'))
class RegressionTests(TestCase):
    """flow เดิมต้องไม่พัง: สร้างใบงาน, list, แก้ใบแล้ว cache โดนล้าง"""

    def setUp(self):
        self.staff = User.objects.create_user('staff1', password='pw1234')
        Group.objects.get_or_create(name='staff')
        self.client.force_login(self.staff)

    def _order_post_data(self, day, shirt_type='short'):
        return {
            'items-0-design_image': tiny_png(),
            'source': 'หน้าร้าน', 'production_place': 'ผลิตเอง',
            'created_date': day.isoformat(),
            'customer_name': 'ลูกค้า regression', 'customer_link': '',
            'shirt_name': 'เสื้อ regression', 'designer_name': '',
            'design_doc_number': '', 'brief_job_id': '',
            'fabric_spec': '', 'special_note': '', 'extra_note': '',
            'total_price': '999', 'deposit': '0',
            'delivery_method': 'รับเอง', 'shipping_address': '',
            'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '0', 'items-MAX_NUM_FORMS': '1000',
            'items-0-shirt_type': shirt_type,
            'items-0-variants-TOTAL_FORMS': '1',
            'items-0-variants-INITIAL_FORMS': '0',
            'items-0-variants-MIN_NUM_FORMS': '0',
            'items-0-variants-MAX_NUM_FORMS': '1000',
            'items-0-variants-0-collar': 'คอกลม',
            'items-0-variants-0-sleeve': 'แขนสั้น',
            'items-0-variants-0-color': '',
            'items-0-variants-0-note': '',
            'items-0-variants-0-sizes_json': '[{"label":"M","qty":7}]',
        }

    def test_order_create_flow_with_shirt_type(self):
        """สร้างใบงานผ่านฟอร์มเดิม + ช่องประเภทเสื้อใหม่ — ต้อง save ได้ครบ"""
        day = timezone.localdate()
        resp = self.client.post(reverse('order_create'), self._order_post_data(day))
        self.assertEqual(resp.status_code, 302, getattr(resp, 'context', None) and
                         resp.context.get('form').errors)
        order = Order.objects.latest('id')
        self.assertEqual(order.items.first().shirt_type, 'short')
        self.assertEqual(order.total_qty, 7)

    def test_order_create_blank_shirt_type_ok(self):
        """ไม่เลือกประเภทเสื้อ (ใบเก่า/ลืมเลือก) ต้องยัง save ได้ — ไม่บังคับ"""
        day = timezone.localdate()
        resp = self.client.post(reverse('order_create'),
                                self._order_post_data(day, shirt_type=''))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Order.objects.latest('id').items.first().shirt_type, '')

    def test_order_edit_invalidates_cache(self):
        """แก้ order ย้อนหลัง → แถว DailySummary ของวันนั้นถูกลบ (invalidate on edit)"""
        day = timezone.localdate() - timedelta(days=3)
        resp = self.client.post(reverse('order_create'), self._order_post_data(day))
        self.assertEqual(resp.status_code, 302)
        order = Order.objects.latest('id')

        get_day_rows(day)  # เขียน cache
        self.assertTrue(DailySummary.objects.filter(date=day).exists())

        data = self._order_post_data(day)
        data['items-INITIAL_FORMS'] = '0'
        resp = self.client.post(reverse('order_edit', args=[order.pk]), data)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(DailySummary.objects.filter(date=day).exists())

    def test_order_list_and_daily_summary_render(self):
        make_order(timezone.localdate(), items=[('short', 1)])
        self.assertEqual(self.client.get(reverse('order_list')).status_code, 200)
        self.assertEqual(self.client.get(reverse('daily_summary')).status_code, 200)
