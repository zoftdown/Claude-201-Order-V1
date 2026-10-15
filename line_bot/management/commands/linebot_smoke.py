"""
linebot_smoke — smoke test บอท LINE **ก่อน systemctl restart** ทุกครั้งที่ scp line_bot/* ขึ้น VPS

ตรวจของจริง 2 อย่าง (ไม่ส่งอะไรเข้า LINE · ไม่เขียน DB — tools ของ answer_question อ่านอย่างเดียว):
1. LINE Messaging API: get_bot_info ต้องได้ display_name = "หมอนทอง" (token ถูก + SDK method มีจริง)
2. RunningHub: answer_question("วันนี้มีกี่ใบ") ต้องได้ข้อความไม่ว่าง (โมเดลหลักหรือสำรอง)
   แล้ว fix_thai_spacing(คำตอบ) ต้องไม่ throw

ข้อใดพัง → พิมพ์สาเหตุ + exit code 1 (CommandError) → **ห้าม restart**
รันบน VPS: set -a && . ./.env && set +a && venv/bin/python manage.py linebot_smoke
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from linebot.v3.messaging import ApiClient, Configuration, MessagingApi

from line_bot import services

EXPECTED_BOT_NAME = 'หมอนทอง'
SMOKE_QUESTION = 'วันนี้มีกี่ใบ'


def check_line_bot_info():
    """→ display_name ของบอท; token ว่าง/ชื่อไม่ตรง/API พัง → raise"""
    if not settings.LINE_CHANNEL_ACCESS_TOKEN:
        raise RuntimeError('LINE_CHANNEL_ACCESS_TOKEN ว่าง')
    config = Configuration(access_token=settings.LINE_CHANNEL_ACCESS_TOKEN)
    with ApiClient(config) as api_client:
        info = MessagingApi(api_client).get_bot_info()
    name = getattr(info, 'display_name', None)
    if name != EXPECTED_BOT_NAME:
        raise RuntimeError('ชื่อบอทได้ %r คาดว่า %r' % (name, EXPECTED_BOT_NAME))
    return name


def check_llm_answer():
    """→ คำตอบจาก RunningHub (ผ่าน fix_thai_spacing); key ว่าง/LLM พัง/คำตอบว่าง → raise"""
    if not settings.RUNNINGHUB_API_KEY:
        raise RuntimeError('RUNNINGHUB_API_KEY ว่าง')
    answer = services.answer_question(SMOKE_QUESTION)
    if not answer or not answer.strip():
        raise RuntimeError('answer_question(%r) คืนค่าว่าง (ดู log line_bot.services)' % SMOKE_QUESTION)
    fixed = services.fix_thai_spacing(answer)
    if not fixed:
        raise RuntimeError('fix_thai_spacing คืนค่าว่างจากคำตอบ %r' % answer[:80])
    return fixed


class Command(BaseCommand):
    help = ('smoke test บอท LINE ก่อน restart: LINE get_bot_info + RunningHub answer_question '
            '+ fix_thai_spacing (ไม่ส่งเข้า LINE ไม่เขียน DB) — พังข้อใด exit 1')

    def handle(self, *args, **options):
        checks = [
            ('LINE get_bot_info', check_line_bot_info),
            ('RunningHub answer_question', check_llm_answer),
        ]
        failed = []
        for name, fn in checks:
            try:
                detail = fn()
            except Exception as exc:  # noqa: BLE001 — รายงานทุกข้อแล้วค่อยสรุป
                failed.append(name)
                self.stderr.write(self.style.ERROR('✗ %s: %s: %s' % (name, type(exc).__name__, exc)))
                continue
            self.stdout.write(self.style.SUCCESS('✓ %s: %s' % (name, str(detail)[:120].replace('\n', ' | '))))

        if services.primary_model_down():
            self.stdout.write(self.style.WARNING(
                'หมายเหตุ: โมเดลหลัก %s ถูกจำว่าล่ม → ตอบด้วยสำรอง %s'
                % (settings.RUNNINGHUB_MODEL, settings.RUNNINGHUB_FALLBACK_MODEL)))

        if failed:
            raise CommandError('linebot_smoke ไม่ผ่าน: %s — ห้าม restart' % ', '.join(failed))
        self.stdout.write(self.style.SUCCESS('linebot_smoke ผ่านทั้ง %d ข้อ' % len(checks)))
