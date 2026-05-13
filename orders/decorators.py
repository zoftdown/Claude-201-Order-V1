"""
Decorators for production-channel access (cookie-based, no Django login).

See CLAUDE-V1.6.md §1 — production users identify themselves once via the
'production_dept' cookie, then act anonymously thereafter.
"""

from functools import wraps
from urllib.parse import urlencode

from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect
from django.urls import reverse

from .departments import VALID_SLUGS, VIEWER_SLUG, get_department


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


def viewer_or_login_required(view_func):
    """Read-only views (list / detail / print) accept either path:

    - Logged-in Django user      → request.is_viewer = False
    - 'viewer' department cookie → request.is_viewer = True
    - Otherwise                  → redirect to /login/?next=...

    Templates that render under this decorator should hide
    edit/delete/create/price elements when `request.is_viewer` is true.
    """

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if request.user.is_authenticated:
            request.is_viewer = False
            return view_func(request, *args, **kwargs)
        if request.COOKIES.get('production_dept') == VIEWER_SLUG:
            request.is_viewer = True
            return view_func(request, *args, **kwargs)
        return redirect_to_login(request.get_full_path())

    return _wrapped
