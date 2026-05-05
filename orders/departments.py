"""
Single source of truth for the 6 production departments.

Used by:
- @require_department decorator (validates cookie value)
- select_department view (renders the 6 buttons)
- dept_dashboard view (looks up label/color for header)
"""

DEPARTMENTS = [
    {'slug': 'print', 'name': 'พิมพ์',     'color': '#e74c3c', 'icon': '🖨️'},
    {'slug': 'roll',  'name': 'โรล',       'color': '#f39c12', 'icon': '📜'},
    {'slug': 'cut',   'name': 'ตัด',       'color': '#3498db', 'icon': '✂️'},
    {'slug': 'sort',  'name': 'คัด',       'color': '#9b59b6', 'icon': '🔍'},
    {'slug': 'sew',   'name': 'ส่งเย็บ',    'color': '#16a085', 'icon': '🧵'},
    {'slug': 'pack',  'name': 'รีด+แพ็ค', 'color': '#34495e', 'icon': '📦'},
]

VALID_SLUGS = frozenset(d['slug'] for d in DEPARTMENTS)

_BY_SLUG = {d['slug']: d for d in DEPARTMENTS}


def get_department(slug):
    return _BY_SLUG.get(slug)
