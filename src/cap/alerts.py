"""Alerts to Hemant (PRD 3 and 6, review R12): an email when something needs him.

The platform fails closed, so nothing wrong is ever published while an alert waits;
alerts exist so that work does not pile up unseen. Every alert is also written to the
audit log by the caller, and the Operator screen lists the recent ones, so an alert
is never lost when email is not set up.

Email is sent only when Hemant has configured it in his own `.env` (never in code,
chat or logs): CAP_ALERT_EMAIL_TO, and CAP_SMTP_URL as
smtps://user:app-password@smtp.example.com:465 (or smtp:// with STARTTLS on 587).
`send` never raises: an alert that cannot be sent says why.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import unquote, urlsplit

from cap.config import get_settings

SUBJECT_PREFIX = "[Content Agent Portfolio] "


def send(subject: str, body: str) -> str:
    """Email the alert if email is configured. Returns what happened, in words."""
    s = get_settings()
    if not s.alert_email_to or s.smtp_url is None:
        return "not emailed (email alerts are not set up)"
    try:
        _deliver(s.smtp_url.get_secret_value(), s.alert_email_to, SUBJECT_PREFIX + subject, body)
    except Exception as e:  # noqa: BLE001 - an alert never stops the caller
        return f"not emailed: {type(e).__name__}"  # never the URL: it holds the password
    return f"emailed to {s.alert_email_to}"


def _deliver(url: str, to: str, subject: str, body: str) -> None:
    u = urlsplit(url)
    if u.scheme not in ("smtps", "smtp") or not u.hostname:
        raise ValueError("CAP_SMTP_URL must be smtps://... or smtp://...")
    user, password = unquote(u.username or ""), unquote(u.password or "")
    msg = EmailMessage()
    msg["Subject"], msg["To"], msg["From"] = subject, to, user or to
    msg.set_content(body)
    context = ssl.create_default_context()
    if u.scheme == "smtps":
        server: smtplib.SMTP = smtplib.SMTP_SSL(
            u.hostname, u.port or 465, timeout=20, context=context
        )
    else:
        server = smtplib.SMTP(u.hostname, u.port or 587, timeout=20)
        server.starttls(context=context)
    with server:
        if user:
            server.login(user, password)
        server.send_message(msg)
