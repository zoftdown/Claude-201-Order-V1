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
from . import pending, services, stage_cmd, views
from .apps import check_allowed_groups_configured

SECRET = b'test-secret'

# ทุก env ที่ line_bot อ่านจาก settings — override ให้เป็นค่าคงที่ของ test เสมอ (รัน tests บน VPS ที่
# source .env แล้วก็ต้องได้ผลเท่ากัน) · class ย่อยที่ต้องการค่าอื่น override ซ้อนเองได้
TEST_ENV = {
    'LINE_CHANNEL_SECRET': 'test-secret',
    'LINE_CHANNEL_ACCESS_TOKEN': '',
    'LINE_ADMIN_USER_IDS': [],
    'LINE_ALLOWED_GROUP_IDS': [],
    'RUNNINGHUB_API_KEY': '',
    'RUNNINGHUB_BASE_URL': 'https://llm.test/v1',
    'RUNNINGHUB_MODEL': 'qwen/test-model',
    'RUNNINGHUB_FALLBACK_MODEL': '',
    'ANTHROPIC_API_KEY': '',
    'ANTHROPIC_MODEL': 'claude-test',
}


@override_settings(**TEST_ENV)
class BotTestCase(TestCase):
    """ฐานของทุก test ใน line_bot — settings ถูก override ตาม TEST_ENV (สืบทอดถึง subclass)"""


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


@override_settings(LINE_ALLOWED_GROUP_IDS=['G1'])
class WebhookBase(BotTestCase):
    """กลุ่ม G1 = กลุ่มที่อนุญาต (guard fail-closed — ไม่ตั้งจะเงียบทุก event; ดู GroupGuardTests)"""

    def setUp(self):
        self.url = reverse('linebot_webhook')
        p = mock.patch.object(views.handler.parser.signature_validator, 'channel_secret', SECRET)
        p.start()
        self.addCleanup(p.stop)
        self.reply = mock.patch.object(views, '_reply', autospec=True)
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
            with mock.patch.object(views, 'MessagingApi', autospec=True) as api, \
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
        dl = mock.patch.object(services, 'download_line_image', return_value=b'img', autospec=True)
        self.dl_mock = dl.start()
        self.addCleanup(dl.stop)
        self.llm = mock.patch.object(services, 'read_order_number', return_value='6910-116', autospec=True)
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
        self.dl_mock.return_value = None  # patch เดิมจาก setUp (autospec) — ไม่ patch ซ้อน
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

    @override_settings(LINE_ALLOWED_GROUP_IDS=['G1', 'G2'])
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


class ParseOrderNumberTests(BotTestCase):
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
class ReadOrderNumberLLMTests(BotTestCase):
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
        with mock.patch('openai.OpenAI', return_value=client, autospec=True):
            self.assertEqual(services.read_order_number(b'img'), '6910-116')
        kwargs = client.chat.completions.create.call_args[1]
        self.assertEqual(kwargs['temperature'], 0)
        self.assertEqual(kwargs['max_tokens'], services.LLM_MAX_TOKENS)
        self.assertEqual(kwargs['extra_body'], {'reasoning_effort': 'none'})
        self.assertTrue(kwargs['messages'][1]['content'][1]['image_url']['url']
                        .startswith('data:image/jpeg;base64,'))

    def test_llm_error_is_none(self):
        client = self._fake_client(error=TimeoutError('slow'))
        with mock.patch('openai.OpenAI', return_value=client, autospec=True), \
                self.assertLogs('line_bot.services', level='ERROR'):
            self.assertIsNone(services.read_order_number(b'img'))

    def test_llm_garbage_is_none(self):
        client = self._fake_client('not json at all')
        with mock.patch('openai.OpenAI', return_value=client, autospec=True):
            self.assertIsNone(services.read_order_number(b'img'))

    @override_settings(RUNNINGHUB_API_KEY='')
    def test_no_key_is_none(self):
        self.assertIsNone(services.read_order_number(b'img'))


class BotOrdersTests(BotTestCase):
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


# ---------------------------------------------------------------------------
# ขั้น 5 — ถาม-ตอบด้วย LLM tool calling
# ---------------------------------------------------------------------------

from . import history, tools  # noqa: E402


def _tool_call(name, args, call_id='call-1'):
    tc = mock.MagicMock()
    tc.id = call_id
    tc.function.name = name
    tc.function.arguments = json.dumps(args, ensure_ascii=False)
    return tc


def _llm_response(content=None, tool_calls=None, prompt_tokens=100, completion_tokens=20):
    msg = mock.MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    resp = mock.MagicMock()
    resp.choices = [mock.MagicMock(message=msg)]
    resp.usage = mock.MagicMock(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                                total_tokens=prompt_tokens + completion_tokens)
    return resp


def _fake_llm(*responses, error=None):
    client = mock.MagicMock()
    if error:
        client.chat.completions.create.side_effect = error
    else:
        client.chat.completions.create.side_effect = list(responses)
    return client


class QaTriggerTests(WebhookBase):
    """trigger: "บอท ..." / "หมอนทอง ..." / mention บอท → answer_question · ข้อความธรรมดาเงียบ"""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        p = mock.patch.object(services, 'answer_question', return_value='วันนี้มี 2 ใบ', autospec=True)
        self.qa_mock = p.start()
        self.addCleanup(p.stop)

    def _post_text(self, text, mention=None):
        ev = _text_event(text)
        if mention is not None:
            ev['message']['mention'] = mention
        resp = self._post({'destination': 'x', 'events': [ev]})
        self.assertEqual(resp.status_code, 200)

    def test_plain_text_not_sent_to_llm(self):
        self._post_text('วันนี้มีกี่ใบ')
        self.qa_mock.assert_not_called()
        self.reply_mock.assert_not_called()

    def test_bot_prefix_triggers(self):
        self._post_text('บอท วันนี้มีกี่ใบ')
        self.qa_mock.assert_called_once()
        self.assertEqual(self.qa_mock.call_args[0][0], 'วันนี้มีกี่ใบ')
        self.assertEqual(self._replied_message().text, 'วันนี้มี 2 ใบ')

    def test_monthong_prefix_triggers(self):
        self._post_text('หมอนทอง 6910-116 ถึงไหนแล้ว')
        self.assertEqual(self.qa_mock.call_args[0][0], '6910-116 ถึงไหนแล้ว')

    def test_self_mention_triggers_and_strips(self):
        text = '@น้องหมอนทอง งานคุณเอถึงไหน'
        mention = {'mentionees': [{'type': 'user', 'index': 0, 'length': len('@น้องหมอนทอง'),
                                   'userId': 'Ubot', 'isSelf': True}]}
        self._post_text(text, mention=mention)
        self.assertEqual(self.qa_mock.call_args[0][0], 'งานคุณเอถึงไหน')

    def test_other_user_mention_does_not_trigger(self):
        mention = {'mentionees': [{'type': 'user', 'index': 0, 'length': 5,
                                   'userId': 'Uxyz', 'isSelf': False}]}
        self._post_text('@สมชาย งานถึงไหน', mention=mention)
        self.qa_mock.assert_not_called()
        self.reply_mock.assert_not_called()

    def test_empty_question_gets_hint(self):
        self._post_text('บอท')
        self.qa_mock.assert_not_called()
        self.assertEqual(self._replied_message().text, views.QA_EMPTY_TEXT)

    def test_llm_failure_replies_apology(self):
        self.qa_mock.return_value = None
        self._post_text('บอท วันนี้มีกี่ใบ')
        self.assertEqual(self._replied_message().text, views.QA_FAIL_TEXT)

    def test_old_commands_take_precedence(self):
        make_order()
        self._post_text('บอท ping')
        self._post_text('บอท เสร็จ 6910-116')
        self._post_text('บอท ยกเลิก')
        self.qa_mock.assert_not_called()
        self.assertEqual(self.reply_mock.call_count, 3)

    def test_confirm_pending_wins_over_qa(self):
        make_order()
        pending.add_pending('G1', 'U123', '6910-116')
        self._post_text('บอท ครบแล้ว')
        self.qa_mock.assert_not_called()
        self.assertTrue(self._replied_message().text.startswith('@แอดมิน ✅ 6910-116'))

    def test_history_kept_per_user_and_passed(self):
        self._post_text('บอท วันนี้มีกี่ใบ')
        self.qa_mock.return_value = 'เมื่อวานมี 1 ใบ'
        self._post_text('บอท เมื่อวานล่ะ')
        hist = self.qa_mock.call_args[1]['history']
        self.assertEqual(hist, [{'role': 'user', 'content': 'วันนี้มีกี่ใบ'},
                                {'role': 'assistant', 'content': 'วันนี้มี 2 ใบ'}])
        self.assertEqual(len(history.get_history('G1', 'U123')), 4)
        self.assertEqual(history.get_history('G1', 'U999'), [])

    def test_history_capped_at_six(self):
        for i in range(5):
            history.append_history('G1', 'U1', 'q%d' % i, 'a%d' % i)
        hist = history.get_history('G1', 'U1')
        self.assertEqual(len(hist), 6)
        self.assertEqual(hist[0]['content'], 'q2')


