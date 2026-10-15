"""
services — ส่วนที่คุยกับโลกภายนอก: ดาวน์โหลดรูปจาก LINE + LLM (อ่านเลขจากรูป / ถาม-ตอบด้วย tools)

ไม่แตะ DB เองโดยตรง (ส่วนค้น/อัปเดตออร์เดอร์อยู่ใน line_bot/orders.py; tools อ่านอย่างเดียวอยู่ใน
line_bot/tools.py ซึ่ง answer_question เรียกผ่าน run_tool)
ทุกฟังก์ชันคืน None เมื่อล้มเหลว + log — ให้ views ตัดสินใจเองโดยไม่ต้อง try/except ซ้ำ

LLM = ลำดับชั้น (llm_tiers) ลองทีละชั้นเมื่อชั้นก่อนพัง "แบบชั่วคราว":
  1. RunningHub โมเดลหลัก   (settings.RUNNINGHUB_MODEL)
  2. RunningHub โมเดลสำรอง  (settings.RUNNINGHUB_FALLBACK_MODEL — env, ว่าง = ไม่มี)
  3. Anthropic              (settings.ANTHROPIC_MODEL ผ่าน anthropic SDK, ANTHROPIC_API_KEY ว่าง = ไม่มี)
ชั้นที่พังถูกจำใน cache TIER_DOWN_TTL วิ ให้คำขอถัดไปข้ามไปชั้นถัดไปเลย (ไม่เสียเวลารอ 502 ซ้ำ)
"""
import base64
import json
import logging
import re
import time

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from linebot.v3.messaging import ApiClient, Configuration, MessagingApiBlob

from . import commands

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

# 2026-10-15 upstream ของ qwen3.8-flash-next บน RunningHub ตอบ 502 "upstream service timed out"
# ทุกคำขอ (~5 วิ) ทั้งจาก VPS และเครื่อง dev ขณะที่โมเดลอื่นบน key เดียวกันปกติ → บอทตอบ
# "ขอโทษ ตอบไม่ได้ตอนนี้" ทุกคำถาม เพราะ max_retries=0 และไม่มีสำรอง → ทำลำดับชั้นด้านบน
TIER_DOWN_KEY = 'linebot:llm:down:%s:%s'
TIER_DOWN_TTL = 300

BACKEND_RUNNINGHUB = 'runninghub'
BACKEND_ANTHROPIC = 'anthropic'


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


# ---------------------------------------------------------------------------
# ลำดับชั้น LLM
# ---------------------------------------------------------------------------

def llm_tiers():
    """[(backend, model), …] ตามลำดับที่จะลอง — เฉพาะชั้นที่ตั้งค่าครบ (key + ชื่อโมเดล)"""
    tiers = []
    if settings.RUNNINGHUB_API_KEY and settings.RUNNINGHUB_MODEL:
        tiers.append((BACKEND_RUNNINGHUB, settings.RUNNINGHUB_MODEL))
        fallback = getattr(settings, 'RUNNINGHUB_FALLBACK_MODEL', '')
        if fallback and fallback != settings.RUNNINGHUB_MODEL:
            tiers.append((BACKEND_RUNNINGHUB, fallback))
    if getattr(settings, 'ANTHROPIC_API_KEY', '') and getattr(settings, 'ANTHROPIC_MODEL', ''):
        tiers.append((BACKEND_ANTHROPIC, settings.ANTHROPIC_MODEL))
    return tiers


def tier_label(tier):
    return '%s:%s' % tier


def tier_down(tier):
    """cache บอกว่าชั้นนี้ล่มอยู่ไหม (ไว้ให้ command smoke/log รายงานด้วย)"""
    try:
        return bool(cache.get(TIER_DOWN_KEY % tier))
    except Exception:  # noqa: BLE001 — cache พังไม่ควรทำบอทพัง
        return False


def mark_tier_down(tier):
    try:
        cache.set(TIER_DOWN_KEY % tier, True, TIER_DOWN_TTL)
    except Exception:  # noqa: BLE001
        logger.exception('เซ็ต cache %s ไม่ได้', TIER_DOWN_KEY % tier)


