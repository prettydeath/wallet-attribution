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

# --- category heuristics for label datasets (eth-labels, etherscan-labels) ---
EXCHANGES = {
    "binance", "coinbase", "kraken", "okx", "okex", "bybit", "kucoin",
    "huobi", "htx", "gate", "gate.io", "gateio", "gate-io", "bitfinex",
    "bitstamp", "gemini", "crypto.com", "cryptocom", "crypto-com", "mexc",
    "bitget", "upbit", "bithumb", "poloniex", "ftx", "robinhood", "bittrex",
    "bitmex", "deribit", "whitebit", "bitso", "coinex", "lbank", "probit",
    "bitkub", "korbit", "coinone", "wazirx", "coincheck", "bitflyer",
    "luno", "cex.io", "nexo", "nobitex", "exchange", "bilaxy", "bitmart",
    "hitbtc", "hotbit", "hoo-com", "digifinex", "latoken", "ascendex",
    "cobinhood", "coinbit", "coinhako", "coinmetro", "crex24", "paribu",
    "remitano", "tidex", "yunbi", "zb-com", "kryptono", "topbtc",
    "quadrigacx", "maskex", "coss-io", "bitcoin-suisse", "changenow",
    "shapeshift", "binance-deposit", "hot-wallet", "cold-wallet", "coinlist",
    "coinsquare", "lcx-ag", "bgogo", "allbit", "abcc", "bitpie",
}
# exact label slugs (eth-labels `label`, etherscan-labels file names)
SLUG_CATEGORY = {
    "ofac-sanctions-lists": "sanctioned",
    "blocked": "frozen",
    "phish-hack": "scam", "something-fishy": "scam", "spam-token": "scam",
    "heist": "hack", "exploit": "hack",
    "gambling": "gambling", "etheroll": "gambling", "fomo3d": "gambling",
    "zethr": "gambling",
    "mining": "mining", "f2pool": "mining", "miningpoolhub": "mining",
    "mev-bot": "mev", "mev-builder": "mev", "backrunning-bots": "mev",
    "ethereum-mixer": "mixer", "tornado-cash": "mixer", "typhoon-cash": "mixer",
    "payments": "service", "fiat-gateway": "service", "wallet-app": "service",
    "otc": "service", "escrow": "service", "wbtc-merchant": "service",
    "wirex": "service", "oobit": "service", "flexa": "service",
    "dex": "defi", "defi": "defi", "staking": "defi", "yield-farming": "defi",
    "vaults": "defi", "liquidity": "defi", "loans": "defi",
    "derivatives": "defi", "options-trading": "defi", "farming": "defi",
    "bridge": "bridge",
}
MIXER_KW = ("tornado", "mixer", "wasabi", "coinjoin", "blender", "sinbad",
            "chipmixer", "railgun")
HACK_KW = ("exploiter", "heist", "hacker", "drainer", "lazarus",
           "stolen funds")
SCAM_KW = ("fake_phishing", "phishing", "scam", "ponzi", "rug pull",
           "sextortion")
GAMBLING_KW = ("casino", "gambling", "sportsbook", "roulette")
MINING_KW = ("mining pool", "miningpool", "f2pool", "ethermine", "sparkpool",
             "spark pool", "nanopool", "2miners", "antpool", "poolin",
             "viabtc", "hiveon", "flexpool")
MEV_KW = ("mev bot", "mev builder", "mev-bot", "sandwich bot")
BRIDGE_KW = ("bridge", "wormhole", "portal", "hop protocol", "across",
             "stargate", "celer", "synapse", "orbiter", "layerzero")
DEFI_KW = ("uniswap", "aave", "curve", "compound", "makerdao", "maker:",
           "lido", "sushi", "1inch", "balancer", "pancakeswap", "gmx",
           "0x-protocol", "0x:", "dydx", "yearn", "convex", "frax")


# label slugs that name a category, not an owner -> take the owner from the tag
GENERIC_SLUGS = set(SLUG_CATEGORY) | {"exchange", "hot-wallet", "cold-wallet",
                                      "binance-deposit"}


