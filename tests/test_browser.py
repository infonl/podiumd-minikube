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

from playwright.sync_api import expect

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
    # from a genuinely broken/blank SPA. `expect(...).to_be_visible()`,
    # not a bare `.is_visible()` assert: found live that "Dashboard" renders
    # synchronously in the app shell, but "Cases"/"Tasks" render slightly
    # later, after an async permissions call resolves - a one-shot
    # `.is_visible()` right after `wait_for_url` races that render and its
    # outcome depends on incidental page-load speed (cold vs warm JS
    # bundle cache), not a page defect. `expect(...)` polls until it's
    # actually true (or its own timeout) instead of checking once.
    expect(page.get_by_text("Dashboard").first).to_be_visible()
    expect(page.get_by_text("Cases").first).to_be_visible()
    expect(page.get_by_text("Tasks").first).to_be_visible()