@override_settings(RUNNINGHUB_API_KEY='k')
class AnswerQuestionLoopTests(BotTestCase):
    """services.answer_question: mock LLM คืน tool_call → tool ถูกเรียกด้วย args ถูก → คำตอบสุดท้าย"""

    def test_tool_call_then_answer(self):
        client = _fake_llm(
            _llm_response(tool_calls=[_tool_call('search_orders', {'customer_name': 'คุณเอ'})]),
            _llm_response(content='เจอ 1 ใบ: 6910-116'),
        )
        with mock.patch('openai.OpenAI', return_value=client, autospec=True), \
                mock.patch.object(tools, 'search_orders', autospec=True,
                                  return_value={'orders': [], 'total_count': 0}) as tool_mock, \
                self.assertLogs('line_bot.services', level='INFO') as logs:
            answer = services.answer_question('งานคุณเอถึงไหน')
        self.assertEqual(answer, 'เจอ 1 ใบ: 6910-116')
        tool_mock.assert_called_once_with(customer_name='คุณเอ')

        calls = client.chat.completions.create.call_args_list
        self.assertEqual(len(calls), 2)
        first = calls[0][1]
        self.assertEqual(first['temperature'], 0)
        self.assertEqual(first['tools'], tools.TOOL_SCHEMAS)
        self.assertIn('วันนี้คือ', first['messages'][0]['content'])
        # รอบ 2 ต้องมี assistant tool_calls + tool result ต่อท้าย
        second_msgs = calls[1][1]['messages']
        self.assertEqual(second_msgs[-2]['role'], 'assistant')
        self.assertEqual(second_msgs[-2]['tool_calls'][0]['function']['name'], 'search_orders')
        self.assertEqual(second_msgs[-1]['role'], 'tool')
        self.assertEqual(second_msgs[-1]['tool_call_id'], 'call-1')
        joined = '\n'.join(logs.output)
        self.assertIn('search_orders', joined)
        self.assertIn('tokens', joined)
        self.assertIn('งานคุณเอถึงไหน', joined)

    def test_history_prepended(self):
        client = _fake_llm(_llm_response(content='เมื่อวานมี 0 ใบ'))
        hist = [{'role': 'user', 'content': 'วันนี้มีกี่ใบ'},
                {'role': 'assistant', 'content': 'วันนี้มี 2 ใบ'}]
        with mock.patch('openai.OpenAI', return_value=client, autospec=True):
            services.answer_question('เมื่อวานล่ะ', history=hist)
        msgs = client.chat.completions.create.call_args[1]['messages']
        self.assertEqual(msgs[1:3], hist)
        self.assertEqual(msgs[-1], {'role': 'user', 'content': 'เมื่อวานล่ะ'})

    def test_timeout_returns_none(self):
        client = _fake_llm(error=TimeoutError('slow'))
        with mock.patch('openai.OpenAI', return_value=client, autospec=True), \
                self.assertLogs('line_bot.services', level='ERROR'):
            self.assertIsNone(services.answer_question('บอท วันนี้มีกี่ใบ'))

    def test_max_rounds_stops(self):
        looping = [_llm_response(tool_calls=[_tool_call('overdue_orders', {})])
                   for _ in range(services.QA_MAX_ROUNDS + 2)]
        client = _fake_llm(*looping)
        with mock.patch('openai.OpenAI', return_value=client, autospec=True):
            self.assertIsNone(services.answer_question('ค้างกี่ใบ'))
        self.assertEqual(client.chat.completions.create.call_count, services.QA_MAX_ROUNDS)

    def test_unknown_tool_reported_not_crash(self):
        client = _fake_llm(
            _llm_response(tool_calls=[_tool_call('get_profit', {})]),
            _llm_response(content='ทำไม่ได้'),
        )
        with mock.patch('openai.OpenAI', return_value=client, autospec=True):
            self.assertEqual(services.answer_question('กำไรเท่าไหร่'), 'ทำไม่ได้')
        tool_msg = client.chat.completions.create.call_args[1]['messages'][-1]
        self.assertIn('unknown tool', tool_msg['content'])

    @override_settings(RUNNINGHUB_API_KEY='')
    def test_no_key_is_none(self):
        self.assertIsNone(services.answer_question('x'))