def entity_from_tag(slug, tag):
    """`Bithumb 487` / `Upbit: Cold Wallet` -> `Bithumb` / `Upbit` for generic slugs."""
    if slug.lower() not in GENERIC_SLUGS:
        return slug
    if not tag:
        return ""
    head = re.split(r"\s*:\s*|\s+\d+$|\s+(?:hot|cold)\s+wallet", tag,
                    flags=re.I)[0].strip()
    return head or slug


def clean_tag(t):
    t = norm_addr(t)
    return "" if t.lower() in ("null", "none") else t


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
    """Severe keywords first, then the slug table, then softer keyword rules."""
    e = (entity or "").lower()
    text = f"{e} {(label or '').lower()}"
    if e == "ofac-sanctions-lists" or "ofac" in text:
        return "sanctioned"
    if any(k in text for k in HACK_KW):
        return "hack"
    if any(k in text for k in SCAM_KW):
        return "scam"
    if any(k in text for k in MIXER_KW):
        return "mixer"
    if e in SLUG_CATEGORY:
        return SLUG_CATEGORY[e]
    if e in EXCHANGES or any(x in text for x in ("exchange:", " cex")):
        return "exchange"
    if any(k in text for k in GAMBLING_KW):
        return "gambling"
    if any(k in text for k in MINING_KW):
        return "mining"
    if any(k in text for k in MEV_KW):
        return "mev"
    if any(k in text for k in BRIDGE_KW):
        return "bridge"
    if any(k in text for k in DEFI_KW):
        return "defi"
    return "entity"


# addr(lower) + network -> record ; later high-priority sources overwrite
records = {}
# When two sources disagree, the more severe category wins (ties keep the first).
CATEGORY_PRIORITY = {
    "sanctioned": 9, "terrorism": 8, "ransomware": 7, "hack": 6, "scam": 5,
    "darknet": 5, "extremism": 4, "frozen": 4,
    "exchange": 3, "mixer": 3,
    "gambling": 2, "mining": 2, "service": 2, "bridge": 2,
    "mev": 1, "defi": 1,
    "entity": 0, "": 0,
}


_FMT = {
    "evm": re.compile(r"0x[0-9a-fA-F]{40}"),
    "bitcoin": re.compile(r"bc1[02-9ac-hj-np-z]{11,87}|[13][1-9A-HJ-NP-Za-km-z]{25,34}"),
    "tron": re.compile(r"T[1-9A-HJ-NP-Za-km-z]{33}"),
}


def _valid(address, network):
    """Reject strings that cannot be an address on that network (scrape junk)."""
    kind = "evm" if network in EVM_SLUGS or network in EVM_CHAINS.values() else network
    rx = _FMT.get(kind)
    return rx is None or bool(rx.fullmatch(address))


