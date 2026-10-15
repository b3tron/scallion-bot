"""Verification rules — no Discord code here, so it is easy to test.

Flow:  start(user, email)  ->  email a 6-digit code  ->  confirm(user, code)  ->  verified
"""
from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from .config import Config
from .mailer import Mailer
from .storage import Pending, Store, Verified

EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$")


class Status(str, Enum):
    CODE_SENT = "code_sent"
    VERIFIED = "verified"
    ALREADY_VERIFIED = "already_verified"
    INVALID_EMAIL = "invalid_email"
    DOMAIN_NOT_ALLOWED = "domain_not_allowed"
    EMAIL_IN_USE = "email_in_use"
    COOLDOWN = "cooldown"
    RATE_LIMITED = "rate_limited"
    SEND_FAILED = "send_failed"
    NO_PENDING = "no_pending"
    EXPIRED = "expired"
    WRONG_CODE = "wrong_code"
    TOO_MANY_ATTEMPTS = "too_many_attempts"
    INVALID_CODE_FORMAT = "invalid_code_format"


@dataclass
class Result:
    status: Status
    email: str | None = None
    retry_after: int | None = None        # seconds, for COOLDOWN / RATE_LIMITED
    attempts_left: int | None = None      # for WRONG_CODE

    @property
    def ok(self) -> bool:
        return self.status in {Status.CODE_SENT, Status.VERIFIED}


def normalize_email(raw: str) -> str:
    return (raw or "").strip().lower()


def domain_of(email: str) -> str:
    return email.rsplit("@", 1)[-1]


