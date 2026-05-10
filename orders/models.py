from django.db import models
from django.utils import timezone


def design_upload_path(instance, filename):
    """Upload to: designs/YYYY/MM/filename"""
    now = timezone.now()
    return f'designs/{now.year}/{now.month:02d}/{filename}'


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

    order_number = models.CharField('เลขออร์เดอร์', max_length=20, unique=True, editable=False)
    created_date = models.DateField('วันที่สร้าง', default=timezone.now)
    print_date = models.DateField('วันที่พิมพ์เสื้อ', null=True, blank=True)
    source = models.CharField('แหล่งที่มา', max_length=50, choices=SOURCE_CHOICES)
    customer_name = models.CharField('ชื่อลูกค้า', max_length=200)
    customer_link = models.CharField('Facebook/เบอร์โทร', max_length=500, blank=True)
    shirt_name = models.CharField('ชื่องาน/ชื่อเสื้อ', max_length=200)
    fabric_spec = models.TextField('spec ผ้า', blank=True, help_text='แสดงเฉพาะ source=เพจเสื้อคนงาน')
    special_note = models.TextField('คำสั่งพิเศษ', blank=True)
    total_price = models.DecimalField('ยอดรวม', max_digits=10, decimal_places=2, default=0)
    deposit = models.DecimalField('มัดจำ', max_digits=10, decimal_places=2, default=0)
    delivery_method = models.CharField('วิธีรับสินค้า', max_length=20, choices=DELIVERY_CHOICES, default='รับเอง')
    shipping_address = models.TextField('ที่อยู่จัดส่ง', blank=True)
    status = models.CharField('สถานะ', max_length=20, choices=STATUS_CHOICES, default='รอดำเนินการ')

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

    def save(self, *args, **kwargs):
        if not self.order_number:
            self.order_number = self._generate_order_number()
        super().save(*args, **kwargs)

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


class ShirtVariant(models.Model):
    """แบบ — 1 OrderItem มีได้หลายแบบ (คอ/แขน/สี/ไซส์ต่างกันบนรูปเดียว)."""

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
