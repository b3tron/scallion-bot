import pytest

from verifybot.core import Status, VerificationService
from verifybot.storage import Store

from conftest import GUILD, FailingMailer

pytestmark = pytest.mark.asyncio

ALICE, BOB = 1, 2


def last_code(mailer):
    return mailer.outbox[-1][1]


async def test_happy_path(service, mailer):
    r = await service.start(GUILD, ALICE, "  Alice@UCSB.edu ")
    assert r.status is Status.CODE_SENT and r.email == "alice@ucsb.edu"
    assert mailer.outbox[-1][0] == "alice@ucsb.edu"
    code = last_code(mailer)
    assert len(code) == 6 and code.isdigit()

    r = await service.confirm(GUILD, ALICE, code)
    assert r.status is Status.VERIFIED
    v = service.is_verified(GUILD, ALICE)
    assert v.email == "alice@ucsb.edu" and v.method == "email"


async def test_code_with_spaces_accepted(service, mailer):
    await service.start(GUILD, ALICE, "alice@ucsb.edu")
    code = last_code(mailer)
    assert (await service.confirm(GUILD, ALICE, f" {code[:3]} {code[3:]} ")).status is Status.VERIFIED


@pytest.mark.parametrize("email", ["alice@gmail.com", "alice@ucsb.edu.evil.com", "alice@notucsb.edu",
                                   "alice@ucla.edu"])
async def test_rejects_non_ucsb(service, email):
    assert (await service.start(GUILD, ALICE, email)).status is Status.DOMAIN_NOT_ALLOWED


@pytest.mark.parametrize("email", ["alice@umail.ucsb.edu", "prof@ece.ucsb.edu"])
async def test_accepts_ucsb_subdomains(service, email):
    assert (await service.start(GUILD, ALICE, email)).status is Status.CODE_SENT


@pytest.mark.parametrize("email", ["", "not-an-email", "a@b", "two@@ucsb.edu", "spa ce@ucsb.edu"])
async def test_rejects_malformed(service, email):
    assert (await service.start(GUILD, ALICE, email)).status is Status.INVALID_EMAIL


async def test_wrong_code_then_lockout(service, mailer, cfg):
    await service.start(GUILD, ALICE, "alice@ucsb.edu")
    real = last_code(mailer)
    wrong = "000000" if real != "000000" else "111111"
    for expected_left in range(cfg.max_attempts - 1, 0, -1):
        r = await service.confirm(GUILD, ALICE, wrong)
        assert r.status is Status.WRONG_CODE and r.attempts_left == expected_left
    assert (await service.confirm(GUILD, ALICE, wrong)).status is Status.TOO_MANY_ATTEMPTS
    # Even the right code no longer works — must request a new one.
    assert (await service.confirm(GUILD, ALICE, real)).status is Status.NO_PENDING


async def test_malformed_code_does_not_burn_attempt(service, mailer, cfg):
    await service.start(GUILD, ALICE, "alice@ucsb.edu")
    r = await service.confirm(GUILD, ALICE, "12345")
    assert r.status is Status.INVALID_CODE_FORMAT and r.attempts_left == cfg.max_attempts
    assert (await service.confirm(GUILD, ALICE, last_code(mailer))).status is Status.VERIFIED


async def test_code_expires(service, mailer, clock, cfg):
    await service.start(GUILD, ALICE, "alice@ucsb.edu")
    clock.advance(cfg.code_ttl_minutes * 60 + 1)
    assert (await service.confirm(GUILD, ALICE, last_code(mailer))).status is Status.EXPIRED


async def test_code_is_bound_to_the_requesting_user(service, mailer):
    await service.start(GUILD, ALICE, "alice@ucsb.edu")
    # Bob somehow learns Alice's code — it's useless to him.
    assert (await service.confirm(GUILD, BOB, last_code(mailer))).status is Status.NO_PENDING


async def test_one_email_one_account(service, mailer):
    await service.start(GUILD, ALICE, "alice@ucsb.edu")
    await service.confirm(GUILD, ALICE, last_code(mailer))
    for alias in ["alice@ucsb.edu", "ALICE@ucsb.edu", "alice+alt@ucsb.edu", "alice@umail.ucsb.edu"]:
        r = await service.start(GUILD, BOB, alias)
        assert r.status is Status.EMAIL_IN_USE, alias


