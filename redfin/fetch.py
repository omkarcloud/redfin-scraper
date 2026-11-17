"""Redfin transport: plain curl_cffi (browser-impersonated TLS) against the
site's own `/stingray/*` JSON, with ONE box-minted AWS WAF token for the
five calls CloudFront refuses without it. No browser on GKE.

Pod probe 2026-10-31 (tools/egress_probe.py --via-pod, pod
scrapers-patchright-depl-68b5779f9c-7wt8m; table in config.py's
"--- Redfin ---" block): every open surface is 200 from the pod's own IP
and from 2/2 datacenter and 2/2 residential ports; the two token calls
(aboveTheFold, location-autocomplete) are CloudFront 403 "Request blocked"
on every tier and every TLS profile (chrome / safari17_0 / edge101 on the
Mac, 2026-10-30) until the `aws-waf-token` cookie rides along.

Surfaces (all GET, envelope `{}&&{"version","errorMessage","resultCode",
"payload"}` unless noted; `resultCode` 0 = success):

  OPEN (no cookie, any exit)
    /stingray/api/gis                      sale / sold search (paginate with
                                           `start`; `num_homes` honoured up to
                                           thousands, 50 here)
    /stingray/api/v1/search/rentals        rentals search (plain JSON, no
                                           envelope; `numMatchedHomes`)
    /stingray/api/region/<t>/<id>/1/region-breadcrumbs   region validation
    /stingray/api/v1/home/details/belowTheFold (+ /schoolsAndDistrictsInfo,
        /riskFactorData), /home/details/{avm, avmHistoricalData,
        propertyParcelInfo, rental-estimate, marketInsightsInfo,
        similars/listings, popularityInfo, hotMarketInfo, tourInsights,
        neighborhoodStats/statsInfo, listingStatusBannerInfo/v1,
        property-neighborhood-info, monthly-weather-averages, web/parcel-zoning},
        /v1/home/details/{location-score, sun-exposure}, /v2/home/details/permits
    /stingray/api/v1/rentals/homecards?propertyIds=   rental building card
    /stingray/api/v1/rentals/<rentalId>/{about, floorPlans, feesAndPolicies,
        amenities, contacts, mediaBrowserInfo}  (plain JSON)
    /stingray/agents/data/agent-profile/<slug>/{url, rollup, phone,
        review-counts, latest-reviews/<n>}/get, /stingray/agents/data/<slug>/
        {agent-stats, gishomes/sell, gishomes/buy, deals/sell/<n>, deals/buy/<n>}/get
    /stingray/real-estate-agents/<slug>/all-reviews
    /stingray/agents/data/radp/<word>/<id>/filterable-agents-data/V3  (plain JSON)
    /stingray/api/market-insights-page, /region/<t>/<id>/1/{trends, affordability,
        best-schools, walk-score-data}, /region/<t>/<id>/{compete-score,
        climate-data}, /graph/<t>/<id>/All/regional-housing-market/{home_prices,
        demand}, /gis/recently-sold

  TOKEN (aws-waf-token cookie; recipes/redfin.py mints it on the Ubuntu box
  through a US residential exit — datacenter exits and the office IP get
  the WAF CAPTCHA page (405) instead of the self-solving challenge; the
  token is host-bound, NOT IP-bound: a Mac-minted token answered 200 from
  another residential exit, from the Mac's own IP and from the pod, and
  one token stayed valid for the full 19-minute probe of 2026-10-30)
    /stingray/do/location-autocomplete     free text / ZIP / address -> ids
    /stingray/api/home/details/initialInfo path -> propertyId + listingId
    /stingray/api/home/details/aboveTheFold
    /stingray/api/home/details/mainHouseInfoPanelInfo

Egress: config.redfin_proxy() (knob REDFIN_EXIT): the pod's own IP; a
blocked thread moves to a sticky datacenter exit for REDFIN_BLOCK_HOLD_S
(the office IP drew a per-IP 403 on /avm after a day of probing while
datacenter and residential exits stayed 200, 2026-10-31). One curl session
per thread; any retryable failure drops it so the retry rides a fresh
connection / exit.

Failure taxonomy (scraper_errors, mapped to HTTP by route_glue):
  RedfinUpstreamError  transport failure / 5xx / bad envelope  — retryable
  RedfinBlocked        403 / 429 / WAF challenge or CAPTCHA    — retryable
  RedfinBadRequest     upstream rejected the params           — never retried
  RedfinNotFound       unknown property / region / agent      — never retried
"""
import fastjson
import os
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait as wait_futures

