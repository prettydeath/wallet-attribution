"""Tests for the merge / categorization logic in scripts/build.py."""
import importlib.util
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "build", os.path.join(ROOT, "scripts", "build.py"))
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)

ROUTER = "0xd90e2f925da726b50c4ed8d0fb90ad053324f31b"
BINANCE = "0x28c6c06298d514db089934071355e5743bf21d60"


@pytest.fixture(autouse=True)
def fresh_records():
    build.records.clear()
    yield
    build.records.clear()


def final(address, network="ethereum", first_seen=None):
    key = (build.evm_lower(address), network)
    return build.finalize(build.records[key], first_seen)


# --- categorize ---------------------------------------------------------------

@pytest.mark.parametrize("entity,label,expected", [
    ("ofac-sanctions-lists", "Tornado.Cash: Router", "sanctioned"),
    ("", "Fake_Phishing123", "scam"),
    ("", "Ronin Bridge Exploiter", "hack"),
    ("tornado-cash", "Tornado.Cash: 1 ETH", "mixer"),
    ("binance", "Binance 14", "exchange"),
    ("blocked", "Blocked: 0x12", "frozen"),
    ("", "F2Pool Old", "mining"),
    ("uniswap", "Uniswap V3: Router", "defi"),
    ("", "Wormhole: Portal Token Bridge", "bridge"),
    ("", "Some Named Wallet", "entity"),
])
def test_categorize(entity, label, expected):
    assert build.categorize(entity, label) == expected


@pytest.mark.parametrize("entity,label,expected", [
    # tickers and LP pairs are names, not verdicts
    ("bscswap", "BSCswap: SCAM-LTC", "entity"),
    ("sushiswap", "SushiSwap: $PONZI-DAI", "defi"),
    ("burgerswap", "BurgerSwap: WASABI", "entity"),
    ("old-contract", "Bernie Madoff: Old PONZI Token", "entity"),
    ("binance-pegged", "Tornado.Cash: TORN Token", "entity"),
    # ...but the source's own slug still decides
    ("heist", "JAC: JAC Token", "hack"),
    # donation / grant addresses and substrings
    ("donate", "Tornado.Cash: Donate", "entity"),
    ("donate", "EtherScamDB: Donate", "entity"),
    ("gitcoin-grants", "Gitcoin Grants: Tornado.cash", "entity"),
    ("charity", "Endaoment: Vermont Hackerspaces Inc", "entity"),
    ("gambling", "BankHeist4D", "gambling"),
    ("reflexer-finance", "Reflexer: CoinJoin", "entity"),
    # real ones keep working
    ("mixer", "Tornado.Cash: 1,000 USDT", "mixer"),
    ("tornado-cash", "Tornado.Cash: Router", "mixer"),
    ("filament-exploit", "Filament Exploiter 48", "hack"),
    ("phish-hack", "Inferno Drainer", "hack"),
    ("bridged-token", "Fake_Phishing325636", "scam"),
    ("typhoon-network", "Typhoon Network: 10 BNB", "mixer"),
    ("typhoon-network", "Typhoon Network: Community Locked Token", "entity"),
])
def test_categorize_false_positives(entity, label, expected):
    assert build.categorize(entity, label) == expected


# --- sanctions ----------------------------------------------------------------

def test_stale_ofac_label_is_demoted_and_tagged():
    build.add(ROUTER, "ethereum", "Tornado.Cash", "Tornado.Cash: Router",
              "sanctioned", "eth-labels", "u", "medium")
    r = final(ROUTER)
    assert r["category"] == "mixer"
    assert "sanctioned" not in r["categories"]
    tags = r["tags"].split(";")
    assert "unverified-sanctions-label" in tags
    assert "ofac-delisted-2025-03-21" in tags
    assert "router" in tags


def test_current_ofac_list_keeps_sanctioned():
    build.add(ROUTER, "ethereum", "Tornado.Cash", "Tornado.Cash: Router",
              "sanctioned", "eth-labels", "u", "medium")
    build.add(ROUTER, "ethereum", "OFAC SDN", "OFAC sanctioned (ETH)",
              "sanctioned", "ofac-sdn", "u2", "high")
    r = final(ROUTER)
    assert r["category"] == "sanctioned"
    assert r["confidence"] == "high"
    assert r["method"] == "official"
    assert "unverified-sanctions-label" not in r["tags"]
    assert r["source"].split("+")[0] == "ofac-sdn"


def test_sanction_claim_without_other_meaning_becomes_entity():
    build.add("1BoatSLRHtKNngkdXEeobR76b53LETtpyT", "bitcoin", "",
              "OFAC Sanctioned", "sanctioned", "graphsense", "u", "high")
    r = final("1BoatSLRHtKNngkdXEeobR76b53LETtpyT", "bitcoin")
    assert r["category"] == "entity"
    assert "unverified-sanctions-label" in r["tags"]
    assert "ofac-delisted-2025-03-21" not in r["tags"]


# --- merge --------------------------------------------------------------------

def test_merge_keeps_all_categories_most_severe_first():
    build.add(BINANCE, "ethereum", "Binance", "Binance 14", "exchange",
              "eth-labels", "u1", "medium")
    build.add(BINANCE, "ethereum", "", "USDT blacklisted by Tether", "frozen",
              "tether-blacklist", "u2", "high")
    r = final(BINANCE)
    assert r["category"] == "frozen"
    assert r["categories"] == "frozen;exchange"
    assert r["entity"] == "Binance"          # back-filled from the other claim
    assert r["label"] == "USDT blacklisted by Tether"
    assert r["source"] == "tether-blacklist+eth-labels"
    assert r["confidence"] == "high"
    assert r["method"] == "onchain-event"
    assert "frozen-current" in r["tags"]