def primary_model_down():
    tiers = llm_tiers()
    return bool(tiers) and tier_down(tiers[0])


def _is_transient(exc):
    """error ที่ควรลองชั้นถัดไป: timeout / ต่อไม่ได้ / HTTP 5xx / 429 — 4xx อื่น (400 param ผิด, 401 key ผิด) ไม่ใช่
    เช็คทั้ง openai และ anthropic SDK (คลาสชื่อเดียวกัน)"""
    if isinstance(exc, TimeoutError):
        return True
    for pkg_name in ('openai', 'anthropic'):
        try:
            pkg = __import__(pkg_name)
        except ImportError:
            continue
        if isinstance(exc, (pkg.APITimeoutError, pkg.APIConnectionError)):
            return True
        if isinstance(exc, pkg.APIStatusError):
            return exc.status_code >= 500 or exc.status_code == 429
    return False


def _call_tier(tier, messages, tools=None, max_tokens=LLM_MAX_TOKENS, timeout=LLM_TIMEOUT):
    """เรียกชั้นเดียว → response หน้าตา OpenAI ChatCompletion (exception หลุดให้ chat_create ตัดสิน)"""
    backend, model = tier
    if backend == BACKEND_RUNNINGHUB:
        client = _client(timeout)
        if client is None:
            raise RuntimeError('RunningHub client ไม่พร้อม (key/package)')
        kwargs = dict(model=model, temperature=0, max_tokens=max_tokens,
                      extra_body=LLM_EXTRA_BODY, messages=messages)
        if tools:
            kwargs.update(tools=tools, tool_choice='auto')
        return client.chat.completions.create(**kwargs)
    if backend == BACKEND_ANTHROPIC:
        from . import anthropic_backend
        return anthropic_backend.create(model, messages, tools=tools, max_tokens=max_tokens, timeout=timeout)
    raise RuntimeError('backend ไม่รู้จัก: %s' % backend)


def chat_create(messages, tools=None, max_tokens=LLM_MAX_TOKENS, tier=None, only=False):
    """เรียก LLM ตามลำดับชั้น → (response, tier_ที่ตอบ)
    - tier=None → เริ่มจากชั้นแรกที่ cache ไม่ได้จำว่าล่ม (ล่มหมด → เริ่มชั้นแรกอยู่ดี)
    - tier=(backend, model) → เริ่มจากชั้นนั้น (ไม่สนใจ cache) แล้วไล่ชั้นถัดไปเมื่อพังชั่วคราว
    - only=True → ลองเฉพาะชั้นที่ระบุ ไม่ไล่ต่อ (ไว้ให้ linebot_smoke ทดสอบทีละชั้น)
    ชั้นพังชั่วคราว (_is_transient) → log WARNING + จำใน cache แล้วลองชั้นถัดไป · ชั้นสุดท้ายพัง /
    error ไม่ชั่วคราว → โยน exception เดิม (ผู้เรียก except เอง)"""
    tiers = llm_tiers()
    if not tiers:
        raise RuntimeError('ไม่มี LLM ตั้งค่าเลย (RUNNINGHUB_API_KEY / ANTHROPIC_API_KEY ว่าง)')
    if tier is None:
        start = next((i for i, t in enumerate(tiers) if not tier_down(t)), 0)
        if start:
            logger.info('LLM ชั้น %s ถูกจำว่าล่ม → เริ่มที่ %s',
                        ', '.join(tier_label(t) for t in tiers[:start]), tier_label(tiers[start]))
    else:
        if tier not in tiers:
            raise RuntimeError('ชั้น %s ไม่ได้ตั้งค่า' % tier_label(tier))
        start = tiers.index(tier)
    for i in range(start, len(tiers)):
        current = tiers[i]
        try:
            return _call_tier(current, messages, tools=tools, max_tokens=max_tokens), current
        except Exception as exc:  # noqa: BLE001
            is_last = i == len(tiers) - 1
            if only or is_last or not _is_transient(exc):
                raise
            logger.warning('LLM ชั้น %s ล้มเหลว (%s: %s) → ลองชั้นถัดไป %s',
                           tier_label(current), type(exc).__name__, exc, tier_label(tiers[i + 1]))
            mark_tier_down(current)
    raise RuntimeError('unreachable')


