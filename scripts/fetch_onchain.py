#!/usr/bin/env python3
"""
Fetch attribution that lives on-chain or behind open (keyless) APIs into
./sources/onchain, for build.py to fold into data/.

  * Ransomwhere        - crowdsourced ransomware payment addresses (BTC)
  * Tether USDT        - current blacklist on Ethereum and TRON, rebuilt from
                         the contract's AddedBlackList / RemovedBlackList events
  * Circle USDC        - current blacklist on Ethereum (Blacklisted /
                         UnBlacklisted events)

Ethereum event logs come from the Etherscan V2 API when a (free) key is
available - env ETHERSCAN_API_KEY or the `etherscan` row of
config/providers.csv - and otherwise from the keyless, Etherscan-compatible
Blockscout API (slower: it is throttled per IP). TRON events come from
TronGrid (keyless). Run:  python3 scripts/fetch_onchain.py
"""
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "sources", "onchain")
UA = {"User-Agent": "wallet-attribution/1.0 (+github.com/prettydeath/wallet-attribution)"}

BLOCKSCOUT = "https://eth.blockscout.com/api"
ETHERSCAN = "https://api.etherscan.io/v2/api?chainid=1"
TRONGRID = "https://api.trongrid.io"

# (token, issuer, contract, add-event topic0, remove-event topic0, start block)
EVM_BLACKLISTS = [
    ("USDT", "Tether", "0xdAC17F958D2ee523a2206206994597C13D831ec7",
     "0x42e160154868087d6bfdc0ca23d96a1c1cfa32f1b72ba9ba27b69b98a0d819dc",
     "0xd7e9ec6e6ecd65492dce6bf513cd6867560d49544421d0783ddf06e76c24470c",
     4634748),
    ("USDC", "Circle", "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
     "0xffa4e6181777692565cf28528fc88fd1516ea86b56da075235fa575af6a4b855",
     "0x117e3210bb9aa7d9baff172026820255c6f6c30ba8999d1c2fd88e2848137c4e",
     6082465),
]
TRON_USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"


def _rate_wait(err, attempt):
    """Seconds to wait after HTTP 429: honour Retry-After / x-ratelimit-reset
    (Blockscout sends milliseconds), capped so a run cannot hang forever."""
    h = err.headers if isinstance(err, urllib.error.HTTPError) else None
    for name in ("Retry-After", "x-ratelimit-reset"):
        v = h.get(name) if h else None
        if v and v.strip().isdigit():
            n = int(v)
            return min(n / 1000 if n > 10_000 else n, MAX_WAIT) + 1
    return min(2 ** (attempt + 1), MAX_WAIT)


MAX_WAIT = 45 * 60     # Blockscout's keyless window is ~40 min


def get_json(url, tries=6):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            limited = getattr(e, "code", None) == 429
            wait = _rate_wait(e, i) if limited else 2 ** i
            sys.stderr.write(f"  retry {i + 1}/{tries} in {wait:.0f}s: {e}\n")
            time.sleep(wait)
    raise RuntimeError(f"giving up on {url}")


# --- TRON hex -> base58check ------------------------------------------------
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def tron_b58(hex20):
    raw = bytes.fromhex("41" + hex20[-40:])
    chk = hashlib.sha256(hashlib.sha256(raw).digest()).digest()[:4]
    n = int.from_bytes(raw + chk, "big")
    s = ""
    while n:
        n, r = divmod(n, 58)
        s = _B58[r] + s
    return s


def _hex(v):
    """Etherscan writes zero as a bare "0x"."""
    return int(v, 16) if v and v != "0x" else 0


def _topic_addr(log):
    """Blacklist events put the address in topic1 (indexed) or in data."""
    t = log.get("topics") or []
    word = t[1] if len(t) > 1 and t[1] else log.get("data", "")
    return "0x" + word[-40:]


PAGE = 1000           # max results per getLogs call (Etherscan and Blockscout)


def etherscan_keys():
    """ETHERSCAN_API_KEY may hold several comma-separated keys (rotated)."""
    raw = os.environ.get("ETHERSCAN_API_KEY", "").strip()
    path = os.path.join(ROOT, "config", "providers.csv")
    if not raw and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                cols = [c.strip() for c in line.split(",")]
                if cols[0] == "etherscan" and len(cols) > 1 and cols[1]:
                    raw = cols[1]
    return [k.strip() for k in raw.split(",") if k.strip()]


KEYS = etherscan_keys()
KEY = bool(KEYS)
_calls = [0]


def _logs_api():
    if not KEYS:
        return f"{BLOCKSCOUT}?"
    _calls[0] += 1
    return f"{ETHERSCAN}&apikey={KEYS[_calls[0] % len(KEYS)]}&"


PAUSE = 0.25 if KEY else 1.0     # Etherscan free: 5 req/s; Blockscout: ~10/window


