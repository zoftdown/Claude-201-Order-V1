# แยก Shopee เป็น 2 ร้าน: 'Shopee' เดิม = ร้านปักผ้า → rename เป็น 'Shopee - ปักผ้า'
# + เพิ่มร้านใหม่ 'Shopee - เสื้อPK' ใน choices
# ต้อง rename ข้อมูลเก่าทั้ง 3 ตารางที่ key ด้วย string นี้: Order.source /
# DailyAdSpend.page / DailySummary.page (rename ใน DailySummary = cache ประวัติยังใช้ได้ต่อ
# ไม่ต้องคำนวณใหม่ — คีย์เปลี่ยนแต่ตัวเลขเดิม)

from django.db import migrations, models


SOURCE_CHOICES = [
    ('เพจเสื้อเนินสูง', 'เพจเสื้อเนินสูง'),
    ('เพจเสื้อคนงาน', 'เพจเสื้อคนงาน'),
    ('เฮีย&เจ๊', 'เฮีย&เจ๊'),
    ('หน้าร้าน', 'หน้าร้าน'),
    ('เพจปักผ้า', 'เพจปักผ้า'),
    ('LINE OA', 'LINE OA'),
    ('เพจร้าน Yada', 'เพจร้าน Yada'),
    ('เพจเสื้อทุเรียน', 'เพจเสื้อทุเรียน'),
    ('เพจเสื้อช่างPK', 'เพจเสื้อช่างPK'),
    ('เพจเจ๊ปิ๋วเสื้อซิ่ง', 'เพจเจ๊ปิ๋วเสื้อซิ่ง'),
    ('Shopee - ปักผ้า', 'Shopee - ปักผ้า'),
    ('Shopee - เสื้อPK', 'Shopee - เสื้อPK'),
    ('Tiktok', 'Tiktok'),
]

OLD = 'Shopee'
NEW_MAIN = 'Shopee - ปักผ้า'
NEW_EXTRA = 'Shopee - เสื้อPK'


def rename_forward(apps, schema_editor):
    Order = apps.get_model('orders', 'Order')
    DailyAdSpend = apps.get_model('orders', 'DailyAdSpend')
    DailySummary = apps.get_model('orders', 'DailySummary')
    Order.objects.filter(source=OLD).update(source=NEW_MAIN)
    DailyAdSpend.objects.filter(page=OLD).update(page=NEW_MAIN)
    DailySummary.objects.filter(page=OLD).update(page=NEW_MAIN)


def rename_backward(apps, schema_editor):
    """rollback: ปักผ้ากลับเป็น 'Shopee' เดิม; ใบของร้านเสื้อPK (ถ้ามี) รวมกลับเป็น 'Shopee'
    ด้วย — ส่วนแถวค่าแอด/cache ของเสื้อPK ลบทิ้ง (กันชน unique(date,page);
    cache คำนวณใหม่เองได้)"""
    Order = apps.get_model('orders', 'Order')
    DailyAdSpend = apps.get_model('orders', 'DailyAdSpend')
    DailySummary = apps.get_model('orders', 'DailySummary')
    Order.objects.filter(source__in=[NEW_MAIN, NEW_EXTRA]).update(source=OLD)
    DailyAdSpend.objects.filter(page=NEW_EXTRA).delete()
    DailyAdSpend.objects.filter(page=NEW_MAIN).update(page=OLD)
    DailySummary.objects.filter(page__in=[NEW_MAIN, NEW_EXTRA]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0026_add_source_pages'),
    ]

    operations = [
        migrations.AlterField(
            model_name='dailyadspend',
            name='page',
            field=models.CharField(choices=SOURCE_CHOICES, max_length=50, verbose_name='เพจ/แหล่งที่มา'),
        ),
        migrations.AlterField(
            model_name='order',
            name='source',
            field=models.CharField(choices=SOURCE_CHOICES, max_length=50, verbose_name='แหล่งที่มา'),
        ),
        migrations.RunPython(rename_forward, rename_backward),
    ]
