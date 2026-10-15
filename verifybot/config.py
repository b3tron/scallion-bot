"""Configuration loaded from environment variables (or a .env file)."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field

try:  # python-dotenv is optional; plain env vars work too
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


def _int(name: str, default: int | None = None) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def _list(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def _aliases(name: str, default: str) -> dict[str, str]:
    """Parse 'umail.ucsb.edu:ucsb.edu,other.edu:main.edu' into a dict."""
    out: dict[str, str] = {}
    for pair in _list(name, default):
        if ":" in pair:
            src, dst = pair.split(":", 1)
            out[src.strip()] = dst.strip()
    return out


@dataclass
class Config:
    # --- Discord ---
    discord_token: str = ""
    guild_id: int | None = None          # sync slash commands instantly to this server
    verified_role_id: int | None = None  # role granted after verification
    unverified_role_id: int | None = None  # optional role removed after verification
    log_channel_id: int | None = None    # optional private channel for officer audit log
    club_name: str = "Assistive Tech"

    # --- Who may verify ---
    allowed_domains: list[str] = field(default_factory=lambda: ["ucsb.edu"])
    domain_aliases: dict[str, str] = field(default_factory=lambda: {"umail.ucsb.edu": "ucsb.edu"})

    # --- Code / abuse limits ---
    code_ttl_minutes: int = 10
    max_attempts: int = 5
    resend_cooldown_seconds: int = 60
    max_codes_per_hour: int = 5

    # --- Email delivery ---
    email_backend: str = "console"  # "console" (prints codes, for testing) or "smtp"
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_ssl: bool = False  # True for port 465; otherwise STARTTLS
    email_from: str = ""

    # --- Storage / secrets ---
    db_path: str = "verification.db"
    secret_key: str = ""

    @classmethod
    def from_env(cls, env_file: str | None = ".env") -> "Config":
        if load_dotenv and env_file:
            load_dotenv(env_file)
        cfg = cls(
            discord_token=os.getenv("DISCORD_TOKEN", "").strip(),
            guild_id=_int("GUILD_ID"),
            verified_role_id=_int("VERIFIED_ROLE_ID"),
            unverified_role_id=_int("UNVERIFIED_ROLE_ID"),
            log_channel_id=_int("LOG_CHANNEL_ID"),
            club_name=os.getenv("CLUB_NAME", "Assistive Tech").strip(),
            allowed_domains=_list("ALLOWED_DOMAINS", "ucsb.edu"),
            domain_aliases=_aliases("DOMAIN_ALIASES", "umail.ucsb.edu:ucsb.edu"),
            code_ttl_minutes=_int("CODE_TTL_MINUTES", 10),
            max_attempts=_int("MAX_ATTEMPTS", 5),
            resend_cooldown_seconds=_int("RESEND_COOLDOWN_SECONDS", 60),
            max_codes_per_hour=_int("MAX_CODES_PER_HOUR", 5),
            email_backend=os.getenv("EMAIL_BACKEND", "console").strip().lower(),
            smtp_host=os.getenv("SMTP_HOST", "smtp.gmail.com").strip(),
            smtp_port=_int("SMTP_PORT", 587),
            smtp_user=os.getenv("SMTP_USER", "").strip(),
            smtp_password=os.getenv("SMTP_PASSWORD", "").strip(),
            smtp_use_ssl=os.getenv("SMTP_USE_SSL", "false").strip().lower() in {"1", "true", "yes"},
            email_from=os.getenv("EMAIL_FROM", "").strip(),
            db_path=os.getenv("DB_PATH", "verification.db").strip(),
            secret_key=os.getenv("SECRET_KEY", "").strip(),
        )
        if not cfg.secret_key:
            # Codes are only valid for minutes, so a per-process key is acceptable,
            # but pending codes won't survive a restart. Set SECRET_KEY to persist.
            cfg.secret_key = secrets.token_hex(32)
        if not cfg.email_from:
            cfg.email_from = cfg.smtp_user
        return cfg

    def problems(self) -> list[str]:
        """Return human-readable setup problems that would stop the bot."""
        issues = []
        if not self.discord_token:
            issues.append("DISCORD_TOKEN is not set.")
        if not self.verified_role_id:
            issues.append("VERIFIED_ROLE_ID is not set.")
        if not self.allowed_domains:
            issues.append("ALLOWED_DOMAINS is empty — nobody could verify.")
        if self.email_backend not in {"console", "smtp"}:
            issues.append("EMAIL_BACKEND must be 'console' or 'smtp'.")
        if self.email_backend == "smtp" and not (self.smtp_user and self.smtp_password):
            issues.append("EMAIL_BACKEND=smtp needs SMTP_USER and SMTP_PASSWORD.")
        return issues
