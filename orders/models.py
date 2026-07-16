import hashlib
import os
import uuid

from django.db import models
from django.utils import timezone
from django.utils.crypto import constant_time_compare


def design_upload_path(instance, filename):
    """Generate a short, deterministic-ish upload path so users can upload
    images with arbitrarily long original filenames without tripping the
    100-char FileField validation.

    Result: designs/YYYY/MM/<order_number>_<8-char-uuid><ext>
    e.g.    designs/2026/05/6905-274_a3f8b2c1.png

    Falls back to 'tmp' for the order_number segment if the OrderItem
    isn't attached to a saved Order yet (shouldn't happen via the normal
    formset flow, but defensive). Existing files keep their old paths —
    only newly uploaded files are renamed.
    """
    ext = (os.path.splitext(filename)[1] or '.jpg').lower()
    order = getattr(instance, 'order', None)
    order_num = getattr(order, 'order_number', None) or 'tmp'
    unique = uuid.uuid4().hex[:8]
    now = timezone.now()
    return f'designs/{now.year}/{now.month:02d}/{order_num}_{unique}{ext}'


def master_upload_path(instance, filename):
    """Upload path for รูปมาสเตอร์: masters/YYYY/MM/<order_number>_<8-char-uuid><ext>.
    Mirrors design_upload_path so long original filenames never trip the
    100-char FileField limit."""
    ext = (os.path.splitext(filename)[1] or '.jpg').lower()
    order = getattr(instance, 'order', None)
    order_num = getattr(order, 'order_number', None) or 'tmp'
    unique = uuid.uuid4().hex[:8]
    now = timezone.now()
    return f'masters/{now.year}/{now.month:02d}/{order_num}_{unique}{ext}'


def extra_upload_path(instance, filename):
    """Upload path for รูปเพิ่มเติม: extras/YYYY/MM/<order_number>_<8-char-uuid><ext>.
    Mirrors master_upload_path so long original filenames never trip the
    100-char FileField limit."""
    ext = (os.path.splitext(filename)[1] or '.jpg').lower()
    order = getattr(instance, 'order', None)
    order_num = getattr(order, 'order_number', None) or 'tmp'
    unique = uuid.uuid4().hex[:8]
    now = timezone.now()
    return f'extras/{now.year}/{now.month:02d}/{order_num}_{unique}{ext}'


def signed_upload_path(instance, filename):
    """Upload path for รูปที่เซ็นแล้ว: signed/YYYY/MM/<order_number>_<8-char-uuid><ext>.
    instance is the Order itself (signed_image lives on Order, 1 per order)."""
    ext = (os.path.splitext(filename)[1] or '.jpg').lower()
    order_num = getattr(instance, 'order_number', None) or 'tmp'
    unique = uuid.uuid4().hex[:8]
    now = timezone.now()
    return f'signed/{now.year}/{now.month:02d}/{order_num}_{unique}{ext}'


def downscale_image_field(field, max_side=1600, quality=85):
    """Shrink an oversized ImageField file in place: cap the long edge at max_side
    and re-encode at ~quality. Local-filesystem storage only — silently skips when
    the file is missing or the storage backend has no local path, so it never
    blocks a save. Shared by MasterImage.image and Order.signed_image."""
    if not field:
        return
    try:
        path = field.path
    except (ValueError, NotImplementedError):
        return
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return
    try:
        img = Image.open(path)
    except (FileNotFoundError, OSError):
        return
    if max(img.size) <= max_side:
        return
    fmt = (img.format or 'JPEG').upper()
    img = ImageOps.exif_transpose(img)
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    save_kwargs = {}
    if fmt in ('JPEG', 'JPG'):
        if img.mode in ('RGBA', 'P', 'LA'):
            img = img.convert('RGB')
        save_kwargs = {'quality': quality, 'optimize': True}
    elif fmt == 'PNG':
        save_kwargs = {'optimize': True}
    try:
        img.save(path, format='JPEG' if fmt == 'JPG' else fmt, **save_kwargs)
    except (OSError, KeyError):
        img.save(path)