def add(address, network, entity, label, category, source, source_url,
        confidence="medium"):
    address = norm_addr(address)
    if not address or not network:
        return
    if not _valid(address, network):
        # a TRON/EVM address filed under the wrong chain: re-route by format
        network = detect_network(address)
        if not network or not _valid(address, network):
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
        if source not in old["source"].split("+"):
            old["source"] += "+" + source
        if category == old["category"] and confidence == "high":
            old["confidence"] = "high"
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
            slug = norm_addr(row.get("label"))
            label = clean_tag(row.get("nameTag")) or slug
            entity = entity_from_tag(slug, clean_tag(row.get("nameTag")))
            add(addr, net, entity, label, categorize(slug, label),
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

# cex/ registry slug -> display name (from api.llama.fi/protocols, category CEX)
DL_CEX_NAMES = {
    "21-co": "21.co", "TradeOgre": "TradeOgre",
    "arkham-exchange": "Arkham Exchange", "backpack": "Backpack",
    "biconomy-cex": "Biconomy.com", "bigone": "BigONE",
    "binance-us": "Binance.US", "bing-cex": "BingX", "bitkan": "BitKan",
    "bitkub-cex": "Bitkub", "bitlo-cex": "Bitlo", "bitmake": "Bitmake",
    "bitmark": "BitMart", "bitmex": "BitMEX", "bitomato": "BiTomato",
    "bitunix-cex": "Bitunix", "bitvavo": "Bitvavo", "bitvenus": "BitVenus",
    "blofin-cex": "BloFin", "btse": "BTSE", "bybit": "Bybit",
    "bydfi": "BYDFi", "bytedex-cex": "Byte Exchange", "cake-defi": "Bake.io",
    "cex-io": "CEX.IO", "coin8-cex": "Coin8", "coindcx": "CoinDCX",
    "coinex": "CoinEx", "coinsquare": "Coinsquare", "coinstore": "Coinstore",
    "coinw": "CoinW", "crypto-com": "Crypto.com", "deribit": "Deribit",
    "exmo": "Exmo", "fastex": "Fastex", "flipster": "Flipster",
    "gate-us": "Gate US", "grovex": "GroveX", "hashkey": "HashKey Global",
    "hashkey-exchange": "HashKey Exchange", "hibt": "HIBT",
    "hotcoin": "Hotcoin", "indodax": "Indodax", "korbit": "Korbit",
    "latoken": "Latoken", "lbank-exchange": "LBank", "levex": "LeveX",
    "mexc-cex": "MEXC", "nbx": "NBX", "nexo-cex": "Nexo", "niza": "Niza",
    "nonkyc": "NonKYC", "okcoin": "Okcoin", "orangex-cex": "OrangeX",
    "osl": "OSL", "osl-hk": "OSL", "ourbit": "Ourbit", "p2pb2b": "P2B",
    "phemex": "Phemex", "pionex-cex": "Pionex", "poloniex-cex": "Poloniex",
    "probit": "ProBit Global", "robinhood": "Robinhood", "sclite": "SCLiTE",
    "swissborg": "SwissBorg", "toobit": "Toobit", "tothemoon": "Tothemoon",
    "valr-cex": "VALR", "voyager": "Voyager", "webot": "Webot",
    "websea": "Websea", "weex-cex": "WEEX", "woo-cex": "WOO X",
    "zoomex-cex": "Zoomex",
}
# address-book keys that are not exchanges -> (entity, label, category)
DL_BOOK_SPECIAL = {
    "fbiDprk": ("Lazarus Group (DPRK)", "FBI-attributed DPRK wallet", "hack"),
    "silkroad": ("Silk Road", "Silk Road (seized by US gov.)", "darknet"),
    "silkroadFBIEntities": ("Silk Road", "Silk Road (seized by US gov.)",
                            "darknet"),
    "mtGox": ("Mt. Gox", "Mt. Gox (defunct exchange)", "exchange"),
    "elSalvador": ("El Salvador", "El Salvador government reserve", "entity"),
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


def _parse_btc_book(text):
    """Return {book key: [BTC/LTC/DOGE/BCH addresses]} from the address book."""
    book = {}
    # match `key: [ ... ]` and `const key = [ ... ]`
    for m in re.finditer(
            r'(?:const\s+)?["\']?([A-Za-z0-9_-]+)["\']?\s*[:=]\s*\[([^\]]*)\]',
            text):
        addrs = [t for t in _ADDR_TOKEN.findall(m.group(2)) if _btc_family(t)]
        if addrs:
            book.setdefault(m.group(1), []).extend(addrs)
    return book


# `bitcoin: "binance"` / `bitcoin: bitcoinAddressBook.binance` inside a config
_BOOK_REF = re.compile(
    r'\b(?:bitcoin|litecoin|doge)\s*:\s*'
    r'(?:bitcoinAddressBook\.([A-Za-z0-9_]+)|["\']([A-Za-z0-9_-]{2,40})["\'])')
# top-level `'exchange-slug': {` entries of cex/index.js (two-space indent)
_CEX_ENTRY = re.compile(r'^  ["\']?([A-Za-z0-9._-]+)["\']?\s*:\s*\{', re.M)


def _cex_name(slug):
    return DL_CEX_NAMES.get(slug) or slug.replace("-cex", "").replace(
        "-", " ").title()


def load_defillama_cex():
    root = os.path.join(SRC, "dl")
    url = "https://github.com/DefiLlama/DefiLlama-Adapters"
    if not os.path.isdir(root):
        print("  ! DefiLlama-Adapters missing, skipping"); return 0
    counter = [0]
    book_refs = {}                      # book key -> exchange display name

    def parse(text, entity):
        _parse_owner_config(text, entity, url, counter)
        for m in _BOOK_REF.finditer(text):
            book_refs.setdefault(m.group(1) or m.group(2), entity)

    def read(path):
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()

    # 1) legacy per-exchange folders under projects/
    for folder, entity in DL_CEX_FOLDERS.items():
        idx = os.path.join(root, "projects", folder, "index.js")
        if os.path.exists(idx):
            parse(read(idx), entity)
    # 2) cex/index.js — one big registry, split into per-exchange blocks
    cex = os.path.join(root, "cex")
    reg = os.path.join(cex, "index.js")
    if os.path.exists(reg):
        text = read(reg)
        marks = list(_CEX_ENTRY.finditer(text))
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            parse(text[m.end():end], _cex_name(m.group(1)))
    # 3) cex/<slug>.js — larger static configs (dynamic ones yield nothing)
    if os.path.isdir(cex):
        for fn in sorted(os.listdir(cex)):
            if fn.endswith(".js") and fn != "index.js":
                parse(read(os.path.join(cex, fn)), _cex_name(fn[:-3]))
    # 4) shared bitcoin address book: keys referenced by an exchange config,
    #    the legacy whitelist, and a few special (non-exchange) entries
    bdir = os.path.join(root, "projects", "helper", "bitcoin-book")
    book = {}
    if os.path.isdir(bdir):
        for fn in os.listdir(bdir):
            if fn.endswith(".js"):
                for k, v in _parse_btc_book(read(os.path.join(bdir, fn))).items():
                    book.setdefault(k, []).extend(v)
    for key, addrs in book.items():
        special = DL_BOOK_SPECIAL.get(key)
        if special:
            entity, label, category = special
        else:
            entity = book_refs.get(key) or DL_BTC_EXCHANGES.get(key)
            if not entity:
                continue
            label, category = f"{entity} (proof-of-reserves)", "exchange"
        for a in addrs:
            add(a, _btc_family(a), entity, label, category,
                "defillama-cex", url, "high")
            counter[0] += 1
    print(f"  defillama-cex: {counter[0]} rows"); return counter[0]


# --- 6) GraphSense TagPacks (MIT; curated, mostly BTC) ----------------------
GS_CURRENCY = {
    "BTC": "bitcoin", "ETH": "ethereum", "TRX": "tron", "LTC": "litecoin",
    "XMR": "monero", "BCH": "bitcoin-cash", "ZEC": "zcash", "EOS": "eos",
    "DASH": "dash", "DOGE": "dogecoin", "SOL": "solana", "AVAX": "avalanche",
    "MATIC": "polygon", "BSC": "bsc", "BEP20": "bsc", "XRP": "xrp",
    "Ripple": "xrp", "ADA": "cardano", "ALGO": "algorand", "BSV": "bitcoin-sv",
    "ETC": "ethereum-classic", "BTG": "bitcoin-gold", "XVG": "verge",
    "DOT": "polkadot", "ATOM": "cosmos", "XTZ": "tezos", "FTM": "fantom",
    "Near": "near", "ZIL": "zilliqa", "Ziliqa": "zilliqa",
    "Elrond": "multiversx", "APT": "aptos",
    "USDT": None, "USDC": None,       # token tags: detect host chain by format
}
GS_CATEGORY = {
    "exchange": "exchange", "miner": "mining", "coinjoin": "mixer",
    "mixing_service": "mixer", "gambling": "gambling",
    "sanction": "sanctioned", "black_list": "frozen", "defi": "defi",
    "defi_lending": "defi", "defi_dex": "defi", "wallet_service": "service",
    "service": "service", "market": "entity", "user": "entity",
    "organization": "entity",
}
# `abuse` describes what the funds were used for and outranks `category`
GS_ABUSE = {
    "ransomware": "ransomware", "sextortion": "scam", "phishing": "scam",
    "scam": "scam", "ponzi_scheme": "scam", "pyramid_scheme": "scam",
    "investment_fraud": "scam", "service_hack": "hack",
    "terrorism": "terrorism", "extremism": "extremism",
    "sanction": "sanctioned",
}
GS_HIGH = {"service_data", "authority_data", "ledger_immanent"}


def load_graphsense():
    try:
        import yaml
    except ImportError:
        print("  ! PyYAML not installed (pip install pyyaml), skipping graphsense")
        return 0
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    base = os.path.join(SRC, "graphsense")
    if not os.path.isdir(base):
        print("  ! graphsense-tagpacks missing, skipping"); return 0
    url = "https://github.com/graphsense/graphsense-tagpacks"

    def load(path):
        with open(path, encoding="utf-8") as f:
            return yaml.load(f, Loader=loader)

    actors = {}
    adir = os.path.join(base, "actors")
    for fn in os.listdir(adir) if os.path.isdir(adir) else []:
        doc = load(os.path.join(adir, fn)) or {}
        for a in doc.get("actors") or []:
            actors[str(a.get("id"))] = a.get("label") or str(a.get("id"))

    n = 0
    for dirpath, _, files in os.walk(os.path.join(base, "packs")):
        for fn in sorted(files):
            if not fn.endswith((".yaml", ".yml")):
                continue
            doc = load(os.path.join(dirpath, fn))
            if not isinstance(doc, dict):
                continue
            header = {k: v for k, v in doc.items() if k != "tags"}
            for tag in doc.get("tags") or []:
                t = {**header, **tag}
                addr = norm_addr(str(t.get("address") or ""))
                cur = str(t.get("currency") or t.get("network") or "")
                if cur not in GS_CURRENCY:
                    continue
                net = GS_CURRENCY[cur] or detect_network(addr)
                if not net:
                    continue
                cat = (GS_ABUSE.get(t.get("abuse"))
                       or GS_CATEGORY.get(t.get("category")) or "entity")
                label = str(t.get("label") or t.get("title") or "")
                actor = t.get("actor")
                entity = actors.get(str(actor), str(actor)) if actor else label
                conf = "high" if t.get("confidence") in GS_HIGH else "medium"
                add(addr, net, entity, label, cat, "graphsense", url, conf)
                n += 1
    print(f"  graphsense: {n} rows"); return n


# --- 7) Forta labelled datasets (MIT; phishing / exploit contracts) ---------
def load_forta():
    base = os.path.join(SRC, "forta", "labels")
    if not os.path.isdir(base):
        print("  ! forta labelled-datasets missing, skipping"); return 0
    url = "https://github.com/forta-network/labelled-datasets"
    n = 0

    def rows(path):
        if not os.path.exists(path):
            return []
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def bad(tag, default="scam"):
        c = categorize("", tag)
        return c if c in ("hack", "scam", "sanctioned", "mixer") else default

    for cid in sorted(os.listdir(base)):
        try:
            net = EVM_CHAINS.get(int(cid))
        except ValueError:
            continue
        if not net:
            continue
        d = os.path.join(base, cid)
        for r in rows(os.path.join(d, "etherscan_malicious_labels.csv")):
            tag = clean_tag(r.get("wallet_tag"))
            add(r.get("banned_address"), net, "", tag or "malicious address",
                bad(tag), "forta", url, "medium"); n += 1
        for r in rows(os.path.join(d, "phishing_scams.csv")):
            tag = clean_tag(r.get("etherscan_tag"))
            add(r.get("address"), net, "", tag or "phishing / scam",
                bad(tag), "forta", url, "medium"); n += 1
        for r in rows(os.path.join(d, "malicious_smart_contracts.csv")):
            ctag = clean_tag(r.get("contract_tag"))
            wtag = clean_tag(r.get("contract_creator_tag"))
            elab = (r.get("contract_creator_etherscan_label") or "").lower()
            cat = "hack" if elab in ("exploit", "heist") else bad(
                f"{ctag} {wtag} {r.get('notes') or ''}")
            add(r.get("contract_address"), net, "",
                ctag or "malicious contract", cat, "forta", url, "medium")
            n += 1
            if r.get("contract_creator"):
                add(r.get("contract_creator"), net, "",
                    wtag or "deployer of a malicious contract", cat,
                    "forta", url, "medium"); n += 1
    print(f"  forta: {n} rows"); return n


# --- 8) etherscan-labels (MIT; scraped Etherscan-family label pages) --------
ESL_SCANNER = {
    "etherscan": "ethereum", "bscscan": "bsc", "polygonscan": "polygon",
    "arbiscan": "arbitrum", "optimism": "optimism",
    "avalanche": "avalanche", "ftmscan": "fantom",
}


def load_etherscanlabels():
    base = os.path.join(SRC, "etherscanlabels", "data")
    if not os.path.isdir(base):
        print("  ! etherscan-labels missing, skipping"); return 0
    url = "https://github.com/brianleect/etherscan-labels"
    n = 0
    for scanner, net in ESL_SCANNER.items():
        d = os.path.join(base, scanner, "accounts")
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".csv"):
                continue
            slug = fn[:-4]
            with open(os.path.join(d, fn), newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    tag = clean_tag(r.get("Name Tag"))
                    add(r.get("Address"), net, entity_from_tag(slug, tag),
                        tag or slug, categorize(slug, tag),
                        "etherscan-labels", url, "medium")
                    n += 1
    print(f"  etherscan-labels: {n} rows"); return n


# --- 9) API-enriched labels (produced by scripts/enrich.py) -----------------
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


# GitHub rejects files over 100 MB and warns over 50 MB: anything larger than
# this is written gzip-compressed (`.csv.gz` / `.json.gz`) instead.
GZIP_OVER = 50 * 1024 * 1024


def _write(name, text):
    """Write data/<name>, or data/<name>.gz when large; drop the stale twin."""
    import gzip
    data = text.encode("utf-8")
    plain = os.path.join(OUT, name)
    packed = plain + ".gz"
    if len(data) > GZIP_OVER:
        with open(packed, "wb") as raw:
            # mtime=0 + no filename -> byte-identical output for identical data
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                               mtime=0) as gz:
                gz.write(data)
        stale = plain
    else:
        with open(plain, "wb") as f:
            f.write(data)
        stale = packed
    if os.path.exists(stale):
        os.remove(stale)


def _csv_text(recs):
    import io
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=FIELDS, lineterminator="\r\n")
    w.writeheader()
    w.writerows(recs)
    return buf.getvalue()


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
        _write(f"{net}.csv", _csv_text(recs))
        _write(f"{net}.json", json.dumps(recs, ensure_ascii=False, indent=2))
        stats["by_network"][net] = len(recs)
        combined.extend(recs)
        for r in recs:
            stats["by_category"][r["category"]] += 1
            stats["by_source"][r["source"]] += 1

    combined.sort(key=lambda r: (r["network"], r["category"], r["address"]))
    _write("_all.csv", _csv_text(combined))
    _write("_all.json", json.dumps(combined, ensure_ascii=False, indent=2))

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
    load_graphsense()
    load_forta()
    load_etherscanlabels()
    load_enriched()
    stats = write_outputs()
    print(f"\n✓ {stats['total']} unique (address, network) records")
    print("Networks:", ", ".join(f"{k}={v}" for k, v in
          sorted(stats["by_network"].items(), key=lambda x: -x[1])))
    print("Categories:", stats["by_category"])


if __name__ == "__main__":
    main()
