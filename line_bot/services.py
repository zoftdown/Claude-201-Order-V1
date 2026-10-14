"""
services — ส่วนที่คุยกับโลกภายนอก: ดาวน์โหลดรูปจาก LINE + LLM (อ่านเลขจากรูป / ถาม-ตอบด้วย tools)

ไม่แตะ DB เองโดยตรง (ส่วนค้น/อัปเดตออร์เดอร์อยู่ใน line_bot/orders.py; tools อ่านอย่างเดียวอยู่ใน
line_bot/tools.py ซึ่ง answer_question เรียกผ่าน run_tool)
ทุกฟังก์ชันคืน None เมื่อล้มเหลว + log — ให้ views ตัดสินใจเองโดยไม่ต้อง try/except ซ้ำ
"""
import base64
import json
import logging
import re
import time

from django.conf import settings
from django.utils import timezone

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


def _client(timeout=LLM_TIMEOUT):
    """OpenAI-compatible client ชี้ RunningHub (None เมื่อไม่มี key / ไม่มี package)"""
    if not settings.RUNNINGHUB_API_KEY:
        logger.warning('RUNNINGHUB_API_KEY ว่าง — ปิดการใช้ LLM')
        return None
    try:
        from openai import OpenAI
    except ImportError:
        logger.error('ไม่มี package openai — pip install openai')
        return None
    return OpenAI(
        api_key=settings.RUNNINGHUB_API_KEY,
        base_url=settings.RUNNINGHUB_BASE_URL,
        timeout=timeout,
        max_retries=0,
    )


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
    client = _client()
    if client is None:
        return None

    data_url = 'data:%s;base64,%s' % (mime, base64.b64encode(image_bytes).decode('ascii'))
    try:
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


# ---------------------------------------------------------------------------
# ขั้น 5 — ถาม-ตอบด้วย tool calling
# ---------------------------------------------------------------------------

QA_MAX_ROUNDS = 5          # รอบเรียก LLM สูงสุด (วนจนไม่มี tool_call)
QA_MAX_TOKENS = 1000       # คำตอบยาวกว่าอ่านเลขจากรูป
QA_TOTAL_DEADLINE = 60     # วินาทีรวมทุกรอบ — reply_token ของ LINE หมดอายุ ~1 นาที
QA_TOOL_RESULT_MAX = 12000  # ตัดผล tool ที่ยาวเกิน กัน context บวม

THAI_WEEKDAYS = ['จันทร์', 'อังคาร', 'พุธ', 'พฤหัสบดี', 'ศุกร์', 'เสาร์', 'อาทิตย์']


def qa_system_prompt(today=None):
    today = today or timezone.localdate()
    return (
        'คุณคือผู้ช่วยของร้านพิมพ์เสื้อ ชื่อ "น้องหมอนทอง" ตอบคำถามพนักงานเรื่องใบออร์เดอร์ในกลุ่ม LINE\n'
        'วันนี้คือวัน%s ที่ %s (เวลาไทย Asia/Bangkok) — ใช้คำนวณ "เมื่อวาน/สัปดาห์นี้/เดือนนี้"\n'
        'กติกา:\n'
        '- ตอบภาษาไทย สั้น กระชับ เป็นข้อความธรรมดา ห้ามใช้ markdown (ไม่มี ** # ` หรือตาราง) เพราะ LINE ไม่แสดงผล\n'
        '- ตอบเฉพาะจากผลของ tool เท่านั้น ห้ามเดาหรือแต่งข้อมูล ถ้ายังไม่ได้เรียก tool ให้เรียกก่อน\n'
        '- ถ้าผลค้นหาเกิน 10 ใบ ให้บอกจำนวนทั้งหมดแล้วขอเงื่อนไขเพิ่ม (ชื่อลูกค้า/ช่วงวัน/เพจ) แทนการไล่ทั้งหมด\n'
        '- ถ้าไม่เจอ ให้บอกว่าไม่เจอตรงๆ\n'
        '- เลขออร์เดอร์รูปแบบ 6910-116 (ปปดด-ลำดับ) · ตัวเลขเงินให้ใส่หน่วยบาท\n'
        '- เมื่อแสดงหลายใบ ให้ 1 บรรทัดต่อใบ: เลขออร์เดอร์ ชื่องาน จำนวนตัว ขั้นปัจจุบัน'
    ) % (THAI_WEEKDAYS[today.weekday()], today.strftime('%Y-%m-%d'))


