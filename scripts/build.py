#!/usr/bin/env python3
"""
Normalize all upstream attribution sources into one schema and write
per-network CSV + JSON files plus a combined dataset and stats.

Run:  python3 scripts/build.py
Sources are expected in ./sources (populate with scripts/fetch_sources.sh).
"""
import csv
import json
import os
import re
from collections import defaultdict, Counter
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "sources")
OUT = os.path.join(ROOT, "data")
TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")

# Output schema (column order for CSV)
FIELDS = ["address", "network", "entity", "label", "category",
          "source", "source_url", "confidence", "last_updated"]

# --- EVM chainId -> network -------------------------------------------------
EVM_CHAINS = {
    1: "ethereum", 56: "bsc", 137: "polygon", 8453: "base",
    42161: "arbitrum", 10: "optimism", 43114: "avalanche",
    100: "gnosis", 42220: "celo", 480: "worldchain", 250: "fantom",
    59144: "linea", 534352: "scroll", 5000: "mantle", 324: "zksync",
}

# --- OFAC ticker -> network -------------------------------------------------
OFAC_TICKER = {
    "XBT": "bitcoin", "ETH": "ethereum", "LTC": "litecoin",
    "BCH": "bitcoin-cash", "XRP": "xrp", "TRX": "tron", "BSC": "bsc",
    "ARB": "arbitrum", "ETC": "ethereum-classic", "DASH": "dash",
    "ZEC": "zcash", "BSV": "bitcoin-sv", "BTG": "bitcoin-gold",
    "XVG": "verge", "XMR": "monero", "SOL": "solana",
    # token lists carry addresses on multiple host chains -> detect by format
    "USDT": None, "USDC": None,
}

# --- category heuristics for eth-labels -------------------------------------
EXCHANGES = {
    "binance", "coinbase", "kraken", "okx", "okex", "bybit", "kucoin",
    "huobi", "htx", "gate", "gate.io", "gateio", "bitfinex", "bitstamp",
    "gemini", "crypto.com", "cryptocom", "mexc", "bitget", "upbit",
    "bithumb", "poloniex", "ftx", "robinhood", "bittrex", "bitmex",
    "deribit", "whitebit", "bitso", "coinex", "lbank", "probit",
    "bitkub", "korbit", "coinone", "wazirx", "coincheck", "bitflyer",
    "luno", "cex.io", "nexo", "nobitex",
}
MIXER_KW = ("tornado", "mixer", "wasabi", "coinjoin", "blender", "sinbad")
BRIDGE_KW = ("bridge", "wormhole", "portal", "hop protocol", "across",
             "stargate", "celer", "synapse", "orbiter", "layerzero")
DEFI_KW = ("uniswap", "aave", "curve", "compound", "makerdao", "maker:",
           "lido", "sushi", "1inch", "balancer", "pancakeswap", "gmx",
           "0x-protocol", "0x:", "dydx", "yearn", "convex", "frax")


def norm_addr(a):
    a = (a or "").strip().strip('"').strip()
    return a


def evm_lower(a):
    return a.lower() if a.startswith("0x") else a


def detect_network(addr, hint=None):
    """Best-effort network detection from address format; hint wins for EVM."""
    a = addr
    if a.startswith("0x") and len(a) == 42:
        return hint if hint in EVM_CHAINS.values() else "ethereum"
    if re.fullmatch(r"T[1-9A-HJ-NP-Za-km-z]{33}", a):
        return "tron"
    if a.startswith(("bc1", "1", "3")):
        return "bitcoin"
    if a.startswith(("ltc1", "L", "M")):
        return "litecoin"
    if a.startswith("D") and len(a) in (33, 34):
        return "dogecoin"
    if a.startswith("r") and 25 <= len(a) <= 35:
        return "xrp"
    if a.startswith(("q", "p")) or a.startswith("bitcoincash:"):
        return "bitcoin-cash"
    return hint