# Allow direct execution (python redfin/fetch.py): flat imports resolve like
# under the server. Idempotent when imported normally.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import browser_client
import config
import egress
import identity_pool
from browser_client import BoxRefused, BoxUnavailable
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError
from redfin.refs import SITE
from scraper_utils import sessions

IMPERSONATE = "chrome"
TIMEOUT = 40            # the agent directory is ~145 KB, a 50-home search ~130 KB
FANOUT_WORKERS = 6      # parallel upstream calls for the stitched endpoints
WAF_COOKIE = "aws-waf-token"
BOX_SITE = "redfin"     # recipes/redfin.py
ENVELOPE_PREFIX = "{}&&"

JSON_HEADERS = {
    "accept": "*/*",
    "accept-language": egress.site_accept_language("redfin"),
    "referer": SITE + "/",
}


class RedfinUpstreamError(UpstreamError):
    """Transport failure, 5xx or an unreadable envelope — retryable."""


class RedfinBlocked(RedfinUpstreamError, Blocked):
    """CloudFront 403 / 429 / WAF challenge page — retryable on a fresh exit (and a fresh token)."""


class RedfinBadRequest(BadRequest):
    """Upstream rejected the params — never retried."""


class RedfinNotFound(NotFound):
    """Property / region / agent / rental does not exist — never retried."""


# ---- sessions ------------------------------------------------------------------
# One curl session per worker thread (a curl handle must not be shared across
# threads). A thread that was blocked on the pod's own IP rides a sticky
# datacenter exit for config.REDFIN_BLOCK_HOLD_S, then returns to direct.
_local = threading.local()


def _session():
    sess = getattr(_local, "session", None)
    if sess is None:
        from curl_cffi import requests as curl_requests
        sess = curl_requests.Session(impersonate=IMPERSONATE)
        proxy = _proxy_for_thread()
        if proxy:
            sess.proxies = {"http": proxy, "https": proxy}
        sess.redfin_proxy = proxy
        _local.session = sess
    return sess


def _proxy_for_thread():
    blocked_until = getattr(_local, "blocked_until", 0.0)
    if blocked_until > time.monotonic():
        return config.redfin_cooldown_proxy()
    return config.redfin_proxy()


def _drop_session():
    """Close the thread's session so a poisoned keep-alive / exit dies."""
    sessions.drop(_local)


def _note_block():
    """A block moves this thread to the cooldown exit for a while."""
    _local.blocked_until = time.monotonic() + config.REDFIN_BLOCK_HOLD_S


# ---- the AWS WAF token (shared, box-minted) ---------------------------------------
# One process-wide token in an identity_pool.Pool (shared mode): the recipe's
# `expires_at` bounds its age, a 403 on a token call replaces it once per
# block wave (get(replace=used): peers blocked on the same token share the
# one re-mint, a peer arriving after it takes the result). The mint goes
# through ONE sticky exit per process (_MINT_EXIT, the pool's exit_pool),
# rotated when the token is replaced after a block and when the box refuses
# a mint, so a bad exit is not reused. Until 2026-11-04 each call drew
# config.redfin_mint_proxy() itself (a random port), so K threads starting
# cold ran K parallel mints and everything past the 5/min re-mint budget
# failed as RedfinBlocked; the pool single-flights the cold burst to one.
# REDFIN_WAF_TOKEN in the environment seeds the token for a local dev run
# (the Mac has no browser box; mint it with patchright and export it).
_MINT_EXIT = browser_client.ExitPool(BOX_SITE, config.redfin_mint_proxy, size=1)
REMINT_GRACE_S = 10      # a re-mint this soon after the last mint reuses it (a peer just re-minted)


