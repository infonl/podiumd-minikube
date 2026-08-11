"""
Verifies mailpit (templates/mailpit/) actually receives mail sent by a
real component, and that both its API and its real web UI (a real
browser via Playwright, same approach as test_browser.py's ZAC dashboard
check) correctly show it - not just that its root path returns 200
(test_reachability.py already covers that alone, which a broken/blank
SPA could still do).

A fresh, unique subject (a UUID marker) is used per test rather than a
fixed string, so neither test can false-positive on a leftover message
from a previous run or from manual testing - each only counts if *its
own* message shows up.
"""

import uuid

import requests
from playwright.sync_api import expect

from conftest import NAMESPACE, host_headers, host_url, kubectl

MAILPIT_HOST = "mailpit.local"


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


def test_mail_sent_by_a_component_arrives_in_mailpit(traefik_ip):
    marker = f"podiumd-minikube test {uuid.uuid4()}"
    _send_test_mail(marker)

    response = requests.get(
        host_url(traefik_ip, "/api/v1/messages"),
        headers=host_headers(MAILPIT_HOST),
        timeout=10,
    )
    assert response.status_code == 200
    subjects = [m["Subject"] for m in response.json()["messages"]]
    assert marker in subjects, (
        f"no message with subject {marker!r} found in mailpit - "
        f"got: {subjects}"
    )


def test_sent_mail_visible_in_mailpit_webui(page):
    marker = f"podiumd-minikube test {uuid.uuid4()}"
    _send_test_mail(marker)

    page.goto("http://mailpit.local/")
    # expect(...).to_be_visible(), not a bare .is_visible() assert: same
    # race as test_browser.py's own dashboard check found live - mailpit's
    # SPA fetches its message list asynchronously after the initial page
    # load, so a one-shot check right after goto() depends on incidental
    # timing instead of the message actually having rendered yet.
    expect(page.get_by_text(marker).first).to_be_visible()
