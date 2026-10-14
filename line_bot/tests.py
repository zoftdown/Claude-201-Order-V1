import base64
import hashlib
import hmac
import json
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from . import views

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
        'source': {'type': 'user', 'userId': 'U123'},
    }
    base.update(extra)
    return base


class LineWebhookTests(TestCase):
    def setUp(self):
        self.url = reverse('linebot_webhook')
        p = mock.patch.object(views.handler.parser.signature_validator, 'channel_secret', SECRET)
        p.start()
        self.addCleanup(p.stop)
        self.reply = mock.patch.object(views, '_reply_text')
        self.reply_mock = self.reply.start()
        self.addCleanup(self.reply.stop)

    def _post(self, payload, signature=None):
        body = json.dumps(payload)
        sig = _sign(body) if signature is None else signature
        return self.client.post(
            self.url, data=body, content_type='application/json',
            headers={'X-Line-Signature': sig},
        )

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

    def test_text_message_echo(self):
        ev = _event({'type': 'message',
                     'message': {'type': 'text', 'id': 'm1', 'text': 'สวัสดี',
                                 'quoteToken': 'q1'}})
        resp = self._post({'destination': 'x', 'events': [ev]})
        self.assertEqual(resp.status_code, 200)
        self.reply_mock.assert_called_once_with('rt-1', 'ได้รับแล้ว: สวัสดี')

    def test_image_message(self):
        ev = _event({'type': 'message', 'message': {
            'type': 'image', 'id': 'm2', 'quoteToken': 'q2',
            'contentProvider': {'type': 'line'}}})
        resp = self._post({'destination': 'x', 'events': [ev]})
        self.assertEqual(resp.status_code, 200)
        self.reply_mock.assert_called_once_with('rt-1', 'ได้รับรูปแล้ว')

    def test_join_event(self):
        ev = _event({'type': 'join', 'source': {'type': 'group', 'groupId': 'G1'}})
        resp = self._post({'destination': 'x', 'events': [ev]})
        self.assertEqual(resp.status_code, 200)
        self.reply_mock.assert_called_once_with('rt-1', 'สวัสดี บอทร้านเสื้อพร้อมใช้งาน')

    def test_other_event_ignored(self):
        ev = _event({'type': 'follow'})
        resp = self._post({'destination': 'x', 'events': [ev]})
        self.assertEqual(resp.status_code, 200)
        self.reply_mock.assert_not_called()

    def test_reply_failure_still_200(self):
        # reply พัง (เช่น token ผิด/เน็ตล่ม) → log แล้วยังตอบ 200 ให้ LINE
        self.reply.stop()
        try:
            with mock.patch.object(views, 'MessagingApi') as api, \
                    mock.patch.object(views.settings, 'LINE_CHANNEL_ACCESS_TOKEN', 'tok'):
                api.return_value.reply_message.side_effect = RuntimeError('boom')
                ev = _event({'type': 'message',
                             'message': {'type': 'text', 'id': 'm1', 'text': 'x',
                                          'quoteToken': 'q1'}})
                resp = self._post({'destination': 'x', 'events': [ev]})
        finally:
            self.reply.start()
        self.assertEqual(resp.status_code, 200)
