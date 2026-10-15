"""Sends a real email through a local SMTP server to prove the SMTP path works."""
import socket

import pytest

aiosmtpd = pytest.importorskip("aiosmtpd")
from aiosmtpd.controller import Controller  # noqa: E402

from verifybot.core import Status, VerificationService  # noqa: E402
from verifybot.mailer import SmtpMailer  # noqa: E402
from verifybot.storage import Store  # noqa: E402

from conftest import GUILD  # noqa: E402

pytestmark = pytest.mark.asyncio


class Inbox:
    def __init__(self):
        self.messages = []

    async def handle_DATA(self, server, session, envelope):
        self.messages.append(envelope)
        return "250 OK"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_code_arrives_by_smtp(cfg, clock):
    inbox = Inbox()
    port = free_port()
    ctl = Controller(inbox, hostname="127.0.0.1", port=port)
    ctl.start()
    try:
        mailer = SmtpMailer(host="127.0.0.1", port=port, user="", password="",
                            sender="atc.discord@gmail.com", starttls=False)
        svc = VerificationService(Store(":memory:"), mailer, cfg, clock=clock)
        assert (await svc.start(GUILD, 1, "student@ucsb.edu")).status is Status.CODE_SENT
    finally:
        ctl.stop()

    assert len(inbox.messages) == 1
    env = inbox.messages[0]
    assert env.rcpt_tos == ["student@ucsb.edu"]
    body = env.content.decode()
    import re
    code = re.search(r"Subject: (\d{6}) is your", body).group(1)
    assert (await svc.confirm(GUILD, 1, code)).status is Status.VERIFIED