# ---------------------------------------------------------------------------
# รูปจาก LINE + อ่านเลขออร์เดอร์
# ---------------------------------------------------------------------------

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


def read_order_number(image_bytes, mime='image/jpeg', tier=None, only=False):
    """ส่งรูปให้ LLM (ตามลำดับชั้น) หาเลขออร์เดอร์ที่หัวใบ
    → '6910-116' / None (ไม่เจอ หรือ LLM พัง/timeout ทุกชั้น → log ERROR แล้ว None)
    tier/only ไว้ให้ smoke ทดสอบชั้นที่ระบุ"""
    if not llm_tiers():
        logger.warning('ไม่มี LLM ตั้งค่า — อ่านรูปไม่ได้')
        return None

    data_url = 'data:%s;base64,%s' % (mime, base64.b64encode(image_bytes).decode('ascii'))
    messages = [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': [
            {'type': 'text', 'text': 'เลขออร์เดอร์ในรูปนี้คืออะไร'},
            {'type': 'image_url', 'image_url': {'url': data_url}},
        ]},
    ]
    try:
        resp, used = chat_create(messages, max_tokens=LLM_MAX_TOKENS, tier=tier, only=only)
        raw = resp.choices[0].message.content
    except Exception:  # noqa: BLE001
        logger.exception('เรียก LLM อ่านรูปไม่สำเร็จ')
        return None

    number = parse_order_number(raw)
    logger.info('LLM (%s) ตอบ %r → order_number=%s', tier_label(used), raw, number)
    return number


# ---------------------------------------------------------------------------
# ตีความคำสั่ง (intent) ด้วย LLM — ใช้เมื่อ regex ใน stage_cmd จับไม่ได้ (views ส่งมาเฉพาะข้อความ
# ที่มีเลขออร์เดอร์หรือคำขาน/mention) · ผลเข้า flow ทวนก่อนทำเดิม จึงยอมให้ LLM เดาได้
# ---------------------------------------------------------------------------

INTENT_MAX_TOKENS = 100
INTENT_COMMANDS = ('stage_done', 'close_job', 'question', 'unknown')
INTENT_STAGES = ('print', 'roll', 'cut', 'sort', 'sew', 'pack', 'pickup', 'shipped')
_INTENT_ORDER_RE = re.compile(r'(?<!\d)\d{4}-\d{1,3}(?!\d)')

INTENT_EXAMPLES = [
    ('พิม 6910-156', {'command': 'stage_done', 'stage': 'print', 'order_numbers': ['6910-156']}),
    ('6910-156 พิมแล้วนะ', {'command': 'stage_done', 'stage': 'print', 'order_numbers': ['6910-156']}),
    ('ตัดเสร็จ 6910-1 6910-2', {'command': 'stage_done', 'stage': 'cut', 'order_numbers': ['6910-1', '6910-2']}),
    ('เลเซอร์เรียบร้อย 6910-9', {'command': 'stage_done', 'stage': 'cut', 'order_numbers': ['6910-9']}),
    ('รีดแพ็คแล้วค้าบ 6910-3', {'command': 'stage_done', 'stage': 'pack', 'order_numbers': ['6910-3']}),
    ('ปิดงาน 6910-7 ด้วยนะ', {'command': 'close_job', 'stage': None, 'order_numbers': ['6910-7']}),
    ('6910-156 ถึงไหนแล้ว', {'command': 'question', 'stage': None, 'order_numbers': ['6910-156']}),
    ('วันนี้มีกี่ใบ', {'command': 'question', 'stage': None, 'order_numbers': []}),
    ('6910-156', {'command': 'unknown', 'stage': None, 'order_numbers': ['6910-156']}),
]


