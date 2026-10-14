"""
history — บริบทต่อเนื่องของการถาม-ตอบ (ขั้น 5): 6 ข้อความล่าสุดต่อ (groupId, userId)

เก็บใน Django cache (DatabaseCache เดียวกับ pending.py — ข้าม gunicorn worker ได้)
key `linebot:history:<groupId>:<userId>` · list ของ {'role': 'user'|'assistant', 'content': str}
· เก็บเฉพาะคำถาม/คำตอบ (ไม่เก็บ tool call) · หมดอายุ 10 นาที นับใหม่ทุกครั้งที่คุย
→ ถามต่อได้ เช่น "เมื่อวานล่ะ" หลังถาม "วันนี้มีกี่ใบ"
"""
from django.core.cache import cache

HISTORY_TTL = 600  # วินาที
HISTORY_MAX = 6    # ข้อความ (user+assistant รวมกัน)


def _key(group_id, user_id):
    return 'linebot:history:%s:%s' % (group_id or '-', user_id or '-')


def get_history(group_id, user_id):
    return list(cache.get(_key(group_id, user_id)) or [])


def append_history(group_id, user_id, question, answer):
    """ต่อท้ายคู่ถาม-ตอบ แล้วตัดให้เหลือ HISTORY_MAX ข้อความล่าสุด + ต่ออายุ"""
    messages = get_history(group_id, user_id)
    messages.append({'role': 'user', 'content': question})
    messages.append({'role': 'assistant', 'content': answer})
    messages = messages[-HISTORY_MAX:]
    cache.set(_key(group_id, user_id), messages, HISTORY_TTL)
    return messages


def clear_history(group_id, user_id):
    cache.delete(_key(group_id, user_id))
