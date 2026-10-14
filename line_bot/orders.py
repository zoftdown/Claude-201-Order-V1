"""
orders — ฝั่งข้อมูลออร์เดอร์ของบอท: ค้นใบงาน / ติ๊กขั้น "รีด+แพ็ค" / จัดข้อความสรุป

ติ๊กขั้นผ่าน `orders.views.mark_pack_done` ตัวเดียวกับปุ่ม "รีดแพ็คแล้ว" ของแผนกแพ็ค
(เซ็ต `Order.packed_at` + `StageLog(department='pack', action='pack_done')`) — หน้า detail
แสดงแถบความคืบหน้าจาก timestamp เหล่านี้ จึงเห็นว่าบอททำอะไร. ไม่แตะ `Order.status`.
"""
import logging

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


def format_done_summary(order):
    return '\n'.join([
        '✅ %s %s' % (order.order_number, order.shirt_name or ''),
        'ลูกค้า: %s' % (order.customer_name or '-'),
        'รวม %d ตัว' % order.total_qty,
        'ยอด %s มัดจำ %s ค้าง %s' % (
            _money(order.total_price), _money(order.deposit), _money(order.remaining)),
        order.delivery_method or '',
    ]).rstrip()


def process_order_number(order_number, note=''):
    """เลขที่อ่านได้จากรูป → ข้อความตอบกลับ (ไม่มี prefix mention)
    - ไม่พบ → "อ่านได้เลข {n} แต่ไม่พบในระบบ"
    - ติ๊กรีด+แพ็คไว้แล้ว → "{n} รีด+แพ็คแล้วอยู่แล้ว"
    - อื่น → ติ๊กขั้นรีด+แพ็ค + สรุปใบ"""
    order = find_order(order_number)
    if order is None:
        logger.info('อ่านได้เลข %s แต่ไม่พบในระบบ', order_number)
        return 'อ่านได้เลข %s แต่ไม่พบในระบบ' % order_number
    if is_pack_done(order):
        return '%s รีด+แพ็คแล้วอยู่แล้ว' % order.order_number
    mark_done(order, note=note)
    return format_done_summary(order)
