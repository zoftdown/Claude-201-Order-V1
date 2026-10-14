"""
Webhook รับ event จาก LINE Messaging API — ขั้น 4: อ่านใบงานจากรูปแล้วอัปเดตสถานะ

views.py ทำแค่ route event:
- text  → ไม่ตอบ (เลิก echo) ยกเว้น "บอท ping" → "pong"
- image → ดาวน์โหลด + LLM อ่านเลขออร์เดอร์ (services.py) → ค้น/อัปเดต/สรุป (orders.py)
          อ่านไม่เจอ หรือ LLM/ดาวน์โหลดพัง → เงียบ แค่ log (รูปเสื้อธรรมดาในกลุ่ม)
- join  → ทักทาย
- ทุก event log groupId/userId ของผู้ส่ง (INFO) ไว้เก็บ id ใส่ LINE_ADMIN_USER_IDS

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
    MentionSubstitutionObject,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
    TextMessageV2,
    UserMentionTarget,
)
from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import (
    ImageMessageContent,
    JoinEvent,
    MessageEvent,
    TextMessageContent,
)

from . import orders as bot_orders
from . import services

logger = logging.getLogger(__name__)

handler = WebhookHandler(settings.LINE_CHANNEL_SECRET or 'unset')

PING_TEXT = 'บอท ping'
JOIN_GREETING = 'สวัสดี น้องหมอนทองพร้อมใช้งาน'


def _reply(reply_token, messages):
    """ส่ง message list ตอบกลับ — best-effort ไม่ให้ exception หลุดไปทำ webhook เป็น 500"""
    if not settings.LINE_CHANNEL_ACCESS_TOKEN:
        logger.warning('LINE_CHANNEL_ACCESS_TOKEN ว่าง — ข้ามการ reply: %r',
                       [getattr(m, 'text', m) for m in messages])
        return
    try:
        config = Configuration(access_token=settings.LINE_CHANNEL_ACCESS_TOKEN)
        with ApiClient(config) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(reply_token=reply_token, messages=messages)
            )
    except Exception:  # noqa: BLE001 — log แล้วปล่อยผ่าน
        logger.exception('reply ไป LINE ไม่สำเร็จ')


def _reply_text(reply_token, text):
    _reply(reply_token, [TextMessage(text=text)])


def _admin_mention_message(text):
    """ข้อความขึ้นต้นด้วย mention แอดมิน:
    มี LINE_ADMIN_USER_IDS → TextMessageV2 + substitution mention ทุกคน
    ว่าง → "@แอดมิน" เป็น text ธรรมดา"""
    admin_ids = settings.LINE_ADMIN_USER_IDS
    if not admin_ids:
        return TextMessage(text='@แอดมิน %s' % text)
    keys = ['admin%d' % i for i in range(len(admin_ids))]
    prefix = ' '.join('{%s}' % k for k in keys)
    substitution = {
        k: MentionSubstitutionObject(mentionee=UserMentionTarget(user_id=uid))
        for k, uid in zip(keys, admin_ids)
    }
    return TextMessageV2(text='%s %s' % (prefix, text), substitution=substitution)


def _source_label(event):
    src = getattr(event, 'source', None)
    if src is None:
        return '?'
    return 'type=%s groupId=%s userId=%s' % (
        getattr(src, 'type', '?'),
        getattr(src, 'group_id', None) or '-',
        getattr(src, 'user_id', None) or '-',
    )


@handler.add(MessageEvent, message=TextMessageContent)
def on_text_message(event):
    text = event.message.text
    logger.info('LINE text จาก %s: %r', _source_label(event), text)
    if text.strip() == PING_TEXT:
        _reply_text(event.reply_token, 'pong')


@handler.add(MessageEvent, message=ImageMessageContent)
def on_image_message(event):
    src = _source_label(event)
    logger.info('LINE image จาก %s (id=%s)', src, event.message.id)

    image = services.download_line_image(event.message.id)
    if image is None:
        return  # log แล้วใน services
    number = services.read_order_number(image)
    if number is None:
        logger.info('รูป %s ไม่พบเลขออร์เดอร์ — ไม่ตอบ', event.message.id)
        return

    text = bot_orders.process_order_number(number, note='บอท LINE อ่านใบงานจากรูป (%s)' % src)
    _reply(event.reply_token, [_admin_mention_message(text)])


@handler.add(JoinEvent)
def on_join(event):
    logger.info('LINE บอทถูกเชิญเข้า %s', _source_label(event))
    _reply_text(event.reply_token, JOIN_GREETING)


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