def categorize(entity, label):
    e = (entity or "").lower()
    text = f"{e} {(label or '').lower()}"
    if e in EXCHANGES or any(x in text for x in ("exchange:", " cex")):
        return "exchange"
    if any(k in text for k in MIXER_KW):
        return "mixer"
    if any(k in text for k in BRIDGE_KW):
        return "bridge"
    if any(k in text for k in DEFI_KW):
        return "defi"
    return "entity"


# addr(lower) + network -> record ; later high-priority sources overwrite
records = {}
CATEGORY_PRIORITY = {"sanctioned": 5, "scam": 4, "exchange": 3,
                     "mixer": 3, "bridge": 2, "defi": 1, "entity": 0, "": 0}


def add(address, network, entity, label, category, source, source_url,
        confidence="medium"):
    address = norm_addr(address)
    if not address or not network:
        return
    key = (evm_lower(address), network)
    rec = {
        "address": address, "network": network, "entity": entity or "",
        "label": label or "", "category": category or "", "source": source,
        "source_url": source_url, "confidence": confidence,
        "last_updated": TODAY,
    }
    old = records.get(key)
    if old is None:
        records[key] = rec
        return
    # keep the record with the higher-priority category; merge missing fields
    if CATEGORY_PRIORITY.get(category, 0) > CATEGORY_PRIORITY.get(old["category"], 0):
        for f in ("entity", "label"):
            if not rec[f] and old[f]:
                rec[f] = old[f]
        merged = list(dict.fromkeys([source] + old["source"].split("+")))
        rec["source"] = "+".join(merged)
        records[key] = rec
    else:
        if not old["entity"] and entity:
            old["entity"] = entity
        if not old["label"] and label:
            old["label"] = label


# --- 1) eth-labels (EVM entity/exchange/protocol) ---------------------------
def load_ethlabels():
    path = os.path.join(SRC, "ethlabels", "data", "csv", "accounts.csv")
    if not os.path.exists(path):
        print("  ! eth-labels missing, skipping"); return 0
    n = 0
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            addr = norm_addr(row.get("address"))
            try:
                cid = int(row.get("chainId") or 0)
            except ValueError:
                cid = 0
            net = EVM_CHAINS.get(cid, f"evm-{cid}")
            entity = norm_addr(row.get("label"))
            label = norm_addr(row.get("nameTag")) or entity
            add(addr, net, entity, label, categorize(entity, label),
                "eth-labels",
                "https://github.com/dawsbot/eth-labels", "medium")
            n += 1
    print(f"  eth-labels: {n} rows"); return n


# --- 2) cex-list (curated CEX hot wallets, ETH mainnet) ---------------------
def load_cexlist():
    ddir = os.path.join(SRC, "cexlist", "data")
    if not os.path.isdir(ddir):
        print("  ! cex-list missing, skipping"); return 0
    n = 0
    for fn in os.listdir(ddir):
        if not fn.endswith(".json"):
            continue
        net = "ethereum" if "ethereum" in fn else fn.replace(".json", "")
        with open(os.path.join(ddir, fn), encoding="utf-8") as f:
            data = json.load(f)
        for addr, ex in data.items():
            add(addr, net, ex.title(), f"{ex.title()} (hot wallet)",
                "exchange", "cex-list",
                "https://github.com/tradezon/cex-list", "high")
            n += 1
    print(f"  cex-list: {n} rows"); return n


# --- 3) MEW ethereum-lists (scam / dark-list) -------------------------------
def load_mew():
    path = os.path.join(SRC, "mew", "src", "addresses",
                        "addresses-darklist.json")
    if not os.path.exists(path):
        print("  ! MEW darklist missing, skipping"); return 0
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    n = 0
    for item in data:
        addr = norm_addr(item.get("address"))
        comment = (item.get("comment") or "").strip()
        net = detect_network(addr, "ethereum") or "ethereum"
        add(addr, net, "", comment[:140] or "dark-list address", "scam",
            "mew-ethereum-lists",
            "https://github.com/MyEtherWallet/ethereum-lists", "medium")
        n += 1
    print(f"  mew-darklist: {n} rows"); return n


