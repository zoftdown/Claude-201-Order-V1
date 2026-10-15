"""
stage_cmd — คำสั่งติ๊กขั้นผลิตผ่านข้อความ LINE (ไม่ผ่าน LLM จับด้วย regex) + ทวนก่อนทำ

รูปแบบ: "<คำขั้น>แล้ว <เลข 1 ตัวขึ้นไป>" หรือ "<เลข> <คำขั้น>" มี/ไม่มี "บอท"/"หมอนทอง" นำหน้าก็ได้
  เช่น "6910-167 พิมพ์แล้ว" · "ตัดแล้ว 6910-167 6910-168" · "หมอนทอง 6910-167 พิมพ์แล้ว"
คำขั้นและคำพ้อง (STAGE_WORDS): พิมพ์ · โรล · ตัด/เลเซอร์ · คัด · ส่งเย็บ/เย็บ · รีด/แพ็ค/รีดแพ็ค · ลูกค้ารับ/รับแล้ว

flow: parse → ทวน "ติ๊ก พิมพ์ · 6910-167 ชื่องาน 72 ตัว\\nใช่ไหม? ตอบ ใช่ / ไม่" เก็บรอใน cache ต่อ
(groupId, userId) PENDING_TTL=2 นาที → คนเดิมตอบ ใช่/ครับ/ค่ะ → ติ๊กทุกใบผ่าน **orders.views.mark_stage_done**
(helper เดียวกับปุ่มเว็บทุกแผนก) note "บอท LINE โดย <userId>" → ตอบผลสรุป · ตอบ ไม่/ยกเลิก หรือหมดเวลา → ไม่ทำ
ใบที่ติ๊กขั้นนั้นแล้ว → "ติ๊กแล้วอยู่แล้ว" ไม่ติ๊กซ้ำ · ไม่เจอเลข → "ไม่พบ 6910-xxx"
**ปิดงาน** ("ปิดงาน/จบงาน/ปิด <เลข…>", pseudo-stage CLOSE): ทวน "ปิดงาน · … — จะติ๊กขั้นที่ยังว่าง: คัด, ส่งเย็บ, ลูกค้ารับ"
→ ใช่ → ติ๊กทุกขั้นที่ยังว่างตามลำดับ CLOSE_STAGES ผ่าน mark_stage_done note "ปิดงานรวบ บอท LINE"
(ขั้นสุดท้าย = ส่งของ ถ้า delivery_method "ส่ง" ไม่งั้น ลูกค้ารับ — แบบเดียวกับ mark_legacy_orders_done)
ขั้นที่ติ๊กแล้วไม่แตะ · ครบทุกขั้นแล้ว → "ติ๊กครบแล้วอยู่แล้ว"
flow เดิม รูป+ครบ (line_bot.orders) ยังติ๊กรีด+แพ็คทันทีไม่ทวน — ไม่แตะ
"""
import logging
import re

from django.core.cache import cache
from django.db import transaction

from orders.views import STAGE_DONE_SPECS, STAGE_NEED_PACK, mark_stage_done, stage_is_done

from . import commands
from .orders import find_order

logger = logging.getLogger(__name__)

PENDING_TTL = 120  # วินาที — รอคำตอบ ใช่/ไม่

ORDER_NUMBER_RE = re.compile(r'(?<!\d)\d{4}-\d{1,3}(?!\d)')
PREFIX_RE = re.compile(r'^(?:หมอนทอง|บอท)\s*')

