"""
pending — "รายการรอ" ของบอท LINE: เลขออร์เดอร์ที่อ่านได้จากรูป รอข้อความยืนยัน ("ครบ"/"เสร็จ")

เก็บใน Django cache (settings.CACHES = DatabaseCache เพราะ gunicorn หลาย worker)
key ต่อ (groupId, userId) · list เลขไม่ซ้ำ เรียงตามลำดับที่ส่งรูป · หมดอายุ 10 นาที
(นับใหม่ทุกครั้งที่มีรูปใหม่เข้ามา)

**race "ครบ" แซงรูปสุดท้าย (2026-10-15 09:18/09:33):** LINE ส่งรูปแต่ละใบเป็น webhook แยก gunicorn 2 worker
ประมวลผลขนาน ใบละ ~2 วิ (ดาวน์โหลด+LLM) คนส่งพิมพ์ "ครบ" ทันที → ข้อความยืนยันถูกประมวลผลก่อนรูปสุดท้ายอ่านเสร็จ
→ เลขรูปสุดท้ายตกค้างในรายการรอจนหมดอายุ ไม่ถูกติ๊ก
แก้: counter "กำลังอ่าน" (inflight) ต่อ (groupId,userId) +1 ก่อนเริ่มอ่านรูป -1 เมื่อเสร็จ (ได้เลขหรือไม่ก็ตาม)
TTL 60 วิ กันค้าง · ฝั่ง "ครบ" ถ้า inflight > 0 รอ poll ทุก WAIT_INTERVAL จน 0 (สูงสุด WAIT_TIMEOUT; reply_token
อยู่ได้ ~1 นาที) ค่อยติ๊ก · เกินเวลา → ติ๊กที่มี + บอกให้ส่ง "ครบ" อีกครั้ง
+ counter "อ่านเลขไม่ได้" ต่อชุด (TTL เท่ารายการรอ) ไว้บอกท้ายข้อความสรุป
"""
import time

from django.core.cache import cache

PENDING_TTL = 600  # วินาที
INFLIGHT_TTL = 60  # วินาที — กัน counter ค้างถ้า worker ตายกลางทาง
WAIT_TIMEOUT = 15  # วินาที — "ครบ" รอรูปที่กำลังอ่านได้นานสุด
WAIT_INTERVAL = 0.5


def _key(group_id, user_id):
    return 'linebot:pending:%s:%s' % (group_id or '-', user_id or '-')


def get_pending(group_id, user_id):
    return list(cache.get(_key(group_id, user_id)) or [])


def add_pending(group_id, user_id, order_number):
    """เพิ่มเลขเข้ารายการรอ (ไม่ซ้ำ) แล้วต่ออายุ 10 นาที — คืน list ปัจจุบัน"""
    numbers = get_pending(group_id, user_id)
    if order_number not in numbers:
        numbers.append(order_number)
    cache.set(_key(group_id, user_id), numbers, PENDING_TTL)
    return numbers


def clear_pending(group_id, user_id):
    cache.delete(_key(group_id, user_id))


# ---------------------------------------------------------------------------
# counter "กำลังอ่านรูป" + "อ่านเลขไม่ได้"
# ---------------------------------------------------------------------------

def _inflight_key(group_id, user_id):
    return 'linebot:inflight:%s:%s' % (group_id or '-', user_id or '-')


def _unreadable_key(group_id, user_id):
    return 'linebot:unreadable:%s:%s' % (group_id or '-', user_id or '-')


def inflight_count(group_id, user_id):
    return int(cache.get(_inflight_key(group_id, user_id)) or 0)


def inflight_start(group_id, user_id):
    """+1 ก่อนเริ่มดาวน์โหลด/อ่านรูป (ต่ออายุ TTL ทุกครั้ง)"""
    key = _inflight_key(group_id, user_id)
    cache.set(key, int(cache.get(key) or 0) + 1, INFLIGHT_TTL)


def inflight_done(group_id, user_id):
    """-1 เมื่ออ่านเสร็จ (ไม่ว่าได้เลขหรือไม่ / พัง) — ไม่ต่ำกว่า 0"""
    key = _inflight_key(group_id, user_id)
    n = int(cache.get(key) or 0) - 1
    if n <= 0:
        cache.delete(key)
    else:
        cache.set(key, n, INFLIGHT_TTL)


def wait_inflight(group_id, user_id, timeout=None, interval=None):
    """รอจน counter = 0 → True · เกิน timeout → False (ผู้เรียกติ๊กที่มีแล้วบอกให้ส่ง "ครบ" ซ้ำ)"""
    timeout = WAIT_TIMEOUT if timeout is None else timeout
    interval = WAIT_INTERVAL if interval is None else interval
    deadline = time.monotonic() + timeout
    while inflight_count(group_id, user_id) > 0:
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)
    return True


def add_unreadable(group_id, user_id):
    """รูปที่ LLM อ่านเลขไม่ได้ในชุดนี้ +1 (TTL เท่ารายการรอ) — คืนจำนวนปัจจุบัน"""
    key = _unreadable_key(group_id, user_id)
    n = int(cache.get(key) or 0) + 1
    cache.set(key, n, PENDING_TTL)
    return n


def pop_unreadable(group_id, user_id):
    """ดึงจำนวนรูปอ่านไม่ได้ของชุดนี้แล้วล้าง (เรียกตอนตอบสรุปหลัง "ครบ")"""
    key = _unreadable_key(group_id, user_id)
    n = int(cache.get(key) or 0)
    cache.delete(key)
    return n
