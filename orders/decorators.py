"""
Decorators for production-channel access (cookie-based, no Django login).

See CLAUDE-V1.6.md §1 — production users identify themselves once via the
'production_dept' cookie, then act anonymously thereafter.
"""

from functools import wraps
from urllib.parse import urlencode

from django.shortcuts import redirect
from django.urls import reverse

from .departments import VALID_SLUGS, get_department


def require_department(view_func):
    """Redirect to /select-department/?next=... unless a valid dept cookie is set.

    On success, attaches request.production_dept (the dept dict) for the view.
    """

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        slug = request.COOKIES.get('production_dept')
        dept = get_department(slug) if slug in VALID_SLUGS else None
        if dept is None:
            target = reverse('select_department')
            qs = urlencode({'next': request.get_full_path()})
            return redirect(f'{target}?{qs}')
        request.production_dept = dept
        return view_func(request, *args, **kwargs)

    return _wrapped
