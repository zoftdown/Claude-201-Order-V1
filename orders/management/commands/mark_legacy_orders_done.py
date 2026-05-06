"""
Backfill stage timestamps on orders created before Phase 1.6.

These legacy orders are already shipped or picked up — we treat every stage
as completed on their `created_date` so the production board doesn't list
them as pending. The destination (shipped vs awaiting pickup) is decided
from `delivery_method`.

Default mode is dry-run; pass --confirm to actually write.
"""

from datetime import datetime, time

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from orders.models import Order

STAGE_FIELDS = (
    'print_done_at',
    'roll_done_at',
    'cut_done_at',
    'sort_done_at',
    'sent_to_tailors_at',
    'packed_at',
)


class Command(BaseCommand):
    help = "Backfill stage timestamps for legacy orders (orders with packed_at IS NULL)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Actually apply changes. Without this flag the command runs as dry-run.',
        )

    def handle(self, *args, **options):
        commit = options['confirm']
        mode = 'COMMIT' if commit else 'DRY-RUN'

        legacy = list(Order.objects.filter(packed_at__isnull=True).order_by('id'))
        total = len(legacy)

        self.stdout.write(self.style.MIGRATE_HEADING(f'[{mode}] Found {total} legacy orders to update'))

        if total == 0:
            self.stdout.write(self.style.SUCCESS('Nothing to do.'))
            return

        # Show first 3 as a preview
        self.stdout.write('\nPreview (first 3 records):')
        for o in legacy[:3]:
            self.stdout.write(
                f'  • {o.order_number}  created={o.created_date}  '
                f'delivery={o.delivery_method}  customer={o.customer_name}'
            )
        if total > 3:
            self.stdout.write(f'  ... and {total - 3} more')

        # Build the in-memory updates (used in both dry-run and commit paths)
        ship_count = 0
        pickup_count = 0
        tz = timezone.get_current_timezone()

        for o in legacy:
            # Anchor every stage to start-of-day on created_date (timezone-aware)
            anchor = datetime.combine(o.created_date, time(9, 0), tzinfo=tz)
            for field in STAGE_FIELDS:
                setattr(o, field, anchor)
            if o.delivery_method == 'ส่ง':
                o.shipped_at = anchor
                ship_count += 1
            else:  # 'รับเอง' (or anything else — treat as pickup)
                o.awaiting_pickup_at = anchor
                pickup_count += 1

        update_fields = list(STAGE_FIELDS) + ['shipped_at', 'awaiting_pickup_at']

        self.stdout.write('')
        self.stdout.write(f'  Will set 6 stage timestamps + 1 destination on each order')
        self.stdout.write(f'  Destination breakdown: shipped={ship_count}, awaiting_pickup={pickup_count}')

        if not commit:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                'DRY-RUN — no changes written. Re-run with --confirm to apply.'
            ))
            return

        with transaction.atomic():
            Order.objects.bulk_update(legacy, update_fields, batch_size=200)

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'✓ Updated {total} orders.'))