def _token_of(ident):
    token = (ident.get("tokens") or {}).get(WAF_COOKIE)
    if not token:
        token = next((c["value"] for c in ident.get("cookies", []) if c.get("name") == WAF_COOKIE), None)
    return token or None


def _mint(key, hint):
    """The pool's mint function: the browser box through `hint` (the pool's
    sticky exit from config.redfin_mint_proxy(), a US residential exit: the
    WAF only self-solves there). Raises RedfinBlocked when the box cannot
    deliver a token; a refused mint rotates the exit."""
    try:
        ident = browser_client.mint(BOX_SITE, proxy=hint)
    except (BoxUnavailable, BoxRefused) as e:
        if isinstance(e, BoxRefused):
            _MINT_EXIT.rotate(hint)
        raise RedfinBlocked(f"aws-waf-token mint failed: {type(e).__name__}: {e}")
    token = _token_of(ident)
    if not token:
        raise RedfinBlocked("browser box answered without an aws-waf-token")
    print(f"redfin: aws-waf-token minted ({len(token)} chars)")
    return identity_pool.Identity(token, exit=hint,
                                  expires_at=float(ident.get("expires_at") or (time.time() + config.REDFIN_TOKEN_TTL_S)))


_tokens = identity_pool.Pool(BOX_SITE, _mint, exit_pool=_MINT_EXIT, mints_per_minute=5, min_interval=REMINT_GRACE_S,
                             seed={"": os.environ["REDFIN_WAF_TOKEN"]} if os.environ.get("REDFIN_WAF_TOKEN") else None)


def _token(replace=None):
    """The shared token identity (its `value` is the cookie), minted when the
    process has none; `replace` = the identity a blocked call carried, re-
    minted only if it is still the current one and older than REMINT_GRACE_S.
    The pool's mint budget maps to RedfinBlocked like a refused mint."""
    try:
        return _tokens.get(replace=replace)
    except identity_pool.MintBudgetExceeded as e:
        raise RedfinBlocked(f"aws-waf-token mint failed: {e}")


# ---- requests --------------------------------------------------------------------

def _is_waf_page(resp):
    head = resp.text[:4000].lower()
    return ("awswaf" in head or "human verification" in head or "are you a robot" in head) \
        and resp.status_code in (202, 405, 403)


def _classify(resp, label):
    if resp.status_code in (403, 429) or _is_waf_page(resp):
        raise RedfinBlocked(f"HTTP {resp.status_code} on {label}")
    if resp.status_code == 404:
        raise RedfinNotFound(f"{label} not found")
    if resp.status_code == 400:
        raise RedfinBadRequest(resp.text[:200])
    if resp.status_code != 200:
        raise RedfinUpstreamError(f"HTTP {resp.status_code} on {label}")


def _decode(text, label):
    """The `{}&&{...}` envelope (or plain JSON) -> dict."""
    body = text[len(ENVELOPE_PREFIX):] if text.startswith(ENVELOPE_PREFIX) else text
    try:
        return fastjson.loads(body)
    except ValueError:
        raise RedfinUpstreamError(f"non-JSON response from {label}: {text[:120]!r}")


def _retrying(fn, remint=None):
    """Shared retry policy: a transport error drops the session; a block
    drops it, parks the thread on the cooldown exit and, for a token call,
    runs `remint()` (replace the token the blocked call carried) before the
    next try."""
    last = None
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            return fn()
        except RedfinBlocked as e:
            last = e
            _drop_session()
            _note_block()
            if remint is not None and attempt < config.MAX_RETRIES:
                try:
                    remint()
                except RedfinBlocked as mint_error:
                    last = mint_error
        except RedfinUpstreamError as e:
            last = e
            _drop_session()
        if attempt < config.MAX_RETRIES:
            time.sleep(config.RETRY_BACKOFF * attempt)
    raise last