def intent_system_prompt():
    examples = '\n'.join('  "%s" → %s' % (t, json.dumps(j, ensure_ascii=False)) for t, j in INTENT_EXAMPLES)
    return (
        'คุณตีความข้อความจากพนักงานร้านพิมพ์เสื้อในกลุ่ม LINE ว่าเป็นคำสั่งอะไรถึงบอท "หมอนทอง" '
        'ตอบเป็น JSON บรรทัดเดียวเท่านั้น ห้ามมีข้อความอื่น:\n'
        '{"command": "stage_done"|"close_job"|"question"|"unknown", "stage": <key หรือ null>, '
        '"order_numbers": [<เลขออร์เดอร์ที่ปรากฏ รูปแบบ 6910-116>]}\n'
        'คำสั่งที่บอทมีจริง:\n%s\n'
        'command:\n'
        '- stage_done = แจ้งว่าทำขั้นผลิตเสร็จ/ให้ติ๊กขั้น (มีเลขออร์เดอร์) · stage ใช้ key: '
        'print=พิมพ์/ปริ้น, roll=โรล, cut=ตัด/เลเซอร์, sort=คัด, sew=ส่งเย็บ/เย็บ, pack=รีด/แพ็ค/รีดแพ็ค, '
        'pickup=ลูกค้ารับ/มารับ, shipped=ส่งของ/ส่งแล้ว(ขนส่ง)\n'
        '- close_job = ปิดงาน/จบงาน/เสร็จหมดทุกขั้น (มีเลขออร์เดอร์)\n'
        '- question = ถามข้อมูล/สถานะ/ยอด/ลิงก์ (ไม่ใช่สั่งให้บันทึก)\n'
        '- unknown = ตีความไม่ได้หรือกำกวม (เช่น มีแค่เลขออร์เดอร์)\n'
        'กติกา: คำสะกดผิด/ภาษาพูด/คำลงท้าย (นะ ค่ะ ครับ ค้าบ เรียบร้อย เสร็จ) ให้ตีความตามเจตนา · '
        'ไม่แน่ใจระหว่างขั้น → unknown · order_numbers ต้องเป็นเลขที่อยู่ในข้อความจริงเท่านั้น\n'
        'ตัวอย่าง:\n%s'
    ) % (commands.commands_for_prompt(), examples)