# --- 4) OFAC sanctioned addresses (all covered chains) ----------------------
def load_ofac():
    d = os.path.join(SRC, "ofac")
    files = [f for f in os.listdir(d)] if os.path.isdir(d) else []
    files = [f for f in files
             if f.startswith("sanctioned_addresses_") and f.endswith(".json")]
    if not files:
        print("  ! OFAC lists missing (need `lists` branch), skipping"); return 0
    n = 0
    for fn in files:
        ticker = fn.replace("sanctioned_addresses_", "").replace(".json", "")
        default_net = OFAC_TICKER.get(ticker, ticker.lower())
        with open(os.path.join(d, fn), encoding="utf-8") as f:
            addrs = json.load(f)
        for addr in addrs:
            addr = norm_addr(addr)
            net = default_net or detect_network(addr) or "unknown"
            if default_net is None:  # USDT/USDC token lists span chains
                net = detect_network(addr) or "ethereum"
            add(addr, net, "OFAC SDN",
                f"OFAC sanctioned ({ticker})", "sanctioned",
                "ofac-sdn",
                "https://github.com/0xB10C/ofac-sanctioned-digital-currency-addresses",
                "high")
            n += 1
    print(f"  ofac: {n} rows"); return n


# --- 5) DefiLlama CEX adapters (official proof-of-reserves wallets) ----------
# DefiLlama-Adapters encodes each exchange's officially-disclosed wallets as
# `config = { <chain>: { owners: [...] } }` plus a shared bitcoin address book.
DL_CHAIN = {
    "ethereum": "ethereum", "bsc": "bsc", "polygon": "polygon",
    "arbitrum": "arbitrum", "optimism": "optimism", "avax": "avalanche",
    "base": "base", "celo": "celo", "gnosis": "gnosis", "fantom": "fantom",
    "era": "zksync", "metis": "metis", "kava": "kava", "cronos": "cronos",
    "core": "core", "klaytn": "klaytn", "kcc": "kcc", "linea": "linea",
    "sonic": "sonic", "hyperliquid": "hyperliquid", "zilliqa": "zilliqa",
    "ethereumclassic": "ethereum-classic", "tezos": "tezos",
    "bitcoin": "bitcoin", "litecoin": "litecoin", "doge": "dogecoin",
    "tron": "tron", "solana": "solana", "ripple": "xrp", "cardano": "cardano",
    "ton": "ton", "sui": "sui", "aptos": "aptos", "near": "near",
    "cosmos": "cosmos", "polkadot": "polkadot", "algorand": "algorand",
    "eos": "eos", "elrond": "multiversx", "starknet": "starknet",
}
EVM_SLUGS = {"ethereum", "bsc", "polygon", "arbitrum", "optimism", "avalanche",
             "base", "celo", "gnosis", "fantom", "zksync", "metis", "kava",
             "cronos", "core", "klaytn", "kcc", "linea", "sonic",
             "hyperliquid", "ethereum-classic"}
