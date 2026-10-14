import logging

from django.apps import AppConfig
from django.conf import settings

logger = logging.getLogger(__name__)


class LineBotConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    # โฟลเดอร์ชื่อ line_bot (ไม่ใช่ linebot) เพราะ package ของ line-bot-sdk ชื่อ `linebot`
    # ถ้าตั้งโฟลเดอร์ชื่อซ้ำ จะบัง SDK แล้ว import ไม่ได้ — label ยังเป็น linebot ตามที่ตั้งใจ
    name = 'line_bot'
    label = 'linebot'
    verbose_name = 'LINE Bot'

    def ready(self):
        check_allowed_groups_configured()


def check_allowed_groups_configured():
    """LINE_ALLOWED_GROUP_IDS ว่าง = fail-closed บล็อกทุกกลุ่ม — เตือนตอน start ให้รู้ว่ายังไม่ตั้งค่า
    (คืน True เมื่อตั้งแล้ว ไว้ให้ test เรียกตรงๆ)"""
    if settings.LINE_ALLOWED_GROUP_IDS:
        return True
    logger.error('LINE_ALLOWED_GROUP_IDS ว่าง — บอท LINE จะไม่ทำงานในกลุ่มใดเลย (fail-closed) '
                 'ตั้งค่า groupId ใน .env แล้ว restart order')
    return False