async def test_race_two_users_same_email(service, mailer, clock):
    await service.start(GUILD, ALICE, "shared@ucsb.edu")
    code_a = last_code(mailer)
    await service.start(GUILD, BOB, "shared@ucsb.edu")
    code_b = last_code(mailer)
    assert (await service.confirm(GUILD, ALICE, code_a)).status is Status.VERIFIED
    assert (await service.confirm(GUILD, BOB, code_b)).status is Status.EMAIL_IN_USE
    assert service.is_verified(GUILD, BOB) is None


async def test_resend_cooldown_and_new_code_replaces_old(service, mailer, clock, cfg):
    await service.start(GUILD, ALICE, "alice@ucsb.edu")
    old = last_code(mailer)
    r = await service.start(GUILD, ALICE, "alice@ucsb.edu")
    assert r.status is Status.COOLDOWN and 0 < r.retry_after <= cfg.resend_cooldown_seconds
    clock.advance(cfg.resend_cooldown_seconds)
    assert (await service.start(GUILD, ALICE, "alice@ucsb.edu")).status is Status.CODE_SENT
    new = last_code(mailer)
    if old != new:
        assert (await service.confirm(GUILD, ALICE, old)).status is Status.WRONG_CODE
    assert (await service.confirm(GUILD, ALICE, new)).status is Status.VERIFIED


async def test_hourly_rate_limit(service, clock, cfg):
    for _ in range(cfg.max_codes_per_hour):
        assert (await service.start(GUILD, ALICE, "alice@ucsb.edu")).status is Status.CODE_SENT
        clock.advance(cfg.resend_cooldown_seconds)
    r = await service.start(GUILD, ALICE, "alice@ucsb.edu")
    assert r.status is Status.RATE_LIMITED and r.retry_after > 0
    clock.advance(3600)
    assert (await service.start(GUILD, ALICE, "alice@ucsb.edu")).status is Status.CODE_SENT


async def test_send_failure_rolls_back(cfg, clock):
    svc = VerificationService(Store(":memory:"), FailingMailer(), cfg, clock=clock)
    assert (await svc.start(GUILD, ALICE, "alice@ucsb.edu")).status is Status.SEND_FAILED
    assert svc.store.get_pending(GUILD, ALICE) is None
    # Failure didn't trigger the cooldown either.
    assert svc.store.sends_since(GUILD, ALICE, 0) == []


async def test_already_verified(service, mailer):
    await service.start(GUILD, ALICE, "alice@ucsb.edu")
    await service.confirm(GUILD, ALICE, last_code(mailer))
    assert (await service.start(GUILD, ALICE, "alice@ucsb.edu")).status is Status.ALREADY_VERIFIED


async def test_codes_are_stored_hashed(service, mailer):
    await service.start(GUILD, ALICE, "alice@ucsb.edu")
    p = service.store.get_pending(GUILD, ALICE)
    assert last_code(mailer) not in p.code_hash and len(p.code_hash) == 64


async def test_manual_verify_revoke_whois_stats(service, mailer):
    service.manual_verify(GUILD, BOB, by=999, note="faculty advisor")
    v = service.is_verified(GUILD, BOB)
    assert v.method == "manual" and v.verified_by == 999 and v.note == "faculty advisor"

    await service.start(GUILD, ALICE, "alice@ucsb.edu")
    await service.confirm(GUILD, ALICE, last_code(mailer))
    assert service.whois(GUILD, "Alice+x@umail.ucsb.edu").user_id == ALICE
    assert service.stats(GUILD) == {"email": 1, "manual": 1, "pending": 0}

    assert service.revoke(GUILD, ALICE) is True
    assert service.is_verified(GUILD, ALICE) is None
    # After revoke the email is free again (e.g. officer fixing a mistake).
    assert (await service.start(GUILD, BOB + 1, "alice@ucsb.edu")).status is Status.CODE_SENT


async def test_servers_are_independent(service, mailer):
    await service.start(GUILD, ALICE, "alice@ucsb.edu")
    await service.confirm(GUILD, ALICE, last_code(mailer))
    assert service.is_verified(GUILD + 1, ALICE) is None


async def test_persists_across_restart(tmp_path, cfg, mailer, clock):
    db = tmp_path / "v.db"
    svc = VerificationService(Store(db), mailer, cfg, clock=clock)
    await svc.start(GUILD, ALICE, "alice@ucsb.edu")
    code = last_code(mailer)
    svc.store.close()
    svc2 = VerificationService(Store(db), mailer, cfg, clock=clock)  # same SECRET_KEY
    assert (await svc2.confirm(GUILD, ALICE, code)).status is Status.VERIFIED
