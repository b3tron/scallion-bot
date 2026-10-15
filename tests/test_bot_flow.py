"""Drives the real Discord handlers with mocked Discord objects, end to end."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from verifybot.bot import (CodeModal, EmailModal, EnterCodeView, VerificationCog, VerifyBot,
                           VerifyPanel, start_from_button, submit_code, submit_email)

from conftest import GUILD

pytestmark = pytest.mark.asyncio


class FakeGuild:
    def __init__(self, verified_role, unverified_role=None, log_channel=None):
        self.id = GUILD
        self._roles = {r.id: r for r in (verified_role, unverified_role) if r}
        self._log = log_channel

    def get_role(self, rid):
        return self._roles.get(rid)

    def get_channel(self, cid):
        return self._log if self._log and cid == self._log.id else None


def make_role(rid):
    r = MagicMock(name=f"role{rid}")
    r.id = rid
    return r


def make_member(uid, guild, roles=None):
    m = MagicMock()
    m.id = uid
    m.mention = f"<@{uid}>"
    m.guild = guild
    m.roles = list(roles or [])

    async def add_roles(*rs, reason=None):
        m.roles.extend(rs)

    async def remove_roles(*rs, reason=None):
        for r in rs:
            m.roles.remove(r)

    m.add_roles = AsyncMock(side_effect=add_roles)
    m.remove_roles = AsyncMock(side_effect=remove_roles)
    return m


def make_interaction(member, guild):
    i = MagicMock()
    i.guild_id = guild.id
    i.guild = guild
    i.user = member
    i.response = SimpleNamespace(defer=AsyncMock(), send_modal=AsyncMock(), send_message=AsyncMock())
    i.followup = SimpleNamespace(send=AsyncMock())
    return i


@pytest.fixture
def env(cfg, service):
    cfg.unverified_role_id = 43
    cfg.log_channel_id = 77
    verified, unverified = make_role(42), make_role(43)
    log_channel = MagicMock(id=77, send=AsyncMock())
    guild = FakeGuild(verified, unverified, log_channel)
    bot = SimpleNamespace(cfg=cfg, service=service)
    return SimpleNamespace(bot=bot, guild=guild, verified=verified, unverified=unverified, log=log_channel)


def last_text(mock):
    call = mock.await_args
    return call.args[0] if call.args else call.kwargs.get("content", "")


async def test_full_button_flow(env, mailer):
    member = make_member(5, env.guild, roles=[env.unverified])

    # 1. New member presses "Verify" -> email pop-up opens
    i = make_interaction(member, env.guild)
    await start_from_button(env.bot, i)
    assert isinstance(i.response.send_modal.await_args.args[0], EmailModal)

    # 2. Submits email -> code emailed, private reply with "Enter code" button
    i = make_interaction(member, env.guild)
    await submit_email(env.bot, i, "newbie@ucsb.edu")
    i.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    assert "newbie@ucsb.edu" in last_text(i.followup.send)
    assert i.followup.send.await_args.kwargs["ephemeral"] is True
    assert isinstance(i.followup.send.await_args.kwargs["view"], EnterCodeView)
    assert mailer.outbox[-1][0] == "newbie@ucsb.edu"
    assert env.verified not in member.roles

    # 3. Enters wrong code -> told attempts left, still locked
    i = make_interaction(member, env.guild)
    real = mailer.outbox[-1][1]
    await submit_code(env.bot, i, "000000" if real != "000000" else "111111")
    assert "attempt" in last_text(i.followup.send)
    assert env.verified not in member.roles

    # 4. Enters right code -> gets Verified role, loses Unverified, officers notified
    i = make_interaction(member, env.guild)
    await submit_code(env.bot, i, real)
    assert "verified" in last_text(i.followup.send).lower()
    assert env.verified in member.roles and env.unverified not in member.roles
    assert "newbie@ucsb.edu" in env.log.send.await_args.args[0]


async def test_rejects_gmail_without_role(env):
    member = make_member(6, env.guild)
    i = make_interaction(member, env.guild)
    await submit_email(env.bot, i, "random.seller@gmail.com")
    assert "@ucsb.edu" in last_text(i.followup.send)
    assert "view" not in i.followup.send.await_args.kwargs
    member.add_roles.assert_not_awaited()


async def test_returning_member_gets_role_back_without_email(env, mailer):
    member = make_member(7, env.guild)
    i = make_interaction(member, env.guild)
    await submit_email(env.bot, i, "back@ucsb.edu")
    await submit_code(env.bot, make_interaction(member, env.guild), mailer.outbox[-1][1])

    # They leave and rejoin: roles are gone, they press Verify again.
    rejoined = make_member(7, env.guild)
    i = make_interaction(rejoined, env.guild)
    n_emails = len(mailer.outbox)
    await start_from_button(env.bot, i)
    i.response.send_modal.assert_not_awaited()
    assert "welcome back" in last_text(i.response.send_message).lower()
    assert env.verified in rejoined.roles and len(mailer.outbox) == n_emails


async def test_alt_account_flagged_to_officers(env, mailer):
    main = make_member(8, env.guild)
    await submit_email(env.bot, make_interaction(main, env.guild), "person@ucsb.edu")
    await submit_code(env.bot, make_interaction(main, env.guild), mailer.outbox[-1][1])

    alt = make_member(9, env.guild)
    i = make_interaction(alt, env.guild)
    await submit_email(env.bot, i, "person+alt@ucsb.edu")
    assert "already linked" in last_text(i.followup.send)
    assert "<@8>" in env.log.send.await_args.args[0]


async def test_role_permission_problem_is_reported(env, mailer):
    member = make_member(10, env.guild)
    resp = MagicMock(status=403, reason="Forbidden")
    member.add_roles = AsyncMock(side_effect=discord.Forbidden(resp, "Missing Permissions"))
    await submit_email(env.bot, make_interaction(member, env.guild), "x@ucsb.edu")
    i = make_interaction(member, env.guild)
    await submit_code(env.bot, i, mailer.outbox[-1][1])
    assert "permission" in last_text(i.followup.send).lower()
    assert "role grant failed" in env.log.send.await_args.args[0]
    # Self-heals: once fixed, pressing Verify re-grants without a new email.
    member.add_roles = AsyncMock()
    i = make_interaction(member, env.guild)
    await start_from_button(env.bot, i)
    member.add_roles.assert_awaited_once()


async def test_ui_components_build(env):
    for modal in (EmailModal(env.bot), CodeModal(env.bot)):
        payload = modal.to_dict()
        assert payload["title"] and len(payload["title"]) <= 45
        assert payload["components"][0]["type"] == 18  # Discord "Label" component
    assert CodeModal(env.bot).code.max_length == 8
    panel = VerifyPanel(env.bot)
    assert panel.is_persistent()
    assert {c.custom_id for c in panel.children} == {"atc-verify:start", "atc-verify:code"}


async def test_slash_commands_register(cfg, service):
    bot = VerifyBot(cfg, service)
    await bot.add_cog(VerificationCog(bot))
    bot.add_view(VerifyPanel(bot))
    top = {c.name: c for c in bot.tree.get_commands()}
    assert {"verify", "confirm", "verification"} <= set(top)
    officer = top["verification"]
    assert {c.name for c in officer.commands} == {"panel", "lookup", "whois", "manual", "revoke", "stats"}
    assert officer.default_permissions.manage_roles
    # Payload Discord would receive is valid JSON-able
    assert all(cmd.to_dict(bot.tree) for cmd in top.values())
