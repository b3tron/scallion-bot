"""Discord layer: buttons, pop-up forms, slash commands, role assignment."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from .config import Config
from .core import Status, VerificationService
from .messages import allowed_domains_text, describe

log = logging.getLogger("verifybot")

PANEL_VERIFY_ID = "atc-verify:start"
PANEL_CODE_ID = "atc-verify:code"


# ============================================================== shared actions
async def audit(bot: "VerifyBot", guild: discord.Guild | None, text: str) -> None:
    """Post to the private officer log channel, if configured."""
    log.info("[audit] %s", text)
    if not guild or not bot.cfg.log_channel_id:
        return
    channel = guild.get_channel(bot.cfg.log_channel_id)
    if channel is None:
        return
    try:
        await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException as exc:
        log.warning("Could not write to log channel: %s", exc)


async def grant_role(bot: "VerifyBot", member: discord.Member, reason: str) -> str | None:
    """Give the Verified role (and drop Unverified). Returns an error string or None."""
    guild = member.guild
    role = guild.get_role(bot.cfg.verified_role_id) if bot.cfg.verified_role_id else None
    if role is None:
        return "The Verified role is missing — an officer needs to fix the bot's VERIFIED_ROLE_ID."
    try:
        if role not in member.roles:
            await member.add_roles(role, reason=reason)
        if bot.cfg.unverified_role_id:
            unverified = guild.get_role(bot.cfg.unverified_role_id)
            if unverified and unverified in member.roles:
                await member.remove_roles(unverified, reason=reason)
    except discord.Forbidden:
        return ("I don't have permission to give you the role. An officer needs to move my bot role "
                "above the Verified role and give me Manage Roles.")
    except discord.HTTPException as exc:
        log.exception("add_roles failed")
        return f"Discord returned an error while giving you the role ({exc.status}). Please try again."
    return None


async def start_from_button(bot: "VerifyBot", interaction: discord.Interaction) -> None:
    """Verify button: re-grant returning members, otherwise open the email form."""
    existing = bot.service.is_verified(interaction.guild_id, interaction.user.id)
    if existing:
        err = await grant_role(bot, interaction.user, "Returning verified member")
        msg = err or "✅ Welcome back — you were already verified, so your access has been restored."
        await interaction.response.send_message(msg, ephemeral=True)
        return
    await interaction.response.send_modal(EmailModal(bot))


async def submit_email(bot: "VerifyBot", interaction: discord.Interaction, email: str) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    result = await bot.service.start(interaction.guild_id, interaction.user.id, email)

    if result.status is Status.ALREADY_VERIFIED:
        err = await grant_role(bot, interaction.user, "Already verified")
        await interaction.followup.send(err or describe(result, bot.cfg), ephemeral=True)
        return
    if result.status is Status.SEND_FAILED:
        await audit(bot, interaction.guild,
                    f"⚠️ Email delivery failed for {interaction.user.mention} ({result.email}). Check SMTP settings.")
    if result.status is Status.EMAIL_IN_USE:
        owner = bot.service.whois(interaction.guild_id, result.email)
        await audit(bot, interaction.guild,
                    f"🚩 {interaction.user.mention} tried to use **{result.email}**, already linked to "
                    f"<@{owner.user_id if owner else '?'}>.")

    view = EnterCodeView(bot) if result.status in {Status.CODE_SENT, Status.COOLDOWN} else None
    kwargs = {"view": view} if view else {}
    await interaction.followup.send(describe(result, bot.cfg), ephemeral=True, **kwargs)


async def submit_code(bot: "VerifyBot", interaction: discord.Interaction, code: str) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    result = await bot.service.confirm(interaction.guild_id, interaction.user.id, code)

    if result.status in {Status.VERIFIED, Status.ALREADY_VERIFIED}:
        err = await grant_role(bot, interaction.user, f"Verified via email {result.email or ''}".strip())
        if err:
            await audit(bot, interaction.guild, f"⚠️ {interaction.user.mention} verified but role grant failed: {err}")
            await interaction.followup.send(f"Your email is confirmed, but: {err}", ephemeral=True)
            return
        if result.status is Status.VERIFIED:
            await audit(bot, interaction.guild, f"✅ {interaction.user.mention} verified as **{result.email}**")
        await interaction.followup.send(describe(result, bot.cfg), ephemeral=True)
        return

    view = EnterCodeView(bot) if result.status in {Status.WRONG_CODE, Status.INVALID_CODE_FORMAT} else None
    kwargs = {"view": view} if view else {}
    await interaction.followup.send(describe(result, bot.cfg), ephemeral=True, **kwargs)


# ============================================================== UI components
class EmailModal(discord.ui.Modal):
    def __init__(self, bot: "VerifyBot"):
        super().__init__(title=f"Verify for {bot.cfg.club_name}"[:45])
        self.bot = bot
        self.email = discord.ui.TextInput(
            placeholder=f"yourname{('@' + bot.cfg.allowed_domains[0]) if bot.cfg.allowed_domains else ''}",
            min_length=5, max_length=254,
        )
        self.add_item(discord.ui.Label(
            text="Your UCSB email", component=self.email,
            description="I'll email you a 6-digit code. Only officers can see your address."))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await submit_email(self.bot, interaction, self.email.value)


class CodeModal(discord.ui.Modal):
    def __init__(self, bot: "VerifyBot"):
        super().__init__(title="Enter your verification code")
        self.bot = bot
        self.code = discord.ui.TextInput(placeholder="123456", min_length=6, max_length=8)
        self.add_item(discord.ui.Label(
            text="6-digit code from your email", component=self.code,
            description="Check Spam/Promotions if you don't see it."))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await submit_code(self.bot, interaction, self.code.value)


class EnterCodeView(discord.ui.View):
    """Buttons attached to the private 'code sent' message."""

    def __init__(self, bot: "VerifyBot"):
        super().__init__(timeout=bot.cfg.code_ttl_minutes * 60)
        self.bot = bot

    @discord.ui.button(label="Enter code", style=discord.ButtonStyle.success, emoji="🔑")
    async def enter_code(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(CodeModal(self.bot))

    @discord.ui.button(label="Use a different email / resend", style=discord.ButtonStyle.secondary)
    async def resend(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(EmailModal(self.bot))


class VerifyPanel(discord.ui.View):
    """Persistent panel posted in #verify. Keeps working after bot restarts."""

    def __init__(self, bot: "VerifyBot"):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Verify with UCSB email", style=discord.ButtonStyle.primary,
                       emoji="✉️", custom_id=PANEL_VERIFY_ID)
    async def verify(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await start_from_button(self.bot, interaction)

    @discord.ui.button(label="I have a code", style=discord.ButtonStyle.secondary,
                       emoji="🔑", custom_id=PANEL_CODE_ID)
    async def have_code(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(CodeModal(self.bot))


def panel_embed(cfg: Config) -> discord.Embed:
    embed = discord.Embed(
        title=f"Welcome to {cfg.club_name}! 👋",
        description=(
            "To keep this server safe for members, we verify everyone with a UCSB email.\n\n"
            f"**1.** Press **Verify with UCSB email** and enter your {allowed_domains_text(cfg)} address\n"
            "**2.** We'll email you a 6-digit code\n"
            "**3.** Press **Enter code** — you'll get access instantly\n\n"
            "Not a UCSB student (alumni, advisor, sponsor)? Message an officer and they'll verify you."
        ),
        color=0x003660,  # UCSB navy
    )
    embed.set_footer(text="Your email is only visible to club officers and is never shared.")
    return embed


# ============================================================== slash commands
def _fmt_time(ts: float) -> str:
    return discord.utils.format_dt(datetime.fromtimestamp(ts, tz=timezone.utc), "f")


class VerificationCog(commands.Cog):
    def __init__(self, bot: "VerifyBot"):
        self.bot = bot

    # ---- member commands (fallback for people who prefer typing) ----
    @app_commands.command(name="verify", description="Verify with your UCSB email to unlock the server")
    @app_commands.describe(email="Your UCSB email address")
    @app_commands.guild_only()
    async def verify(self, interaction: discord.Interaction, email: str | None = None) -> None:
        if email is None:
            await start_from_button(self.bot, interaction)
        else:
            await submit_email(self.bot, interaction, email)

    @app_commands.command(name="confirm", description="Enter the 6-digit code we emailed you")
    @app_commands.describe(code="The code from your email")
    @app_commands.guild_only()
    async def confirm(self, interaction: discord.Interaction, code: str) -> None:
        await submit_code(self.bot, interaction, code)

    # ---- officer commands (hidden from members without Manage Roles) ----
    officer = app_commands.Group(
        name="verification", description="Officer tools for member verification",
        guild_only=True, default_permissions=discord.Permissions(manage_roles=True),
    )

    @officer.command(name="panel", description="Post the Verify button panel in this channel")
    async def panel(self, interaction: discord.Interaction) -> None:
        await interaction.channel.send(embed=panel_embed(self.bot.cfg), view=VerifyPanel(self.bot))
        await interaction.response.send_message("Panel posted.", ephemeral=True)

    @officer.command(name="lookup", description="Show how a member was verified")
    async def lookup(self, interaction: discord.Interaction, member: discord.Member) -> None:
        v = self.bot.service.is_verified(interaction.guild_id, member.id)
        if not v:
            msg = f"{member.mention} is **not verified**."
        elif v.method == "email":
            msg = f"{member.mention} verified as **{v.email}** on {_fmt_time(v.verified_at)}."
        else:
            msg = (f"{member.mention} was manually verified by <@{v.verified_by}> on "
                   f"{_fmt_time(v.verified_at)}" + (f" — note: {v.note}" if v.note else "") + ".")
        await interaction.response.send_message(msg, ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())

    @officer.command(name="whois", description="Find which member verified with an email")
    async def whois(self, interaction: discord.Interaction, email: str) -> None:
        v = self.bot.service.whois(interaction.guild_id, email)
        msg = (f"**{email}** belongs to <@{v.user_id}> (verified {_fmt_time(v.verified_at)})."
               if v else f"No member has verified with **{email}**.")
        await interaction.response.send_message(msg, ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())

    @officer.command(name="manual", description="Verify someone without a UCSB email")
    @app_commands.describe(note="Who they are, e.g. 'alumni 2025' or 'faculty advisor'")
    async def manual(self, interaction: discord.Interaction, member: discord.Member,
                     note: str | None = None) -> None:
        self.bot.service.manual_verify(interaction.guild_id, member.id, interaction.user.id, note)
        err = await grant_role(self.bot, member, f"Manually verified by {interaction.user}")
        await audit(self.bot, interaction.guild,
                    f"🛠️ {interaction.user.mention} manually verified {member.mention}"
                    + (f" — {note}" if note else ""))
        await interaction.response.send_message(err or f"✅ {member.mention} is verified.", ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())

    @officer.command(name="revoke", description="Remove a member's verification (and the role)")
    async def revoke(self, interaction: discord.Interaction, member: discord.Member) -> None:
        had = self.bot.service.revoke(interaction.guild_id, member.id)
        role = interaction.guild.get_role(self.bot.cfg.verified_role_id or 0)
        try:
            if role and role in member.roles:
                await member.remove_roles(role, reason=f"Verification revoked by {interaction.user}")
        except discord.HTTPException:
            pass
        await audit(self.bot, interaction.guild, f"🗑️ {interaction.user.mention} revoked {member.mention}")
        await interaction.response.send_message(
            f"Revoked {member.mention}." if had else f"{member.mention} wasn't verified; role removed if present.",
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @officer.command(name="stats", description="How many members are verified")
    async def stats(self, interaction: discord.Interaction) -> None:
        s = self.bot.service.stats(interaction.guild_id)
        await interaction.response.send_message(
            f"Verified by email: **{s['email']}** · manually: **{s['manual']}** · codes pending: **{s['pending']}**",
            ephemeral=True)


# ============================================================== bot
class VerifyBot(commands.Bot):
    def __init__(self, cfg: Config, service: VerificationService):
        # No privileged intents needed: role changes happen on the member who clicked.
        super().__init__(command_prefix=commands.when_mentioned, intents=discord.Intents.default())
        self.cfg = cfg
        self.service = service

    async def setup_hook(self) -> None:
        self.add_view(VerifyPanel(self))  # re-attach persistent buttons after restart
        await self.add_cog(VerificationCog(self))
        if self.cfg.guild_id:
            guild = discord.Object(id=self.cfg.guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
        else:
            synced = await self.tree.sync()  # global sync can take a while to appear
        log.info("Synced %d slash commands", len(synced))

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id %s)", self.user, self.user.id)
        for guild in self.guilds:
            role = guild.get_role(self.cfg.verified_role_id or 0)
            if role is None:
                log.warning("[%s] Verified role %s not found!", guild.name, self.cfg.verified_role_id)
            elif guild.me.top_role <= role:
                log.warning("[%s] My highest role must be ABOVE '%s' in Server Settings → Roles, "
                            "or I can't assign it.", guild.name, role.name)
            elif not guild.me.guild_permissions.manage_roles:
                log.warning("[%s] I need the Manage Roles permission.", guild.name)
            else:
                log.info("[%s] Ready — will grant role '%s'.", guild.name, role.name)
