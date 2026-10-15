"""
Webhook รับ event จาก LINE Messaging API — ขั้น 4.1 (อ่านใบงานจากรูป → ยืนยัน → ติ๊ก "รีด+แพ็ค")
+ ขั้น 5 (ถาม-ตอบด้วย LLM tool calling)

views.py ทำแค่ route event:
- image → ดาวน์โหลด + LLM อ่านเลขออร์เดอร์ (services.py) → **ไม่ตอบ** เก็บเข้า "รายการรอ"
          ของ (groupId, userId) ใน cache (pending.py) หมดอายุ 10 นาที
          อ่านไม่เจอ หรือ LLM/ดาวน์โหลดพัง → เงียบ แค่ log (รูปเสื้อธรรมดาในกลุ่ม)
- text  → "บอท ping" → "pong"
          "บอท ยกเลิก" → ล้างรายการรอของคนนั้น → "ยกเลิกแล้ว"
          "บอท เสร็จ 6910-171 6910-172" → ติ๊กเลขที่ระบุทันที (ไม่ต้องมีรูป)
          มีคำว่า "ครบ"/"เสร็จ" + คนเดิมในกลุ่มเดิมมีรายการรอ → ติ๊กทุกใบ ล้างรายการ
            reply ข้อความเดียวสรุปทุกใบ (orders.py)
          ขึ้นต้นด้วย "บอท"/"หมอนทอง" หรือ mention บอท (event.message.mention is_self)
            และไม่ตรงคำสั่งด้านบน → ถาม-ตอบด้วย LLM + tools อ่านอย่างเดียว (services.answer_question)
            พร้อมบริบท 6 ข้อความล่าสุดของ (groupId, userId) (history.py) ·
            LLM พัง/timeout → "ขอโทษ ตอบไม่ได้ตอนนี้" ไม่เงียบ · ข้อความอื่นเงียบ
          ลำดับความสำคัญ: คำสั่งเดิม > ยืนยันรายการรอ (เฉพาะคนที่มีรายการรออยู่) > ถาม-ตอบ
- join  → ทักทาย
- ทุก event log groupId/userId ของผู้ส่ง (INFO) ไว้เก็บ id ใส่ LINE_ADMIN_USER_IDS / LINE_ALLOWED_GROUP_IDS

- **guard กลุ่ม (fail-closed):** ทุก handler เรียก `_allowed(event)` ก่อน — ทำงานเฉพาะ source.type=group
  ที่ groupId อยู่ใน settings.LINE_ALLOWED_GROUP_IDS · กลุ่มอื่น/1:1 chat (user)/room → ไม่ตอบ
  ไม่ประมวลผล log WARNING · JoinEvent จากกลุ่มนอกลิสต์ → MessagingApi.leave_group ออกทันที ·
  ลิสต์ว่าง = บล็อกทุกกลุ่ม (apps.py log ERROR ตอน start)

- ตรวจลายเซ็น X-Line-Signature ด้วย WebhookHandler ของ line-bot-sdk v3
- ตอบ 200 เสมอเมื่อลายเซ็นถูก แม้ events ว่าง (ปุ่ม Verify ใน LINE console)
- reply ล้มเหลว (token ว่าง/เน็ตล่ม) → log แล้วยังตอบ 200 ให้ LINE ไม่ retry ซ้ำ
"""
import logging
import re

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
from . import history, pending, services

logger = logging.getLogger(__name__)

handler = WebhookHandler(settings.LINE_CHANNEL_SECRET or 'unset')

PING_TEXT = 'บอท ping'
CANCEL_TEXT = 'บอท ยกเลิก'
DONE_COMMAND_PREFIX = 'บอท เสร็จ'
CONFIRM_WORDS = ('ครบ', 'เสร็จ')
JOIN_GREETING = 'สวัสดี น้องหมอนทองพร้อมใช้งาน'
QA_PREFIXES = ('บอท', 'หมอนทอง')
QA_FAIL_TEXT = 'ขอโทษ ตอบไม่ได้ตอนนี้'
QA_EMPTY_TEXT = ('ถามได้เลย เช่น "บอท วันนี้มีกี่ใบ" · "บอท 6910-116 ถึงไหนแล้ว" · '
                 '"บอท งานของคุณเอถึงไหน" · "บอท ใบไหนค้างเกิน 7 วัน"')
