import logging

from longshot import redact
from longshot.clients.odds import OddsApiError

# Assembled at runtime so that no key-shaped string sits in the repository.
FAKE_KEY = "0123456789abcdef" * 2
URL = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds?apiKey=" + FAKE_KEY + "&markets=h2h"


def test_api_key_parameter_is_removed_from_a_url():
    cleaned = redact.redact(URL)
    assert FAKE_KEY not in cleaned
    assert "apiKey=[redacted]&markets=h2h" in cleaned


def test_registered_secret_is_removed_wherever_it_appears():
    redact.register_secret("super-secret-token-value")
    assert redact.redact("login failed for super-secret-token-value") == "login failed for [redacted]"


def test_odds_api_error_never_carries_the_key():
    assert FAKE_KEY not in str(OddsApiError(f"Cannot connect to host: {URL}"))


def test_log_records_are_scrubbed(caplog):
    logger = logging.getLogger("longshot.test")
    handler = logging.Handler()
    handler.addFilter(redact.RedactingFilter())
    seen = []
    handler.emit = lambda record: seen.append(record.getMessage())
    logger.addHandler(handler)
    try:
        logger.warning("request to %s failed", URL)
    finally:
        logger.removeHandler(handler)
    assert seen and FAKE_KEY not in seen[0]