def get_raw(path, params=None, *, label=None, token=False):
    """GET SITE+path -> decoded JSON (envelope or plain). `token=True`
    sends the WAF cookie (minting it first when the process has none)."""
    label = label or path.split("?")[0]
    used = [None]           # the token identity the last attempt carried (replaced after a block)

    def once():
        sess = _session()
        cookies = None
        if token:
            used[0] = _token()
            cookies = {WAF_COOKIE: used[0].value}
        try:
            resp = sess.get(SITE + path, params=params, headers=JSON_HEADERS, cookies=cookies,
                            timeout=TIMEOUT, allow_redirects=True)
        except Exception as e:
            raise RedfinUpstreamError(f"request failed: {type(e).__name__}: {e}")
        _classify(resp, label)
        return _decode(resp.text, label)
    return _retrying(once, remint=(lambda: _token(replace=used[0])) if token else None)


# resultCode values seen 2026-10-30/31: 0 success; 101 "this property does
# not exist" / "Invalid parameters"; 122 "Search region not found";
# 100 "<param> is required"; 1000 "Cannot get historical data" (an optional
# section with nothing to show) / "GIS Search Service timeout error".
_NOT_FOUND_CODES = (101, 122)


def get_payload(path, params=None, *, label=None, token=False, optional=False):
    """GET an enveloped call -> its `payload`. An error envelope maps to
    NotFound (101 / 122), BadRequest (100) or, for `optional` sections,
    None (the site has nothing for that section)."""
    label = label or path.split("?")[0]
    data = get_raw(path, params, label=label, token=token)
    if not isinstance(data, dict):
        raise RedfinUpstreamError(f"unexpected payload shape from {label}")
    code = data.get("resultCode", 0)
    message = data.get("errorMessage") or ""
    if code in (0, None):
        return data.get("payload")
    if optional:
        return None
    if code in _NOT_FOUND_CODES:
        raise RedfinNotFound(f"{label}: {message or 'not found'}")
    if code == 100:
        raise RedfinBadRequest(f"{label}: {message}")
    if "timeout" in message.lower():
        raise RedfinUpstreamError(f"{label}: {message}")
    raise RedfinUpstreamError(f"{label}: resultCode {code} {message}")


def get_payload_optional(path, params=None, *, label=None, token=False):
    """get_payload for a section the site may lack: NotFound / BadRequest /
    an error envelope -> None (transport failures still raise)."""
    try:
        return get_payload(path, params, label=label, token=token, optional=True)
    except (RedfinNotFound, RedfinBadRequest):
        return None


# ---- fan-out ------------------------------------------------------------------------
# A long-lived pool keeps its threads' curl sessions warm between requests
# (transfermarkt/fetch.py). A fan-out running inside a fan-out runs inline.
_pool = ThreadPoolExecutor(max_workers=FANOUT_WORKERS * 4, thread_name_prefix="redfin-fanout")


def run_parallel(fns):
    """Run zero-arg callables in parallel; results align with `fns`. The
    first failure (in `fns` order) propagates once the started ones finish."""
    if not fns:
        return []
    if len(fns) == 1 or threading.current_thread().name.startswith("redfin-fanout"):
        return [fn() for fn in fns]
    results = [None] * len(fns)
    pending, errors, nxt = {}, {}, 0
    while nxt < len(fns) or pending:
        while nxt < len(fns) and len(pending) < FANOUT_WORKERS and not errors:
            pending[_pool.submit(fns[nxt])] = nxt
            nxt += 1
        if not pending:
            break
        done, _ = wait_futures(pending, return_when=FIRST_COMPLETED)
        for future in done:
            index = pending.pop(future)
            try:
                results[index] = future.result()
            except BaseException as e:
                errors[index] = e
    if errors:
        raise errors[min(errors)]
    return results


if __name__ == "__main__":
    # Smoke test: python redfin/fetch.py
    payload = get_payload("/stingray/api/gis", {"al": 1, "region_id": 30818, "region_type": 6, "v": 8,
                                                "num_homes": 3, "status": 9, "uipt": "1,2,3,4,5,6,7,8",
                                                "sf": "1,2,3,5,6,7", "ord": "redfin-recommended-asc"})
    print(len(payload["homes"]), "homes;", payload["homes"][0]["url"])
