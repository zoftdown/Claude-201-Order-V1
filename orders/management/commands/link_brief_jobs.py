"""จับคู่ design_doc_number (text ที่พิมพ์ไว้ในใบเก่า) → brief_job_id (id ใบงานฝั่ง Brief)

หลักการจับคู่ — เฉพาะแบบมั่นใจเท่านั้น กำกวมให้ข้าม ไม่เดา:
  1. เลขตรงเป๊ะกับ job_number ฝั่ง Brief (ตัดช่องว่าง/ตัวพิมพ์เล็กใหญ่;
     พิมพ์เลขล้วน "123" ถือว่าตรงกับ "D-123")
  2. และ ชื่อลูกค้าตรง/ใกล้เคียง (normalize แล้วเท่ากัน หรือฝ่ายหนึ่งเป็น
     substring ของอีกฝ่าย ยาว >= 3 ตัวอักษร) — เลขตรงแต่ชื่อไม่เข้าเกณฑ์ = ข้าม (โชว์ในรายการ)

ปลอดภัยต่อข้อมูลเดิม: เขียนเฉพาะคอลัมน์ brief_job_id (ที่ยังเป็น NULL) —
ไม่แตะ design_doc_number เลย. rollback = เคลียร์ brief_job_id กลับเป็น NULL.

default = dry-run (โชว์รายการจับคู่ให้คนตรวจก่อน) — เขียนจริงต้องส่ง --confirm
(pattern เดียวกับ mark_legacy_orders_done). --confirm จะยิงเลขออร์เดอร์กลับไปเซ็ต
Job.order_ref ฝั่ง Brief ด้วย (เฉพาะใบที่ฝั่งโน้นยังว่าง, ใบแรกของชุดเป็นคนยิง)
"""
from django.core.management.base import BaseCommand, CommandError

from orders.models import Order
from orders.views import _brief_api_get, _push_order_ref_to_brief


def _norm_name(name):
    """normalize ชื่อลูกค้าไว้เทียบ: ตัดช่องว่างทั้งหมด + ตัวพิมพ์เล็ก"""
    return ''.join((name or '').split()).casefold()


def _names_similar(a, b):
    """ตรง/ใกล้เคียง = normalize แล้วเท่ากัน หรือเป็น substring กัน (ยาว >= 3)"""
    na, nb = _norm_name(a), _norm_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    shorter, longer = sorted((na, nb), key=len)
    return len(shorter) >= 3 and shorter in longer


class Command(BaseCommand):
    help = ('จับคู่เลขใบงานออกแบบ (text) ในใบเก่า → brief_job_id. '
            'default = dry-run, เขียนจริงใส่ --confirm')

    def add_arguments(self, parser):
        parser.add_argument('--confirm', action='store_true',
                            help='เขียน brief_job_id จริง (ไม่ใส่ = โชว์รายการเฉยๆ)')

    def handle(self, *args, **options):
        payload = _brief_api_get('/api/jobs/export/')
        if payload is None:
            raise CommandError(
                'ติดต่อระบบ Brief ไม่ได้ — เช็ค BRIEF_API_BASE/BRIEF_API_TOKEN ใน .env '
                'และว่า service brief_system รันอยู่')
        by_number = {}
        for j in payload.get('results', []):
            num = (j.get('job_number') or '').strip().upper()
            if num:
                by_number[num] = j

        candidates = (Order.objects
                      .filter(brief_job_id__isnull=True)
                      .exclude(design_doc_number='')
                      .order_by('created_date', 'id'))

        matched, name_mismatch, unmatched = [], [], []
        for o in candidates:
            text = o.design_doc_number.strip().upper()
            key = text if text in by_number else (
                f'D-{text}' if text.isdigit() and f'D-{text}' in by_number else None)
            job = by_number.get(key) if key else None
            if not job:
                unmatched.append(o)
            elif _names_similar(o.customer_name, job.get('customer_name')):
                matched.append((o, job))
            else:
                name_mismatch.append((o, job))

        w = self.stdout.write
        w(f'ใบงานฝั่ง Brief ทั้งหมด: {len(by_number)} ใบ · '
          f'ออร์เดอร์ที่มีเลขใบงานแต่ยังไม่ผูก: {candidates.count()} ใบ\n')

        w(self.style.SUCCESS(f'=== จับคู่ได้ (มั่นใจ): {len(matched)} คู่ ==='))
        for o, j in matched:
            w(f'  {o.order_number:<10} "{o.design_doc_number}" -> {j["job_number"]:<8} '
              f'| ลูกค้า Order: "{o.customer_name}" | Brief: "{j["customer_name"]}"')

        w(self.style.WARNING(
            f'=== เลขตรงแต่ชื่อลูกค้าไม่ตรง (ข้าม — ตรวจมือถ้าต้องการ): {len(name_mismatch)} ใบ ==='))
        for o, j in name_mismatch:
            w(f'  {o.order_number:<10} "{o.design_doc_number}" -> {j["job_number"]:<8} '
              f'| ลูกค้า Order: "{o.customer_name}" | Brief: "{j["customer_name"]}"')

        w(f'=== เลขไม่ตรงกับใบงานไหนเลย (ข้าม): {len(unmatched)} ใบ ===')
        for o in unmatched:
            w(f'  {o.order_number:<10} "{o.design_doc_number}" | ลูกค้า: "{o.customer_name}"')

        if not options['confirm']:
            w(self.style.NOTICE(
                '\nDRY-RUN — ยังไม่เขียนอะไรทั้งนั้น. ตรวจรายการข้างบนแล้วรันซ้ำด้วย --confirm'))
            return

        for o, j in matched:
            o.brief_job_id = j['id']
        Order.objects.bulk_update([o for o, _ in matched], ['brief_job_id'])
        # ยิง order_ref กลับฝั่ง Brief: เฉพาะใบงานที่ฝั่งโน้นยังว่าง และยิงครั้งเดียว
        # ต่อใบงาน (candidates เรียงวันเก่า→ใหม่ = ใบแรกของชุดเป็นเลขที่ถูกเซ็ต)
        pushed = set()
        for o, j in matched:
            if not j.get('order_ref') and j['id'] not in pushed:
                _push_order_ref_to_brief(o)
                pushed.add(j['id'])
        w(self.style.SUCCESS(
            f'\nเขียนแล้ว: brief_job_id {len(matched)} ใบ · '
            f'ยิง order_ref กลับ Brief {len(pushed)} ใบงาน '
            f'(design_doc_number ไม่ถูกแตะ)'))
