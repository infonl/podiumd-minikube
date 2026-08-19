"""
Real-browser smoke test through https://zac.johnb00.pd.test-rig.nl via
Playwright - adapted from ../tests/test_browser.py. Real DNS + real TLS
means Playwright navigates directly, no --host-resolver-rules needed.

Same PABC-authorization caveat as test_login_flow.py: the "johnb00" user
has no group/role mapping, so it may land on ZAC's "Geen toestemming" page
after a successful login rather than the real dashboard - treated as a
skip, not a failure, since that's a PABC authorization gap, not a rendering
or infra problem.
"""

import pytest
from playwright.sync_api import expect

ZAC_URL = "https://zac.johnb00.pd.test-rig.nl/"


def test_dashboard_renders_after_login(page, keycloak_test_user):
    username, password = keycloak_test_user
    page.goto(ZAC_URL)

    page.fill("#username", username)
    page.fill("#password", password)
    with page.expect_navigation(url=f"{ZAC_URL}**") as nav_info:
        page.click("#kc-login")
    response = nav_info.value

    # Confirmed live via test_login_flow.py's raw-requests version of this
    # same flow: a 403 here means ZAC's own PABC-authorization-denied
    # response (the 'johnb00' user has no PABC role/domain mapping), not a
    # rendering/infra problem - the OIDC round trip already succeeded.
    if response.status == 403:
        pytest.skip(
            "OIDC login succeeded, but ZAC returned 403 - the 'johnb00' "
            "user isn't authorized in PABC's own role/domain mapping - "
            "see test_login_flow.py's docstring"
        )

    assert "Zaakafhandelcomponent" in page.title()
    expect(page.get_by_text("Dashboard").first).to_be_visible()
    expect(page.get_by_text("Cases").first).to_be_visible()
    expect(page.get_by_text("Tasks").first).to_be_visible()
