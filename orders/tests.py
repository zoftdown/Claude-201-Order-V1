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


class RoiReportTests(TestCase):
    """tab ROI แอดรายเพจ — reuse get_day_rows ไม่แตะ logic กำไรเดิม"""

    def setUp(self):
        self.admin = User.objects.create_superuser('boss2', password='pw1234')
        self.client.force_login(self.admin)
        self.url = reverse('reports')
        self.today = timezone.localdate()

    def unlock(self):
        return self.client.post(f'{self.url}?report=roi',
                                {'stats_pin': settings.STATS_PIN})

    def test_roi_locked_until_pin(self):
        """tab ROI อยู่ในกลุ่ม MONEY_REPORTS — ต้องใส่ STATS_PIN ก่อน"""
        resp = self.client.get(f'{self.url}?report=roi')
        self.assertTrue(resp.context['stats_locked'])
        self.unlock()
        resp = self.client.get(f'{self.url}?report=roi')
        self.assertNotIn('stats_locked', resp.context)
        self.assertIn('roi_rows', resp.context)

    def test_roi_math_and_colors(self):
        """ROI = กำไรขั้นต้น ÷ ค่าแอด + สีตามเกณฑ์ 2.0/1.2 + วันไม่มีแอด = None"""
        page = 'เพจเสื้อคนงาน'
        d_green = self.today - timedelta(days=1)   # gross 1500, ad 500 → 3.0x เขียว
        d_yellow = self.today - timedelta(days=2)  # gross 1500, ad 1000 → 1.5x เหลือง
        d_red = self.today - timedelta(days=3)     # gross 1500, ad 2000 → 0.75x แดง
        d_noad = self.today - timedelta(days=4)    # ไม่กรอกค่าแอด → "—"
        for d in (d_green, d_yellow, d_red, d_noad):
            make_order(d, source=page, price='2000', items=[('short', 10)])
        DailyAdSpend.objects.create(date=d_green, page=page, amount=Decimal('500'))
        DailyAdSpend.objects.create(date=d_yellow, page=page, amount=Decimal('1000'))
        DailyAdSpend.objects.create(date=d_red, page=page, amount=Decimal('2000'))

        self.unlock()
        resp = self.client.get(f'{self.url}?report=roi&page={page}')
        rows = {r['date']: r for r in resp.context['roi_rows']}

        self.assertEqual(rows[d_green]['roi'], 3.0)
        self.assertEqual(rows[d_green]['roi_class'], 'bg-success')
        self.assertEqual(rows[d_green]['ad_per_shirt'], Decimal('50'))
        self.assertEqual(rows[d_yellow]['roi'], 1.5)
        self.assertEqual(rows[d_yellow]['roi_class'], 'bg-warning text-dark')
        self.assertEqual(rows[d_red]['roi'], 0.75)
        self.assertEqual(rows[d_red]['roi_class'], 'bg-danger')
        self.assertIsNone(rows[d_noad]['roi'])
        self.assertIsNone(rows[d_noad]['ad_per_shirt'])

    def test_roi_window_avg_weighted(self):
        """ค่าเฉลี่ย = Σgross ÷ Σad ของวันที่มีแอด (ถ่วงตามเงิน ไม่ใช่ mean รายวัน)"""
        page = 'เพจเสื้อคนงาน'
        d1 = self.today - timedelta(days=1)
        d2 = self.today - timedelta(days=2)
        make_order(d1, source=page, price='2000', items=[('short', 10)])  # gross 1500
        make_order(d2, source=page, price='1000', items=[('short', 10)])  # gross 500
        DailyAdSpend.objects.create(date=d1, page=page, amount=Decimal('500'))
        DailyAdSpend.objects.create(date=d2, page=page, amount=Decimal('500'))

        self.unlock()
        resp = self.client.get(f'{self.url}?report=roi&page={page}')
        # (1500+500) ÷ (500+500) = 2.0 · แอด/ตัว = 1000 ÷ 20 = 50
        self.assertEqual(resp.context['roi_7'], 2.0)
        self.assertEqual(resp.context['roi_ad_per_shirt_7'], Decimal('50'))
        # สัปดาห์ก่อน (วัน 8–14) ไม่มีแอด → เทียบ trend ไม่ได้
        self.assertIsNone(resp.context['roi_prev7'])
        self.assertIsNone(resp.context['roi_trend'])

    def test_roi_page_dropdown_default_and_validate(self):
        """ไม่ส่ง ?page=/ส่งค่ามั่ว → fallback เพจเสื้อคนงาน"""
        self.unlock()
        resp = self.client.get(f'{self.url}?report=roi')
        self.assertEqual(resp.context['roi_page'], 'เพจเสื้อคนงาน')
        resp = self.client.get(f'{self.url}?report=roi&page=ไม่มีเพจนี้')
        self.assertEqual(resp.context['roi_page'], 'เพจเสื้อคนงาน')


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


