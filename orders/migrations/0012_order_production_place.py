# Generated for Order.production_place ("ผลิตที่")

from django.db import migrations, models


def backfill_production_place(apps, schema_editor):
    """Legacy orders predate this field — pin them all to 'ผลิตเอง' explicitly
    (RunPython, not just the column default) so the green "outsourced" highlight
    never shows on old orders. New orders default to 'ผลิตเอง' too."""
    Order = apps.get_model('orders', 'Order')
    Order.objects.exclude(production_place='ผลิตเอง').update(production_place='ผลิตเอง')


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0011_order_printed_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='production_place',
            field=models.CharField(
                'ผลิตที่',
                max_length=20,
                choices=[
                    ('ผลิตเอง', 'ผลิตเอง'),
                    ('ร้านแอม', 'ร้านแอม'),
                    ('ร้านแบ้งค์', 'ร้านแบ้งค์'),
                ],
                default='ผลิตเอง',
            ),
        ),
        migrations.RunPython(backfill_production_place, noop),
    ]