class QaToolsTests(BotTestCase):
    """tools อ่านอย่างเดียว: search ตามชื่อลูกค้าเจอ/ไม่เจอ · daily_summary/overdue ตรงกับหน้ารายงาน"""

    def test_search_by_customer_found(self):
        make_order('6910-116')
        make_order('6910-117', qtys=(2,))
        Order.objects.filter(order_number='6910-117').update(customer_name='คุณบี')
        res = tools.search_orders(customer_name='ทดสอบ')
        self.assertEqual(res['total_count'], 1)
        self.assertFalse(res['truncated'])
        row = res['orders'][0]
        self.assertEqual(row['order_number'], '6910-116')
        self.assertEqual(row['total_qty'], 10)
        self.assertEqual(row['total_price'], 1500)
        self.assertEqual(row['balance'], 1000)
        self.assertEqual(row['current_stage'], 'ยังไม่เริ่ม')

    def test_search_by_customer_not_found(self):
        make_order()
        res = tools.search_orders(customer_name='ไม่มีคนนี้')
        self.assertEqual(res, {'orders': [], 'total_count': 0, 'truncated': False})

    def test_search_limit_and_total_count(self):
        for i in range(12):
            make_order('6910-%d' % (200 + i), qtys=(1,))
        res = tools.search_orders(limit=10)
        self.assertEqual(len(res['orders']), 10)
        self.assertEqual(res['total_count'], 12)
        self.assertTrue(res['truncated'])

    def test_search_stage_and_balance(self):
        o = make_order('6910-116')
        o.print_done_at = timezone.now()
        o.save()
        make_order('6910-117', qtys=(1,))
        Order.objects.filter(order_number='6910-117').update(deposit=Decimal('1500'))
        self.assertEqual([r['order_number'] for r in tools.search_orders(stage='printed')['orders']],
                         ['6910-116'])
        self.assertEqual([r['order_number'] for r in tools.search_orders(stage='not_started')['orders']],
                         ['6910-117'])
        self.assertEqual([r['order_number'] for r in tools.search_orders(has_balance=True)['orders']],
                         ['6910-116'])

    def test_get_order_detail_and_progress(self):
        o = make_order()
        views_orders.mark_pack_done(o, note='t')
        data = tools.get_order('6910-116')
        self.assertTrue(data['found'])
        self.assertEqual(len(data['items']), 2)
        self.assertEqual(data['items'][0]['shirt_type'], 'แขนสั้น')
        self.assertEqual(data['items'][0]['variants'][0]['sizes'], {'M': 5, 'L': 1})
        progress = {p['stage']: p['done_at'] for p in data['progress']}
        self.assertIsNone(progress['พิมพ์'])
        self.assertIsNotNone(progress['รีด+แพ็ค'])
        self.assertEqual(data['current_stage'], 'รีด+แพ็คแล้ว')
        self.assertEqual(tools.get_order('0000-1'), {'found': False, 'order_number': '0000-1'})

    def test_daily_summary_matches_report(self):
        make_order('6910-116')
        make_order('6910-117', qtys=(2,))
        old = make_order('6909-1', qtys=(7,))
        Order.objects.filter(pk=old.pk).update(created_date=timezone.localdate() - timedelta(days=1))

        today = timezone.localdate()
        ctx = views_orders._daily_summary_context(
            mock.Mock(GET={'date': today.isoformat()}))
        res = tools.daily_summary(today.isoformat())
        self.assertEqual(res['order_count'], ctx['order_count'])
        self.assertEqual(res['total_qty'], ctx['total_qty'])
        self.assertEqual([r['order_number'] for r in res['orders']],
                         [o.order_number for o in ctx['orders']])
        self.assertEqual(res['order_count'], 2)
        # default = วันนี้ · เมื่อวาน = ใบเก่า
        self.assertEqual(tools.daily_summary()['order_count'], 2)
        yesterday = tools.daily_summary((today - timedelta(days=1)).isoformat())
        self.assertEqual([r['order_number'] for r in yesterday['orders']], ['6909-1'])

    def test_overdue_matches_report(self):
        stuck = make_order('6909-5', qtys=(1,))
        Order.objects.filter(pk=stuck.pk).update(created_date=timezone.localdate() - timedelta(days=10))
        make_order('6910-116')
        res = tools.overdue_orders()
        rows = views_orders._report_stuck_rows(sort='date_asc')
        self.assertEqual([r['order_number'] for r in res['orders']],
                         [r['order'].order_number for r in rows])
        self.assertEqual(res['count'], 1)
        self.assertEqual(res['orders'][0]['days_stuck'], 10)

    def test_run_tool_bad_args_and_unknown(self):
        self.assertIn('error', tools.run_tool('search_orders', {'nope': 1}))
        self.assertIn('error', tools.run_tool('get_profit', {}))
        self.assertEqual(tools.run_tool('overdue_orders', None)['count'], 0)

    def test_schema_names_match_functions(self):
        for name in tools.TOOL_NAMES:
            self.assertTrue(callable(getattr(tools, name)))
        self.assertNotIn('profit', ' '.join(tools.TOOL_NAMES))


def _join_event(**src):
    source = {'type': 'group', 'groupId': 'G1'}
    source.update(src)
    return _event({'type': 'join', 'source': source})


class GroupGuardTests(WebhookBase):
    """guard กลุ่ม (fail-closed): ทำงานเฉพาะ group ที่อยู่ใน LINE_ALLOWED_GROUP_IDS
    กลุ่มอื่น/1:1/room เงียบ · join กลุ่มแปลก → leave · ลิสต์ว่าง = บล็อกหมด"""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        self.leave_patcher = mock.patch.object(views, '_leave_group', autospec=True)
        self.leave_mock = self.leave_patcher.start()
        self.addCleanup(self.leave_patcher.stop)

    def _post_from(self, text, **src):
        ev = _text_event(text)
        ev['source'] = src
        with self.assertLogs('line_bot.views', level='WARNING') as cm:
            resp = self._post({'destination': 'x', 'events': [ev]})
        self.assertEqual(resp.status_code, 200)
        return '\n'.join(cm.output)

    def test_unlisted_group_silent_with_warning(self):
        log = self._post_from('บอท ping', type='group', groupId='G9', userId='U123')
        self.reply_mock.assert_not_called()
        self.assertIn('WARNING', log)
        self.assertIn('groupId=G9', log)

    def test_unlisted_group_image_not_processed(self):
        with mock.patch.object(services, 'download_line_image', autospec=True) as dl:
            ev = _image_event()
            ev['source']['groupId'] = 'G9'
            resp = self._post({'destination': 'x', 'events': [ev]})
        self.assertEqual(resp.status_code, 200)
        dl.assert_not_called()
        self.reply_mock.assert_not_called()

    def test_join_unlisted_group_leaves(self):
        with self.assertLogs('line_bot.views', level='WARNING'):
            resp = self._post({'destination': 'x', 'events': [_join_event(groupId='G9')]})
        self.assertEqual(resp.status_code, 200)
        self.leave_mock.assert_called_once_with('G9')
        self.reply_mock.assert_not_called()

    def test_join_allowed_group_greets_and_stays(self):
        resp = self._post({'destination': 'x', 'events': [_join_event()]})
        self.assertEqual(resp.status_code, 200)
        self.leave_mock.assert_not_called()
        self.assertEqual(self._replied_message().text, views.JOIN_GREETING)

    def test_dm_silent(self):
        self._post_from('บอท ping', type='user', userId='U123')
        self.reply_mock.assert_not_called()

    def test_room_silent(self):
        self._post_from('บอท ping', type='room', roomId='R1', userId='U123')
        self.reply_mock.assert_not_called()
        self.leave_mock.assert_not_called()

    @override_settings(LINE_ALLOWED_GROUP_IDS=[])
    def test_empty_allowlist_blocks_everything(self):
        log = self._post_from('บอท ping', type='group', groupId='G1', userId='U123')
        self.reply_mock.assert_not_called()
        self.assertIn('LINE_ALLOWED_GROUP_IDS ว่าง', log)

    @override_settings(LINE_ALLOWED_GROUP_IDS=[])
    def test_startup_check_logs_error_when_empty(self):
        with self.assertLogs('line_bot.apps', level='ERROR') as cm:
            self.assertFalse(check_allowed_groups_configured())
        self.assertIn('LINE_ALLOWED_GROUP_IDS ว่าง', '\n'.join(cm.output))

    def test_startup_check_ok_when_set(self):
        self.assertTrue(check_allowed_groups_configured())

    def test_leave_group_calls_api_best_effort(self):
        self.leave_patcher.stop()
        self.addCleanup(self.leave_patcher.start)
        # autospec: ชื่อ method ต้องมีจริงใน SDK (prod เคยพังเพราะเรียก leave_from_group ที่ไม่มี)
        with mock.patch.object(views, 'MessagingApi', autospec=True) as api, \
                mock.patch.object(views.settings, 'LINE_CHANNEL_ACCESS_TOKEN', 'tok'):
            views._leave_group('G9')
            api.return_value.leave_group.assert_called_once_with('G9')
            # API พัง → log ไม่ raise
            api.return_value.leave_group.side_effect = RuntimeError('boom')
            with self.assertLogs('line_bot.views', level='ERROR'):
                views._leave_group('G9')


