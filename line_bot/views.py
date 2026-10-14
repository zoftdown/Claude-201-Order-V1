"""
Webhook รับ event จาก LINE Messaging API — ขั้นนี้เป็น echo bot ล้วน
(ยังไม่แตะข้อมูลออร์เดอร์ ยังไม่ต่อ LLM)

- ตรวจลายเซ็น X-Line-Signature ด้วย WebhookHandler ของ line-bot-sdk v3
- ตอบ 200 เสมอเมื่อลายเซ็นถูก แม้ events ว่าง (ปุ่ม Verify ใน LINE console)
- reply ล้มเหลว (token ว่าง/เน็ตล่ม) → log แล้วยังตอบ 200 ให้ LINE ไม่ retry ซ้ำ
"""
import logging

from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotAllowed
from django.views.decorators.csrf import csrf_exempt

from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import (
    ImageMessageContent,
    JoinEvent,
    MessageEvent,
    TextMessageContent,
)

logger = logging.getLogger(__name__)

handler = WebhookHandler(settings.LINE_CHANNEL_SECRET or 'unset')


def _reply_text(reply_token, text):
    """ส่งข้อความตอบกลับ — best-effort ไม่ให้ exception หลุดไปทำ webhook เป็น 500"""
    if not settings.LINE_CHANNEL_ACCESS_TOKEN:
        logger.warning('LINE_CHANNEL_ACCESS_TOKEN ว่าง — ข้ามการ reply: %r', text)
        return
    try:
        config = Configuration(access_token=settings.LINE_CHANNEL_ACCESS_TOKEN)
        with ApiClient(config) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=text)],
                )
            )
    except Exception:  # noqa: BLE001 — log แล้วปล่อยผ่าน
        logger.exception('reply ไป LINE ไม่สำเร็จ')


def _source_label(event):
    src = getattr(event, 'source', None)
    if src is None:
        return '?'
    return '%s:%s' % (
        getattr(src, 'type', '?'),
        getattr(src, 'group_id', None) or getattr(src, 'user_id', None) or '?',
    )


@handler.add(MessageEvent, message=TextMessageContent)
def on_text_message(event):
    logger.info('LINE text จาก %s: %r', _source_label(event), event.message.text)
    _reply_text(event.reply_token, 'ได้รับแล้ว: %s' % event.message.text)


@handler.add(MessageEvent, message=ImageMessageContent)
def on_image_message(event):
    logger.info('LINE image จาก %s (id=%s)', _source_label(event), event.message.id)
    _reply_text(event.reply_token, 'ได้รับรูปแล้ว')


@handler.add(JoinEvent)
def on_join(event):
    logger.info('LINE บอทถูกเชิญเข้า %s', _source_label(event))
    _reply_text(event.reply_token, 'สวัสดี บอทร้านเสื้อพร้อมใช้งาน')


@handler.default()
def on_other_event(event):
    # event อื่น (follow/unfollow/leave/sticker/postback ฯลฯ) — แค่ log ไม่ตอบอะไร
    logger.info('LINE event อื่น: %s จาก %s', event.type, _source_label(event))


@csrf_exempt
def webhook(request):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    signature = request.headers.get('X-Line-Signature', '')
    body = request.body.decode('utf-8')
    logger.info('LINE webhook POST (%d bytes)', len(body))

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logger.warning('LINE webhook ลายเซ็นไม่ถูกต้อง')
        return HttpResponseBadRequest('invalid signature')

    return HttpResponse('OK')