def test_tie_keeps_first_claim_and_high_confidence_from_any_tied_source():
    build.add(BINANCE, "ethereum", "Binance", "Binance 14", "exchange",
              "eth-labels", "u1", "medium")
    build.add(BINANCE, "ethereum", "Binance", "Binance (hot wallet)",
              "exchange", "cex-list", "u2", "high")
    r = final(BINANCE)
    assert r["label"] == "Binance 14"
    assert r["source_url"] == "u1"
    assert r["confidence"] == "high"
    assert r["method"] == "curated"
    assert "hot-wallet" in r["tags"]


def test_lower_category_does_not_raise_confidence():
    build.add(BINANCE, "ethereum", "", "Fake_Phishing1", "scam",
              "eth-labels", "u1", "medium")
    build.add(BINANCE, "ethereum", "Binance", "Binance 14", "exchange",
              "cex-list", "u2", "high")
    r = final(BINANCE)
    assert r["category"] == "scam"
    assert r["confidence"] == "medium"
    assert build.is_conflict(r)


def test_free_text_sources_do_not_produce_tags():
    build.add(BINANCE, "ethereum", "", "fake deposit page, cold wallet scam",
              "scam", "mew-ethereum-lists", "u", "medium")
    assert final(BINANCE)["tags"] == ""


def test_first_seen_is_carried_over():
    build.add(BINANCE, "ethereum", "Binance", "Binance 14", "exchange",
              "eth-labels", "u", "medium")
    assert final(BINANCE, first_seen="2026-10-01")["first_seen"] == "2026-10-01"
    assert final(BINANCE)["first_seen"] == build.TODAY


def test_invalid_and_rerouted_addresses():
    build.add("0x12…ab", "ethereum", "", "junk", "scam", "x", "u")
    assert not build.records
    tron = "TNPeeaaFB7K9cmo4uQpcU32zGK8G1NYqeL"
    build.add(tron, "ethereum", "", "TRON addr on wrong chain", "scam", "x", "u")
    assert (tron, "tron") in build.records


# --- outputs ------------------------------------------------------------------

def _row(addr, cat, **kw):
    return {"address": addr, "network": "ethereum", "category": cat,
            "entity": kw.get("entity", ""), "label": "", "source": "s",
            "first_seen": kw.get("first_seen", "2026-10-01"),
            "last_updated": "2026-10-01"}


def test_diff_builds():
    prev = {("0xa", "ethereum"): _row("0xa", "sanctioned"),
            ("0xb", "ethereum"): _row("0xb", "exchange")}
    cur = {("0xa", "ethereum"): _row("0xa", "mixer"),
           ("0xc", "ethereum"): _row("0xc", "scam")}
    ch = {c["address"]: c for c in build.diff_builds(prev, cur)}
    assert ch["0xa"]["change"] == "category_changed"
    assert (ch["0xa"]["old_category"], ch["0xa"]["new_category"]) == (
        "sanctioned", "mixer")
    assert ch["0xb"]["change"] == "removed"
    assert ch["0xc"]["change"] == "added"
    assert all(c["date"] == build.TODAY for c in ch.values())


def test_json_text_is_valid_one_record_per_line():
    rows = [build.finalize({"address": BINANCE, "network": "ethereum",
                            "claims": [{"entity": "Binance", "label": "B",
                                        "category": "exchange",
                                        "source": "cex-list",
                                        "source_url": "u",
                                        "confidence": "high"}]})]
    text = build._json_text(rows)
    assert json.loads(text)[0]["address"] == BINANCE
    assert list(json.loads(text)[0]) == build.FIELDS
    assert len(text.strip().splitlines()) == 3
    assert json.loads(build._json_text([])) == []


def test_write_outputs_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "OUT", str(tmp_path / "data"))
    monkeypatch.setattr(build, "ROOT", str(tmp_path))
    # build 1 (no baseline -> no diff files)
    build.add(ROUTER, "ethereum", "Tornado.Cash", "Tornado.Cash: Router",
              "sanctioned", "eth-labels", "u", "medium")
    build.add(BINANCE, "ethereum", "Binance", "Binance 14", "exchange",
              "eth-labels", "u", "medium")
    s1 = build.write_outputs()
    assert s1["total"] == 2 and "changes" not in s1
    assert not (tmp_path / "data" / "changes.csv").exists()
    # build 2: Binance gone, a new address appears
    build.records.clear()
    build.add(ROUTER, "ethereum", "Tornado.Cash", "Tornado.Cash: Router",
              "sanctioned", "eth-labels", "u", "medium")
    build.add("0x" + "1" * 40, "ethereum", "", "Fake_Phishing9", "scam",
              "forta", "u", "medium")
    s2 = build.write_outputs()
    assert s2["changes"]["added"] == 1 and s2["changes"]["removed"] == 1
    removed = build._read_csv("removed.csv")
    assert [r["address"] for r in removed] == [BINANCE]
    assert removed[0]["removed_at"] == build.TODAY
    log = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    assert log.count(f"## {build.TODAY}") == 1
    # build 3: identical -> keeps last diff, changelog section not duplicated
    s3 = build.write_outputs()
    assert s3["changes"] == s2["changes"]
    assert (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8") == log
    # build 4: Binance returns -> leaves removed.csv
    build.add(BINANCE, "ethereum", "Binance", "Binance 14", "exchange",
              "eth-labels", "u", "medium")
    build.write_outputs()
    assert build._read_csv("removed.csv") == []
    all_rows = {r["address"]: r for r in build._read_csv("_all.csv")}
    assert all_rows[ROUTER]["category"] == "mixer"
    assert all_rows[ROUTER]["first_seen"] == build.TODAY