class FixThaiSpacingTests(BotTestCase):
    """services.fix_thai_spacing — ลบช่องว่างที่ LLM แทรกระหว่างอักษรไทยกับสระ/วรรณยุกต์"""

    def test_space_before_mark_removed(self):
        self.assertEqual(services.fix_thai_spacing('เสื ้อทีมงาน ส ีแดง'), 'เสื้อทีมงาน สีแดง')
        self.assertEqual(services.fix_thai_spacing('ชื่ อ คุณเอ'), 'ชื่อ คุณเอ')  # ช่องว่างหลัง อ (พยัญชนะ) คงไว้

    def test_space_after_mark_before_thai_removed(self):
        self.assertEqual(services.fix_thai_spacing('ที่ นี่ มี งาน'), 'ที่นี่มีงาน')  # มี ลงท้ายสระอี → ช่องว่างถัดมาก็ถูกลบ
        self.assertEqual(services.fix_thai_spacing('สี\tแดง'), 'สีแดง')  # tab ก็นับ

    def test_legit_spacing_untouched(self):
        text = 'วันนี้ 2 ใบ\nรวม 10 ตัว · 6910-116 เสื้อทีมงาน 8 ตัว ขั้นพิมพ์\nยอด 1,500 บาท'
        self.assertEqual(services.fix_thai_spacing(text), text)
        self.assertEqual(services.fix_thai_spacing(''), '')
        self.assertIsNone(services.fix_thai_spacing(None))

    @override_settings(RUNNINGHUB_API_KEY='k')
    def test_answer_question_applies_fix(self):
        client = _fake_llm(_llm_response(content='ชื่ องาน เสื้ อทีม'))
        with mock.patch('openai.OpenAI', return_value=client, autospec=True):
            self.assertEqual(services.answer_question('งานอะไร'), 'ชื่องาน เสื้อทีม')


@override_settings(RUNNINGHUB_API_KEY='k', RUNNINGHUB_MODEL='primary/m', RUNNINGHUB_FALLBACK_MODEL='fallback/m',
                   ANTHROPIC_API_KEY='')
class LlmFallbackTests(BotTestCase):
    """services.chat_create — โมเดลหลักพังชั่วคราว (5xx/timeout) → ลองสำรอง + จำใน cache
    (2026-10-15 upstream qwen3.8-flash-next ตอบ 502 ทั้งวัน บอทตอบ "ขอโทษ ตอบไม่ได้ตอนนี้" ทุกคำถาม)"""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    @staticmethod
    def _status_error(code):
        # ไม่ import httpx ตรงๆ (openai 3.x bundle เป็น httpx2) — stub response ให้ APIStatusError อ่าน status/headers
        import openai
        resp = mock.MagicMock(status_code=code, headers={})
        return openai.APIStatusError('upstream timed out', response=resp, body=None)

    def test_502_on_primary_uses_fallback_and_marks_down(self):
        client = mock.MagicMock()
        client.chat.completions.create.side_effect = [
            self._status_error(502), _llm_response(content='วันนี้ 2 ใบ')]
        with mock.patch('openai.OpenAI', return_value=client, autospec=True), \
                self.assertLogs('line_bot.services', level='WARNING') as logs:
            self.assertEqual(services.answer_question('วันนี้มีกี่ใบ'), 'วันนี้ 2 ใบ')
        models = [c[1]['model'] for c in client.chat.completions.create.call_args_list]
        self.assertEqual(models, ['primary/m', 'fallback/m'])
        self.assertTrue(services.primary_model_down())
        self.assertIn('ลองชั้นถัดไป runninghub:fallback/m', '\n'.join(logs.output))

    def test_primary_marked_down_skips_straight_to_fallback(self):
        services.mark_tier_down(('runninghub', 'primary/m'))
        client = _fake_llm(_llm_response(content='ok'))
        with mock.patch('openai.OpenAI', return_value=client, autospec=True):
            self.assertEqual(services.answer_question('x'), 'ok')
        client.chat.completions.create.assert_called_once()
        self.assertEqual(client.chat.completions.create.call_args[1]['model'], 'fallback/m')

    def test_fallback_sticks_for_remaining_rounds(self):
        client = mock.MagicMock()
        client.chat.completions.create.side_effect = [
            self._status_error(503),
            _llm_response(tool_calls=[_tool_call('overdue_orders', {})]),
            _llm_response(content='ไม่มีใบค้าง'),
        ]
        with mock.patch('openai.OpenAI', return_value=client, autospec=True), \
                mock.patch.object(tools, 'overdue_orders', return_value={'orders': []}, autospec=True):
            self.assertEqual(services.answer_question('ค้างไหม'), 'ไม่มีใบค้าง')
        models = [c[1]['model'] for c in client.chat.completions.create.call_args_list]
        self.assertEqual(models, ['primary/m', 'fallback/m', 'fallback/m'])

    def test_non_transient_error_does_not_fallback(self):
        client = _fake_llm(error=self._status_error(400))
        with mock.patch('openai.OpenAI', return_value=client, autospec=True), \
                self.assertLogs('line_bot.services', level='ERROR'):
            self.assertIsNone(services.answer_question('x'))
        client.chat.completions.create.assert_called_once()
        self.assertFalse(services.primary_model_down())

    def test_both_models_fail_is_none(self):
        client = _fake_llm(error=self._status_error(502))
        with mock.patch('openai.OpenAI', return_value=client, autospec=True), \
                self.assertLogs('line_bot.services', level='ERROR'):
            self.assertIsNone(services.answer_question('x'))
        self.assertEqual(client.chat.completions.create.call_count, 2)

    @override_settings(RUNNINGHUB_FALLBACK_MODEL='')
    def test_no_fallback_configured_keeps_old_behaviour(self):
        client = _fake_llm(error=self._status_error(502))
        with mock.patch('openai.OpenAI', return_value=client, autospec=True), \
                self.assertLogs('line_bot.services', level='ERROR'):
            self.assertIsNone(services.answer_question('x'))
        client.chat.completions.create.assert_called_once()

    def test_read_order_number_also_falls_back(self):
        client = mock.MagicMock()
        client.chat.completions.create.side_effect = [
            TimeoutError('slow'),
            mock.MagicMock(choices=[mock.MagicMock(message=mock.MagicMock(
                content='{"order_number": "6910-116"}'))]),
        ]
        with mock.patch('openai.OpenAI', return_value=client, autospec=True):
            self.assertEqual(services.read_order_number(b'img'), '6910-116')
        models = [c[1]['model'] for c in client.chat.completions.create.call_args_list]
        self.assertEqual(models, ['primary/m', 'fallback/m'])


