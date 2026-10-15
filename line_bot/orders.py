"""
orders — ฝั่งข้อมูลออร์เดอร์ของบอท: ค้นใบงาน / ติ๊กขั้น "รีด+แพ็ค" / จัดข้อความสรุป

ติ๊กขั้นผ่าน `orders.views.mark_pack_done` ตัวเดียวกับปุ่ม "รีดแพ็คแล้ว" ของแผนกแพ็ค
(เซ็ต `Order.packed_at` + `StageLog(department='pack', action='pack_done')`) — หน้า detail
แสดงแถบความคืบหน้าจาก timestamp เหล่านี้ จึงเห็นว่าบอททำอะไร. ไม่แตะ `Order.status`.
"""
import logging

from django.conf import settings
from django.db import transaction

from orders.models import Order
from orders.views import mark_pack_done

logger = logging.getLogger(__name__)

BOT_NOTE = 'บอท LINE อ่านใบงานจากรูป'


def find_order(order_number):
    return (Order.objects.filter(order_number=order_number)
            .prefetch_related('items__variants').first())


def is_pack_done(order):
    """ขั้น "รีด+แพ็ค" ติ๊กแล้วหรือยัง — ใช้ packed_at ตัวเดียวกับที่ปุ่มเว็บ/timeline ใช้
    (ใบเก่าที่ backfill มี packed_at แต่ไม่มีแถว StageLog จึงเช็คจาก StageLog อย่างเดียวไม่ได้)"""
    return order.packed_at is not None


def mark_done(order, note=''):
    """ติ๊กขั้น "รีด+แพ็ค" ด้วยกลไกเดียวกับหน้าเว็บ (packed_at + StageLog pack_done) พร้อม
    หมายเหตุว่ามาจากบอท LINE. คืน False ถ้าติ๊กไว้แล้ว."""
    with transaction.atomic():
        ok = mark_pack_done(order, note=note or BOT_NOTE)
    if ok:
        logger.info('ออร์เดอร์ %s → ติ๊ก "รีด+แพ็ค" (ผ่านบอท LINE)', order.order_number)
    return ok


def _money(value):
    """แสดงเงินแบบ 1,500 หรือ 1,500.50 (ตัด .00)"""
    value = value or 0
    if value == int(value):
        return '{:,}'.format(int(value))
    return '{:,.2f}'.format(value)


def order_url(order):
    """ลิงก์หน้า detail ของใบงานบนเว็บ (settings.ORDER_PUBLIC_BASE + /<pk>/) — ส่งเต็มเส้น LINE ทำเป็นลิงก์กดได้เอง"""
    base = (getattr(settings, 'ORDER_PUBLIC_BASE', '') or '').rstrip('/')
    return '%s/%d/' % (base, order.pk)


def format_done_summary(order, suffix=''):
    """ข้อความสรุปต่อใบ (ใช้ร่วมทุก flow: ครบ/เสร็จ · บอท เสร็จ · ติ๊กขั้น · ปิดงาน):
    ✅ 6910-171 ช่างเอกรีโนเวท[ suffix เช่น "→ พิมพ์แล้ว"]
    ลูกค้า: บ่าว เอก · 19 ตัว · รับเอง
    ค้างชำระ 1,110 บาท   (0 → ชำระครบแล้ว)
    https://dr89.cloud/order/2521/   (ลิงก์เต็ม บรรทัดของตัวเอง)"""
    first = ('✅ %s %s' % (order.order_number, order.shirt_name or '')).rstrip()
    if suffix:
        first = '%s %s' % (first, suffix)
    balance = order.remaining or 0
    paid = 'ชำระครบแล้ว' if balance <= 0 else 'ค้างชำระ %s บาท' % _money(balance)
    return '\n'.join([
        first,
        'ลูกค้า: %s · %d ตัว · %s' % (order.customer_name or '-', order.total_qty, order.delivery_method or '-'),
        paid,
        order_url(order),
    ])


def process_order_number(order_number, note=''):
    """เลขออร์เดอร์ 1 ใบ → ข้อความสรุปของใบนั้น (ไม่มี prefix mention)
    - ไม่พบ → "อ่านได้เลข {n} แต่ไม่พบในระบบ"
    - ติ๊กรีด+แพ็คไว้แล้ว → "{n} ติ๊กแล้วอยู่แล้ว (รีด+แพ็ค)"
    - อื่น → ติ๊กขั้นรีด+แพ็ค + สรุปใบ"""
    order = find_order(order_number)
    if order is None:
        logger.info('อ่านได้เลข %s แต่ไม่พบในระบบ', order_number)
        return 'อ่านได้เลข %s แต่ไม่พบในระบบ' % order_number
    if is_pack_done(order):
        return '%s ติ๊กแล้วอยู่แล้ว (รีด+แพ็ค)' % order.order_number
    mark_done(order, note=note)
    return format_done_summary(order)


SEPARATOR = '──────────'


def process_order_numbers(order_numbers, note=''):
    """หลายใบ → ข้อความเดียว: สรุปต่อใบ format เดิม คั่นด้วยเส้น (เรียงตามลำดับที่ส่งมา)"""
    parts = [process_order_number(n, note=note) for n in order_numbers]
    return ('\n%s\n' % SEPARATOR).join(parts)
