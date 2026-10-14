"""
orders — ฝั่งข้อมูลออร์เดอร์ของบอท: ค้นใบงาน / เปลี่ยนสถานะเป็น "เสร็จแล้ว" / จัดข้อความสรุป

เปลี่ยนสถานะผ่าน StageLog (department='linebot') เหมือน dept dashboard
เพื่อให้ประวัติการเปลี่ยนสถานะครบ — ไม่มี endpoint/model ใหม่
"""
import logging

from django.db import transaction

from orders.models import Order, StageLog

logger = logging.getLogger(__name__)

DONE_STATUSES = ('เสร็จแล้ว', 'ส่งแล้ว')
LINEBOT_DEPARTMENT = 'linebot'
ACTION_MARK_DONE = 'status_done'


def find_order(order_number):
    return (Order.objects.filter(order_number=order_number)
            .prefetch_related('items__variants').first())


def mark_done(order, note=''):
    """เปลี่ยน status → เสร็จแล้ว + เขียน StageLog (กลไกเดิมของระบบ)"""
    with transaction.atomic():
        order.status = 'เสร็จแล้ว'
        order.save(update_fields=['status', 'updated_at'])
        StageLog.objects.create(
            order=order, department=LINEBOT_DEPARTMENT, action=ACTION_MARK_DONE,
            note=note or 'บอท LINE อ่านใบงานจากรูป',
        )
    logger.info('ออร์เดอร์ %s → เสร็จแล้ว (ผ่านบอท LINE)', order.order_number)


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
    - เสร็จ/ส่งแล้วอยู่แล้ว → "{n} เสร็จอยู่แล้ว"
    - อื่น → เปลี่ยนเป็นเสร็จแล้ว + สรุปใบ"""
    order = find_order(order_number)
    if order is None:
        logger.info('อ่านได้เลข %s แต่ไม่พบในระบบ', order_number)
        return 'อ่านได้เลข %s แต่ไม่พบในระบบ' % order_number
    if order.status in DONE_STATUSES:
        return '%s เสร็จอยู่แล้ว' % order.order_number
    mark_done(order, note=note)
    return format_done_summary(order)