class LinebotSmokeCommandTests(BotTestCase):
    """management command linebot_smoke — ต้องรันผ่านบน VPS ก่อน systemctl restart (กติกา deploy)
    mock LINE + LLM ไม่ยิง network; autospec กัน method ที่ไม่มีจริงใน SDK"""

    def setUp(self):
        from line_bot.management.commands import linebot_smoke
        self.cmd = linebot_smoke
        cache.clear()
        self.addCleanup(cache.clear)

    def _run(self, bot_name='หมอนทอง', answer='วันนี้ 2 ใบ', token='tok', key='k', an_key='', strict=False):
        from io import StringIO
        from django.core.management import call_command
        out, err = StringIO(), StringIO()
        with mock.patch.object(self.cmd, 'MessagingApi', autospec=True) as api, \
                mock.patch.object(services, 'answer_question', autospec=True, side_effect=answer
                                  if callable(answer) else (lambda *a, **k: answer)) as qa, \
                override_settings(LINE_CHANNEL_ACCESS_TOKEN=token, RUNNINGHUB_API_KEY=key,
                                  RUNNINGHUB_MODEL='primary/m', RUNNINGHUB_FALLBACK_MODEL='fallback/m',
                                  ANTHROPIC_API_KEY=an_key, ANTHROPIC_MODEL='claude-haiku-5-5'):
            api.return_value.get_bot_info.return_value = mock.MagicMock(display_name=bot_name)
            args = ['--strict'] if strict else []
            call_command('linebot_smoke', *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue(), api, qa

    def test_all_pass(self):
        out, err, api, qa = self._run()
        api.return_value.get_bot_info.assert_called_once_with()
        # 2 ชั้น RunningHub ถูกยิงทีละชั้นแบบ only=True · ชั้น 3 ไม่มี key → รายงานยังไม่ตั้งค่า ไม่เรียก LLM
        self.assertEqual(qa.call_args_list, [
            mock.call(self.cmd.SMOKE_QUESTION, tier=('runninghub', 'primary/m'), only=True),
            mock.call(self.cmd.SMOKE_QUESTION, tier=('runninghub', 'fallback/m'), only=True),
        ])
        self.assertIn('LLM ผ่าน 2/3 ชั้น', out)
        self.assertIn('ผ่าน (LINE + LLM 2/3 ชั้น)', out)
        self.assertIn('ชั้น 3 Anthropic: ยังไม่ตั้งค่า', err)

    def test_wrong_bot_name_fails(self):
        from django.core.management import CommandError
        with self.assertRaises(CommandError) as cm:
            self._run(bot_name='บอทอื่น')
        self.assertIn('LINE get_bot_info', str(cm.exception))
        self.assertIn('ห้าม restart', str(cm.exception))

    def test_all_tiers_empty_fails(self):
        from django.core.management import CommandError
        with self.assertRaises(CommandError) as cm:
            self._run(answer=None)
        self.assertIn('LLM ทุกชั้น', str(cm.exception))

    def test_one_tier_down_still_passes_unless_strict(self):
        # ชั้น 1 ล่ม (upstream 502) แต่ชั้น 2 ตอบได้ → deploy ได้ (ไม่บล็อกเพราะผู้ให้บริการล่ม)
        def qa(question, tier=None, only=False):
            return None if tier == ('runninghub', 'primary/m') else 'วันนี้ 2 ใบ'
        out, err, _, _ = self._run(answer=qa)
        self.assertIn('✗ ชั้น 1 RunningHub หลัก', err)
        self.assertIn('✓ ชั้น 2 RunningHub สำรอง', out)
        self.assertIn('LLM ผ่าน 1/3 ชั้น', out)
        from django.core.management import CommandError
        with self.assertRaises(CommandError) as cm:
            self._run(answer=qa, strict=True)
        self.assertIn('ชั้น 1 RunningHub หลัก', str(cm.exception))

    def test_anthropic_tier_tested_when_key_set(self):
        out, err, _, qa = self._run(an_key='sk-ant-test')
        self.assertIn(mock.call(self.cmd.SMOKE_QUESTION, tier=('anthropic', 'claude-haiku-5-5'), only=True),
                      qa.call_args_list)
        self.assertIn('LLM ผ่าน 3/3 ชั้น', out)

    def test_missing_env_fails_both(self):
        from django.core.management import CommandError
        with self.assertRaises(CommandError) as cm:
            self._run(token='', key='')
        self.assertIn('LINE get_bot_info', str(cm.exception))
        self.assertIn('LLM ทุกชั้น', str(cm.exception))

    def test_line_api_exception_fails_not_crash(self):
        from io import StringIO
        from django.core.management import CommandError, call_command
        with mock.patch.object(self.cmd, 'MessagingApi', autospec=True) as api, \
                mock.patch.object(services, 'answer_question', return_value='ok', autospec=True), \
                override_settings(LINE_CHANNEL_ACCESS_TOKEN='tok', RUNNINGHUB_API_KEY='k', ANTHROPIC_API_KEY=''):
            api.return_value.get_bot_info.side_effect = RuntimeError('401')
            with self.assertRaises(CommandError):
                call_command('linebot_smoke', stdout=StringIO(), stderr=StringIO())


@override_settings(RUNNINGHUB_API_KEY='k', RUNNINGHUB_MODEL='primary/m', RUNNINGHUB_FALLBACK_MODEL='fallback/m',
                   ANTHROPIC_API_KEY='sk-ant-test', ANTHROPIC_MODEL='claude-haiku-5-5')
class AnthropicTierTests(BotTestCase):
    """ชั้นสำรองที่ 3 (line_bot/anthropic_backend.py) — แปลง OpenAI ↔ Anthropic + เข้าคิวหลัง RunningHub ทั้ง 2"""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        from . import anthropic_backend
        self.backend = anthropic_backend

    @staticmethod
    def _rh_error(code):
        import openai
        return openai.APIStatusError('upstream timed out',
                                     response=mock.MagicMock(status_code=code, headers={}), body=None)

    @staticmethod
    def _an_message(blocks, stop_reason='end_turn', input_tokens=10, output_tokens=5):
        content = []
        for b in blocks:
            ns = mock.MagicMock()
            ns.type = b['type']
            for k, v in b.items():
                setattr(ns, k, v)
            content.append(ns)
        return mock.MagicMock(content=content, stop_reason=stop_reason, model='claude-haiku-5-5',
                              usage=mock.MagicMock(input_tokens=input_tokens, output_tokens=output_tokens))

    def test_tiers_order(self):
        self.assertEqual(services.llm_tiers(), [
            ('runninghub', 'primary/m'), ('runninghub', 'fallback/m'), ('anthropic', 'claude-haiku-5-5')])
        with override_settings(ANTHROPIC_API_KEY=''):
            self.assertEqual(len(services.llm_tiers()), 2)

    def test_convert_tools(self):
        out = self.backend.convert_tools(tools.TOOL_SCHEMAS)
        self.assertEqual([t['name'] for t in out], tools.TOOL_NAMES)
        self.assertEqual(out[0]['input_schema'], tools.TOOL_SCHEMAS[0]['function']['parameters'])
        self.assertNotIn('parameters', out[0])

    def test_convert_messages_tool_loop_and_image(self):
        data_url = 'data:image/jpeg;base64,' + base64.b64encode(b'img').decode()
        system, msgs = self.backend.convert_messages([
            {'role': 'system', 'content': 'sys'},
            {'role': 'user', 'content': [{'type': 'text', 'text': 'ดูรูป'},
                                         {'type': 'image_url', 'image_url': {'url': data_url}}]},
            {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': 'c1', 'type': 'function', 'function': {'name': 'get_order', 'arguments': '{"order_number": "6910-1"}'}},
                {'id': 'c2', 'type': 'function', 'function': {'name': 'overdue_orders', 'arguments': '{}'}}]},
            {'role': 'tool', 'tool_call_id': 'c1', 'content': '{"a": 1}'},
            {'role': 'tool', 'tool_call_id': 'c2', 'content': '{"b": 2}'},
            {'role': 'user', 'content': 'ต่อ'},
        ])
        self.assertEqual(system, 'sys')
        self.assertEqual(msgs[0]['role'], 'user')
        self.assertEqual(msgs[0]['content'][1], {'type': 'image', 'source': {
            'type': 'base64', 'media_type': 'image/jpeg', 'data': base64.b64encode(b'img').decode()}})
        self.assertEqual(msgs[1]['role'], 'assistant')
        self.assertEqual([b['type'] for b in msgs[1]['content']], ['tool_use', 'tool_use'])
        self.assertEqual(msgs[1]['content'][0]['input'], {'order_number': '6910-1'})
        # ผล tool 2 อันรวมใน user message เดียว (Anthropic บังคับ)
        self.assertEqual(msgs[2]['role'], 'user')
        self.assertEqual([b['tool_use_id'] for b in msgs[2]['content']], ['c1', 'c2'])
        self.assertEqual(msgs[2]['content'][0]['type'], 'tool_result')
        self.assertEqual(msgs[3], {'role': 'user', 'content': 'ต่อ'})

    def test_to_openai_like(self):
        resp = self.backend.to_openai_like(self._an_message([
            {'type': 'text', 'text': 'ขอดูก่อน'},
            {'type': 'tool_use', 'id': 'tu1', 'name': 'daily_summary', 'input': {'date': '2026-10-15'}},
        ], stop_reason='tool_use'))
        msg = resp.choices[0].message
        self.assertEqual(msg.content, 'ขอดูก่อน')
        self.assertEqual(msg.tool_calls[0].id, 'tu1')
        self.assertEqual(msg.tool_calls[0].function.name, 'daily_summary')
        self.assertEqual(json.loads(msg.tool_calls[0].function.arguments), {'date': '2026-10-15'})
        self.assertEqual(resp.usage.total_tokens, 15)
        plain = self.backend.to_openai_like(self._an_message([{'type': 'text', 'text': 'ok'}]))
        self.assertIsNone(plain.choices[0].message.tool_calls)

    def test_create_calls_sdk_with_converted_payload(self):
        client = mock.MagicMock()
        client.messages.create.return_value = self._an_message([{'type': 'text', 'text': 'ok'}])
        with mock.patch('anthropic.Anthropic', return_value=client, autospec=True) as ctor:
            resp = self.backend.create('claude-haiku-5-5',
                                       [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'q'}],
                                       tools=tools.TOOL_SCHEMAS, max_tokens=123, timeout=7)
        self.assertEqual(resp.choices[0].message.content, 'ok')
        ctor.assert_called_once_with(api_key='sk-ant-test', timeout=7, max_retries=0)
        kwargs = client.messages.create.call_args[1]
        self.assertEqual(kwargs['model'], 'claude-haiku-5-5')
        self.assertEqual(kwargs['max_tokens'], 123)
        self.assertEqual(kwargs['system'], 'sys')
        self.assertEqual(kwargs['messages'], [{'role': 'user', 'content': 'q'}])
        self.assertEqual([t['name'] for t in kwargs['tools']], tools.TOOL_NAMES)
        self.assertNotIn('temperature', kwargs)

    def test_qa_falls_through_to_anthropic_when_runninghub_all_down(self):
        rh = mock.MagicMock()
        rh.chat.completions.create.side_effect = [self._rh_error(502), self._rh_error(503)]
        an = mock.MagicMock()
        an.messages.create.side_effect = [
            self._an_message([{'type': 'tool_use', 'id': 'tu1', 'name': 'daily_summary',
                               'input': {'date': '2026-10-15'}}], stop_reason='tool_use'),
            self._an_message([{'type': 'text', 'text': 'วันนี้ 3 ใบ'}]),
        ]
        with mock.patch('openai.OpenAI', return_value=rh, autospec=True), \
                mock.patch('anthropic.Anthropic', return_value=an, autospec=True), \
                mock.patch.object(tools, 'daily_summary', return_value={'orders': 3}, autospec=True) as tool_mock, \
                self.assertLogs('line_bot.services', level='INFO') as logs:
            self.assertEqual(services.answer_question('วันนี้มีกี่ใบ'), 'วันนี้ 3 ใบ')
        tool_mock.assert_called_once_with(date='2026-10-15')
        self.assertEqual(rh.chat.completions.create.call_count, 2)
        self.assertEqual(an.messages.create.call_count, 2)
        # รอบ 2 ยังอยู่ชั้น anthropic (ไม่ย้อนกลับ RunningHub) และส่ง tool_result ไป
        second = an.messages.create.call_args_list[1][1]['messages']
        self.assertEqual(second[-1]['content'][0]['type'], 'tool_result')
        self.assertEqual(second[-1]['content'][0]['tool_use_id'], 'tu1')
        joined = '\n'.join(logs.output)
        self.assertIn('QA รอบ 1 (anthropic:claude-haiku-5-5)', joined)
        self.assertTrue(services.tier_down(('runninghub', 'primary/m')))
        self.assertTrue(services.tier_down(('runninghub', 'fallback/m')))

    def test_read_order_number_via_anthropic_vision(self):
        rh = mock.MagicMock()
        rh.chat.completions.create.side_effect = TimeoutError('slow')
        an = mock.MagicMock()
        an.messages.create.return_value = self._an_message([{'type': 'text', 'text': '{"order_number": "6910-116"}'}])
        with mock.patch('openai.OpenAI', return_value=rh, autospec=True), \
                mock.patch('anthropic.Anthropic', return_value=an, autospec=True):
            self.assertEqual(services.read_order_number(b'img'), '6910-116')
        user_blocks = an.messages.create.call_args[1]['messages'][0]['content']
        self.assertEqual(user_blocks[1]['type'], 'image')
        self.assertEqual(user_blocks[1]['source']['media_type'], 'image/jpeg')
        self.assertEqual(an.messages.create.call_args[1]['max_tokens'], services.LLM_MAX_TOKENS)

    def test_anthropic_5xx_is_transient_but_401_not(self):
        import anthropic
        err5 = anthropic.APIStatusError('x', response=mock.MagicMock(status_code=529, headers={}), body=None)
        err4 = anthropic.APIStatusError('x', response=mock.MagicMock(status_code=401, headers={}), body=None)
        self.assertTrue(services._is_transient(err5))
        self.assertFalse(services._is_transient(err4))

    def test_only_tier_does_not_fall_through(self):
        rh = mock.MagicMock()
        rh.chat.completions.create.side_effect = self._rh_error(502)
        an = mock.MagicMock()
        with mock.patch('openai.OpenAI', return_value=rh, autospec=True), \
                mock.patch('anthropic.Anthropic', return_value=an, autospec=True), \
                self.assertLogs('line_bot.services', level='ERROR'):
            self.assertIsNone(services.answer_question('x', tier=('runninghub', 'primary/m'), only=True))
        rh.chat.completions.create.assert_called_once()
        an.messages.create.assert_not_called()
        self.assertFalse(services.tier_down(('runninghub', 'primary/m')))