def parse_intent(raw_text, text=''):
    """แกะ JSON intent แบบทนทาน (ตัด ```fence / หยิบก้อน {…} แรก) + validate
    → {'command','stage','order_numbers'} หรือ None เมื่อ parse ไม่ได้
    order_numbers: ใช้ของ LLM เฉพาะที่อยู่ในข้อความจริง ถ้าว่างเติมจาก regex ของข้อความ"""
    if not raw_text:
        return None
    t = raw_text.strip()
    t = re.sub(r'^```(?:json)?\s*', '', t)
    t = re.sub(r'\s*```$', '', t).strip()
    try:
        data = json.loads(t)
    except (ValueError, TypeError):
        m = re.search(r'\{.*?\}', t, re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except (ValueError, TypeError):
            return None
    if not isinstance(data, dict):
        return None
    command = data.get('command')
    if command not in INTENT_COMMANDS:
        command = 'unknown'
    stage = data.get('stage')
    if stage not in INTENT_STAGES:
        stage = None
    in_text = _INTENT_ORDER_RE.findall(text or '')
    raw_numbers = data.get('order_numbers') or []
    numbers = [n for n in raw_numbers if isinstance(n, str) and n in in_text] if isinstance(raw_numbers, list) else []
    if not numbers:
        numbers = list(dict.fromkeys(in_text))
    if command == 'stage_done' and not stage:
        command = 'unknown'
    return {'command': command, 'stage': stage, 'order_numbers': list(dict.fromkeys(numbers))}


def classify_command(text):
    """ส่งข้อความ + รายการคำสั่ง (commands.py) ให้ LLM ตีความ → dict intent หรือ None เมื่อ LLM พัง/ไม่มี LLM
    temperature 0 · max_tokens 100 · ลำดับชั้น LLM เดียวกับ Q&A (chat_create)"""
    if not llm_tiers():
        logger.warning('ไม่มี LLM ตั้งค่า — ตีความคำสั่งไม่ได้')
        return None
    messages = [
        {'role': 'system', 'content': intent_system_prompt()},
        {'role': 'user', 'content': text},
    ]
    try:
        resp, used = chat_create(messages, max_tokens=INTENT_MAX_TOKENS)
        raw = resp.choices[0].message.content
    except Exception:  # noqa: BLE001
        logger.exception('ตีความคำสั่ง (intent) ไม่สำเร็จ: %r', text)
        return None
    intent = parse_intent(raw, text)
    logger.info('intent (%s) %r → %r → %s', tier_label(used), text, raw, json.dumps(intent, ensure_ascii=False))
    return intent


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
        'บุคลิก (ล็อกตายตัว ทุกคำตอบ): เรียกตัวเองว่า "หมอนทอง" (ไม่ใช่ ฉัน/เรา/ผม/ดิฉัน/AI) '
        'ลงท้ายประโยคด้วย "ค่ะ" หรือ "นะคะ" ทุกครั้ง ไม่ใช้ ครับ/จ้า/จ้ะ · สุภาพ เป็นกันเอง ไม่ยืดยาว\n'
        'คำสั่งที่หมอนทองทำได้จริงในแชท (มีแค่นี้ ห้ามแต่งคำสั่งอื่น):\n'
        '%s\n'
        '- ถ้าผู้ใช้ขอให้ "ทำ" อะไรที่เป็นการเขียน/แก้ข้อมูล (ติ๊กขั้น ปิดงาน แก้สถานะ แก้ใบงาน) '
        'หมอนทองทำเองผ่านการถาม-ตอบไม่ได้ ให้แนะนำให้พิมพ์คำสั่งจากรายการข้างบน**เท่านั้น** '
        'โดยยกตัวอย่างรูปแบบจริงพร้อมเลขออร์เดอร์ของเขา (เช่น "6910-167 พิมพ์แล้ว" หรือ "ปิดงาน 6910-167") '
        'ห้ามบอกให้ไปกดในเว็บถ้ามีคำสั่งในแชทอยู่แล้ว · สิ่งที่ไม่มีคำสั่ง (แก้ราคา/ชื่อ/ที่อยู่ ลบใบ) ให้บอกว่าต้องแก้ในเว็บ\n'
        'กติกา:\n'
        '- ตอบภาษาไทย สั้น กระชับ เป็นข้อความธรรมดา ห้ามใช้ markdown (ไม่มี ** # ` หรือตาราง) เพราะ LINE ไม่แสดงผล\n'
        '- ถามสถานะใบเดียว ตอบไม่เกิน 6 บรรทัด: เลข+ชื่องาน, จำนวนตัว, ขั้นปัจจุบัน, '
        'ยอด/ค้าง (ถ้าถาม), วิธีรับ — ไม่ต้องไล่ทุกขั้นถ้าไม่ได้ถาม\n'
        '- ห้ามมีอักษรจีน/ญี่ปุ่น/เกาหลี ปนในคำตอบเด็ดขาด\n'
        '- ตอบเฉพาะจากผลของ tool เท่านั้น ห้ามเดาหรือแต่งข้อมูล ถ้ายังไม่ได้เรียก tool ให้เรียกก่อน\n'
        '- ถ้าผลค้นหาเกิน 10 ใบ ให้บอกจำนวนทั้งหมดแล้วขอเงื่อนไขเพิ่ม (ชื่อลูกค้า/ช่วงวัน/เพจ) แทนการไล่ทั้งหมด\n'
        '- ถ้าไม่เจอ ให้บอกว่าไม่เจอตรงๆ\n'
        '- เลขออร์เดอร์รูปแบบ 6910-116 (ปปดด-ลำดับ) · ตัวเลขเงินให้ใส่หน่วยบาท\n'
        '- เมื่อแสดงหลายใบ ให้ 1 บรรทัดต่อใบ: เลขออร์เดอร์ ชื่องาน จำนวนตัว ขั้นปัจจุบัน\n'
        '- คำถามเชิงจำนวน/ยอดรวม (เช่น "กี่ตัว" "กี่ใบ" "เท่าไหร่") ให้ตอบตัวเลขสรุป 1-2 บรรทัด '
        'ไม่ต้องแจกแจงรายใบ เว้นแต่ถูกถามว่า "มีอะไรบ้าง" หรือขอ "รายการ"\n'
        '- ชื่องาน ชื่อลูกค้า และเลขออร์เดอร์ ต้องคัดลอกตรงจากผล tool ทุกตัวอักษร '
        'ห้ามแก้สะกด ห้ามย่อ ห้ามแปล ห้ามเติมหรือตัดช่องว่าง\n'
        '- ถ้าถูกถามลิงก์/เบอร์/ที่อยู่ลูกค้า ให้ตอบค่าตรงจากฟิลด์ customer_link / shipping_address '
        'ของผล tool ทั้งค่า ถ้าเป็น URL ให้ส่งทั้งเส้นเต็มๆ ห้ามย่อ ห้ามตัด ห้ามเติม … · ฟิลด์ว่าง = บอกว่าไม่มีข้อมูล'
    ) % (THAI_WEEKDAYS[today.weekday()], today.strftime('%Y-%m-%d'), commands.commands_for_prompt())


