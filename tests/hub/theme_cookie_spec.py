"""The hub base template persists the theme choice in a parent-domain cookie.

Covers the client-side theme system (UAT #6a): the toggle writes ``pl_theme`` to
a cookie so an explicit light/dark choice survives a hop across subdomains of the
registrable domain, and the early inline script resolves the cookie *before* the
surface's own default. Pure-JS behaviour can't run under pytest, so these assert
that the rendered ``hub/base.html`` head carries the cookie-writing logic and the
server-injected ``THEME_COOKIE_DOMAIN`` value.
"""

import pytest
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory

pytestmark = pytest.mark.django_db


def _render_base() -> str:
    """Render hub/base.html for an anonymous request through the real pipeline.

    Passing ``request`` makes Django build a RequestContext, so every registered
    context processor (including ``core.context_processors.theme``) runs and the
    injected value reaches the template exactly as it would in production.
    """
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    return render_to_string("hub/base.html", request=request)


def describe_theme_cookie_persistence():
    def it_writes_the_theme_to_a_cookie_on_toggle():
        html = _render_base()
        # The setter persists to the pl_theme cookie (in addition to localStorage).
        assert "writeThemeCookie(theme)" in html
        assert "'pl_theme=' + theme + '; path=/; max-age=31536000; SameSite=Lax'" in html
        assert "localStorage.setItem('theme', theme)" in html

    def it_only_marks_the_cookie_secure_over_https():
        # Guarded so the cookie still stores on local http dev (pastlives.test).
        html = _render_base()
        assert "if (location.protocol === 'https:') c += '; Secure';" in html

    def it_prefers_the_cookie_over_localstorage_and_the_surface_default():
        html = _render_base()
        # The resolution order is what carries a cross-subdomain choice: cookie
        # first, then per-origin localStorage, then this surface's meta default.
        assert "var cookieTheme = readThemeCookie();" in html
        cookie_idx = html.index("cookieTheme === 'light'")
        ls_idx = html.index("lsTheme === 'light'")
        def_idx = html.index(": def;")
        assert cookie_idx < ls_idx < def_idx

    def describe_when_theme_cookie_domain_is_unset():
        def it_injects_an_empty_domain_for_a_host_only_cookie(settings):
            settings.THEME_COOKIE_DOMAIN = ""
            html = _render_base()
            assert "var themeCookieDomain = '';" in html

    def describe_when_theme_cookie_domain_is_set():
        def it_injects_the_parent_domain_so_the_cookie_spans_subdomains(settings):
            settings.THEME_COOKIE_DOMAIN = ".pastlives.app"
            html = _render_base()
            assert "var themeCookieDomain = '.pastlives.app';" in html
            assert "c += '; domain=' + themeCookieDomain;" in html
