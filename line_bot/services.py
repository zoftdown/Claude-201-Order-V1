"""
services — ส่วนที่คุยกับโลกภายนอก: ดาวน์โหลดรูปจาก LINE + ให้ LLM อ่านเลขออร์เดอร์

ไม่แตะ DB เลย (ส่วนค้น/อัปเดตออร์เดอร์อยู่ใน line_bot/orders.py)
ทุกฟังก์ชันคืน None เมื่อล้มเหลว + log — ให้ views ตัดสินใจเงียบได้โดยไม่ต้อง try/except ซ้ำ
"""
import base64
import json
import logging
import re

from django.conf import settings

from linebot.v3.messaging import ApiClient, Configuration, MessagingApiBlob

logger = logging.getLogger(__name__)

ORDER_NUMBER_RE = re.compile(r'^\d{4}-\d{1,3}$')

SYSTEM_PROMPT = (
    'คุณอ่านรูปใบออร์เดอร์ของร้านพิมพ์เสื้อ ให้หา "เลขออร์เดอร์" ที่หัวใบ '
    'รูปแบบคือ ตัวเลข 4 หลัก ขีด แล้วตามด้วยตัวเลข 1 ถึง 3 หลัก เช่น 6910-116 '
    'ตอบเป็น JSON เท่านั้น ห้ามมีข้อความอื่น: '
    '{"order_number": "6910-116"} ถ้าเจอ หรือ {"order_number": null} ถ้าไม่เจอ'
)

LLM_TIMEOUT = 20
# qwen3.8-flash-next เป็น reasoning model — ถ้าไม่ปิด thinking มันกิน max_tokens ไปกับ
# reasoning จน content ว่าง (ทดสอบจริง 2026-10-14: max_tokens=50 → finish=length, content='')
# → ส่ง reasoning_effort='none' (ตอบ ~15 token) + เผื่อเพดาน 300 กันกรณี provider ไม่รับ flag
LLM_MAX_TOKENS = 300
LLM_EXTRA_BODY = {'reasoning_effort': 'none'}


def download_line_image(message_id):
    """ดึงไฟล์รูปของ message จาก LINE → bytes (None เมื่อ token ว่าง/ดึงไม่ได้)"""
    if not settings.LINE_CHANNEL_ACCESS_TOKEN:
        logger.warning('LINE_CHANNEL_ACCESS_TOKEN ว่าง — ดาวน์โหลดรูป %s ไม่ได้', message_id)
        return None
    try:
        config = Configuration(access_token=settings.LINE_CHANNEL_ACCESS_TOKEN)
        with ApiClient(config) as api_client:
            return bytes(MessagingApiBlob(api_client).get_message_content(message_id))
    except Exception:  # noqa: BLE001
        logger.exception('ดาวน์โหลดรูป %s จาก LINE ไม่สำเร็จ', message_id)
        return None


def parse_order_number(raw_text):
    """แกะ order_number จากคำตอบ LLM แบบทนทาน:
    ตัด code fence ```json ... ``` ออก, parse ไม่ได้/ไม่ตรง regex → None"""
    if not raw_text:
        return None
    text = raw_text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text).strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        # เผื่อ LLM พ่นข้อความห่อ JSON — หยิบก้อน {...} ก้อนแรก
        m = re.search(r'\{.*?\}', text, re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except (ValueError, TypeError):
            return None
    if not isinstance(data, dict):
        return None
    value = data.get('order_number')
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if ORDER_NUMBER_RE.match(value) else None


def read_order_number(image_bytes, mime='image/jpeg'):
    """ส่งรูปให้ LLM (OpenAI-compatible) หาเลขออร์เดอร์ที่หัวใบ
    → '6910-116' / None (ไม่เจอ หรือ LLM พัง/timeout → log ERROR แล้ว None)"""
    if not settings.RUNNINGHUB_API_KEY:
        logger.warning('RUNNINGHUB_API_KEY ว่าง — ข้ามการอ่านรูปด้วย LLM')
        return None
    try:
        from openai import OpenAI
    except ImportError:
        logger.error('ไม่มี package openai — pip install openai')
        return None

    data_url = 'data:%s;base64,%s' % (mime, base64.b64encode(image_bytes).decode('ascii'))
    try:
        client = OpenAI(
            api_key=settings.RUNNINGHUB_API_KEY,
            base_url=settings.RUNNINGHUB_BASE_URL,
            timeout=LLM_TIMEOUT,
            max_retries=0,
        )
        resp = client.chat.completions.create(
            model=settings.RUNNINGHUB_MODEL,
            temperature=0,
            max_tokens=LLM_MAX_TOKENS,
            extra_body=LLM_EXTRA_BODY,
            messages=[
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': [
                    {'type': 'text', 'text': 'เลขออร์เดอร์ในรูปนี้คืออะไร'},
                    {'type': 'image_url', 'image_url': {'url': data_url}},
                ]},
            ],
        )
        raw = resp.choices[0].message.content
    except Exception:  # noqa: BLE001
        logger.exception('เรียก LLM อ่านรูปไม่สำเร็จ')
        return None

    number = parse_order_number(raw)
    logger.info('LLM ตอบ %r → order_number=%s', raw, number)
    return number
