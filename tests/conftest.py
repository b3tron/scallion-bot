import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from verifybot.config import Config  # noqa: E402
from verifybot.core import VerificationService  # noqa: E402
from verifybot.mailer import ConsoleMailer  # noqa: E402
from verifybot.storage import Store  # noqa: E402

GUILD = 1111


class Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FailingMailer:
    async def send_code(self, to, code):
        raise ConnectionError("SMTP down")


@pytest.fixture
def cfg():
    return Config(secret_key="test-secret", verified_role_id=42)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def mailer():
    return ConsoleMailer()


@pytest.fixture
def service(cfg, mailer, clock):
    return VerificationService(Store(":memory:"), mailer, cfg, clock=clock)