# อักษรจีน/ญี่ปุ่น/เกาหลี — โมเดลจีน (qwen/deepseek/glm) บางครั้งหลุดคำจีนปนในคำตอบไทย
_CJK_RE = re.compile('[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff\u3000-\u303f\uff00-\uffef]')


def has_cjk(text):
    return bool(text) and _CJK_RE.search(text) is not None


def strip_cjk(text):
    """ลบอักษร CJK ออก แล้วเก็บช่องว่างซ้ำให้เหลือตัวเดียว"""
    if not text:
        return text
    out = _CJK_RE.sub('', text)
    return re.sub(r'[ \t]{2,}', ' ', out).strip()


# สระบน/ล่าง + วรรณยุกต์ (combining marks) ที่ LLM บางตัวแทรกช่องว่างคั่นจากพยัญชนะ เช่น "เสื้ อ" / "ชื่ อ"
_THAI_MARKS = 'ัิ-ฺ็-๎'
_THAI_ANY = '฀-๿'
# ช่องว่าง (space/tab ไม่รวม newline) ระหว่าง อักษรไทย → mark  หรือ  mark → อักษรไทย
_THAI_SPACE_BEFORE_MARK_RE = re.compile('(?<=[%s])[ \t]+(?=[%s])' % (_THAI_ANY, _THAI_MARKS))
_THAI_SPACE_AFTER_MARK_RE = re.compile('(?<=[%s])[ \t]+(?=[%s])' % (_THAI_MARKS, _THAI_ANY))


def fix_thai_spacing(text):
    """ลบช่องว่างที่ LLM แทรกระหว่างอักษรไทยกับสระ/วรรณยุกต์ (ติดกับ U+0E31, U+0E34–3A, U+0E47–4E
    ทั้งสองข้าง) — "เสื้ อ" → "เสื้อ", "ที่ นี่" → "ที่นี่" · ไม่แตะช่องว่างที่ติดตัวเลข/ละติน/ขึ้นบรรทัดใหม่"""
    if not text:
        return text
    text = _THAI_SPACE_BEFORE_MARK_RE.sub('', text)
    return _THAI_SPACE_AFTER_MARK_RE.sub('', text)


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