class VerificationService:
    def __init__(self, store: Store, mailer: Mailer, cfg: Config,
                 clock: Callable[[], float] = time.time):
        self.store = store
        self.mailer = mailer
        self.cfg = cfg
        self.clock = clock

    # ------------------------------------------------------------------ helpers
    def domain_allowed(self, email: str) -> bool:
        d = domain_of(email)
        return any(d == allowed or d.endswith("." + allowed) for allowed in self.cfg.allowed_domains)

    def canonical(self, email: str) -> str:
        """Collapse addresses that reach the same inbox so one person can't
        verify several Discord accounts (jdoe+alt@ucsb.edu, jdoe@umail.ucsb.edu)."""
        local, domain = email.rsplit("@", 1)
        local = local.split("+", 1)[0]
        domain = self.cfg.domain_aliases.get(domain, domain)
        return f"{local}@{domain}"

    def _hash(self, guild_id: int, user_id: int, code: str) -> str:
        msg = f"{guild_id}:{user_id}:{code}".encode()
        return hmac.new(self.cfg.secret_key.encode(), msg, hashlib.sha256).hexdigest()

    @staticmethod
    def _new_code() -> str:
        return f"{secrets.randbelow(1_000_000):06d}"

    def is_verified(self, guild_id: int, user_id: int) -> Verified | None:
        return self.store.get_verified(guild_id, user_id)

    # ------------------------------------------------------------------ step 1
    async def start(self, guild_id: int, user_id: int, raw_email: str) -> Result:
        email = normalize_email(raw_email)

        if self.store.get_verified(guild_id, user_id):
            return Result(Status.ALREADY_VERIFIED)
        if not EMAIL_RE.match(email):
            return Result(Status.INVALID_EMAIL, email=email)
        if not self.domain_allowed(email):
            return Result(Status.DOMAIN_NOT_ALLOWED, email=email)

        canon = self.canonical(email)
        owner = self.store.find_by_email(guild_id, canon)
        if owner and owner.user_id != user_id:
            return Result(Status.EMAIL_IN_USE, email=email)

        now = self.clock()
        self.store.prune_sends(now - 3600)
        recent = self.store.sends_since(guild_id, user_id, now - 3600)
        if recent:
            remaining = recent[-1] + self.cfg.resend_cooldown_seconds - now
            if remaining > 0:
                return Result(Status.COOLDOWN, email=email, retry_after=math.ceil(remaining))
        if len(recent) >= self.cfg.max_codes_per_hour:
            remaining = recent[0] + 3600 - now
            return Result(Status.RATE_LIMITED, email=email, retry_after=max(math.ceil(remaining), 1))

        code = self._new_code()
        self.store.put_pending(Pending(
            guild_id=guild_id, user_id=user_id, email=email, canonical=canon,
            code_hash=self._hash(guild_id, user_id, code),
            expires_at=now + self.cfg.code_ttl_minutes * 60, attempts=0, created_at=now,
        ))
        self.store.log_send(guild_id, user_id, now)
        try:
            await self.mailer.send_code(email, code)
        except Exception:
            # Don't leave a code nobody received, and don't charge the user's rate limit.
            self.store.delete_pending(guild_id, user_id)
            self.store.unlog_last_send(guild_id, user_id)
            return Result(Status.SEND_FAILED, email=email)
        return Result(Status.CODE_SENT, email=email)

    # ------------------------------------------------------------------ step 2
    async def confirm(self, guild_id: int, user_id: int, raw_code: str) -> Result:
        code = re.sub(r"\s+", "", raw_code or "")
        if self.store.get_verified(guild_id, user_id):
            return Result(Status.ALREADY_VERIFIED)

        p = self.store.get_pending(guild_id, user_id)
        if not p:
            return Result(Status.NO_PENDING)
        if self.clock() > p.expires_at:
            self.store.delete_pending(guild_id, user_id)
            return Result(Status.EXPIRED, email=p.email)
        if p.attempts >= self.cfg.max_attempts:
            self.store.delete_pending(guild_id, user_id)
            return Result(Status.TOO_MANY_ATTEMPTS, email=p.email)
        if not re.fullmatch(r"\d{6}", code):
            # Typos like "12345" don't burn an attempt.
            return Result(Status.INVALID_CODE_FORMAT, email=p.email,
                          attempts_left=self.cfg.max_attempts - p.attempts)

        if not hmac.compare_digest(p.code_hash, self._hash(guild_id, user_id, code)):
            used = self.store.bump_attempts(guild_id, user_id)
            left = self.cfg.max_attempts - used
            if left <= 0:
                self.store.delete_pending(guild_id, user_id)
                return Result(Status.TOO_MANY_ATTEMPTS, email=p.email)
            return Result(Status.WRONG_CODE, email=p.email, attempts_left=left)

        try:
            self.store.add_verified(Verified(
                guild_id=guild_id, user_id=user_id, email=p.email, canonical=p.canonical,
                method="email", verified_by=None, note=None, verified_at=self.clock(),
            ))
        except sqlite3.IntegrityError:
            # Someone else verified this email between step 1 and step 2.
            self.store.delete_pending(guild_id, user_id)
            return Result(Status.EMAIL_IN_USE, email=p.email)
        self.store.delete_pending(guild_id, user_id)
        return Result(Status.VERIFIED, email=p.email)

    # ------------------------------------------------------------------ officers
    def manual_verify(self, guild_id: int, user_id: int, by: int, note: str | None = None) -> None:
        """For people without a UCSB email (alumni, advisors, sponsors)."""
        self.store.add_verified(Verified(
            guild_id=guild_id, user_id=user_id, email=None, canonical=None, method="manual",
            verified_by=by, note=note, verified_at=self.clock(),
        ))
        self.store.delete_pending(guild_id, user_id)

    def revoke(self, guild_id: int, user_id: int) -> bool:
        self.store.delete_pending(guild_id, user_id)
        return self.store.remove_verified(guild_id, user_id)

    def whois(self, guild_id: int, raw_email: str) -> Verified | None:
        email = normalize_email(raw_email)
        if "@" not in email:
            return None
        return self.store.find_by_email(guild_id, self.canonical(email))

    def stats(self, guild_id: int) -> dict[str, int]:
        counts = self.store.count_verified(guild_id)
        return {
            "email": counts.get("email", 0),
            "manual": counts.get("manual", 0),
            "pending": self.store.count_pending(guild_id, self.clock()),
        }