# ---------------------------------------------------------------------------
# เชื่อมระบบ Brief ลึกขึ้น (V3.6): prefill ?design_job= / command จับคู่ใบเก่า /
# conversion ในรายงาน ROI — mock urllib.request.urlopen ทั้งหมด ไม่ยิง network จริง
# ---------------------------------------------------------------------------
import io
import json as _json
from unittest import mock

from django.core.management import call_command


def _brief_resp(payload):
    """ตัวแทน response ของ urlopen — BytesIO เป็น context manager อยู่แล้ว"""
    return BytesIO(_json.dumps(payload).encode('utf-8'))


def _brief_job_payload(pk, number, customer, **extra):
    row = {
        'id': pk, 'job_number': number, 'customer_name': customer,
        'customer_chat_url': '', 'status': 'รอทำแบบ', 'round': 'รอบแรก',
        'order_ref': '', 'source': '', 'source_label': '',
        'created_at': timezone.localdate().isoformat(),
    }
    row.update(extra)
    return row


@override_settings(BRIEF_API_TOKEN='test-token')
class BriefPrefillTests(TestCase):
    """ปุ่ม "สร้างออเดอร์จากใบนี้" ฝั่ง Brief -> /create/?design_job=<id>"""

    def setUp(self):
        self.user = User.objects.create_superuser('boss3', 'b@x.com', 'x')
        self.client.force_login(self.user)

    def test_prefill_from_design_job(self):
        detail = _brief_job_payload(
            42, 'D-42', 'ป้าแดง', customer_chat_url='https://m.me/pd',
            source='konngan', source_label='เพจเสื้อคนงาน')
        with mock.patch('urllib.request.urlopen', return_value=_brief_resp(detail)):
            resp = self.client.get(reverse('order_create') + '?design_job=42')
        self.assertEqual(resp.status_code, 200)
        initial = resp.context['form'].initial
        self.assertEqual(initial['design_doc_number'], 'D-42')
        self.assertEqual(initial['customer_name'], 'ป้าแดง')
        self.assertEqual(initial['customer_link'], 'https://m.me/pd')
        self.assertEqual(initial['source'], 'เพจเสื้อคนงาน')
        # hidden brief_job_id ผูกให้เลย (ผ่าน context brief_job)
        self.assertEqual(resp.context['brief_job']['id'], 42)

    def test_prefill_source_not_matching_choices_skipped(self):
        # ใบออกแบบ source "อื่นๆ" (label ไม่อยู่ใน SOURCE_CHOICES ฝั่งนี้) -> ไม่เซ็ต source
        detail = _brief_job_payload(7, 'D-7', 'ลุงมี', source='other', source_label='งานวัด')
        with mock.patch('urllib.request.urlopen', return_value=_brief_resp(detail)):
            resp = self.client.get(reverse('order_create') + '?design_job=7')
        self.assertNotIn('source', resp.context['form'].initial)

    def test_brief_down_form_still_works(self):
        with mock.patch('urllib.request.urlopen', side_effect=OSError):
            resp = self.client.get(reverse('order_create') + '?design_job=42')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['form'].initial, {})
        self.assertIsNone(resp.context['brief_job'])

    def test_design_job_garbage_ignored(self):
        resp = self.client.get(reverse('order_create') + '?design_job=abc')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context['brief_job'])