LINE_TEXT_MAX = 4900  # LINE จำกัด 5000 ตัวอักษร/ข้อความ

ORDER_NUMBER_IN_TEXT_RE = re.compile(r'(?<!\d)\d{4}-\d{1,3}(?!\d)')


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


def _source_ids(event):
    src = getattr(event, 'source', None)
    return (getattr(src, 'group_id', None), getattr(src, 'user_id', None))


def _allowed(event):
    """event นี้มาจากกลุ่มที่อนุญาตไหม (fail-closed):
    - source.type ต้องเป็น group และ groupId อยู่ใน LINE_ALLOWED_GROUP_IDS
    - 1:1 chat (user) / room / กลุ่มนอกลิสต์ / ลิสต์ว่าง → False + log WARNING"""
    src = getattr(event, 'source', None)
    src_type = getattr(src, 'type', None)
    group_id = getattr(src, 'group_id', None)
    if src_type == 'group' and group_id and group_id in settings.LINE_ALLOWED_GROUP_IDS:
        return True
    if not settings.LINE_ALLOWED_GROUP_IDS:
        logger.warning('LINE_ALLOWED_GROUP_IDS ว่าง — บล็อก event %s จาก %s',
                       getattr(event, 'type', '?'), _source_label(event))
    else:
        logger.warning('ไม่อนุญาต event %s จาก %s (ไม่ใช่กลุ่มในลิสต์)',
                       getattr(event, 'type', '?'), _source_label(event))
    return False


def _leave_group(group_id):
    """ออกจากกลุ่มที่ไม่อนุญาต — best-effort เหมือน _reply"""
    if not settings.LINE_CHANNEL_ACCESS_TOKEN:
        logger.warning('LINE_CHANNEL_ACCESS_TOKEN ว่าง — ออกจากกลุ่ม %s ไม่ได้', group_id)
        return
    try:
        config = Configuration(access_token=settings.LINE_CHANNEL_ACCESS_TOKEN)
        with ApiClient(config) as api_client:
            MessagingApi(api_client).leave_group(group_id)  # SDK v3: leave_group (ไม่ใช่ leave_from_group)
    except Exception:  # noqa: BLE001
        logger.exception('ออกจากกลุ่ม %s ไม่สำเร็จ', group_id)


def _reply_done(event, numbers, note):
    text = bot_orders.process_order_numbers(numbers, note=note)
    _reply(event.reply_token, [_admin_mention_message(text)])


def _self_mention_spans(message):
    """ช่วง (index, length) ของ mention ที่ชี้มาที่บอทเอง (is_self) ในข้อความ"""
    mention = getattr(message, 'mention', None)
    spans = []
    for m in (getattr(mention, 'mentionees', None) or []):
        if getattr(m, 'is_self', False):
            spans.append((getattr(m, 'index', 0) or 0, getattr(m, 'length', 0) or 0))
    return spans


def _qa_question(message, text):
    """ข้อความนี้เป็นคำถามถึงบอทไหม → คืนคำถามที่ตัด prefix/mention แล้ว หรือ None
    trigger: ขึ้นต้นด้วย "บอท"/"หมอนทอง" หรือ mention บอท"""
    spans = _self_mention_spans(message)
    if spans:
        raw = message.text or ''
        for index, length in sorted(spans, reverse=True):
            raw = raw[:index] + ' ' + raw[index + length:]
        return raw.strip()
    for prefix in QA_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    return None