class StageHelperTests(BotTestCase):
    """orders.views.mark_stage_done — helper เดียวที่ปุ่มเว็บทุกแผนกและบอทใช้ (timestamp + StageLog)"""

    def test_each_stage_sets_field_and_stagelog(self):
        order = make_order()
        order.packed_at = None
        for stage, spec in views_orders.STAGE_DONE_SPECS.items():
            if stage in ('shipped', 'pickup'):
                continue
            self.assertIsNone(views_orders.mark_stage_done(order, stage, note='n-%s' % stage))
            order.refresh_from_db()
            self.assertIsNotNone(getattr(order, spec['field']))
            log = StageLog.objects.get(order=order, action=spec['action'])
            self.assertEqual(log.department, spec['department'])
            self.assertEqual(log.note, 'n-%s' % stage)
            # ติ๊กซ้ำ → already ไม่เขียนเพิ่ม
            self.assertEqual(views_orders.mark_stage_done(order, stage), views_orders.STAGE_ALREADY)
            self.assertEqual(StageLog.objects.filter(order=order, action=spec['action']).count(), 1)
        self.assertEqual(order.progress_label, 'รีด+แพ็คแล้ว')

    def test_pickup_requires_pack_and_exclusive_with_shipped(self):
        order = make_order()
        self.assertEqual(views_orders.mark_stage_done(order, 'pickup'), views_orders.STAGE_NEED_PACK)
        self.assertIsNone(views_orders.mark_stage_done(order, 'pack'))
        self.assertIsNone(views_orders.mark_stage_done(order, 'pickup'))
        self.assertEqual(views_orders.mark_stage_done(order, 'shipped'), views_orders.STAGE_ALREADY)
        order.refresh_from_db()
        self.assertEqual(order.progress_label, 'รอลูกค้ารับ')

    def test_mark_pack_done_wrapper_unchanged(self):
        order = make_order()
        self.assertTrue(views_orders.mark_pack_done(order, note='x'))
        self.assertFalse(views_orders.mark_pack_done(order))
        self.assertEqual(StageLog.objects.filter(order=order, department='pack', action='pack_done').count(), 1)

    def test_web_apply_action_goes_through_helper(self):
        """ปุ่มเว็บทุกแผนก (_apply_action) → mark_stage_done ตัวเดียวกับบอท (patch แล้วต้องถูกเรียก)"""
        order = make_order()
        request = mock.MagicMock()
        calls = []
        real = views_orders.mark_stage_done

        def spy(o, stage, note='', now=None):
            calls.append(stage)
            return real(o, stage, note=note, now=now)

        with mock.patch.object(views_orders, 'mark_stage_done', side_effect=spy, autospec=True):
            for dept, action in [('print', 'print_done'), ('roll', 'roll_done'), ('cut', 'cut_done'),
                                 ('sort', 'sort_done'), ('pack', 'pack_done'), ('pack', 'pack_pickup')]:
                ok, _msg = views_orders._apply_action(order, dept, action, request)
                self.assertTrue(ok, action)
            ok, msg = views_orders._apply_action(order, 'print', 'print_done', request)
            self.assertFalse(ok)
            self.assertIn('ไปแล้ว', msg)
            ok, msg = views_orders._apply_action(order, 'pack', 'pack_shipped', request)
            self.assertFalse(ok)
            self.assertEqual(msg, 'งานนี้ถูกบันทึกไปแล้ว')
        self.assertEqual(calls, ['print', 'roll', 'cut', 'sort', 'pack', 'pickup', 'print', 'shipped'])

    def test_web_pickup_needs_pack_message(self):
        order = make_order()
        ok, msg = views_orders._apply_action(order, 'pack', 'pack_pickup', mock.MagicMock())
        self.assertFalse(ok)
        self.assertEqual(msg, 'ต้องรีดแพ็คก่อน')