# คำขั้น (หลังตัดเลข/ช่องว่าง/"แล้ว" ออก ต้องเหลือตรงกับ key นี้เป๊ะ) → stage key ของ STAGE_DONE_SPECS
# กติกา: ข้อความ = เลข 6910-xxx ≥1 ตัว + คำขั้น 1 คำ (+ "แล้ว" หรือไม่ก็ได้) เท่านั้น สลับลำดับได้
# มีคำอื่นปน (เช่น "พิมพ์แล้วหรือยัง") = ไม่ใช่คำสั่ง → ไปถาม-ตอบ LLM ตามเดิม
# รวมคำสะกดผิด/ย่อที่พนักงานพิมพ์จริง: 2026-10-15 "6910-156 พิมแล้ว" (ไม่มีการันต์) → เงียบ/หลุดไป LLM
STAGE_WORDS = {
    'พิมพ์': 'print', 'พิม': 'print', 'พิมพ': 'print', 'ปริ้น': 'print', 'ปริ้นท์': 'print', 'print': 'print',
    'โรล': 'roll', 'โรว': 'roll', 'roll': 'roll',
    'ตัด': 'cut', 'เลเซอร์': 'cut', 'เลเซอ': 'cut', 'cut': 'cut',
    'คัด': 'sort',
    'ส่งเย็บ': 'sew', 'เย็บ': 'sew',
    'รีด': 'pack', 'แพ็ค': 'pack', 'แพค': 'pack', 'แพ๊ค': 'pack', 'รีดแพ็ค': 'pack', 'รีดแพค': 'pack',
    'รีด+แพ็ค': 'pack', 'pack': 'pack',
    'ลูกค้ารับ': 'pickup', 'รับ': 'pickup',  # "รับแล้ว" → ตัด "แล้ว" เหลือ "รับ"
}
# ปิดงาน = pseudo-stage (ไม่อยู่ใน STAGE_DONE_SPECS) — ติ๊กทุกขั้นที่ยังว่างตามลำดับนี้ + ขั้นสุดท้ายตามวิธีรับ
CLOSE = 'close'
CLOSE_WORDS = {'ปิดงาน': CLOSE, 'จบงาน': CLOSE, 'ปิด': CLOSE}
CLOSE_STAGES = ['print', 'roll', 'cut', 'sort', 'sew', 'pack']
CLOSE_NOTE = 'ปิดงานรวบ บอท LINE'

YES_WORDS = {'ใช่', 'ใช่ครับ', 'ใช่ค่ะ', 'ใช่คับ', 'ใช่ค่า', 'ค่ะ', 'ครับ', 'คับ', 'ค่า'}
NO_WORDS = {'ไม่', 'ไม่ใช่', 'ยกเลิก', 'ไม่ครับ', 'ไม่ค่ะ'}


def normalize(text):
    """ตัดช่องว่างหัวท้าย + แก้ "เเ" (สระเอ 2 ตัว ที่คนพิมพ์มือถือชอบใช้แทน แ)"""
    return (text or '').strip().replace('เเ', 'แ')


def strip_prefix(text):
    return PREFIX_RE.sub('', normalize(text), count=1).strip()


def parse_stage_command(text):
    """→ (stage_key, [เลขไม่ซ้ำตามลำดับ]) หรือ None เมื่อไม่ใช่คำสั่งติ๊กขั้น"""
    t = strip_prefix(text)
    numbers = list(dict.fromkeys(ORDER_NUMBER_RE.findall(t)))
    if not numbers:
        return None
    rest = ORDER_NUMBER_RE.sub(' ', t)
    rest = rest.replace('แล้ว', '').replace(' ', '').replace('\t', '').strip()
    stage = STAGE_WORDS.get(rest.lower()) or CLOSE_WORDS.get(rest)
    if stage is None:
        return None
    return stage, numbers


def is_yes(text):
    return normalize(text) in YES_WORDS


def is_no(text):
    return normalize(text) in NO_WORDS


def stage_label(stage):
    return 'ปิดงาน' if stage == CLOSE else STAGE_DONE_SPECS[stage]['label']


def _final_stage(order):
    """ขั้นออกจากร้านของใบนี้: ส่งของ เมื่อวิธีรับ = "ส่ง" ไม่งั้น ลูกค้ารับ (เหมือน mark_legacy_orders_done)"""
    return 'shipped' if (order.delivery_method or '') == 'ส่ง' else 'pickup'


def missing_stages(order):
    """ขั้นที่ยังว่างตามลำดับผลิต + ขั้นออกจากร้าน (ว่าง = ยังไม่ได้ทั้ง ส่งของ/ลูกค้ารับ)"""
    out = [s for s in CLOSE_STAGES if not stage_is_done(order, s)]
    if not stage_is_done(order, 'shipped'):  # exclusive คู่กัน เช็คตัวไหนก็ได้
        out.append(_final_stage(order))
    return out


# ---------------------------------------------------------------------------
# รายการรอทวน (cache ต่อ groupId,userId)
# ---------------------------------------------------------------------------

def _key(group_id, user_id):
    return 'linebot:stagecmd:%s:%s' % (group_id or '-', user_id or '-')


def get_pending(group_id, user_id):
    """→ {'stage': key, 'numbers': [...]} หรือ None"""
    return cache.get(_key(group_id, user_id))


def set_pending(group_id, user_id, stage, numbers):
    cache.set(_key(group_id, user_id), {'stage': stage, 'numbers': list(numbers)}, PENDING_TTL)


def clear_pending(group_id, user_id):
    cache.delete(_key(group_id, user_id))


