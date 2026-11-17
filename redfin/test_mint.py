"""The aws-waf-token lives in an identity_pool.Pool whose mint goes through
one sticky exit per process, so a cold burst single-flights to one box mint;
a block replaces the token once per wave on a fresh exit, a refused mint
rotates the exit.

    python -m pytest redfin/test_mint.py -q
"""
import threading
import time

import pytest

import browser_client
from redfin import fetch
from scraper_utils.testing import hosted_only


def _reset():
    fetch._tokens.clear()
    fetch._tokens._mint_times.clear()
    fetch._tokens._last_mint.clear()
    fetch._MINT_EXIT.reset()


@pytest.fixture
def cold(monkeypatch):
    """No token (no env seed either), a counting fake box mint."""
    monkeypatch.setattr(fetch._tokens, "_seed", {})
    _reset()
    calls = []
    lock = threading.Lock()

    def fake_mint(site, proxy=None, variant=""):
        with lock:
            calls.append(proxy)
        time.sleep(0.05)                                 # long enough for every thread to pile up
        return {"tokens": {fetch.WAF_COOKIE: f"tok-{len(calls)}"}, "expires_at": time.time() + 600}
    monkeypatch.setattr(browser_client, "mint", fake_mint)
    yield calls
    _reset()


def test_cold_burst_mints_once_on_one_exit(cold):
    tokens, errors = [], []

    def worker():
        try:
            tokens.append(fetch._token().value)
        except Exception as e:                           # pragma: no cover - the bug's symptom
            errors.append(e)
    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert errors == []
    assert len(cold) == 1, cold                          # 8 threads, one mint (was 8, past the 5/min budget)
    assert set(tokens) == {"tok-1"}
    assert fetch._MINT_EXIT.current() == [cold[0]]       # the sticky mint exit
    assert fetch._tokens.peek().exit == cold[0]


@hosted_only
def test_a_block_rotates_the_exit_and_peers_share_the_new_mint(cold):
    blocked = fetch._token()
    first = cold[0]
    fetch._tokens._last_mint[""] -= fetch.REMINT_GRACE_S     # the token is old enough to be re-minted
    tokens = []
    threads = [threading.Thread(target=lambda: tokens.append(fetch._token(replace=blocked).value)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert len(cold) == 2 and cold[1] != first           # one re-mint, on a fresh exit
    assert set(tokens) == {"tok-2"} and blocked.retired
    assert fetch._MINT_EXIT.current() == [cold[1]]


@hosted_only
def test_a_refused_mint_rotates_the_exit(cold, monkeypatch):
    before = fetch._MINT_EXIT.get()
    monkeypatch.setattr(browser_client, "mint",
                        lambda site, proxy=None, variant="": (_ for _ in ()).throw(browser_client.BoxRefused("502 recipe")))
    with pytest.raises(fetch.RedfinBlocked):
        fetch._token()
    assert fetch._MINT_EXIT.current() != [before]
    assert fetch._tokens.stats()["minting"] == 0


def test_a_block_right_after_a_remint_reuses_it(cold):
    blocked = fetch._token()
    assert fetch._token(replace=blocked).value == "tok-1"    # a peer just minted it
    assert len(cold) == 1


def test_an_expired_token_is_re_minted(cold):
    old = fetch._token()
    old.expires_at = time.time() - 1
    assert fetch._token().value == "tok-2" and old.retired and len(cold) == 2
    assert fetch._MINT_EXIT.current() == [cold[0]], "expiry is not a refusal: the exit stays"


def test_the_mint_budget_is_blocked(cold, monkeypatch):
    monkeypatch.setattr(fetch._tokens, "mints_per_minute", 1)
    old = fetch._token()
    old.expires_at = time.time() - 1
    with pytest.raises(fetch.RedfinBlocked, match="mint"):
        fetch._token()
    assert len(cold) == 1
