"""
Single source of truth for production departments.

Used by:
- @require_department decorator (validates cookie value)
- @viewer_or_login_required (special-cases the 'viewer' slug)
- select_department view (renders the buttons)
- dept_dashboard view (looks up label/color for header)
"""

DEPARTMENTS = [
    {'slug': 'print',  'name': 'พิมพ์',     'color': '#e74c3c', 'icon': '🖨️'},
    {'slug': 'roll',   'name': 'โรล',       'color': '#f39c12', 'icon': '📜'},
    {'slug': 'cut',    'name': 'ตัด',       'color': '#3498db', 'icon': '✂️'},
    {'slug': 'sort',   'name': 'คัด',       'color': '#9b59b6', 'icon': '🔍'},
    {'slug': 'sew',    'name': 'ส่งเย็บ',    'color': '#16a085', 'icon': '🧵'},
    {'slug': 'pack',   'name': 'รีด+แพ็ค', 'color': '#34495e', 'icon': '📦'},
    # Read-only viewer: no production-stage actions, browses list/detail/print.
    # See viewer_or_login_required in decorators.py.
    {'slug': 'viewer', 'name': 'ดู/ค้นหา', 'color': '#7f8c8d', 'icon': '👁️'},
]

VALID_SLUGS = frozenset(d['slug'] for d in DEPARTMENTS)
VIEWER_SLUG = 'viewer'

_BY_SLUG = {d['slug']: d for d in DEPARTMENTS}


def get_department(slug):
    return _BY_SLUG.get(slug)
