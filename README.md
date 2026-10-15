# Assistive Tech Discord Verification Bot

Replaces the Google Form + manual review with instant, automatic verification using a UCSB email.

```
New member joins → sees only #verify → presses "Verify with UCSB email"
   → types jdoe@ucsb.edu → gets a 6-digit code by email → enters it
   → bot gives them the Verified role → rest of the server unlocks (≈ 30 seconds, no officer needed)
```

Strangers can't get in without access to a real UCSB inbox, and officers still get a log of who verified with which email.

## What it handles

| Situation | What happens |
|---|---|
| Gmail / other school / typo'd address | Rejected with a clear message. Only `@ucsb.edu` (and subdomains like `@umail.ucsb.edu`) accepted. |
| One person making alt accounts | Each email unlocks **one** Discord account. `jdoe+alt@ucsb.edu` and `jdoe@umail.ucsb.edu` count as the same person. Attempts are flagged in the officer log. |
| Guessing codes | 5 tries per code, then it's void. Codes expire after 10 min and only work for the account that requested them. |
| Spamming someone's inbox | 60 s between codes, max 5 codes/hour per account. |
| Member leaves and rejoins | Pressing Verify restores their role instantly — no new email. |
| Alumni, advisors, sponsors | Officer runs `/verification manual @person note:"faculty advisor"`. |
| Email server down / bot role misconfigured | Member gets a friendly message; officers get an alert in the log channel. |
| Bot restarts | Verify button keeps working; data is in `verification.db`. |

Codes are stored only as salted hashes. No privileged Discord intents are needed.

## Setup (about 15 minutes)

### 1. Create the bot
1. Go to <https://discord.com/developers/applications> → **New Application** → name it e.g. "ATC Verify".
2. **Bot** tab → **Reset Token** → copy it into `.env` as `DISCORD_TOKEN`. (Leave all "Privileged Gateway Intents" off.)
3. **OAuth2 → URL Generator**: scopes `bot` + `applications.commands`; bot permissions **Manage Roles**, **View Channels**, **Send Messages**, **Embed Links**. Open the generated URL and add the bot to the server.

### 2. Set up the server
Turn on **User Settings → Advanced → Developer Mode** so you can right-click → *Copy ID*.

1. **Verified role** — reuse the role Srihari currently hands out after the form (so existing members are unaffected), or create one. Put its ID in `VERIFIED_ROLE_ID`.
2. **Lock the server** — for every category except the one with #verify: `@everyone` → *View Channel* ❌, `Verified` → *View Channel* ✅.
3. **#verify channel** — `@everyone` can *View Channel* but not *Send Messages*; `Verified` → *View Channel* ❌ (keeps it out of members' sidebar).
4. **Role order** — Server Settings → Roles: drag the bot's role **above** Verified. (Discord won't let a bot assign a role higher than its own; the bot warns about this on startup.)
5. Optional: a private `#verify-log` channel for officers → `LOG_CHANNEL_ID`.

### 3. Email account
Use a club Gmail account (e.g. `atc.discord.bot@gmail.com`) rather than a personal one:
Google Account → Security → turn on 2-Step Verification → **App passwords** → create one → put it in `SMTP_PASSWORD`.
Gmail allows ~500 emails/day, far more than needed. (UCSB-managed accounts often block app passwords, so a regular Gmail is easier.)

### 4. Run it
Requires Python 3.10+.
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env      # then fill it in
python -m verifybot
```
In #verify, an officer runs **`/verification panel`** once to post the Verify button.

## Demo plan (to show it works)
1. Make a throwaway test server and follow the setup above with `EMAIL_BACKEND=console` — codes print in the terminal, so you can demo with no email setup.
2. Join the test server from an alt account: only #verify is visible.
3. Try `someone@gmail.com` → rejected. Try a real `@ucsb.edu` → code appears in the terminal → enter it → channels unlock.
4. From another alt, try the same email → blocked, flagged in `#verify-log`.
5. Switch to `EMAIL_BACKEND=smtp` and repeat with a real inbox.
6. Run the test suite (`pytest`) to show the edge cases: 35 tests covering everything in the table above, including real email delivery through a local SMTP server.

## Commands

**Members:** the panel buttons, or `/verify email:` and `/confirm code:` if they prefer typing.

**Officers** (visible only to people with *Manage Roles*; change under Server Settings → Integrations):

| Command | Purpose |
|---|---|
| `/verification panel` | Post the Verify panel in the current channel |
| `/verification lookup @member` | Which email they verified with, and when |
| `/verification whois email` | Which member used an email |
| `/verification manual @member [note]` | Verify someone without a UCSB email |
| `/verification revoke @member` | Remove verification + role (frees their email) |
| `/verification stats` | Counts of verified/pending members |

## Hosting (later)
Any always-on machine works: a Raspberry Pi, an old laptop, or a free/cheap host (Oracle Cloud free tier, Railway, fly.io). Keep `verification.db` and `.env` on a persistent disk, and set `SECRET_KEY` so in-flight codes survive restarts.

## Project layout
```
verifybot/core.py      verification rules (no Discord code — easy to test)
verifybot/storage.py   SQLite tables: pending codes, send log, verified members
verifybot/mailer.py    SMTP + console email backends
verifybot/bot.py       buttons, pop-ups, slash commands, role assignment
verifybot/messages.py  all member-facing text
tests/                 pytest suite
```

## Open questions for Arjun
- Should non-UCSB people (alumni, other UCs) self-verify via an extra domain, or always go through an officer?
- Is the existing member role the right one to reuse, or do we want a separate Verified role?
- Is storing members' emails OK, or should we store only a hash (still blocks alts, but officers lose `lookup`/`whois`)?
- Should verification also set the member's nickname to their real name (would need a name field)?
