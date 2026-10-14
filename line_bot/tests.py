import base64
import hashlib
import hmac
import json
from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from orders.models import Order, OrderItem, ShirtVariant, StageLog

from . import orders as bot_orders
from . import services, views

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
    """ข้อ 3 c/d/e/f — mock ดาวน์โหลดรูป + LLM ไม่ยิง network"""

    def setUp(self):
        super().setUp()
        dl = mock.patch.object(services, 'download_line_image', return_value=b'img')
        dl.start()
        self.addCleanup(dl.stop)
        self.llm = mock.patch.object(services, 'read_order_number', return_value='6910-116')
        self.llm_mock = self.llm.start()
        self.addCleanup(self.llm.stop)

    def _post_image(self):
        resp = self._post({'destination': 'x', 'events': [_image_event()]})
        self.assertEqual(resp.status_code, 200)
        return resp

    def test_c_no_number_silent(self):
        self.llm_mock.return_value = None
        self._post_image()
        self.reply_mock.assert_not_called()

    def test_download_failed_silent(self):
        with mock.patch.object(services, 'download_line_image', return_value=None):
            self._post_image()
        self.reply_mock.assert_not_called()
        self.llm_mock.assert_not_called()

    def test_d_not_found(self):
        self._post_image()
        msg = self._replied_message()
        self.assertEqual(msg.text, '@แอดมิน อ่านได้เลข 6910-116 แต่ไม่พบในระบบ')
        self.assertEqual(StageLog.objects.count(), 0)

    def test_e_already_done(self):
        for status in ('เสร็จแล้ว', 'ส่งแล้ว'):
            with self.subTest(status=status):
                StageLog.objects.all().delete()
                Order.objects.all().delete()
                self.reply_mock.reset_mock()
                make_order(status=status)
                self._post_image()
                self.assertEqual(self._replied_message().text, '@แอดมิน 6910-116 เสร็จอยู่แล้ว')
                self.assertEqual(StageLog.objects.count(), 0)

    def test_f_mark_done_and_summary(self):
        order = make_order()
        self._post_image()
        order.refresh_from_db()
        self.assertEqual(order.status, 'เสร็จแล้ว')
        log = StageLog.objects.get(order=order)
        self.assertEqual((log.department, log.action), ('linebot', 'status_done'))
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

    def test_money_format(self):
        self.assertEqual(bot_orders._money(Decimal('1500.00')), '1,500')
        self.assertEqual(bot_orders._money(Decimal('1500.50')), '1,500.50')
        self.assertEqual(bot_orders._money(None), '0')