class ToolsStageKeyTests(BotTestCase):
    """บั๊ก 2026-10-15: LLM อ่าน printed_at (เวลาพิมพ์ใบงานกระดาษ) เป็นขั้น "พิมพ์" → เปลี่ยนชื่อ key
    current_stage ต้องมาจาก Order.progress_label ตัวเดียวกับหน้าเว็บ"""

    def test_sheet_printed_at_not_mistaken_for_stage(self):
        order = make_order()
        order.printed_at = timezone.now()
        order.save(update_fields=['printed_at'])
        data = tools.get_order(order.order_number)
        self.assertNotIn('printed_at', data)
        self.assertIsNotNone(data['sheet_printed_at'])
        self.assertEqual(data['current_stage'], 'ยังไม่เริ่ม')
        self.assertEqual(data['current_stage'], order.progress_label)
        self.assertIsNone(data['progress'][0]['done_at'])  # ขั้นพิมพ์ยังว่าง
        views_orders.mark_stage_done(order, 'print')
        data = tools.get_order(order.order_number)
        self.assertEqual(data['current_stage'], 'พิมพ์แล้ว')
        self.assertIsNotNone(data['progress'][0]['done_at'])


class StageCommandParseTests(BotTestCase):
    def test_parse_variants(self):
        P = stage_cmd.parse_stage_command
        self.assertEqual(P('6910-167 พิมพ์แล้ว'), ('print', ['6910-167']))
        self.assertEqual(P('6910-167 พิมพ์เเล้ว'), ('print', ['6910-167']))  # สระเอ 2 ตัว
        self.assertEqual(P('ตัดแล้ว 6910-167 6910-168 6910-167'), ('cut', ['6910-167', '6910-168']))
        self.assertEqual(P('หมอนทอง 6910-167 พิมพ์แล้ว'), ('print', ['6910-167']))
        self.assertEqual(P('บอท เลเซอร์แล้ว 6910-1'), ('cut', ['6910-1']))
        self.assertEqual(P('คัด 6910-1'), ('sort', ['6910-1']))
        self.assertEqual(P('เย็บแล้ว 6910-1'), ('sew', ['6910-1']))
        self.assertEqual(P('ส่งเย็บ 6910-1'), ('sew', ['6910-1']))
        for w in ('รีดแล้ว', 'แพ็คแล้ว', 'รีดแพ็คแล้ว', 'รีด+แพ็ค'):
            self.assertEqual(P('%s 6910-1' % w), ('pack', ['6910-1']), w)
        self.assertEqual(P('6910-1 ลูกค้ารับแล้ว'), ('pickup', ['6910-1']))
        self.assertEqual(P('รับแล้ว 6910-1'), ('pickup', ['6910-1']))

    def test_not_a_command(self):
        P = stage_cmd.parse_stage_command
        self.assertIsNone(P('พิมพ์แล้ว'))                      # ไม่มีเลข
        self.assertIsNone(P('บอท 6910-167 ถึงไหนแล้ว'))        # คำถาม → QA
        self.assertIsNone(P('6910-167 พิมพ์แล้วครึ่งนึง'))       # คำเกิน
        self.assertIsNone(P('บอท เสร็จ 6910-167'))             # คำสั่งเดิม
        self.assertIsNone(P('วันนี้ 6910-167'))

    def test_yes_no_help(self):
        for w in ('ใช่', 'ใช่ครับ', ' ใช่ค่ะ ', 'ค่ะ', 'ครับ'):
            self.assertTrue(stage_cmd.is_yes(w), w)
        self.assertFalse(stage_cmd.is_yes('ใช่เลย'))
        for w in ('ไม่', 'ยกเลิก', 'ไม่ใช่'):
            self.assertTrue(stage_cmd.is_no(w), w)
        for w in ('ช่วย', 'บอท ช่วย', 'หมอนทอง ช่วย'):
            self.assertTrue(stage_cmd.is_help(w), w)
        self.assertFalse(stage_cmd.is_help('ช่วยหน่อย'))