def _logs_page(contract, topic0, frm, to):
    """One getLogs call; rate limits are retried, never mistaken for 'empty'."""
    q = urllib.parse.urlencode({
        "module": "logs", "action": "getLogs", "fromBlock": frm,
        "toBlock": to, "address": contract, "topic0": topic0})
    for i in range(10):
        d = get_json(_logs_api() + q)
        res, msg = d.get("result"), str(d.get("message") or "")
        if isinstance(res, list) and (res or d.get("status") == "1"):
            return res
        if "no records found" in msg.lower() or "no logs found" in msg.lower():
            return []
        wait = min(120, 2 ** (i + 1))
        sys.stderr.write(f"  logs api: {msg[:60]!r}, retry in {wait}s\n")
        time.sleep(wait)
    raise RuntimeError(f"logs api kept failing for {frm}-{to}")


def eth_logs(contract, topic0, start):
    """All logs for one event over the whole history. Results come back in
    block order, 1000 per call, so a full page continues from its last block
    (re-read, de-duplicated) - about ten calls for USDT + USDC in total."""
    out, seen, frm = [], set(), start
    while True:
        res = _logs_page(contract, topic0, frm, "latest")
        fresh = [l for l in res
                 if (l["transactionHash"], l["logIndex"]) not in seen]
        for l in fresh:
            seen.add((l["transactionHash"], l["logIndex"]))
        out.extend(fresh)
        if len(res) < PAGE or not fresh:
            return out
        frm = max(_hex(l["blockNumber"]) for l in res)
        time.sleep(PAUSE)


def evm_blacklists():
    recs = []
    for token, issuer, contract, add_t, rem_t, start in EVM_BLACKLISTS:
        events = []
        for topic, kind in ((add_t, "add"), (rem_t, "remove")):
            for l in eth_logs(contract, topic, start):
                events.append((_hex(l["blockNumber"]), _hex(l["logIndex"]),
                               kind, _topic_addr(l), l["transactionHash"]))
        current = {}
        for blk, _, kind, addr, tx in sorted(events):
            if kind == "add":
                current[addr.lower()] = (addr, blk, tx)
            else:
                current.pop(addr.lower(), None)
        print(f"  {token} ethereum: {len(events)} events -> "
              f"{len(current)} currently blacklisted")
        for addr, blk, tx in current.values():
            recs.append({"address": addr, "network": "ethereum",
                         "token": token, "issuer": issuer, "contract": contract,
                         "block": blk, "tx": tx})
    return recs


def trongrid_events(event):
    out, url = [], (f"{TRONGRID}/v1/contracts/{TRON_USDT}/events?"
                    f"event_name={event}&limit=200&order_by=block_timestamp,asc")
    while url:
        d = get_json(url)
        out.extend(d.get("data") or [])
        url = (d.get("meta") or {}).get("links", {}).get("next")
        time.sleep(0.4)
    return out


def tron_blacklist():
    events = []
    for name, kind in (("AddedBlackList", "add"), ("RemovedBlackList", "remove")):
        for e in trongrid_events(name):
            user = (e.get("result") or {}).get("_user") or ""
            if user:
                events.append((e["block_number"], e.get("event_index", 0), kind,
                               tron_b58(user), e["transaction_id"]))
    current = {}
    for blk, _, kind, addr, tx in sorted(events):
        if kind == "add":
            current[addr] = (blk, tx)
        else:
            current.pop(addr, None)
    print(f"  USDT tron: {len(events)} events -> {len(current)} currently blacklisted")
    return [{"address": a, "network": "tron", "token": "USDT", "issuer": "Tether",
             "contract": TRON_USDT, "block": blk, "tx": tx}
            for a, (blk, tx) in current.items()]


def ransomwhere():
    rows = get_json("https://api.ransomwhe.re/export").get("result") or []
    print(f"  ransomwhere: {len(rows)} addresses")
    return [{"address": r["address"], "network": r.get("blockchain") or "bitcoin",
             "family": r.get("family") or "", "balance_usd": r.get("balanceUSD")}
            for r in rows]


def save(name, rows):
    rows.sort(key=lambda r: (r["network"], r["address"]))
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)


def main():
    os.makedirs(OUT, exist_ok=True)
    print("Fetching on-chain / open-API attribution (ETH logs via "
          f"{'Etherscan' if KEY else 'Blockscout, keyless'})...")
    ok = True
    for name, fn in (("ransomwhere.json", ransomwhere),
                     ("stablecoin_blacklists.json",
                      lambda: evm_blacklists() + tron_blacklist())):
        try:
            save(name, fn())
        except Exception as e:      # keep the previous snapshot on failure
            ok = False
            sys.stderr.write(f"  ! {name} not refreshed: {e}\n")
    print("✓ on-chain sources ready" if ok else "! some sources were not refreshed")


if __name__ == "__main__":
    main()
