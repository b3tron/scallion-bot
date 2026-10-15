"""Email delivery backends."""
from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger("verifybot.mail")


def build_message(*, to: str, sender: str, code: str, club_name: str, ttl_minutes: int) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = f"{code} is your {club_name} Discord verification code"
    msg["From"] = f"{club_name} Discord <{sender}>" if sender else f"{club_name} Discord"
    msg["To"] = to
    msg.set_content(
        f"Hi!\n\n"
        f"Your {club_name} Discord verification code is:\n\n"
        f"    {code}\n\n"
        f"Enter it in Discord within {ttl_minutes} minutes to unlock the server.\n\n"
        f"If you didn't request this, you can ignore this email — someone may have "
        f"typed your address by mistake, and nothing happens unless the code is entered.\n"
    )
    msg.add_alternative(
        f"""<div style="font-family:Arial,sans-serif;max-width:480px">
  <p>Hi!</p>
  <p>Your <b>{club_name}</b> Discord verification code is:</p>
  <p style="font-size:32px;letter-spacing:6px;font-weight:bold;margin:16px 0">{code}</p>
  <p>Enter it in Discord within {ttl_minutes} minutes to unlock the server.</p>
  <p style="color:#666;font-size:13px">Didn't request this? You can ignore this email —
  nothing happens unless the code is entered.</p>
</div>""",
        subtype="html",
    )
    return msg


class Mailer:
    async def send_code(self, to: str, code: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class ConsoleMailer(Mailer):
    """For local testing: prints the code instead of emailing it. Never use in production."""

    def __init__(self, club_name: str = "Assistive Tech", ttl_minutes: int = 10):
        self.club_name = club_name
        self.ttl_minutes = ttl_minutes
        self.outbox: list[tuple[str, str]] = []

    async def send_code(self, to: str, code: str) -> None:
        self.outbox.append((to, code))
        log.warning("[CONSOLE EMAIL] to=%s code=%s  (EMAIL_BACKEND=console — not actually sent)", to, code)


class SmtpMailer(Mailer):
    """Sends real email over SMTP (e.g. a club Gmail account with an App Password)."""

    def __init__(self, *, host: str, port: int, user: str, password: str, sender: str,
                 use_ssl: bool = False, club_name: str = "Assistive Tech", ttl_minutes: int = 10,
                 starttls: bool = True, timeout: float = 20):
        self.host, self.port = host, port
        self.user, self.password = user, password
        self.sender = sender or user
        self.use_ssl = use_ssl
        self.starttls = starttls
        self.club_name = club_name
        self.ttl_minutes = ttl_minutes
        self.timeout = timeout

    def _send_sync(self, msg: EmailMessage) -> None:
        ctx = ssl.create_default_context()
        if self.use_ssl:
            with smtplib.SMTP_SSL(self.host, self.port, context=ctx, timeout=self.timeout) as s:
                if self.user:
                    s.login(self.user, self.password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as s:
                if self.starttls:
                    s.starttls(context=ctx)
                if self.user:
                    s.login(self.user, self.password)
                s.send_message(msg)

    async def send_code(self, to: str, code: str) -> None:
        msg = build_message(to=to, sender=self.sender, code=code,
                            club_name=self.club_name, ttl_minutes=self.ttl_minutes)
        # smtplib is blocking; keep the Discord event loop responsive.
        await asyncio.to_thread(self._send_sync, msg)
        log.info("Sent verification code to %s", to)


def mailer_from_config(cfg) -> Mailer:
    if cfg.email_backend == "smtp":
        return SmtpMailer(host=cfg.smtp_host, port=cfg.smtp_port, user=cfg.smtp_user,
                          password=cfg.smtp_password, sender=cfg.email_from,
                          use_ssl=cfg.smtp_use_ssl, club_name=cfg.club_name,
                          ttl_minutes=cfg.code_ttl_minutes)
    return ConsoleMailer(club_name=cfg.club_name, ttl_minutes=cfg.code_ttl_minutes)