class Customer(models.Model):
    """โปรไฟล์ลูกค้า — ฐานลูกค้าของร้าน (1 คน = 1 โปรไฟล์).

    Orders ที่สร้างใหม่จะผูกมาที่นี่ผ่าน Order.customer; ใบเก่าก่อนมี model นี้
    มีแค่ customer_name/customer_link (free text) และไม่ถูกผูก — ปล่อยไว้
    (ผูกมือทีหลังได้จากหน้าโปรไฟล์)."""
    name = models.CharField('ชื่อลูกค้า', max_length=200)
    facebook_link = models.CharField('Facebook/ลิงก์', max_length=500, blank=True)
    phone = models.CharField('เบอร์โทร', max_length=50, blank=True)
    note = models.TextField('โน้ต', blank=True)
    created_at = models.DateTimeField('สร้างเมื่อ', auto_now_add=True)
    updated_at = models.DateTimeField('แก้ไขล่าสุด', auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'ลูกค้า'
        verbose_name_plural = 'ลูกค้า'

    def __str__(self):
        return self.name


class CustomerPrice(models.Model):
    """ราคาประจำตัวลูกค้า — หลายรายการต่อคน (เช่น "คอกลมแขนสั้น" 120 / "โปโล" 185).
    ใช้เป็นตัวช่วยคำนวณยอดรวมในฟอร์มใบงาน (จำนวนตัว × ราคา) — ไม่บังคับ."""
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='prices')
    label = models.CharField('รายการ', max_length=100)
    price = models.DecimalField('ราคา/ตัว', max_digits=10, decimal_places=2)
    order_index = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order_index', 'id']
        verbose_name = 'ราคาลูกค้า'
        verbose_name_plural = 'ราคาลูกค้า'

    def __str__(self):
        return f'{self.customer.name} - {self.label} {self.price}'


