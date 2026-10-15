"""Run with:  python -m verifybot"""
import logging
import sys

from .bot import VerifyBot
from .config import Config
from .core import VerificationService
from .mailer import mailer_from_config
from .storage import Store


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    cfg = Config.from_env()
    problems = cfg.problems()
    if problems:
        print("Can't start — fix these in your .env file:\n  - " + "\n  - ".join(problems))
        sys.exit(1)
    if cfg.email_backend == "console":
        logging.getLogger("verifybot").warning(
            "EMAIL_BACKEND=console: codes are printed here instead of emailed. Use only for testing!")

    service = VerificationService(Store(cfg.db_path), mailer_from_config(cfg), cfg)
    bot = VerifyBot(cfg, service)
    bot.run(cfg.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