@override_settings(BRIEF_API_TOKEN='test-token')
class LinkBriefJobsCommandTests(TestCase):
    """command จับคู่ design_doc_number (text) -> brief_job_id — เฉพาะแบบมั่นใจ"""

    EXPORT = {'results': [
        _brief_job_payload(1, 'D-10', 'ร้านกาแฟดอยสูง'),
        _brief_job_payload(2, 'D-11', 'คนละคนเลย'),
        _brief_job_payload(3, 'D-12', 'โรงเรียนบ้านไกล', order_ref='6905-1'),
    ]}

    def _make(self, doc, customer):
        return Order.objects.create(
            created_date=timezone.localdate(), source='หน้าร้าน',
            customer_name=customer, shirt_name='งาน', design_doc_number=doc,
        )

    def _run(self, *args):
        self.calls = []

        def fake(req, timeout=None):
            self.calls.append(req)
            if '/api/jobs/export/' in req.full_url:
                return _brief_resp(self.EXPORT)
            return _brief_resp({'ok': True})

        with mock.patch('urllib.request.urlopen', side_effect=fake):
            call_command('link_brief_jobs', *args, stdout=io.StringIO())

    def test_dry_run_writes_nothing(self):
        o = self._make('D-10', 'ร้านกาแฟดอยสูง')
        self._run()
        o.refresh_from_db()
        self.assertIsNone(o.brief_job_id)
        self.assertEqual(o.design_doc_number, 'D-10')

    def test_confirm_matches_only_confident_pairs(self):
        exact = self._make('D-10', 'ร้านกาแฟดอยสูง')          # เลข+ชื่อตรง -> จับคู่
        loose = self._make('d-10', 'กาแฟดอยสูง')              # เลขตรง (เคสต่าง) + ชื่อ substring -> จับคู่
        digits = self._make('10', 'ร้านกาแฟดอยสูง')           # เลขล้วน = D-10 -> จับคู่
        mismatch = self._make('D-11', 'โรงเรียนบ้านไกล')      # เลขตรง ชื่อไม่ตรง -> ข้าม
        nojob = self._make('D-99', 'ใครก็ไม่รู้')             # ไม่มีใบงาน -> ข้าม
        already = self._make('D-12', 'โรงเรียนบ้านไกล')
        already.brief_job_id = 3
        already.save(update_fields=['brief_job_id'])          # ผูกแล้ว -> ไม่แตะ

        self._run('--confirm')
        for o in (exact, loose, digits, mismatch, nojob, already):
            o.refresh_from_db()
        self.assertEqual(exact.brief_job_id, 1)
        self.assertEqual(loose.brief_job_id, 1)
        self.assertEqual(digits.brief_job_id, 1)
        self.assertIsNone(mismatch.brief_job_id)
        self.assertIsNone(nojob.brief_job_id)
        self.assertEqual(already.brief_job_id, 3)
        # text เดิมไม่ถูกแตะสักใบ
        self.assertEqual(loose.design_doc_number, 'd-10')
        self.assertEqual(digits.design_doc_number, '10')
        # ยิง order_ref กลับครั้งเดียวต่อใบงาน (D-10 ยังว่างฝั่ง Brief) —
        # ใบแรกของชุด (สร้างก่อน = exact) เป็นเลขที่ถูกยิง
        pushes = [r for r in self.calls if r.data]
        self.assertEqual(len(pushes), 1)
        self.assertIn('/api/jobs/1/order-ref/', pushes[0].full_url)


class RoiConversionTests(TestCase):
    """ตาราง conversion ใบออกแบบ -> ออเดอร์ ใน tab ROI"""

    def setUp(self):
        self.user = User.objects.create_superuser('boss4', 'b@x.com', 'x')
        self.client.force_login(self.user)

    def unlock(self):
        self.client.post(reverse('reports') + '?report=roi',
                         {'stats_pin': settings.STATS_PIN})

    def _get_roi(self, export):
        def fake(req, timeout=None):
            if '/api/jobs/export/' in req.full_url:
                return _brief_resp(export)
            raise OSError

        with mock.patch('urllib.request.urlopen', side_effect=fake):
            with override_settings(BRIEF_API_TOKEN='test-token'):
                return self.client.get(reverse('reports') + '?report=roi')

    def test_conversion_counts_and_stale_list(self):
        today = timezone.localdate()

        def iso(days_ago):
            return (today - timedelta(days=days_ago)).isoformat()

        export = {'results': [
            # ล่าสุด: มีออเดอร์ผ่าน order_ref
            _brief_job_payload(1, 'D-1', 'ลูกค้า ก', order_ref='6908-1',
                               source_label='เพจเสื้อคนงาน', created_at=iso(1)),
            # ค้างเกิน 7 วัน ไม่มีออเดอร์ -> เข้าลิสต์ stale
            _brief_job_payload(2, 'D-2', 'ลูกค้า ข',
                               source_label='เพจเสื้อคนงาน', created_at=iso(10)),
            # ใหม่ 2 วัน ไม่มีออเดอร์ -> ยังไม่ stale
            _brief_job_payload(3, 'D-3', 'ลูกค้า ค', created_at=iso(2)),
            # ไม่มี order_ref แต่มีออร์เดอร์ผูกผ่าน brief_job_id -> นับว่ามีออเดอร์
            _brief_job_payload(4, 'D-4', 'ลูกค้า ง', created_at=iso(9)),
        ]}
        Order.objects.create(
            created_date=today, source='หน้าร้าน', customer_name='ลูกค้า ง',
            shirt_name='งาน', brief_job_id=4)
        self.unlock()
        resp = self._get_roi(export)
        self.assertTrue(resp.context['conv_ok'])
        stale = resp.context['conv_stale']
        self.assertEqual([j['job_number'] for j in stale], ['D-2'])
        self.assertEqual(stale[0]['days'], 10)
        # รวมทุกสัปดาห์: 4 ใบ, มีออเดอร์ 2 (D-1 order_ref + D-4 ผูกผ่าน brief_job_id)
        weeks = resp.context['conv_weeks']
        self.assertEqual(sum(w['total'] for w in weeks), 4)
        self.assertEqual(sum(w['with_order'] for w in weeks), 2)

    def test_brief_down_roi_tab_still_renders(self):
        self.unlock()
        with mock.patch('urllib.request.urlopen', side_effect=OSError):
            with override_settings(BRIEF_API_TOKEN='test-token'):
                resp = self.client.get(reverse('reports') + '?report=roi')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['conv_ok'])
        self.assertContains(resp, 'ติดต่อระบบ Brief ไม่ได้')
