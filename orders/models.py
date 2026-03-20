from django.db import models
from django.utils import timezone


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
    status = models.CharField('สถานะ', max_length=20, choices=STATUS_CHOICES, default='รอดำเนินการ')

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
    SHIRT_TYPE_CHOICES = [
        ('แขนสั้น', 'แขนสั้น'),
        ('แขนยาว', 'แขนยาว'),
        ('โปโล', 'โปโล'),
        ('อื่นๆ', 'อื่นๆ'),
    ]

    DEFAULT_SIZES = ['S', 'M', 'L', 'XL', '2XL', '3XL', '4XL']

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    design_image = models.ImageField('รูปดีไซน์', upload_to='designs/', blank=True)
    shirt_type = models.CharField('ประเภทเสื้อ', max_length=20, choices=SHIRT_TYPE_CHOICES, default='แขนสั้น')
    color = models.CharField('สีเสื้อ', max_length=50)
    sizes = models.JSONField('ไซส์และจำนวน', default=list, blank=True,
                             help_text='[{"label": "S", "qty": 5}, {"label": "M", "qty": 10}, ...]')

    class Meta:
        verbose_name = 'รายการเสื้อ'
        verbose_name_plural = 'รายการเสื้อ'

    def __str__(self):
        return f'{self.shirt_type} {self.color}'

    @property
    def total_qty(self):
        if not self.sizes:
            return 0
        return sum(s.get('qty', 0) for s in self.sizes if s.get('qty'))

    @property
    def sizes_display(self):
        """Return only sizes with qty > 0."""
        if not self.sizes:
            return []
        return [s for s in self.sizes if s.get('qty', 0) > 0]
