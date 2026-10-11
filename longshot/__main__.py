"""Start the bot: python -m longshot"""

from __future__ import annotations

import logging
import sys

import discord

from . import redact
from .bot import Longshot
from .config import ConfigError, load_config


# Returned for anything a person has to fix in .env or in Discord. The systemd unit does not
# restart on it, so a misconfigured bot does not log in to Discord over and over.
NEEDS_ATTENTION = 2


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    redact.install()
    try:
        config = load_config()
        bot = Longshot(config)
        bot.run(config.discord_token, log_handler=None)
    except ConfigError as error:
        print(f"Configuration problem: {error}", file=sys.stderr)
        return NEEDS_ATTENTION
    except ValueError as error:  # a malformed DEFAULT_* setting
        print(f"Configuration problem: {redact.redact(error)}", file=sys.stderr)
        return NEEDS_ATTENTION
    except discord.LoginFailure:
        print("Discord rejected DISCORD_TOKEN. Reset it in the Developer Portal and update .env.", file=sys.stderr)
        return NEEDS_ATTENTION
    return 0


if __name__ == "__main__":
    sys.exit(main())