# ---------------------------------------------------------------------------
# ข้อความ
# ---------------------------------------------------------------------------

def _order_line(order):
    return '%s %s %d ตัว' % (order.order_number, order.shirt_name or '', order.total_qty)


def start_confirm(group_id, user_id, stage, numbers):
    """ตรวจเลขทุกใบ → เก็บรอทวนเฉพาะใบที่ติ๊กได้ → คืนข้อความทวน (หรือข้อความแจ้งปัญหาอย่างเดียว
    เมื่อไม่มีใบไหนติ๊กได้ — ไม่ต้องรอคำตอบ)"""
    label = stage_label(stage)
    problems = []
    valid = []
    lines = []
    for n in numbers:
        order = find_order(n)
        if order is None:
            problems.append('ไม่พบ %s' % n)
        elif stage == CLOSE:
            missing = missing_stages(order)
            if not missing:
                problems.append('%s ติ๊กครบแล้วอยู่แล้ว' % n)
            else:
                valid.append(n)
                lines.append('%s — จะติ๊กขั้นที่ยังว่าง: %s' % (
                    _order_line(order), ', '.join(STAGE_DONE_SPECS[s]['label'] for s in missing)))
        elif stage_is_done(order, stage):
            problems.append('%s ติ๊กแล้วอยู่แล้ว (%s)' % (n, label))
        else:
            valid.append(n)
            lines.append(_order_line(order))
    if not valid:
        clear_pending(group_id, user_id)
        return '\n'.join(problems)
    set_pending(group_id, user_id, stage, valid)
    verb = label if stage == CLOSE else 'ติ๊ก %s' % label  # "ปิดงาน · …" / "ติ๊ก พิมพ์ · …"
    if len(lines) == 1:
        head = '%s · %s' % (verb, lines[0])
    else:
        head = '%s\n' % verb + '\n'.join('· %s' % ln for ln in lines)
    parts = [head] + problems + ['ใช่ไหม? ตอบ ใช่ / ไม่']
    return '\n'.join(parts)


def apply_stage(stage, numbers, note=''):
    """ติ๊กขั้น `stage` ให้ทุกใบผ่าน mark_stage_done → ข้อความสรุปต่อใบ"""
    label = stage_label(stage)
    lines = []
    for n in numbers:
        order = find_order(n)
        if order is None:
            lines.append('ไม่พบ %s' % n)
            continue
        if stage == CLOSE:
            lines.append(_close_order(order))
            continue
        with transaction.atomic():
            reason = mark_stage_done(order, stage, note=note)
        if reason is None:
            logger.info('ออร์เดอร์ %s → ติ๊ก "%s" (%s)', n, label, note)
            lines.append('✓ %s → %sแล้ว' % (_order_line(order), label))
        elif reason == STAGE_NEED_PACK:
            lines.append('%s ต้องรีด+แพ็คก่อน' % n)
        else:
            lines.append('%s ติ๊กแล้วอยู่แล้ว (%s)' % (n, label))
    return '\n'.join(lines)


def _close_order(order):
    """ติ๊กทุกขั้นที่ยังว่างของใบเดียวตามลำดับ (ขั้นที่ติ๊กแล้วไม่แตะ) → บรรทัดสรุป"""
    missing = missing_stages(order)
    if not missing:
        return '%s ติ๊กครบแล้วอยู่แล้ว' % order.order_number
    with transaction.atomic():
        for s in missing:
            mark_stage_done(order, s, note=CLOSE_NOTE)
    labels = ', '.join(STAGE_DONE_SPECS[s]['label'] for s in missing)
    logger.info('ออร์เดอร์ %s → ปิดงาน ติ๊ก %s (%s)', order.order_number, labels, CLOSE_NOTE)
    return '✓ %s → ปิดงานแล้ว (ติ๊ก: %s)' % (_order_line(order), labels)


def confirm(group_id, user_id, note=''):
    """คนเดิมตอบ "ใช่" → ติ๊กรายการที่รอ ล้างรายการ → ข้อความสรุป (None เมื่อไม่มีรายการรอ)"""
    pend = get_pending(group_id, user_id)
    if not pend:
        return None
    clear_pending(group_id, user_id)
    return apply_stage(pend['stage'], pend['numbers'], note=note)


HELP_TEXT = commands.help_text()  # ตาราง COMMANDS เดียวกับ system prompt (line_bot/commands.py)


def is_help(text):
    return strip_prefix(text) == 'ช่วย'