def _answer_qa(event, question):
    group_id, user_id = _source_ids(event)
    if not question:
        _reply_text(event.reply_token, QA_EMPTY_TEXT)
        return
    answer = services.answer_question(question, history=history.get_history(group_id, user_id))
    if not answer:
        _reply_text(event.reply_token, QA_FAIL_TEXT)
        return
    answer = answer[:LINE_TEXT_MAX]
    history.append_history(group_id, user_id, question, answer)
    _reply_text(event.reply_token, answer)


@handler.add(MessageEvent, message=TextMessageContent)
def on_text_message(event):
    if not _allowed(event):
        return
    text = event.message.text.strip()
    src = _source_label(event)
    logger.info('LINE text จาก %s: %r', src, text)
    group_id, user_id = _source_ids(event)

    if text == PING_TEXT:
        _reply_text(event.reply_token, 'pong')
        return

    if text == CANCEL_TEXT:
        pending.clear_pending(group_id, user_id)
        _reply_text(event.reply_token, 'ยกเลิกแล้ว')
        return

    # คำสั่งตรง "บอท เสร็จ 6910-171 6910-172" → ติ๊กทันทีไม่ต้องมีรูป
    if text.startswith(DONE_COMMAND_PREFIX):
        numbers = list(dict.fromkeys(ORDER_NUMBER_IN_TEXT_RE.findall(text)))
        if numbers:
            _reply_done(event, numbers, note='บอท LINE คำสั่ง "บอท เสร็จ" (%s)' % src)
            return
        # ไม่มีเลข → ใช้เป็นข้อความยืนยันรายการรอ (มีคำว่า "เสร็จ") ไม่ส่งเข้าถาม-ตอบ
        numbers = pending.get_pending(group_id, user_id)
        if numbers:
            pending.clear_pending(group_id, user_id)
            _reply_done(event, numbers, note='บอท LINE ยืนยันจากข้อความ %r (%s)' % (text, src))
        return

    # ข้อความยืนยัน "ครบ"/"เสร็จ" จากคนเดิมในกลุ่มเดิมที่มีรายการรอ (กลไกเดิม มาก่อนถาม-ตอบ)
    if any(w in text for w in CONFIRM_WORDS):
        numbers = pending.get_pending(group_id, user_id)
        if numbers:
            pending.clear_pending(group_id, user_id)
            _reply_done(event, numbers, note='บอท LINE ยืนยันจากข้อความ %r (%s)' % (text, src))
            return

    # ถาม-ตอบ: "บอท ..." / "หมอนทอง ..." / mention บอท
    question = _qa_question(event.message, text)
    if question is not None:
        _answer_qa(event, question)


@handler.add(MessageEvent, message=ImageMessageContent)
def on_image_message(event):
    if not _allowed(event):
        return
    src = _source_label(event)
    logger.info('LINE image จาก %s (id=%s)', src, event.message.id)

    image = services.download_line_image(event.message.id)
    if image is None:
        return  # log แล้วใน services
    number = services.read_order_number(image)
    if number is None:
        logger.info('รูป %s ไม่พบเลขออร์เดอร์ — ไม่ตอบ', event.message.id)
        return

    group_id, user_id = _source_ids(event)
    numbers = pending.add_pending(group_id, user_id, number)
    logger.info('รูป %s อ่านได้เลข %s → เข้ารายการรอ (%d ใบ) รอข้อความยืนยัน',
                event.message.id, number, len(numbers))


@handler.add(JoinEvent)
def on_join(event):
    if not _allowed(event):
        group_id, _ = _source_ids(event)
        if group_id:
            logger.warning('ถูกเชิญเข้ากลุ่มนอกลิสต์ %s → ออกจากกลุ่ม', group_id)
            _leave_group(group_id)
        return
    logger.info('LINE บอทถูกเชิญเข้า %s', _source_label(event))
    _reply_text(event.reply_token, JOIN_GREETING)


@handler.default()
def on_other_event(event):
    # event อื่น (follow/unfollow/leave/sticker/postback ฯลฯ) — แค่ log ไม่ตอบอะไร
    if not _allowed(event):
        return  # log WARNING แล้วใน _allowed
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
