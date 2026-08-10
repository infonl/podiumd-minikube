"""
Real-browser smoke test through http://zac.local via Playwright -
complements test_login_flow.py's raw-requests version of the same OIDC
flow. That test proves the HTTP redirect chain and cookies are wired
correctly; this one proves the actual SPA renders/hydrates for a real
browser - something a raw HTTP status/redirect check can't catch (a JS
error, a blank dashboard, or a frontend API-parsing failure would all
still return HTTP 200 and the initial <zac-root> shell markup).

Same dev-only test credentials as test_login_flow.py - see that module's
docstring for how to reset them if this fails on a credential error
rather than a rendering one.
"""

TEST_USERNAME = "beheerder1newiam"
TEST_PASSWORD = "beheerder1newiam"


def test_dashboard_renders_after_login(page):
    page.goto("http://zac.local/")

    page.fill("#username", TEST_USERNAME)
    page.fill("#password", TEST_PASSWORD)
    page.click("#kc-login")

    page.wait_for_url("http://zac.local/**")
    assert "Zaakafhandelcomponent" in page.title()

    # Visible, hydrated navigation content - not just the initial
    # <zac-root> shell markup, which HTTP-only checks can't tell apart
    # from a genuinely broken/blank SPA.
    assert page.get_by_text("Dashboard").first.is_visible()
    assert page.get_by_text("Cases").first.is_visible()
    assert page.get_by_text("Tasks").first.is_visible()