def _usage_tuple(resp):
    usage = getattr(resp, 'usage', None)
    if usage is None:
        return (None, None, None)
    return (getattr(usage, 'prompt_tokens', None),
            getattr(usage, 'completion_tokens', None),
            getattr(usage, 'total_tokens', None))


def _parse_tool_args(raw):
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw or '{}')
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def answer_question(question, history=None, today=None):
    """ถาม-ตอบด้วย LLM + tools (line_bot.tools) แบบ OpenAI tool calling
    วนจนกว่า LLM จะไม่เรียก tool (สูงสุด QA_MAX_ROUNDS) · temperature 0 · timeout 20 วิ/รอบ
    history = list ของ {'role','content'} ก่อนหน้า (จาก line_bot.history)
    → ข้อความคำตอบ (str) หรือ None เมื่อ LLM พัง/timeout/เกินรอบโดยไม่มีคำตอบ (views ตอบขอโทษ)
    log ทุกรอบ: คำถาม, tool ที่เรียก+args, จำนวน tokens (INFO)"""
    from . import tools  # import ที่นี่ กัน import ฝั่ง DB ตอนโหลด module

    client = _client()
    if client is None:
        return None

    logger.info('QA คำถาม: %r (history %d ข้อความ)', question, len(history or []))
    messages = [{'role': 'system', 'content': qa_system_prompt(today)}]
    messages.extend(history or [])
    messages.append({'role': 'user', 'content': question})

    started = time.monotonic()
    last_content = None
    for round_no in range(1, QA_MAX_ROUNDS + 1):
        if time.monotonic() - started > QA_TOTAL_DEADLINE:
            logger.warning('QA เกินเวลารวม %ds ที่รอบ %d', QA_TOTAL_DEADLINE, round_no)
            break
        try:
            resp = client.chat.completions.create(
                model=settings.RUNNINGHUB_MODEL,
                temperature=0,
                max_tokens=QA_MAX_TOKENS,
                extra_body=LLM_EXTRA_BODY,
                tools=tools.TOOL_SCHEMAS,
                tool_choice='auto',
                messages=messages,
            )
        except Exception:  # noqa: BLE001
            logger.exception('QA เรียก LLM รอบ %d ไม่สำเร็จ', round_no)
            return None

        msg = resp.choices[0].message
        tool_calls = list(getattr(msg, 'tool_calls', None) or [])
        content = (getattr(msg, 'content', None) or '').strip()
        prompt_t, completion_t, total_t = _usage_tuple(resp)
        logger.info('QA รอบ %d: tool_calls=%d tokens prompt=%s completion=%s total=%s',
                    round_no, len(tool_calls), prompt_t, completion_t, total_t)

        if not tool_calls:
            if content:
                logger.info('QA คำตอบ: %r', content)
                return content
            logger.warning('QA รอบ %d ไม่มีทั้ง tool_call และ content', round_no)
            return None

        last_content = content or last_content
        messages.append({
            'role': 'assistant',
            'content': content,
            'tool_calls': [{
                'id': tc.id,
                'type': 'function',
                'function': {'name': tc.function.name, 'arguments': tc.function.arguments or '{}'},
            } for tc in tool_calls],
        })
        for tc in tool_calls:
            args = _parse_tool_args(tc.function.arguments)
            logger.info('QA tool %s args=%s', tc.function.name, json.dumps(args, ensure_ascii=False))
            result = tools.run_tool(tc.function.name, args)
            payload = json.dumps(result, ensure_ascii=False, default=str)
            if len(payload) > QA_TOOL_RESULT_MAX:
                payload = payload[:QA_TOOL_RESULT_MAX] + '…(ตัด)'
            messages.append({'role': 'tool', 'tool_call_id': tc.id, 'content': payload})

    logger.warning('QA ครบ %d รอบแล้วยังไม่ได้คำตอบสุดท้าย', QA_MAX_ROUNDS)
    return last_content or None
