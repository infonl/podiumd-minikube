"""
Verifies mailpit actually receives mail sent by a real component (openzaak)
- adapted from ../tests/test_mailpit.py. Mailpit itself and every app's SMTP
config pointing at it were added to johnb00 this session (see
values/johnb00/mailpit.yaml and each app's "pointed at mailpit" comment in
values/johnb00/podiumd.yaml) specifically so this test could run for real
instead of skipping.

Same fresh-UUID-marker approach as the reference test, for the same reason
(no false positive from a leftover message).
"""

import uuid

import requests
from playwright.sync_api import expect

from conftest import NAMESPACE, kubectl, app_url

MAILPIT_HOST = "mailpit"


def _send_test_mail(marker):
    kubectl(
        "exec",
        "-n",
        NAMESPACE,
        "deploy/openzaak",
        "--",
        "python",
        "/app/src/manage.py",
        "shell",
        "-c",
        "from django.core.mail import send_mail; "
        f"send_mail({marker!r}, 'test body', 'from@example.com', ['to@example.com'])",
    )


def test_mail_sent_by_a_component_arrives_in_mailpit():
    marker = f"johnb00 test {uuid.uuid4()}"
    _send_test_mail(marker)

    response = requests.get(app_url(MAILPIT_HOST, "/api/v1/messages"), timeout=10)
    assert response.status_code == 200
    subjects = [m["Subject"] for m in response.json()["messages"]]
    assert marker in subjects, (
        f"no message with subject {marker!r} found in mailpit - got: {subjects}"
    )


def test_sent_mail_visible_in_mailpit_webui(page):
    marker = f"johnb00 test {uuid.uuid4()}"
    _send_test_mail(marker)

    page.goto(app_url(MAILPIT_HOST))
    expect(page.get_by_text(marker).first).to_be_visible()