# Exchange CEX project folders present in DefiLlama-Adapters -> display name
DL_CEX_FOLDERS = {
    "binance": "Binance", "bitfinex": "Bitfinex", "bitget": "Bitget",
    "gate-io": "Gate.io", "huobi": "HTX (Huobi)", "kucoin": "KuCoin",
    "coinbase-btc": "Coinbase", "coinbase-ltc": "Coinbase",
    "coinbase-xrp": "Coinbase", "coinbase-ada": "Coinbase",
    "bitstamp": "Bitstamp", "gemini": "Gemini", "bitrue-cex": "Bitrue",
    "okex": "OKX (OKEx)",
}
# Whitelisted exchange keys in helper/bitcoin-book (avoid tagging L2s/protocols)
DL_BTC_EXCHANGES = {
    "binance": "Binance", "binance2": "Binance", "bitfinex": "Bitfinex",
    "bitget": "Bitget", "bybit": "Bybit", "cryptoCom": "Crypto.com",
    "huobi": "HTX (Huobi)", "deribit": "Deribit", "coinex": "CoinEx",
    "coinw": "CoinW", "bingCex": "BingX", "bigone": "BigONE", "btse": "BTSE",
    "hashkey": "HashKey", "hashkeyExchange": "HashKey", "hibt": "HIBT",
    "hotbit": "Hotbit", "fastex": "Fastex", "fire": "Fire", "flipster":
    "Flipster", "gateIo": "Gate.io", "poloniex-cex": "Poloniex",
    "coindcx": "CoinDCX", "coinsquare": "Coinsquare", "biconomy": "Biconomy",
    "bitmake": "Bitmake", "bitunixCex": "Bitunix", "bitvenus": "BitVenus",
    "blofinCex": "Blofin", "bitkub": "Bitkub", "lbank": "LBank",
    "p2pb2b": "P2B", "kraken": "Kraken", "krakenBTC": "Kraken", "okex": "OKX",
    "kucoin": "KuCoin", "bitstamp": "Bitstamp", "gemini": "Gemini",
    "mexc": "MEXC", "bitmex": "BitMEX", "whitebit": "WhiteBIT",
    "nobitex": "Nobitex", "korbit": "Korbit", "coinone": "Coinone",
    "bithumb": "Bithumb", "upbit": "Upbit",
}

_ADDR_TOKEN = re.compile(r'["\']([0-9A-Za-z:_]{20,120})["\']')


def _addr_for_network(tok, net):
    """Validate/normalize a token as an address for the given network slug."""
    if net in EVM_SLUGS:
        return tok if re.fullmatch(r"0x[0-9a-fA-F]{40}", tok) else None
    if tok.startswith("0x"):
        return None                       # EVM addr under a non-EVM header
    if net == "tron" and not re.fullmatch(r"T[1-9A-HJ-NP-Za-km-z]{33}", tok):
        return None
    if net == "xrp" and not tok.startswith("r"):
        return None
    if net == "cardano" and not tok.startswith("addr1"):
        return None
    return tok


def _btc_family(addr):
    if re.fullmatch(r"(bc1|[13])[0-9a-zA-Z]{20,90}", addr):
        return "bitcoin"
    if re.fullmatch(r"(ltc1|[LM])[0-9a-zA-Z]{20,90}", addr):
        return "litecoin"
    if addr.startswith("D") and 33 <= len(addr) <= 34:
        return "dogecoin"
    if addr.startswith("bitcoincash:") or re.fullmatch(r"[qp][0-9a-z]{40,60}", addr):
        return "bitcoin-cash"
    return None


def _parse_owner_config(text, entity, url, counter):
    """Line-scan a DefiLlama CEX index.js: attribute addresses to chain headers."""
    net = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("//"):
            continue
        m = re.match(r'["\']?([A-Za-z0-9_]+)["\']?\s*:\s*\{', s)
        if m and m.group(1) in DL_CHAIN:
            net = DL_CHAIN[m.group(1)]
            continue
        # named non-EVM arrays in coinbase-btc (XRP_ADDRESSES, DOGE_ADDRESSES..)
        m2 = re.match(r'(?:const\s+)?([A-Z]+)_ADDRESSES\s*=', s)
        if m2:
            net = {"XRP": "xrp", "DOGE": "dogecoin", "ADA": "cardano",
                   "BTC": "bitcoin", "LTC": "litecoin"}.get(m2.group(1))
            continue
        if not net:
            continue
        for tok in _ADDR_TOKEN.findall(line):
            a = _addr_for_network(tok, net)
            if a:
                add(a, net, entity, f"{entity} (proof-of-reserves)",
                    "exchange", "defillama-cex", url, "high")
                counter[0] += 1


