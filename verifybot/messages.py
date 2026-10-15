"""User-facing text for each verification outcome."""
from __future__ import annotations

from .config import Config
from .core import Result, Status


def allowed_domains_text(cfg: Config) -> str:
    return " or ".join(f"@{d}" for d in cfg.allowed_domains)


def describe(result: Result, cfg: Config) -> str:
    s = result.status
    if s is Status.CODE_SENT:
        return (f"📬 We emailed a 6-digit code to **{result.email}**.\n"
                f"Check your inbox (and Spam/Promotions). It expires in {cfg.code_ttl_minutes} minutes.\n"
                f"When you have it, press **Enter code** below.")
    if s is Status.VERIFIED:
        return f"✅ You're verified — welcome to **{cfg.club_name}**! The rest of the server is now unlocked."
    if s is Status.ALREADY_VERIFIED:
        return "✅ You're already verified. If you still can't see the channels, contact an officer."
    if s is Status.INVALID_EMAIL:
        return "❌ That doesn't look like an email address. Please check it and try again."
    if s is Status.DOMAIN_NOT_ALLOWED:
        return (f"❌ Please use your UCSB email (ending in {allowed_domains_text(cfg)}).\n"
                f"Not a UCSB student (alumni, advisor, sponsor)? Message an officer and they can verify you.")
    if s is Status.EMAIL_IN_USE:
        return ("❌ That email is already linked to a different Discord account in this server.\n"
                "If that's a mistake (e.g. you made a new account), contact an officer.")
    if s is Status.COOLDOWN:
        return f"⏳ A code was just sent. Please wait {result.retry_after}s before requesting another."
    if s is Status.RATE_LIMITED:
        minutes = max(1, round((result.retry_after or 60) / 60))
        return f"⏳ Too many codes requested. Please try again in about {minutes} minute(s)."
    if s is Status.SEND_FAILED:
        return ("⚠️ We couldn't send the email just now. Please try again in a minute — "
                "if it keeps failing, contact an officer.")
    if s is Status.NO_PENDING:
        return "You don't have an active code. Press **Verify with UCSB email** to get one."
    if s is Status.EXPIRED:
        return "⌛ That code expired. Press **Verify with UCSB email** to get a new one."
    if s is Status.WRONG_CODE:
        n = result.attempts_left
        return f"❌ That code isn't right — {n} attempt{'s' if n != 1 else ''} left."
    if s is Status.TOO_MANY_ATTEMPTS:
        return "🔒 Too many wrong attempts, so that code no longer works. Request a new one."
    if s is Status.INVALID_CODE_FORMAT:
        return "Codes are exactly 6 digits — please double-check and try again."
    return "Something went wrong. Please contact an officer."  # pragma: no cover
