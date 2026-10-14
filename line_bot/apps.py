from django.apps import AppConfig


class LineBotConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    # โฟลเดอร์ชื่อ line_bot (ไม่ใช่ linebot) เพราะ package ของ line-bot-sdk ชื่อ `linebot`
    # ถ้าตั้งโฟลเดอร์ชื่อซ้ำ จะบัง SDK แล้ว import ไม่ได้ — label ยังเป็น linebot ตามที่ตั้งใจ
    name = 'line_bot'
    label = 'linebot'
    verbose_name = 'LINE Bot'
