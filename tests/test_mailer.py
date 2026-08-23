"""Tests for the self-contained SMTP mailer (mocked, no real network)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import smtplib  # noqa: E402

import pytest  # noqa: E402

from src import mailer  # noqa: E402


class _FakeSMTP:
    instances = []

    def __init__(self, host, port):
        self.host, self.port = host, port
        self.started_tls = False
        self.logged_in = None
        self.sent = None
        _FakeSMTP.instances.append(self)

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def starttls(self, context=None): self.started_tls = True
    def login(self, u, p): self.logged_in = (u, p)
    def sendmail(self, frm, to, msg): self.sent = (frm, to, msg)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "SMTP_FROM",
              "SMTP_SSL", "MAIL_TO"):
        monkeypatch.delenv(k, raising=False)
    _FakeSMTP.instances.clear()


def test_smtp_mode_starttls(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASS", "secret")
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)

    to = mailer.send_email("me@example.com", "Subj", "<b>hi</b>")
    assert to == "me@example.com"
    s = _FakeSMTP.instances[-1]
    assert s.host == "smtp.example.com" and s.port == 587
    assert s.started_tls and s.logged_in == ("bot@example.com", "secret")
    assert "Subj" in s.sent[2] and "me@example.com" in s.sent[1]


def test_smtp_uses_mail_to_default(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASS", "secret")
    monkeypatch.setenv("MAIL_TO", "default@example.com")
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)

    to = mailer.send_email(None, "S", "<i>x</i>")
    assert to == "default@example.com"


def test_no_smtp_and_no_claude_mail_raises(monkeypatch):
    # No SMTP_HOST, and point claude-mail at a nonexistent dir.
    monkeypatch.setattr(mailer, "CLAUDE_MAIL", Path("/no/such/claude-mail"))
    with pytest.raises(RuntimeError):
        mailer.send_email("x@y.z", "S", "<p>x</p>")
