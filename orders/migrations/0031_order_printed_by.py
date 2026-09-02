# Additive only: who pressed "พิมพ์ใบงานแล้ว" (set alongside printed_at).
# No backfill — legacy orders keep NULL.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0030_orderitem_design_doc_number'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='printed_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='printed_orders', to=settings.AUTH_USER_MODEL, verbose_name='คนกดพิมพ์ใบงาน'),
        ),
    ]