@override_settings(LINE_ALLOWED_GROUP_IDS=['G1'])
class StageCommandFlowTests(WebhookBase):
    """คำสั่งติ๊กขั้นผ่าน LINE: ทวน → ใช่/ไม่/หมดเวลา → ติ๊กผ่าน mark_stage_done · ไม่เข้า LLM"""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        p = mock.patch.object(services, 'answer_question', return_value='llm', autospec=True)
        self.qa_mock = p.start()
        self.addCleanup(p.stop)
        self.order = make_order('6910-167')
        self.order.shirt_name = 'คิชฌกูฏลายพราง'
        self.order.save()

    def _post_text(self, text, user_id='U123', mention=None):
        ev = _text_event(text)
        ev['source']['userId'] = user_id
        if mention is not None:
            ev['message']['mention'] = mention
        self.reply_mock.reset_mock()
        resp = self._post({'destination': 'x', 'events': [ev]})
        self.assertEqual(resp.status_code, 200)

    def _reply_text(self):
        self.reply_mock.assert_called_once()
        return self.reply_mock.call_args[0][1][0].text

    def test_confirm_then_yes_marks_stage(self):
        self._post_text('6910-167 พิมพ์แล้ว')
        self.assertEqual(self._reply_text(),
                         'ติ๊ก พิมพ์ · 6910-167 คิชฌกูฏลายพราง 10 ตัว\nใช่ไหม? ตอบ ใช่ / ไม่')
        self.qa_mock.assert_not_called()
        self.order.refresh_from_db()
        self.assertIsNone(self.order.print_done_at)  # ยังไม่ติ๊กจนกว่าจะตอบ ใช่

        self._post_text('ใช่ครับ')
        self.assertEqual(self._reply_text(), '✓ 6910-167 คิชฌกูฏลายพราง 10 ตัว → พิมพ์แล้ว')
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.print_done_at)
        self.assertEqual(self.order.progress_label, 'พิมพ์แล้ว')
        log = StageLog.objects.get(order=self.order, action='print_done')
        self.assertEqual(log.department, 'print')
        self.assertEqual(log.note, 'บอท LINE โดย U123')
        self.assertIsNone(stage_cmd.get_pending('G1', 'U123'))
        # "ใช่" ซ้ำโดยไม่มีรายการ → เงียบ
        self._post_text('ใช่')
        self.reply_mock.assert_not_called()

    def test_prefix_and_mention_forms(self):
        self._post_text('หมอนทอง 6910-167 พิมพ์เเล้ว')
        self.assertIn('ติ๊ก พิมพ์ · 6910-167', self._reply_text())
        self.qa_mock.assert_not_called()
        mention = {'mentionees': [{'type': 'user', 'index': 0, 'length': len('@น้องหมอนทอง'),
                                   'userId': 'Ubot', 'isSelf': True}]}
        self._post_text('@น้องหมอนทอง ตัดแล้ว 6910-167', mention=mention)
        self.assertIn('ติ๊ก ตัด · 6910-167', self._reply_text())
        self.qa_mock.assert_not_called()

    def test_no_cancels(self):
        self._post_text('ตัดแล้ว 6910-167')
        self._post_text('ไม่')
        self.assertEqual(self._reply_text(), 'ยกเลิกแล้ว')
        self.order.refresh_from_db()
        self.assertIsNone(self.order.cut_done_at)
        self.assertIsNone(stage_cmd.get_pending('G1', 'U123'))

    def test_bot_cancel_clears_stage_pending(self):
        self._post_text('ตัดแล้ว 6910-167')
        self._post_text('บอท ยกเลิก')
        self.assertIsNone(stage_cmd.get_pending('G1', 'U123'))

    def test_expired_pending_ignored(self):
        with mock.patch.object(stage_cmd, 'PENDING_TTL', 0):
            self._post_text('ตัดแล้ว 6910-167')
        self._post_text('ใช่')
        self.reply_mock.assert_not_called()
        self.order.refresh_from_db()
        self.assertIsNone(self.order.cut_done_at)

    def test_other_user_cannot_confirm(self):
        self._post_text('ตัดแล้ว 6910-167', user_id='U123')
        self._post_text('ใช่', user_id='U999')
        self.reply_mock.assert_not_called()
        self.order.refresh_from_db()
        self.assertIsNone(self.order.cut_done_at)

    def test_multiple_orders_with_not_found_and_already(self):
        other = make_order('6910-168')
        other.shirt_name = 'งานสอง'
        other.save()
        views_orders.mark_stage_done(other, 'print')
        self._post_text('พิมพ์แล้ว 6910-167 6910-168 6910-999')
        text = self._reply_text()
        self.assertEqual(text, '\n'.join([
            'ติ๊ก พิมพ์ · 6910-167 คิชฌกูฏลายพราง 10 ตัว',
            '6910-168 ติ๊กแล้วอยู่แล้ว (พิมพ์)',
            'ไม่พบ 6910-999',
            'ใช่ไหม? ตอบ ใช่ / ไม่',
        ]))
        self.assertEqual(stage_cmd.get_pending('G1', 'U123')['numbers'], ['6910-167'])
        self._post_text('ค่ะ')
        self.assertEqual(self._reply_text(), '✓ 6910-167 คิชฌกูฏลายพราง 10 ตัว → พิมพ์แล้ว')
        self.assertEqual(StageLog.objects.filter(order=other, action='print_done').count(), 1)

    def test_two_valid_orders_listed(self):
        other = make_order('6910-168')
        other.shirt_name = 'งานสอง'
        other.save()
        self._post_text('ตัดแล้ว 6910-167 6910-168')
        self.assertEqual(self._reply_text(), '\n'.join([
            'ติ๊ก ตัด', '· 6910-167 คิชฌกูฏลายพราง 10 ตัว', '· 6910-168 งานสอง 10 ตัว', 'ใช่ไหม? ตอบ ใช่ / ไม่']))
        self._post_text('ใช่')
        self.assertEqual(self._reply_text(), '\n'.join([
            '✓ 6910-167 คิชฌกูฏลายพราง 10 ตัว → ตัดแล้ว', '✓ 6910-168 งานสอง 10 ตัว → ตัดแล้ว']))

    def test_all_problem_orders_no_question(self):
        views_orders.mark_stage_done(self.order, 'print')
        self._post_text('พิมพ์แล้ว 6910-167 6910-999')
        self.assertEqual(self._reply_text(), '6910-167 ติ๊กแล้วอยู่แล้ว (พิมพ์)\nไม่พบ 6910-999')
        self.assertIsNone(stage_cmd.get_pending('G1', 'U123'))

    def test_pickup_needs_pack_reported_on_apply(self):
        self._post_text('รับแล้ว 6910-167')
        self._post_text('ใช่')
        self.assertEqual(self._reply_text(), '6910-167 ต้องรีด+แพ็คก่อน')

    def test_pack_via_command_uses_same_helper_as_image_flow(self):
        self._post_text('รีดแพ็คแล้ว 6910-167')
        self._post_text('ครับ')
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.packed_at)
        self.assertEqual(StageLog.objects.get(order=self.order, action='pack_done').department, 'pack')
        self.assertFalse(bot_orders.mark_done(self.order))  # flow รูป เห็นว่าติ๊กแล้ว

    def test_help_command(self):
        for t in ('ช่วย', 'บอท ช่วย', 'หมอนทอง ช่วย'):
            self._post_text(t)
            self.assertEqual(self._reply_text(), stage_cmd.HELP_TEXT)
        self.qa_mock.assert_not_called()
        self.assertIn('พิมพ์แล้ว', stage_cmd.HELP_TEXT)
        self.assertIn('ping', stage_cmd.HELP_TEXT)

    def test_question_still_goes_to_llm(self):
        self._post_text('หมอนทอง 6910-167 ถึงไหนแล้ว')
        self.qa_mock.assert_called_once()
        self.assertEqual(self._reply_text(), 'llm')

    def test_image_confirm_flow_untouched(self):
        pending.add_pending('G1', 'U123', '6910-167')
        self._post_text('ครบแล้ว')
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.packed_at)
        self.reply_mock.assert_called_once()
