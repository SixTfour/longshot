"""Start the bot: python -m longshot"""

from __future__ import annotations

import logging
import sys

import discord

from . import redact
from .bot import Longshot
from .config import ConfigError, load_config


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    redact.install()
    try:
        config = load_config()
        bot = Longshot(config)
        bot.run(config.discord_token, log_handler=None)
    except ConfigError as error:
        print(f"Configuration problem: {error}", file=sys.stderr)
        return 1
    except ValueError as error:  # a malformed DEFAULT_* setting
        print(f"Configuration problem: {redact.redact(error)}", file=sys.stderr)
        return 1
    except discord.LoginFailure:
        print("Discord rejected DISCORD_TOKEN. Reset it in the Developer Portal and update .env.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
