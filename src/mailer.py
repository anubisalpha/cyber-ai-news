"""Email sending — self-contained SMTP with a local claude-mail fallback.

Two modes, chosen automatically:
  1. If SMTP_HOST is set (the deployed/container path), send directly via smtplib
     using SMTP_* env vars. Nothing outside this project is needed.
  2. Otherwise (local dev), fall back to importing the sibling claude-mail
     project's send() so existing local usage keeps working.

Env vars (mode 1):
  SMTP_HOST      smtp server (e.g. smtp.gmail.com)      [required to enable mode 1]
  SMTP_PORT      default 587
  SMTP_USER      login user
  SMTP_PASS      login password / app password
  SMTP_FROM      From address (default: SMTP_USER)
  SMTP_SSL       "true" to use implicit SSL (default: STARTTLS; auto-on for port 465)
  MAIL_TO        default recipient if none is passed
"""
from __future__ import annotations

import os
import smtplib
import ssl
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_MAIL = ROOT.parent / "claude-mail"


def default_recipient() -> str | None:
    return os.environ.get("MAIL_TO")


def send_email(to: str | None, subject: str, html: str) -> str:
    """Send an HTML email. Returns the recipient (or a description)."""
    host = os.environ.get("SMTP_HOST")
    if host:
        return _send_smtp(host, to, subject, html)
    return _send_via_claude_mail(to, subject, html)


def _send_smtp(host: str, to: str | None, subject: str, html: str) -> str:
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    sender = os.environ.get("SMTP_FROM") or user or "cyber-ai-news@localhost"
    to = to or default_recipient() or user
    if not to:
        raise RuntimeError("No recipient: set MAIL_TO or pass an address.")

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg.attach(MIMEText(html, "html"))

    use_ssl = os.environ.get("SMTP_SSL", "").lower() in ("1", "true", "yes") or port == 465
    ctx = ssl.create_default_context()
    if use_ssl:
        with smtplib.SMTP_SSL(host, port, context=ctx) as s:
            if user:
                s.login(user, password)
            s.sendmail(sender, [to], msg.as_string())
    else:
        with smtplib.SMTP(host, port) as s:
            s.starttls(context=ctx)
            if user:
                s.login(user, password)
            s.sendmail(sender, [to], msg.as_string())
    return to


def _send_via_claude_mail(to: str | None, subject: str, html: str) -> str:
    if str(CLAUDE_MAIL) not in sys.path:
        sys.path.insert(0, str(CLAUDE_MAIL))
    try:
        import send as claude_mail  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "No SMTP_HOST set and claude-mail is not available "
            f"({CLAUDE_MAIL}): {exc}. Configure SMTP_* env vars to send email."
        ) from exc
    claude_mail.send(to=to, subject=subject, body=html, html=True)
    return to or "(claude-mail default)"
