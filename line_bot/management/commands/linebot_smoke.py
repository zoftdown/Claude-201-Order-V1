"""
linebot_smoke — smoke test บอท LINE **ก่อน systemctl restart** ทุกครั้งที่ scp line_bot/* ขึ้น VPS

ตรวจของจริง (ไม่ส่งอะไรเข้า LINE · ไม่เขียน DB — tools ของ answer_question อ่านอย่างเดียว):
1. LINE Messaging API: get_bot_info ต้องได้ display_name = "หมอนทอง" (token ถูก + SDK method มีจริง)
2. LLM ทีละชั้น (ครบทั้ง 3 ชั้น ไม่ไล่ข้ามชั้น): answer_question("วันนี้มีกี่ใบ", tier=…, only=True)
   ต้องได้ข้อความไม่ว่าง แล้ว fix_thai_spacing(คำตอบ) ต้องไม่ throw — รายงานว่าชั้นไหนผ่าน/ไม่ผ่าน
   ชั้น 1 RunningHub โมเดลหลัก · ชั้น 2 RunningHub โมเดลสำรอง · ชั้น 3 Anthropic (คนละผู้ให้บริการ)

ผลรวม: exit 1 (CommandError) = **ห้าม restart** เมื่อ LINE ไม่ผ่าน หรือ ไม่มีชั้น LLM ไหนผ่านเลย
  (ชั้นที่ล่มเพราะ upstream ของผู้ให้บริการ ไม่ควรบล็อก deploy ตราบใดที่ยังมีชั้นอื่นตอบได้)
  --strict = ชั้นใดไม่ผ่าน (รวมยังไม่ตั้ง key) ก็ exit 1
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


def expected_tiers():
    """ทั้ง 3 ชั้นตามลำดับ → [(ชื่อชั้น, tier หรือ None, เหตุผลที่ยังไม่ตั้งค่า)]
    (คนละตัวกับ services.llm_tiers ที่คืนเฉพาะชั้นที่ตั้งค่าครบ — smoke ต้องรายงานชั้นที่ขาดด้วย)"""
    rh_key = settings.RUNNINGHUB_API_KEY
    primary = settings.RUNNINGHUB_MODEL
    fallback = getattr(settings, 'RUNNINGHUB_FALLBACK_MODEL', '')
    an_key = getattr(settings, 'ANTHROPIC_API_KEY', '')
    an_model = getattr(settings, 'ANTHROPIC_MODEL', '')
    out = []
    if rh_key and primary:
        out.append(('ชั้น 1 RunningHub หลัก %s' % primary, (services.BACKEND_RUNNINGHUB, primary), None))
    else:
        out.append(('ชั้น 1 RunningHub หลัก', None, 'RUNNINGHUB_API_KEY/RUNNINGHUB_MODEL ว่าง'))
    if rh_key and fallback and fallback != primary:
        out.append(('ชั้น 2 RunningHub สำรอง %s' % fallback, (services.BACKEND_RUNNINGHUB, fallback), None))
    else:
        out.append(('ชั้น 2 RunningHub สำรอง', None,
                    'RUNNINGHUB_FALLBACK_MODEL ว่าง/ซ้ำกับหลัก' if rh_key else 'RUNNINGHUB_API_KEY ว่าง'))
    if an_key and an_model:
        out.append(('ชั้น 3 Anthropic %s' % an_model, (services.BACKEND_ANTHROPIC, an_model), None))
    else:
        out.append(('ชั้น 3 Anthropic', None, 'ANTHROPIC_API_KEY ว่าง (ตั้งใน .env)'))
    return out


def check_llm_tier(tier):
    """→ คำตอบจากชั้นนั้น (ผ่าน fix_thai_spacing); LLM พัง/คำตอบว่าง → raise"""
    answer = services.answer_question(SMOKE_QUESTION, tier=tier, only=True)
    if not answer or not answer.strip():
        raise RuntimeError('answer_question(%r) คืนค่าว่าง (ดู log line_bot.services)' % SMOKE_QUESTION)
    fixed = services.fix_thai_spacing(answer)
    if not fixed:
        raise RuntimeError('fix_thai_spacing คืนค่าว่างจากคำตอบ %r' % answer[:80])
    return fixed


class Command(BaseCommand):
    help = ('smoke test บอท LINE ก่อน restart: LINE get_bot_info + LLM ทุกชั้น (RunningHub หลัก/สำรอง, '
            'Anthropic) ด้วย answer_question + fix_thai_spacing — ไม่ส่งเข้า LINE ไม่เขียน DB · '
            'exit 1 เมื่อ LINE ไม่ผ่าน หรือไม่มีชั้น LLM ไหนผ่าน (--strict = ชั้นใดไม่ผ่านก็ exit 1)')

    def add_arguments(self, parser):
        parser.add_argument('--strict', action='store_true',
                            help='ชั้น LLM ใดไม่ผ่าน (รวมยังไม่ตั้ง key) → exit 1')

    def _ok(self, name, detail):
        self.stdout.write(self.style.SUCCESS('✓ %s: %s' % (name, str(detail)[:120].replace('\n', ' | '))))

    def _fail(self, name, exc):
        self.stderr.write(self.style.ERROR('✗ %s: %s: %s' % (name, type(exc).__name__, exc)))

    def handle(self, *args, **options):
        failed = []

        try:
            self._ok('LINE get_bot_info', check_line_bot_info())
        except Exception as exc:  # noqa: BLE001 — รายงานทุกข้อแล้วค่อยสรุป
            failed.append('LINE get_bot_info')
            self._fail('LINE get_bot_info', exc)

        tiers = expected_tiers()
        tier_passed = []
        tier_failed = []
        for name, tier, reason in tiers:
            if tier is None:
                tier_failed.append(name)
                self.stderr.write(self.style.WARNING('✗ %s: ยังไม่ตั้งค่า — %s' % (name, reason)))
                continue
            try:
                self._ok(name, check_llm_tier(tier))
                tier_passed.append(name)
            except Exception as exc:  # noqa: BLE001
                tier_failed.append(name)
                self._fail(name, exc)

        self.stdout.write('LLM ผ่าน %d/%d ชั้น' % (len(tier_passed), len(tiers)))
        down = [services.tier_label(t) for t in services.llm_tiers() if services.tier_down(t)]
        if down:
            self.stdout.write(self.style.WARNING('หมายเหตุ: ชั้นที่ cache จำว่าล่ม (ข้ามอัตโนมัติ 5 นาที): %s'
                                                 % ', '.join(down)))

        if not tier_passed:
            failed.append('LLM ทุกชั้น')
        elif options['strict'] and tier_failed:
            failed.extend(tier_failed)

        if failed:
            raise CommandError('linebot_smoke ไม่ผ่าน: %s — ห้าม restart' % ', '.join(failed))
        self.stdout.write(self.style.SUCCESS('linebot_smoke ผ่าน (LINE + LLM %d/%d ชั้น)'
                                             % (len(tier_passed), len(tiers))))
