import base64
import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from orders import views as views_orders
from orders.models import Order, OrderItem, ShirtVariant, StageLog

from . import orders as bot_orders
from . import pending, services, views

SECRET = b'test-secret'


def _sign(body):
    digest = hmac.new(SECRET, body.encode('utf-8'), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _event(extra):
    base = {
        'replyToken': 'rt-1',
        'timestamp': 1700000000000,
        'mode': 'active',
        'webhookEventId': 'ev-1',
        'deliveryContext': {'isRedelivery': False},
        'source': {'type': 'group', 'groupId': 'G1', 'userId': 'U123'},
    }
    base.update(extra)
    return base


def _text_event(text):
    return _event({'type': 'message',
                   'message': {'type': 'text', 'id': 'm1', 'text': text, 'quoteToken': 'q1'}})


def _image_event():
    return _event({'type': 'message', 'message': {
        'type': 'image', 'id': 'm2', 'quoteToken': 'q2',
        'contentProvider': {'type': 'line'}}})


def make_order(number='6910-116', status='รอดำเนินการ', qtys=(5, 3)):
    order = Order.objects.create(
        order_number=number, created_date=timezone.localdate(), source='หน้าร้าน',
        customer_name='คุณทดสอบ', shirt_name='เสื้อทีมงาน', status=status,
        total_price=Decimal('1500'), deposit=Decimal('500'), delivery_method='ส่ง',
    )
    for qty in qtys:
        item = OrderItem.objects.create(order=order, shirt_type='short')
        ShirtVariant.objects.create(
            item=item, collar='คอกลม', sleeve='แขนสั้น',
            sizes=[{'label': 'M', 'qty': qty}, {'label': 'L', 'qty': 1}],
        )
    return order


class WebhookBase(TestCase):
    def setUp(self):
        self.url = reverse('linebot_webhook')
        p = mock.patch.object(views.handler.parser.signature_validator, 'channel_secret', SECRET)
        p.start()
        self.addCleanup(p.stop)
        self.reply = mock.patch.object(views, '_reply')
        self.reply_mock = self.reply.start()
        self.addCleanup(self.reply.stop)

    def _post(self, payload, signature=None):
        body = json.dumps(payload)
        sig = _sign(body) if signature is None else signature
        return self.client.post(
            self.url, data=body, content_type='application/json',
            headers={'X-Line-Signature': sig},
        )

    def _replied_message(self):
        self.reply_mock.assert_called_once()
        token, messages = self.reply_mock.call_args[0]
        self.assertEqual(token, 'rt-1')
        self.assertEqual(len(messages), 1)
        return messages[0]


class LineWebhookBasicTests(WebhookBase):
    def test_get_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_bad_signature_400(self):
        resp = self._post({'destination': 'x', 'events': []}, signature='bad')
        self.assertEqual(resp.status_code, 400)
        self.reply_mock.assert_not_called()

    def test_empty_events_200(self):
        # ปุ่ม Verify ใน LINE console ส่ง events ว่างมาเช็ค
        resp = self._post({'destination': 'x', 'events': []})
        self.assertEqual(resp.status_code, 200)
        self.reply_mock.assert_not_called()

    def test_text_message_no_echo(self):
        resp = self._post({'destination': 'x', 'events': [_text_event('สวัสดี')]})
        self.assertEqual(resp.status_code, 200)
        self.reply_mock.assert_not_called()

    def test_text_ping_pong(self):
        resp = self._post({'destination': 'x', 'events': [_text_event(' บอท ping ')]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._replied_message().text, 'pong')

    def test_join_event(self):
        ev = _event({'type': 'join', 'source': {'type': 'group', 'groupId': 'G1'}})
        resp = self._post({'destination': 'x', 'events': [ev]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._replied_message().text, 'สวัสดี น้องหมอนทองพร้อมใช้งาน')

    def test_other_event_ignored(self):
        ev = _event({'type': 'follow'})
        resp = self._post({'destination': 'x', 'events': [ev]})
        self.assertEqual(resp.status_code, 200)
        self.reply_mock.assert_not_called()

    def test_source_logged_with_group_and_user(self):
        with self.assertLogs('line_bot.views', level='INFO') as cm:
            self._post({'destination': 'x', 'events': [_text_event('x')]})
        joined = '\n'.join(cm.output)
        self.assertIn('groupId=G1', joined)
        self.assertIn('userId=U123', joined)

    def test_reply_failure_still_200(self):
        # reply พัง (เช่น token ผิด/เน็ตล่ม) → log แล้วยังตอบ 200 ให้ LINE
        self.reply.stop()
        try:
            with mock.patch.object(views, 'MessagingApi') as api, \
                    mock.patch.object(views.settings, 'LINE_CHANNEL_ACCESS_TOKEN', 'tok'):
                api.return_value.reply_message.side_effect = RuntimeError('boom')
                resp = self._post({'destination': 'x', 'events': [_text_event('บอท ping')]})
        finally:
            self.reply.start()
        self.assertEqual(resp.status_code, 200)


class ImageOrderFlowTests(WebhookBase):
    """flow รูป → รายการรอ → ข้อความยืนยัน — mock ดาวน์โหลดรูป + LLM ไม่ยิง network"""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        dl = mock.patch.object(services, 'download_line_image', return_value=b'img')
        dl.start()
        self.addCleanup(dl.stop)
        self.llm = mock.patch.object(services, 'read_order_number', return_value='6910-116')
        self.llm_mock = self.llm.start()
        self.addCleanup(self.llm.stop)

    def _post_image(self, number=None, **src):
        if number is not None:
            self.llm_mock.return_value = number
        ev = _image_event()
        ev['source'].update(src)
        resp = self._post({'destination': 'x', 'events': [ev]})
        self.assertEqual(resp.status_code, 200)
        return resp

    def _post_text(self, text, **src):
        ev = _text_event(text)
        ev['source'].update(src)
        resp = self._post({'destination': 'x', 'events': [ev]})
        self.assertEqual(resp.status_code, 200)
        return resp

    def test_c_no_number_silent(self):
        self.llm_mock.return_value = None
        self._post_image()
        self.reply_mock.assert_not_called()
        self.assertEqual(pending.get_pending('G1', 'U123'), [])

    def test_download_failed_silent(self):
        with mock.patch.object(services, 'download_line_image', return_value=None):
            self._post_image()
        self.reply_mock.assert_not_called()
        self.llm_mock.assert_not_called()

    def test_image_alone_does_not_tick(self):
        """รูปอย่างเดียว ไม่มีข้อความยืนยัน → ไม่ reply ไม่ติ๊ก แค่เข้ารายการรอ"""
        order = make_order()
        self._post_image()
        self.reply_mock.assert_not_called()
        order.refresh_from_db()
        self.assertIsNone(order.packed_at)
        self.assertEqual(StageLog.objects.count(), 0)
        self.assertEqual(pending.get_pending('G1', 'U123'), ['6910-116'])

    def test_three_images_then_confirm_one_reply(self):
        """รูป 3 ใบ (ใบหนึ่งส่งซ้ำ) + "ครบครับ" → ติ๊ก 3 ใบ สรุปข้อความเดียว mention ครั้งเดียว"""
        orders = [make_order('6910-1', qtys=(1,)), make_order('6910-2', qtys=(2,)),
                  make_order('6910-3', qtys=(3,))]
        for n in ('6910-1', '6910-2', '6910-1', '6910-3'):
            self._post_image(n)
        self.reply_mock.assert_not_called()
        self._post_text('ครบครับ')
        msg = self._replied_message()
        self.assertEqual(msg.text.count('@แอดมิน'), 1)
        self.assertEqual(msg.text.count('✅'), 3)
        self.assertEqual(msg.text.count(bot_orders.SEPARATOR), 2)
        self.assertLess(msg.text.index('6910-1 '), msg.text.index('6910-2 '))
        self.assertLess(msg.text.index('6910-2 '), msg.text.index('6910-3 '))
        for o in orders:
            o.refresh_from_db()
            self.assertIsNotNone(o.packed_at)
        self.assertEqual(StageLog.objects.filter(action='pack_done').count(), 3)
        self.assertIn('ยืนยันจากข้อความ', StageLog.objects.first().note)
        # รายการรอถูกล้าง — พิมพ์ "ครบ" ซ้ำไม่ตอบอะไรอีก
        self.assertEqual(pending.get_pending('G1', 'U123'), [])
        self.reply_mock.reset_mock()
        self._post_text('ครบ')
        self.reply_mock.assert_not_called()

    def test_confirm_word_in_sentence(self):
        make_order()
        self._post_image()
        self._post_text('อันนี้เสร็จแล้วนะ')
        self.assertTrue(self._replied_message().text.startswith('@แอดมิน ✅ 6910-116'))

    def test_other_user_confirm_does_not_tick(self):
        """คนละคนพิมพ์ครบ → ไม่ติ๊กของอีกคน และรายการรอของคนเดิมยังอยู่"""
        order = make_order()
        self._post_image()
        self._post_text('ครบครับ', userId='U999')
        self.reply_mock.assert_not_called()
        order.refresh_from_db()
        self.assertIsNone(order.packed_at)
        self.assertEqual(pending.get_pending('G1', 'U123'), ['6910-116'])

    def test_other_group_confirm_does_not_tick(self):
        make_order()
        self._post_image()
        self._post_text('ครบครับ', groupId='G2')
        self.reply_mock.assert_not_called()
        self.assertEqual(pending.get_pending('G1', 'U123'), ['6910-116'])

    def test_pending_expires(self):
        """รายการรอหมดอายุ (10 นาที) → "ครบ" ไม่ติ๊กอะไร"""
        order = make_order()
        with mock.patch.object(pending, 'PENDING_TTL', 0):
            self._post_image()
        self.assertEqual(pending.get_pending('G1', 'U123'), [])
        self._post_text('ครบครับ')
        self.reply_mock.assert_not_called()
        order.refresh_from_db()
        self.assertIsNone(order.packed_at)

    def test_cancel_clears_pending(self):
        order = make_order()
        self._post_image()
        self._post_text('บอท ยกเลิก')
        self.assertEqual(self._replied_message().text, 'ยกเลิกแล้ว')
        self.assertEqual(pending.get_pending('G1', 'U123'), [])
        self.reply_mock.reset_mock()
        self._post_text('ครบครับ')
        self.reply_mock.assert_not_called()
        order.refresh_from_db()
        self.assertIsNone(order.packed_at)

    def test_direct_command_without_image(self):
        """"บอท เสร็จ 6910-171 6910-172" → ติ๊กทันที หลายเลขคั่นช่องว่าง"""
        a, b = make_order('6910-171', qtys=(1,)), make_order('6910-172', qtys=(2,))
        self._post_text('บอท เสร็จ 6910-171 6910-172')
        msg = self._replied_message()
        self.assertEqual(msg.text.count('✅'), 2)
        self.assertEqual(msg.text.count(bot_orders.SEPARATOR), 1)
        for o in (a, b):
            o.refresh_from_db()
            self.assertIsNotNone(o.packed_at)
        self.assertIn('บอท เสร็จ', StageLog.objects.first().note)
        self.llm_mock.assert_not_called()

    def test_direct_command_not_found_and_already(self):
        order = make_order('6910-5', qtys=(1,))
        Order.objects.filter(pk=order.pk).update(packed_at=timezone.now())
        self._post_text('บอท เสร็จ 6910-5 6910-6')
        msg = self._replied_message()
        self.assertEqual(msg.text, '\n'.join([
            '@แอดมิน 6910-5 ติ๊กแล้วอยู่แล้ว (รีด+แพ็ค)',
            bot_orders.SEPARATOR,
            'อ่านได้เลข 6910-6 แต่ไม่พบในระบบ',
        ]))
        self.assertEqual(StageLog.objects.count(), 0)

    def test_direct_command_does_not_consume_pending(self):
        """คำสั่งตรงไม่แตะรายการรอของคนนั้น"""
        make_order('6910-1', qtys=(1,))
        make_order('6910-2', qtys=(1,))
        self._post_image('6910-1')
        self._post_text('บอท เสร็จ 6910-2')
        self.assertEqual(pending.get_pending('G1', 'U123'), ['6910-1'])

    def test_d_not_found(self):
        self._post_image()
        self._post_text('ครบ')
        msg = self._replied_message()
        self.assertEqual(msg.text, '@แอดมิน อ่านได้เลข 6910-116 แต่ไม่พบในระบบ')
        self.assertEqual(StageLog.objects.count(), 0)

    def test_e_already_packed(self):
        """ติ๊กรีด+แพ็คไว้แล้ว (packed_at มีค่า — รวมใบเก่าที่ backfill ไม่มี StageLog) → ไม่ติ๊กซ้ำ"""
        order = make_order()
        packed = timezone.now() - timedelta(days=1)
        Order.objects.filter(pk=order.pk).update(packed_at=packed)
        self._post_image()
        self._post_text('เสร็จ')
        self.assertEqual(self._replied_message().text,
                         '@แอดมิน 6910-116 ติ๊กแล้วอยู่แล้ว (รีด+แพ็ค)')
        self.assertEqual(StageLog.objects.count(), 0)
        order.refresh_from_db()
        self.assertEqual(order.packed_at, packed)

    def test_e_status_done_but_not_packed_still_ticks(self):
        """status เป็น "เสร็จแล้ว" ไม่ใช่ตัวตัดสิน — ดูที่ขั้นรีด+แพ็คเท่านั้น"""
        make_order(status='เสร็จแล้ว')
        self._post_image()
        self._post_text('ครบ')
        self.assertTrue(self._replied_message().text.startswith('@แอดมิน ✅ 6910-116'))
        self.assertEqual(StageLog.objects.filter(action='pack_done').count(), 1)

    def test_f_mark_pack_done_and_summary(self):
        order = make_order()
        self._post_image()
        self._post_text('ครบครับ')
        order.refresh_from_db()
        # ติ๊กขั้นรีด+แพ็คผ่านกลไกเดียวกับปุ่มแผนกแพ็ค — status ไม่ถูกแตะ
        self.assertIsNotNone(order.packed_at)
        self.assertEqual(order.status, 'รอดำเนินการ')
        self.assertIsNone(order.shipped_at)
        self.assertIsNone(order.awaiting_pickup_at)
        log = StageLog.objects.get(order=order)
        self.assertEqual((log.department, log.action), ('pack', 'pack_done'))
        self.assertIn('บอท LINE', log.note)
        self.assertIn('G1', log.note)
        msg = self._replied_message()
        self.assertEqual(msg.text, '\n'.join([
            '@แอดมิน ✅ 6910-116 เสื้อทีมงาน',
            'ลูกค้า: คุณทดสอบ',
            'รวม 10 ตัว',
            'ยอด 1,500 มัดจำ 500 ค้าง 1,000',
            'ส่ง',
        ]))

    @override_settings(LINE_ADMIN_USER_IDS=['Uaaa', 'Ubbb'])
    def test_f_mention_admins_textv2(self):
        make_order()
        self._post_image()
        self._post_text('ครบครับ')
        msg = self._replied_message()
        self.assertIsInstance(msg, views.TextMessageV2)
        self.assertTrue(msg.text.startswith('{admin0} {admin1} ✅ 6910-116'))
        d = msg.to_dict()
        self.assertEqual(d['substitution']['admin0']['mentionee']['userId'], 'Uaaa')
        self.assertEqual(d['substitution']['admin1']['mentionee']['userId'], 'Ubbb')


class ParseOrderNumberTests(TestCase):
    def test_plain_json(self):
        self.assertEqual(services.parse_order_number('{"order_number": "6910-116"}'), '6910-116')

    def test_code_fence(self):
        self.assertEqual(
            services.parse_order_number('```json\n{"order_number": "6910-1"}\n```'), '6910-1')

    def test_null(self):
        self.assertIsNone(services.parse_order_number('{"order_number": null}'))

    def test_broken_json_is_none(self):
        self.assertIsNone(services.parse_order_number('ไม่แน่ใจครับ {order_number'))
        self.assertIsNone(services.parse_order_number(''))
        self.assertIsNone(services.parse_order_number(None))

    def test_regex_rejects_bad_format(self):
        self.assertIsNone(services.parse_order_number('{"order_number": "69101-16"}'))
        self.assertIsNone(services.parse_order_number('{"order_number": "6910-1234"}'))
        self.assertIsNone(services.parse_order_number('{"order_number": 6910}'))

    def test_text_wrapped_json(self):
        self.assertEqual(
            services.parse_order_number('คำตอบ: {"order_number": "6910-116"} ครับ'), '6910-116')


@override_settings(RUNNINGHUB_API_KEY='k')
class ReadOrderNumberLLMTests(TestCase):
    def _fake_client(self, content=None, error=None):
        client = mock.MagicMock()
        if error:
            client.chat.completions.create.side_effect = error
        else:
            client.chat.completions.create.return_value = mock.MagicMock(
                choices=[mock.MagicMock(message=mock.MagicMock(content=content))])
        return client

    def test_llm_returns_number(self):
        client = self._fake_client('{"order_number": "6910-116"}')
        with mock.patch('openai.OpenAI', return_value=client):
            self.assertEqual(services.read_order_number(b'img'), '6910-116')
        kwargs = client.chat.completions.create.call_args[1]
        self.assertEqual(kwargs['temperature'], 0)
        self.assertEqual(kwargs['max_tokens'], services.LLM_MAX_TOKENS)
        self.assertEqual(kwargs['extra_body'], {'reasoning_effort': 'none'})
        self.assertTrue(kwargs['messages'][1]['content'][1]['image_url']['url']
                        .startswith('data:image/jpeg;base64,'))

    def test_llm_error_is_none(self):
        client = self._fake_client(error=TimeoutError('slow'))
        with mock.patch('openai.OpenAI', return_value=client), \
                self.assertLogs('line_bot.services', level='ERROR'):
            self.assertIsNone(services.read_order_number(b'img'))

    def test_llm_garbage_is_none(self):
        client = self._fake_client('not json at all')
        with mock.patch('openai.OpenAI', return_value=client):
            self.assertIsNone(services.read_order_number(b'img'))

    @override_settings(RUNNINGHUB_API_KEY='')
    def test_no_key_is_none(self):
        self.assertIsNone(services.read_order_number(b'img'))


class BotOrdersTests(TestCase):
    def test_process_unknown(self):
        self.assertEqual(bot_orders.process_order_number('6901-1'),
                         'อ่านได้เลข 6901-1 แต่ไม่พบในระบบ')

    def test_detail_timeline_shows_pack_done(self):
        """สิ่งที่ผู้ใช้เห็น: แถบความคืบหน้าหน้า detail ติ๊ก "รีด+แพ็ค" หลังบอททำงาน"""
        order = make_order()
        bot_orders.process_order_number('6910-116')
        order.refresh_from_db()
        timeline = {s['label']: s['state'] for s in views_orders._build_detail_timeline(order)}
        self.assertEqual(timeline['รีด+แพ็ค'], 'done')
        # ขั้นก่อนหน้าไม่ถูกแตะ — ขั้นว่างแรก (พิมพ์) ยังเป็น current ตาม logic เดิมของหน้า detail
        self.assertEqual(timeline['พิมพ์'], 'current')
        self.assertEqual(timeline['ส่งของ'], 'pending')

    def test_web_pack_button_uses_same_helper(self):
        """ปุ่ม "รีดแพ็คแล้ว" ของแผนกแพ็คกับบอทใช้ mark_pack_done ตัวเดียว → log หน้าตาเดียวกัน"""
        order = make_order()
        ok, _ = views_orders._apply_action(order, 'pack', 'pack_done', None)
        self.assertTrue(ok)
        log = StageLog.objects.get(order=order)
        self.assertEqual((log.department, log.action, log.note), ('pack', 'pack_done', ''))
        self.assertEqual(bot_orders.process_order_number('6910-116'),
                         '6910-116 ติ๊กแล้วอยู่แล้ว (รีด+แพ็ค)')
        self.assertEqual(StageLog.objects.count(), 1)

    def test_money_format(self):
        self.assertEqual(bot_orders._money(Decimal('1500.00')), '1,500')
        self.assertEqual(bot_orders._money(Decimal('1500.50')), '1,500.50')
        self.assertEqual(bot_orders._money(None), '0')