def _parse_btc_book(text, url, counter):
    """Extract whitelisted exchange BTC/LTC/DOGE arrays from the address book."""
    # match `key: [ ... ]` and `const key = [ ... ]`
    for m in re.finditer(
            r'(?:const\s+)?["\']?([A-Za-z0-9_-]+)["\']?\s*[:=]\s*\[([^\]]*)\]',
            text):
        key, body = m.group(1), m.group(2)
        entity = DL_BTC_EXCHANGES.get(key)
        if not entity:
            continue
        for tok in _ADDR_TOKEN.findall(body):
            net = _btc_family(tok)
            if net:
                add(tok, net, entity, f"{entity} (proof-of-reserves)",
                    "exchange", "defillama-cex", url, "high")
                counter[0] += 1


def load_defillama_cex():
    base = os.path.join(SRC, "dl", "projects")
    url = "https://github.com/DefiLlama/DefiLlama-Adapters"
    if not os.path.isdir(base):
        print("  ! DefiLlama-Adapters missing, skipping"); return 0
    counter = [0]
    for folder, entity in DL_CEX_FOLDERS.items():
        idx = os.path.join(base, folder, "index.js")
        if os.path.exists(idx):
            with open(idx, encoding="utf-8", errors="ignore") as f:
                _parse_owner_config(f.read(), entity, url, counter)
    book = os.path.join(base, "helper", "bitcoin-book")
    if os.path.isdir(book):
        for fn in os.listdir(book):
            if fn.endswith(".js"):
                with open(os.path.join(book, fn), encoding="utf-8",
                          errors="ignore") as f:
                    _parse_btc_book(f.read(), url, counter)
    print(f"  defillama-cex: {counter[0]} rows"); return counter[0]


# --- 6) API-enriched labels (produced by scripts/enrich.py) -----------------
def load_enriched():
    path = os.path.join(ROOT, "enriched", "api_labels.jsonl")
    if not os.path.exists(path):
        return 0
    n = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            add(r.get("address"), r.get("network"), r.get("entity"),
                r.get("label"), r.get("category") or "entity",
                r.get("source") or "api", r.get("source_url") or "",
                r.get("confidence") or "medium")
            n += 1
    if n:
        print(f"  api-enriched: {n} rows")
    return n


def write_outputs():
    os.makedirs(OUT, exist_ok=True)
    by_net = defaultdict(list)
    for rec in records.values():
        by_net[rec["network"]].append(rec)

    combined = []
    stats = {"generated": TODAY, "total": len(records),
             "by_network": {}, "by_category": Counter(),
             "by_source": Counter()}

    for net, recs in sorted(by_net.items()):
        recs.sort(key=lambda r: (r["category"], r["entity"], r["address"]))
        # CSV
        with open(os.path.join(OUT, f"{net}.csv"), "w", newline="",
                  encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(recs)
        # JSON
        with open(os.path.join(OUT, f"{net}.json"), "w",
                  encoding="utf-8") as f:
            json.dump(recs, f, ensure_ascii=False, indent=2)
        stats["by_network"][net] = len(recs)
        combined.extend(recs)
        for r in recs:
            stats["by_category"][r["category"]] += 1
            stats["by_source"][r["source"]] += 1

    combined.sort(key=lambda r: (r["network"], r["category"], r["address"]))
    with open(os.path.join(OUT, "_all.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(combined)
    with open(os.path.join(OUT, "_all.json"), "w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)

    stats["by_category"] = dict(stats["by_category"])
    stats["by_source"] = dict(stats["by_source"])
    with open(os.path.join(OUT, "stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    return stats


def main():
    print("Building attribution dataset...")
    load_ethlabels()
    load_cexlist()
    load_mew()
    load_ofac()
    load_defillama_cex()
    load_enriched()
    stats = write_outputs()
    print(f"\n✓ {stats['total']} unique (address, network) records")
    print("Networks:", ", ".join(f"{k}={v}" for k, v in
          sorted(stats["by_network"].items(), key=lambda x: -x[1])))
    print("Categories:", stats["by_category"])


if __name__ == "__main__":
    main()
