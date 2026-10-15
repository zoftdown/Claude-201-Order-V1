"""
stage_cmd — คำสั่งติ๊กขั้นผลิตผ่านข้อความ LINE (ไม่ผ่าน LLM จับด้วย regex) + ทวนก่อนทำ

รูปแบบ: "<คำขั้น>แล้ว <เลข 1 ตัวขึ้นไป>" หรือ "<เลข> <คำขั้น>" มี/ไม่มี "บอท"/"หมอนทอง" นำหน้าก็ได้
  เช่น "6910-167 พิมพ์แล้ว" · "ตัดแล้ว 6910-167 6910-168" · "หมอนทอง 6910-167 พิมพ์แล้ว"
คำขั้นและคำพ้อง (STAGE_WORDS): พิมพ์ · โรล · ตัด/เลเซอร์ · คัด · ส่งเย็บ/เย็บ · รีด/แพ็ค/รีดแพ็ค · ลูกค้ารับ/รับแล้ว

flow: parse → ทวน "ติ๊ก พิมพ์ · 6910-167 ชื่องาน 72 ตัว\\nใช่ไหม? ตอบ ใช่ / ไม่" เก็บรอใน cache ต่อ
(groupId, userId) PENDING_TTL=2 นาที → คนเดิมตอบ ใช่/ครับ/ค่ะ → ติ๊กทุกใบผ่าน **orders.views.mark_stage_done**
(helper เดียวกับปุ่มเว็บทุกแผนก) note "บอท LINE โดย <userId>" → ตอบผลสรุป · ตอบ ไม่/ยกเลิก หรือหมดเวลา → ไม่ทำ
ใบที่ติ๊กขั้นนั้นแล้ว → "ติ๊กแล้วอยู่แล้ว" ไม่ติ๊กซ้ำ · ไม่เจอเลข → "ไม่พบ 6910-xxx"
flow เดิม รูป+ครบ (line_bot.orders) ยังติ๊กรีด+แพ็คทันทีไม่ทวน — ไม่แตะ
"""
import logging
import re

from django.core.cache import cache
from django.db import transaction

from orders.views import STAGE_DONE_SPECS, STAGE_NEED_PACK, mark_stage_done, stage_is_done

from .orders import find_order

logger = logging.getLogger(__name__)

PENDING_TTL = 120  # วินาที — รอคำตอบ ใช่/ไม่

ORDER_NUMBER_RE = re.compile(r'(?<!\d)\d{4}-\d{1,3}(?!\d)')
PREFIX_RE = re.compile(r'^(?:หมอนทอง|บอท)\s*')

# คำขั้น (หลังตัดเลข/ช่องว่าง/"แล้ว" ออก ต้องเหลือตรงกับ key นี้เป๊ะ) → stage key ของ STAGE_DONE_SPECS
STAGE_WORDS = {
    'พิมพ์': 'print',
    'โรล': 'roll',
    'ตัด': 'cut', 'เลเซอร์': 'cut',
    'คัด': 'sort',
    'ส่งเย็บ': 'sew', 'เย็บ': 'sew',
    'รีด': 'pack', 'แพ็ค': 'pack', 'รีดแพ็ค': 'pack', 'รีด+แพ็ค': 'pack',
    'ลูกค้ารับ': 'pickup', 'รับ': 'pickup',  # "รับแล้ว" → ตัด "แล้ว" เหลือ "รับ"
}
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
    stage = STAGE_WORDS.get(rest)
    if stage is None:
        return None
    return stage, numbers


def is_yes(text):
    return normalize(text) in YES_WORDS


def is_no(text):
    return normalize(text) in NO_WORDS


def stage_label(stage):
    return STAGE_DONE_SPECS[stage]['label']


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
        elif stage_is_done(order, stage):
            problems.append('%s ติ๊กแล้วอยู่แล้ว (%s)' % (n, label))
        else:
            valid.append(n)
            lines.append(_order_line(order))
    if not valid:
        clear_pending(group_id, user_id)
        return '\n'.join(problems)
    set_pending(group_id, user_id, stage, valid)
    if len(lines) == 1:
        head = 'ติ๊ก %s · %s' % (label, lines[0])
    else:
        head = 'ติ๊ก %s\n' % label + '\n'.join('· %s' % ln for ln in lines)
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


def confirm(group_id, user_id, note=''):
    """คนเดิมตอบ "ใช่" → ติ๊กรายการที่รอ ล้างรายการ → ข้อความสรุป (None เมื่อไม่มีรายการรอ)"""
    pend = get_pending(group_id, user_id)
    if not pend:
        return None
    clear_pending(group_id, user_id)
    return apply_stage(pend['stage'], pend['numbers'], note=note)


HELP_TEXT = '\n'.join([
    'คำสั่งน้องหมอนทอง:',
    '• ติ๊กขั้น: "6910-167 พิมพ์แล้ว" / "ตัดแล้ว 6910-167 6910-168" (จะทวนก่อน ตอบ ใช่/ไม่)',
    '  คำขั้น: พิมพ์ · โรล · ตัด/เลเซอร์ · คัด · ส่งเย็บ · รีด/แพ็ค · ลูกค้ารับ',
    '• ส่งรูปใบงาน แล้วพิมพ์ "ครบ"/"เสร็จ" → ติ๊กรีด+แพ็ค (ไม่ทวน)',
    '• "บอท เสร็จ 6910-167" → ติ๊กรีด+แพ็คทันที · "บอท ยกเลิก" → ล้างรายการรอ',
    '• ถาม: "บอท วันนี้มีกี่ใบ" / "หมอนทอง 6910-167 ถึงไหนแล้ว" / "บอท ใบไหนค้างเกิน 7 วัน"',
    '• "บอท ping" → pong · "ช่วย" → ข้อความนี้',
])


def is_help(text):
    return strip_prefix(text) == 'ช่วย'
