"""
pending — "รายการรอ" ของบอท LINE: เลขออร์เดอร์ที่อ่านได้จากรูป รอข้อความยืนยัน ("ครบ"/"เสร็จ")

เก็บใน Django cache (settings.CACHES = DatabaseCache เพราะ gunicorn หลาย worker)
key ต่อ (groupId, userId) · list เลขไม่ซ้ำ เรียงตามลำดับที่ส่งรูป · หมดอายุ 10 นาที
(นับใหม่ทุกครั้งที่มีรูปใหม่เข้ามา)
"""
from django.core.cache import cache

PENDING_TTL = 600  # วินาที


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
