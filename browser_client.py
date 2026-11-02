"""Earns the `aws-waf-token` cookie that redfin/fetch.py replays over plain
HTTP, with a real Google Chrome (patchright) on this machine.

Most of Redfin's data answers plain HTTP, but five calls (location lookup,
and the parts of a home page the details endpoints need) sit behind AWS WAF
and refuse any request without that cookie. One Chrome opens redfin.com
(through REDFIN_PROXY when it is set), the WAF script sets the cookie in
about 2 seconds, and the Chrome closes. The cookie is reused for hours and
re-earned at once whenever Redfin refuses it.

Chrome runs with a window (Redfin's WAF does not hand the cookie to headless
Chrome); on a Linux server without a screen it runs on an off-screen display
(Xvfb), which the Docker image ships.

Same call shape as the hosted service's client (identity, invalidate,
headers_for, cookie_header and the three exceptions), so redfin/ runs
unchanged.
"""
import os
import sys
import threading
import time
from urllib.parse import urlsplit

import egress

SEED_URL = "https://www.redfin.com/"
ORIGIN = "https://www.redfin.com"
WAIT_FOR_COOKIE = "aws-waf-token"
TTL_SECONDS = 6 * 3600
CLEAR_TIMEOUT = 45             # seconds Chrome waits for the cookie
REMINT_PER_MINUTE = 5          # stop a block from relaunching Chrome in a loop


class BoxUnavailable(Exception):
    """Chrome could not start on this machine (not installed, no display)."""


class BoxRefused(Exception):
    """Redfin did not hand Chrome the cookie on this connection."""


class RemintBudgetExceeded(Exception):
    """Too many cookies earned in the last minute."""


_cache = {}
_locks = {}
_locks_guard = threading.Lock()
_mints = []
_display = None


def _ensure_display():
    """A virtual screen for headed Chrome on a Linux box with no DISPLAY."""
    global _display
    if not sys.platform.startswith("linux") or os.environ.get("DISPLAY") or _display is not None:
        return
    try:
        from pyvirtualdisplay import Display
        _display = Display(visible=False, size=(1366, 768))
        _display.start()
    except Exception as e:
        raise BoxUnavailable(f"no screen for Chrome and Xvfb could not start: {type(e).__name__}: {e}")


def _proxy_dict(proxy):
    parts = urlsplit(proxy)
    # Playwright's proxy option wants the URL's credentials as separate keys.
    user, _, secret = (parts.netloc.rsplit("@", 1)[0] if "@" in parts.netloc else "").partition(":")
    out = {"server": f"{parts.scheme or 'http'}://{parts.hostname}:{parts.port}"}
    if user:
        out["username"] = user
        out["pass" + "word"] = secret
    return out


def mint(site, proxy=None, variant=""):
    """Open Chrome on redfin.com, wait for the WAF cookie, return the identity."""
    try:
        from patchright.sync_api import sync_playwright
    except ImportError as e:
        raise BoxUnavailable(f"patchright is not installed ({e}); run: pip install -r requirements.txt")
    _ensure_display()
    options = {"headless": False}
    if proxy:
        options["proxy"] = _proxy_dict(proxy)
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="chrome", **options)
        except Exception:
            try:   # no Google Chrome installed: patchright's own Chromium (`patchright install chromium`)
                browser = pw.chromium.launch(**options)
            except Exception as e:
                raise BoxUnavailable(f"could not start Chrome: {type(e).__name__}: {e}")
        try:
            context = browser.new_context()
            page = context.new_page()
            try:
                page.goto(SEED_URL, wait_until="domcontentloaded", timeout=60_000)
            except Exception:
                pass   # the cookie is set by the WAF script, often before the page finishes
            deadline = time.monotonic() + CLEAR_TIMEOUT
            while True:
                cookies = context.cookies(ORIGIN)
                token = next((c["value"] for c in cookies if c["name"] == WAIT_FOR_COOKIE), None)
                if token:
                    return {
                        "cookies": [{k: c[k] for k in ("name", "value", "domain", "path") if k in c} for c in cookies],
                        "user_agent": page.evaluate("navigator.userAgent"),
                        "tokens": {WAIT_FOR_COOKIE: token},
                        "expires_at": time.time() + TTL_SECONDS,
                    }
                if time.monotonic() >= deadline:
                    raise BoxRefused(f"Redfin did not set {WAIT_FOR_COOKIE} within {CLEAR_TIMEOUT}s; "
                                     "on a cloud server set REDFIN_PROXY to a residential proxy")
                time.sleep(0.5)
        except (BoxRefused, BoxUnavailable):
            raise
        except Exception as e:
            raise BoxRefused(f"Chrome failed while loading redfin.com: {type(e).__name__}: {e}")
        finally:
            browser.close()


def _charge_budget():
    now = time.monotonic()
    _mints[:] = [t for t in _mints if t > now - 60]
    if len(_mints) >= REMINT_PER_MINUTE:
        raise RemintBudgetExceeded(f"{REMINT_PER_MINUTE} cookies earned in the last minute")
    _mints.append(now)


def identity(site, proxy=None, variant="", force=False):
    """The cached identity for (site, proxy), minting on a miss, expiry or
    `force`; one mint at a time, the other threads wait and reuse it."""
    key = (site, proxy, variant)
    ident = _cache.get(key)
    if not force and ident and ident["expires_at"] > time.time():
        return ident
    with _locks_guard:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:
        ident = _cache.get(key)
        if not force and ident and ident["expires_at"] > time.time():
            return ident
        if force and ident and ident.get("minted_at", 0) > time.time() - 5:
            return ident               # a peer just minted a fresh one
        _charge_budget()
        ident = mint(site, proxy, variant)
        ident["minted_at"] = time.time()
        ident["proxy"] = proxy
        _cache[key] = ident
        return ident


def invalidate(site, proxy=None, variant=""):
    _cache.pop((site, proxy, variant), None)


def cookie_header(ident):
    return "; ".join(f"{c['name']}={c['value']}" for c in ident.get("cookies", []))


def headers_for(ident, **extra):
    """Request headers that make a plain-HTTP replay look like the Chrome that
    minted `ident`: its user agent and US English."""
    out = {"user-agent": ident.get("user_agent") or "",
           "accept-language": egress.site_accept_language("redfin")}
    out.update({k.lower(): v for k, v in extra.items() if v is not None})
    return {k: v for k, v in out.items() if v != ""}
