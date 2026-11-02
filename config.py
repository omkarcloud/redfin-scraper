"""Configuration for the Redfin Scraper. Everything can be set with an
environment variable; the defaults work out of the box.

    PORT              port the API listens on (default 8000)
    REDFIN_PROXY      proxy URL for every request and for the Chrome that
                      earns Redfin's access cookie, e.g. http://user:pass@host:port
                      (default: none — direct). From a home or office
                      connection you don't need one. Set it only if Redfin
                      starts refusing your IP at high volume.
    REDFIN_WAF_TOKEN  optional: an `aws-waf-token` cookie value copied from
                      your own browser. When set, the scraper uses it instead
                      of opening Chrome to earn one.

Everything else below is a plain constant with a working default — edit it
here if you need to.
"""
import os

PORT = int(os.environ.get("PORT", "8000"))

# Retry policy for transport errors and blocks (every request).
MAX_RETRIES = 3
RETRY_BACKOFF = 2          # seconds, multiplied by the attempt number

# After a block, a request thread keeps using REDFIN_PROXY's route for this
# many seconds (with no proxy set, the retry simply opens a fresh connection).
REDFIN_BLOCK_HOLD_S = 600
# How long one Chrome-earned access cookie is reused before a fresh one is
# earned (a cookie stayed valid for 40 hours in testing; a refusal re-earns
# it at once anyway).
REDFIN_TOKEN_TTL_S = 6 * 3600

REDFIN_PROXY = os.environ.get("REDFIN_PROXY") or None


def redfin_proxy():
    """The proxy for one plain-HTTP session (None = direct)."""
    return os.environ.get("REDFIN_PROXY") or None


def redfin_cooldown_proxy():
    """The proxy a session uses right after a block (None = direct)."""
    return os.environ.get("REDFIN_PROXY") or None


def redfin_mint_proxy():
    """The proxy the Chrome that earns the access cookie uses (None = direct)."""
    return os.environ.get("REDFIN_PROXY") or None