class Order(models.Model):
    SOURCE_CHOICES = [
        ('เพจเสื้อเนินสูง', 'เพจเสื้อเนินสูง'),
        ('เพจเสื้อคนงาน', 'เพจเสื้อคนงาน'),
        ('เฮีย&เจ๊', 'เฮีย&เจ๊'),
        ('หน้าร้าน', 'หน้าร้าน'),
        ('เพจปักผ้า', 'เพจปักผ้า'),
        ('LINE OA', 'LINE OA'),
        ('เพจร้าน Yada', 'เพจร้าน Yada'),
        ('เพจเสื้อทุเรียน', 'เพจเสื้อทุเรียน'),
        ('Shopee', 'Shopee'),
        ('Tiktok', 'Tiktok'),
    ]

    STATUS_CHOICES = [
        ('รอดำเนินการ', 'รอดำเนินการ'),
        ('กำลังผลิต', 'กำลังผลิต'),
        ('เสร็จแล้ว', 'เสร็จแล้ว'),
        ('ส่งแล้ว', 'ส่งแล้ว'),
    ]

    DELIVERY_CHOICES = [
        ('รับเอง', 'รับเอง'),
        ('ส่ง', 'ส่ง'),
    ]

    PRODUCTION_CHOICES = [
        ('ผลิตเอง', 'ผลิตเอง'),
        ('ร้านแอม', 'ร้านแอม'),
        ('ร้านแบ้งค์', 'ร้านแบ้งค์'),
        ('ร้านตูน', 'ร้านตูน'),
    ]

    order_number = models.CharField('เลขออร์เดอร์', max_length=20, unique=True, editable=False)
    created_date = models.DateField('วันที่สร้าง', default=timezone.now)
    print_date = models.DateField('วันที่พิมพ์เสื้อ', null=True, blank=True)
    source = models.CharField('แหล่งที่มา', max_length=50, choices=SOURCE_CHOICES)
    production_place = models.CharField('ผลิตที่', max_length=20, choices=PRODUCTION_CHOICES, default='ผลิตเอง')
    customer_name = models.CharField('ชื่อลูกค้า', max_length=200)
    customer_link = models.CharField('Facebook/เบอร์โทร', max_length=500, blank=True)
    # โปรไฟล์ลูกค้า (เฟส 1 CRM) — nullable: ใบเก่าไม่ถูกผูก, ใบใหม่ผูกอัตโนมัติ
    # ตอน save (เลือกจาก autocomplete หรือ match/สร้างจากชื่อ+ลิงก์).
    # customer_name/customer_link ยังเป็น source of truth ของ "ข้อความบนใบงาน" เสมอ.
    customer = models.ForeignKey('Customer', on_delete=models.SET_NULL,
                                 null=True, blank=True, related_name='orders',
                                 verbose_name='โปรไฟล์ลูกค้า')
    # เฟส 2: ใบงานชุดเดียวกัน — "ใบเพิ่ม" ชี้กลับใบแรกของชุด (root) เสมอ
    # (สร้างใบเพิ่มจากใบเพิ่ม → flatten ไปชี้ root เดิม ไม่ทำ chain ซ้อน).
    # ลบใบแรก → SET_NULL ใบเพิ่มที่เหลือกลายเป็นใบเดี่ยว/root ใหม่.
    parent_order = models.ForeignKey('self', on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name='child_orders',
                                     verbose_name='ใบเพิ่มจากใบ')
    shirt_name = models.CharField('ชื่องาน/ชื่อเสื้อ', max_length=200)
    # คนออกแบบ + เลขใบงานออกแบบ — คนละคนกับ created_by (คนคีย์ออร์เดอร์).
    # designer_name = กราฟิกที่ทำดีไซน์ (มีหลายคน), design_doc_number = อ้างอิงงานออกแบบ.
    designer_name = models.CharField('คนออกแบบ', max_length=100, blank=True)
    design_doc_number = models.CharField('เลขใบงานออกแบบ', max_length=50, blank=True)
    # เฟส 3: id ของ Job ฝั่งระบบ Brief (dr89.cloud/brief) ที่ใบงานออกแบบนี้ชี้ถึง
    # — เซ็ตเมื่อเลือกจาก autocomplete (design_doc_number เก็บ "D-xxx" เป็นข้อความ
    # เหมือนเดิม; id นี้ไว้ทำลิงก์เปิดใบงาน + ยิง order_ref กลับ). พิมพ์เลขเองไม่เลือก
    # จาก dropdown = null (ไม่มีลิงก์ แต่ข้อความยังอยู่ครบ)
    brief_job_id = models.IntegerField('Brief job id', null=True, blank=True)
    fabric_spec = models.TextField('spec ผ้า', blank=True, help_text='แสดงเฉพาะ source=เพจเสื้อคนงาน')
    special_note = models.TextField('คำสั่งพิเศษ', blank=True)
    total_price = models.DecimalField('ยอดรวม', max_digits=10, decimal_places=2, default=0)
    deposit = models.DecimalField('มัดจำ', max_digits=10, decimal_places=2, default=0)
    delivery_method = models.CharField('วิธีรับสินค้า', max_length=20, choices=DELIVERY_CHOICES, default='รับเอง')
    shipping_address = models.TextField('ที่อยู่จัดส่ง', blank=True)
    status = models.CharField('สถานะ', max_length=20, choices=STATUS_CHOICES, default='รอดำเนินการ')

    # Production-urgent flag: highlights the order red across list/detail/form.
    is_urgent = models.BooleanField('ด่วน!', default=False)

    # Waiting-for-customer-confirm flag: fades print/pick pages with a big
    # "รอคอนเฟิร์ม" overlay so nobody prints/produces an unconfirmed order.
    waiting_confirm = models.BooleanField('รอคอนเฟิร์ม', default=False)

    # Phase 1.6: stage timestamps (null = ยังไม่เสร็จ stage นั้น)
    print_done_at = models.DateTimeField('พิมพ์เสร็จเมื่อ', null=True, blank=True)
    roll_done_at = models.DateTimeField('โรลเสร็จเมื่อ', null=True, blank=True)
    cut_done_at = models.DateTimeField('ตัดเสร็จเมื่อ', null=True, blank=True)
    sort_done_at = models.DateTimeField('คัดเสร็จเมื่อ', null=True, blank=True)
    sent_to_tailors_at = models.DateTimeField('ส่งเย็บเมื่อ', null=True, blank=True)
    packed_at = models.DateTimeField('รีดแพ็คเสร็จเมื่อ', null=True, blank=True)
    shipped_at = models.DateTimeField('ส่งของเมื่อ', null=True, blank=True)
    awaiting_pickup_at = models.DateTimeField('รอมารับเมื่อ', null=True, blank=True)

    # Phase 1.6: repair flag
    needs_repair = models.BooleanField('ต้องซ่อม', default=False)
    repair_done_at = models.DateTimeField('ซ่อมเสร็จล่าสุดเมื่อ', null=True, blank=True)

    # Phase 1.6: tailors (M2M เพราะ 1 order ส่งหลายเจ้าได้)
    tailors = models.ManyToManyField('Tailor', blank=True, related_name='orders', verbose_name='คนเย็บ')

    # Audit: when/who created + last edit. nullable so legacy orders (created
    # before this field existed) read as null → shown as "-".
    created_at = models.DateTimeField('สร้างเมื่อ', auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField('แก้ไขล่าสุด', auto_now=True, null=True, blank=True)
    created_by = models.ForeignKey('auth.User', on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='created_orders',
                                   verbose_name='คนลงข้อมูล')

    # Work-order printed flag (null = ยังไม่พิมพ์). Backfilled = created_date
    # for legacy orders so they don't show "ยังไม่พิมพ์ใบงาน".
    printed_at = models.DateTimeField('พิมพ์ใบงานเมื่อ', null=True, blank=True)

    # Signed copy: photo of the master sheet after every dept reviewed + signed.
    # 1 per order (all signatures on one sheet). nullable — legacy orders have
    # none. Auto-downscaled on save (phone photos). See save() below.
    signed_image = models.ImageField('รูปที่เซ็นแล้ว', upload_to=signed_upload_path,
                                     null=True, blank=True)

    # "เพิ่มเติม" block (1 per order): free-text note + ExtraImage(s) + ExtraNameRow(s).
    # Shown below the shirt items on the form and the print sheet.
    extra_note = models.TextField('โน้ตเพิ่มเติม', blank=True)

    class Meta:
        ordering = ['-id']
        verbose_name = 'ออร์เดอร์'
        verbose_name_plural = 'ออร์เดอร์'

    def __str__(self):
        return f'{self.order_number} - {self.customer_name}'

    @property
    def remaining(self):
        return self.total_price - self.deposit

    @property
    def total_qty(self):
        return sum(item.total_qty for item in self.items.all())

    def group_orders(self):
        """ทุกใบในชุดเดียวกัน (root ก่อน แล้วใบเพิ่มเรียงเก่า→ใหม่).
        คืน [] เมื่อเป็นใบเดี่ยว (ไม่มี parent และไม่มีใบเพิ่ม) — ให้ template
        gate แบนเนอร์ชุดได้ด้วย if เดียว."""
        root = self.parent_order or self
        children = list(root.child_orders.order_by('id'))
        if not children:
            return []
        return [root] + children

    @property
    def not_printed(self):
        """ยังไม่พิมพ์ใบงาน: the work-order sheet hasn't been printed yet."""
        return self.printed_at is None

    @property
    def recently_edited(self):
        """แก้ใบงาน: actually edited (>1 min after create) within the last 24h."""
        from datetime import timedelta
        if not self.updated_at or not self.created_at:
            return False
        was_edited = (self.updated_at - self.created_at) > timedelta(minutes=1)
        within_24h = (timezone.now() - self.updated_at) < timedelta(hours=24)
        return was_edited and within_24h

    @property
    def created_time_display(self):
        """HH:MM (Asia/Bangkok) of created_at; None when 00:00 (legacy backfilled
        orders have midnight, so they hide). created_at is stored UTC → localtime()."""
        if not self.created_at:
            return None
        local = timezone.localtime(self.created_at)
        if local.hour == 0 and local.minute == 0:
            return None
        return local.strftime('%H:%M')

    @property
    def progress_label(self):
        """ป้ายความคืบหน้า = stage ที่เสร็จล่าสุด (เช่น "พิมพ์แล้ว"). คืน "ยังไม่เริ่ม"
        ถ้ายังไม่มี stage ใดเสร็จ. ใช้ในการ์ดสรุปรายวัน."""
        if self.shipped_at:
            return 'ส่งแล้ว'
        if self.awaiting_pickup_at:
            return 'รอลูกค้ารับ'
        if self.packed_at:
            return 'รีด+แพ็คแล้ว'
        if self.sent_to_tailors_at:
            return 'ส่งเย็บแล้ว'
        if self.sort_done_at:
            return 'คัดแล้ว'
        if self.cut_done_at:
            return 'ตัดแล้ว'
        if self.roll_done_at:
            return 'โรลแล้ว'
        if self.print_done_at:
            return 'พิมพ์แล้ว'
        return 'ยังไม่เริ่ม'

    @property
    def progress_done(self):
        """งานออกจากร้านแล้ว (ส่ง/รอลูกค้ารับ) → ใช้ทำ badge สีเขียว."""
        return bool(self.shipped_at or self.awaiting_pickup_at)

    def save(self, *args, **kwargs):
        if not self.order_number:
            self.order_number = self._generate_order_number()
        super().save(*args, **kwargs)
        # Auto-downscale the signed photo (no-op when empty or already ≤1600px,
        # so the common save path — and stage updates — pays ~nothing).
        downscale_image_field(self.signed_image)

    def _generate_order_number(self):
        today = self.created_date or timezone.now().date()
        thai_year = today.year + 543
        prefix = f'{thai_year % 100:02d}{today.month:02d}'

        last_order = (
            Order.objects
            .filter(order_number__startswith=f'{prefix}-')
            .order_by('-id')
            .first()
        )
        if last_order:
            last_num = int(last_order.order_number.split('-')[1])
            next_num = last_num + 1
        else:
            next_num = 1

        return f'{prefix}-{next_num}'


class OrderItem(models.Model):
    """รายการเสื้อ — 1 รูปดีไซน์ มีได้หลาย "แบบ" (ShirtVariant)."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    design_image = models.ImageField('รูปดีไซน์', upload_to=design_upload_path, blank=True)
    order_index = models.PositiveIntegerField('ลำดับ', default=0)

    class Meta:
        ordering = ['order_index', 'id']
        verbose_name = 'รายการเสื้อ'
        verbose_name_plural = 'รายการเสื้อ'

    def __str__(self):
        return f'Item #{self.pk} (order {self.order_id})'

    @property
    def total_qty(self):
        return sum(v.total_qty for v in self.variants.all())


class MasterImage(models.Model):
    """รูปมาสเตอร์ — 1 Order มีได้หลายรูป. ใช้พิมพ์ "ใบมาสเตอร์" ให้ทีมเซ็นตรวจ
    (กราฟิก/วางพิมพ์/เลเซอร์/คนคัด). แยกจาก OrderItem.design_image โดยสิ้นเชิง."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='master_images')
    image = models.ImageField('รูปมาสเตอร์', upload_to=master_upload_path)
    order_index = models.PositiveIntegerField('ลำดับ', default=0)

    class Meta:
        ordering = ['order_index', 'id']
        verbose_name = 'รูปมาสเตอร์'
        verbose_name_plural = 'รูปมาสเตอร์'

    def __str__(self):
        return f'Master #{self.pk} (order {self.order_id})'

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        downscale_image_field(self.image)


class ExtraImage(models.Model):
    """รูปเพิ่มเติม — 1 Order มีได้หลายรูป. แนบในบล็อก "เพิ่มเติม" ท้ายรายการเสื้อ
    (แสดงในฟอร์ม + ใบ print). เลียนแบบ MasterImage ทุกอย่าง."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='extra_images')
    image = models.ImageField('รูปเพิ่มเติม', upload_to=extra_upload_path)
    order_index = models.PositiveIntegerField('ลำดับ', default=0)

    class Meta:
        ordering = ['order_index', 'id']
        verbose_name = 'รูปเพิ่มเติม'
        verbose_name_plural = 'รูปเพิ่มเติม'

    def __str__(self):
        return f'Extra #{self.pk} (order {self.order_id})'

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        downscale_image_field(self.image)


class ExtraNameRow(models.Model):
    """แถวในตารางรันชื่อ-เบอร์ของช่องเพิ่มเติม. export เป็น CSV เข้าโปรแกรม nesting ได้."""
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='extra_name_rows')
    size = models.CharField('ไซส์', max_length=20, blank=True)
    number = models.CharField('เบอร์', max_length=20, blank=True)
    name = models.CharField('ชื่อ', max_length=100, blank=True)
    order_index = models.PositiveIntegerField('ลำดับ', default=0)

    class Meta:
        ordering = ['order_index', 'id']
        verbose_name = 'แถวรันชื่อ'
        verbose_name_plural = 'แถวรันชื่อ'

    def __str__(self):
        return f'NameRow #{self.pk} (order {self.order_id})'


class ShirtVariant(models.Model):
    """แบบ — 1 OrderItem มีได้หลายแบบ (คอ/แขน/สี/ไซส์ต่างกันบนรูปเดียว)."""

    # Display order for standard adult size labels. Custom labels (เด็ก S,
    # เด็ก 24, etc.) fall through to a large sentinel so they sort to the end
    # while keeping their insertion order (Python's sorted() is stable).
    STANDARD_SIZE_ORDER = {
        'XS': 1, 'SS': 2, 'S': 3, 'M': 4, 'L': 5, 'XL': 6,
        '2XL': 7, '3XL': 8, '4XL': 9, '5XL': 10, '6XL': 11, '7XL': 12,
    }

    item = models.ForeignKey(
        OrderItem, on_delete=models.CASCADE, related_name='variants',
        verbose_name='รายการเสื้อ',
    )
    collar = models.CharField('คอ', max_length=50, blank=True,
                              help_text='เช่น คอกลม, คอวี, คอเรือ')
    sleeve = models.CharField('แขน', max_length=50, blank=True,
                              help_text='เช่น แขนสั้น, แขนยาว, แขนกุด')
    color = models.CharField('สี', max_length=50, blank=True)
    sizes = models.JSONField('ไซส์และจำนวน', default=list,
                             help_text='[{"label":"S","qty":5}, ...]')
    note = models.CharField('โน้ตเฉพาะแบบนี้', max_length=200, blank=True)
    order_index = models.PositiveIntegerField('ลำดับ', default=0)

    class Meta:
        ordering = ['order_index', 'id']
        verbose_name = 'แบบ'
        verbose_name_plural = 'แบบ'

    def __str__(self):
        parts = [self.collar, self.sleeve, self.color]
        return ' / '.join(p for p in parts if p) or f'Variant #{self.pk}'

    @property
    def total_qty(self):
        return sum(s.get('qty', 0) for s in self.sizes if isinstance(s, dict))

    @property
    def sizes_sorted(self):
        """Display-only ordering: standard adult sizes first in chart order,
        then any custom labels (เด็ก, etc.) in their original insertion order.
        Does not mutate self.sizes — pure derived view of the JSON list.
        """
        sizes = self.sizes or []
        def _key(item):
            label = (item.get('label') or '').strip().upper()
            return self.STANDARD_SIZE_ORDER.get(label, 999)
        return sorted(sizes, key=_key)


class Tailor(models.Model):
    name = models.CharField('ชื่อ', max_length=100)
    phone = models.CharField('เบอร์โทร', max_length=20, blank=True)
    is_active = models.BooleanField('ใช้งาน', default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'คนเย็บ'
        verbose_name_plural = 'คนเย็บ'

    def __str__(self):
        return self.name


class DepartmentPIN(models.Model):
    """Singleton — the 4-digit PIN that gates department cookie setup.

    Admin can change the PIN from /admin/. The decorator stores a sha256
    of the current PIN in a cookie alongside the dept slug; when the PIN
    changes, every existing cookie becomes invalid and every floor device
    has to enter the new PIN on its next request.
    """
    pin = models.CharField('PIN 4 หลัก', max_length=4)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'PIN แผนก'
        verbose_name_plural = 'PIN แผนก'

    def __str__(self):
        return f'PIN (updated {self.updated_at:%Y-%m-%d %H:%M})'

    @classmethod
    def _row(cls):
        return cls.objects.first()

    @classmethod
    def current_hash(cls):
        row = cls._row()
        if not row or not row.pin:
            return ''
        return hashlib.sha256(row.pin.encode('utf-8')).hexdigest()

    @classmethod
    def verify(cls, candidate):
        row = cls._row()
        if not row or not row.pin or not candidate:
            return False
        return constant_time_compare(str(candidate), row.pin)


class StageLog(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='stage_logs')
    department = models.CharField('แผนก', max_length=20)
    action = models.CharField('การกระทำ', max_length=30)
    note = models.TextField('หมายเหตุ', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'log สถานะ'
        verbose_name_plural = 'log สถานะ'

    def __str__(self):
        return f'{self.order.order_number} · {self.department} · {self.action}'