def answer_question(question, history=None, today=None, tier=None, only=False):
    """ถาม-ตอบด้วย LLM + tools (line_bot.tools) แบบ OpenAI tool calling
    วนจนกว่า LLM จะไม่เรียก tool (สูงสุด QA_MAX_ROUNDS) · timeout 20 วิ/รอบ
    history = list ของ {'role','content'} ก่อนหน้า (จาก line_bot.history)
    tier/only = บังคับชั้น LLM (ไว้ให้ linebot_smoke ทดสอบทีละชั้น) · ปกติให้ chat_create เลือกเอง
    → ข้อความคำตอบ (str) หรือ None เมื่อ LLM พัง/timeout/เกินรอบโดยไม่มีคำตอบ (views ตอบขอโทษ)
    log ทุกรอบ: คำถาม, ชั้นที่ตอบ, tool ที่เรียก+args, จำนวน tokens (INFO)
    post-process: คำตอบมีอักษร CJK → ลองใหม่ด้วยชั้นถัดไป 1 ครั้ง (ถ้ามีและไม่ใช่ only) ยังมี → ลบออก + log WARNING"""
    answer, used = _answer_question_once(question, history, today, tier, only)
    if answer is None or not has_cjk(answer):
        return answer
    tiers = llm_tiers()
    idx = tiers.index(used) if used in tiers else -1
    if not only and 0 <= idx < len(tiers) - 1:
        retry_tier = tiers[idx + 1]
        logger.warning('QA คำตอบจาก %s มีอักษร CJK → ลองใหม่ด้วย %s: %r',
                       tier_label(used), tier_label(retry_tier), answer[:120])
        retry, _ = _answer_question_once(question, history, today, retry_tier, True)
        if retry and not has_cjk(retry):
            return retry
        if retry:
            answer = retry
    cleaned = strip_cjk(answer)
    logger.warning('QA คำตอบยังมีอักษร CJK หลังลองใหม่ → ลบออก: %r → %r', answer[:120], cleaned[:120])
    return cleaned or None


def _answer_question_once(question, history=None, today=None, tier=None, only=False):
    """loop ถาม-ตอบ 1 ครั้ง → (คำตอบหรือ None, tier ที่ตอบรอบสุดท้าย)"""
    from . import tools  # import ที่นี่ กัน import ฝั่ง DB ตอนโหลด module

    if not llm_tiers():
        logger.warning('ไม่มี LLM ตั้งค่า — ตอบคำถามไม่ได้')
        return None, None

    logger.info('QA คำถาม: %r (history %d ข้อความ)', question, len(history or []))
    messages = [{'role': 'system', 'content': qa_system_prompt(today)}]
    messages.extend(history or [])
    messages.append({'role': 'user', 'content': question})

    started = time.monotonic()
    last_content = None
    current = tier  # ชั้นที่ตอบรอบก่อน — สลับชั้นแล้วใช้ต่อทุกรอบ (ไม่ย้อนกลับไปชั้นที่ล่ม)
    for round_no in range(1, QA_MAX_ROUNDS + 1):
        if time.monotonic() - started > QA_TOTAL_DEADLINE:
            logger.warning('QA เกินเวลารวม %ds ที่รอบ %d', QA_TOTAL_DEADLINE, round_no)
            break
        try:
            resp, current = chat_create(messages, tools=tools.TOOL_SCHEMAS, max_tokens=QA_MAX_TOKENS,
                                        tier=current, only=only)
        except Exception:  # noqa: BLE001
            logger.exception('QA เรียก LLM รอบ %d ไม่สำเร็จ', round_no)
            return None, current

        msg = resp.choices[0].message
        tool_calls = list(getattr(msg, 'tool_calls', None) or [])
        content = (getattr(msg, 'content', None) or '').strip()
        prompt_t, completion_t, total_t = _usage_tuple(resp)
        logger.info('QA รอบ %d (%s): tool_calls=%d tokens prompt=%s completion=%s total=%s',
                    round_no, tier_label(current), len(tool_calls), prompt_t, completion_t, total_t)

        if not tool_calls:
            if content:
                logger.info('QA คำตอบ: %r', content)
                return fix_thai_spacing(content), current
            logger.warning('QA รอบ %d ไม่มีทั้ง tool_call และ content', round_no)
            return None, current

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
    return (fix_thai_spacing(last_content) if last_content else None), current
