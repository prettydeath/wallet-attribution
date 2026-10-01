#!/usr/bin/env python3
"""
Enrich the attribution dataset with labels fetched live from block-explorer /
data-provider APIs.

Everything is driven by two CSV files (no environment variables required):

  config/providers.csv  - which providers to use and their API keys
  config/addresses.csv  - which addresses to look up

So the common case is simply:

    python3 scripts/enrich.py        # reads both CSVs, appends results
    python3 scripts/build.py         # fold new labels into data/

Built-in providers:
  - tronscan  : TRON address tags (publicTag / addressTag / exchange names)
  - trongrid  : TRON account metadata (flags smart contracts; no public labels)
  - etherscan : EVM verified-contract names via the Etherscan v2 unified API
                (ethereum, bsc, polygon, arbitrum, optimism, base, ...)
  - goplus    : GoPlus Security address risk flags (SlowMist/BlockSec data) on
                EVM chains; keyless; only flagged addresses yield a label

Results are normalized to the repository schema and appended (de-duplicated)
to  enriched/api_labels.jsonl , which build.py reads on the next rebuild.

Optional overrides:
    --config PATH        provider CSV (default config/providers.csv)
    --input PATH         address CSV/TXT (default config/addresses.csv)
    --addresses a,b,c    inline addresses instead of the CSV
    --from-data          enrich addresses already in data/ that lack an entity
    --network SLUG       force a network for all targets
    --limit N            cap number of addresses
    --dry-run            print, do not write
    --selftest           verify parsing offline (no network) and exit
"""
import argparse
import csv
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CONFIG_PROVIDERS = os.path.join(ROOT, "config", "providers.csv")
CONFIG_ADDRESSES = os.path.join(ROOT, "config", "addresses.csv")
ENRICHED_FILE = os.path.join(ROOT, "enriched", "api_labels.jsonl")

FIELDS = ["address", "network", "entity", "label", "category",
          "source", "source_url", "confidence", "last_updated"]
TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")

# --- lightweight category classifier (self-contained for this script) -------
EXCHANGE_HINTS = (
    "binance", "coinbase", "kraken", "okx", "okex", "bybit", "kucoin",
    "huobi", "htx", "gate", "bitfinex", "bitstamp", "gemini", "crypto.com",
    "mexc", "bitget", "upbit", "bithumb", "poloniex", "bittrex", "whitebit",
    "coinex", "lbank", "probit", "exchange", "hot wallet", "cold wallet",
    "hotwallet", "deposit",
)
SCAM_HINTS = ("scam", "phish", "fake", "fraud", "hack", "exploit", "drainer",
              "ponzi", "theft", "stolen")
MIXER_HINTS = ("tornado", "mixer", "tumbler", "coinjoin")


def classify(text):
    """Map a free-text label to one of the schema categories."""
    t = (text or "").lower()
    if any(h in t for h in SCAM_HINTS):
        return "scam"
    if any(h in t for h in MIXER_HINTS):
        return "mixer"
    if any(h in t for h in EXCHANGE_HINTS):
        return "exchange"
    return "entity"


def entity_from_label(label):
    """Derive a coarse entity name from a tag like 'Binance-Hot 3'."""
    if not label:
        return ""
    head = label.replace("_", "-").split("-")[0].split(":")[0]
    return head.strip().rstrip("0123456789 ").strip() or label.strip()


# --- HTTP helper ------------------------------------------------------------
def http_get_json(url, headers=None, timeout=20, retries=3, pause=0.6):
    """GET a URL and parse JSON, with basic retry/backoff. Returns None on failure."""
    req = urllib.request.Request(url, headers=headers or {})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except (urllib.error.HTTPError, urllib.error.URLError,
                TimeoutError, json.JSONDecodeError) as e:
            if attempt == retries - 1:
                sys.stderr.write(f"    ! request failed ({url[:60]}...): {e}\n")
                return None
            time.sleep(pause * (attempt + 1))
    return None


# --- provider framework -----------------------------------------------------
class Provider:
    """Base class. Config (api_key / base_url / sleep) comes from providers.csv.
    Subclasses implement endpoint() and _parse() (a pure function)."""
    name = "base"
    networks = ()
    key_header = None          # HTTP header used to send the API key, if any
    DEFAULT_BASE = ""

    def __init__(self, api_key="", base_url="", sleep=0.25):
        self.api_key = api_key or ""
        self.base = base_url or self.DEFAULT_BASE
        self.sleep = sleep

    def supports(self, network):
        return network in self.networks

    def endpoint(self, address, network):
        raise NotImplementedError

    def headers(self):
        if self.key_header and self.api_key:
            return {self.key_header: self.api_key}
        return {}

    def _parse(self, data, address, network):
        """Turn a raw API response into a normalized record dict, or None."""
        raise NotImplementedError

    def fetch(self, address, network):
        data = http_get_json(self.endpoint(address, network), self.headers())
        if data is None:
            return None
        return self._parse(data, address, network)

    def record(self, address, network, entity, label, category, source_url,
               confidence="medium"):
        return {"address": address, "network": network, "entity": entity or "",
                "label": label or "", "category": category,
                "source": self.name, "source_url": source_url,
                "confidence": confidence, "last_updated": TODAY}


class TronscanProvider(Provider):
    """TRON address tags from Tronscan. This is the strongest TRON source:
    it exposes exchange tags such as 'Binance-Hot' on deposit/hot wallets."""
    name = "tronscan"
    networks = ("tron",)
    key_header = "TRON-PRO-API-KEY"
    DEFAULT_BASE = "https://apilist.tronscanapi.com"
    # Checked in order; first non-empty value wins. Kept broad so the parser
    # survives field-name changes in the Tronscan API.
    TAG_FIELDS = ("publicTag", "addressTag", "redTag", "blueTag", "greyTag",
                  "tag", "name")

    def endpoint(self, address, network):
        return f"{self.base}/api/accountv2?address={address}"

    def _parse(self, data, address, network):
        if not isinstance(data, dict):
            return None
        label = None
        for f in self.TAG_FIELDS:
            v = data.get(f)
            if isinstance(v, str) and v.strip():
                label = v.strip()
                break
        # Tronscan sometimes nests exchange info under an object.
        if not label:
            ex = data.get("exchange") or data.get("addressTagLogo")
            if isinstance(ex, dict):
                label = (ex.get("name") or "").strip() or None
        if not label:
            return None
        url = f"https://tronscan.org/#/address/{address}"
        return self.record(address, network, entity_from_label(label),
                           label, classify(label), url, "medium")


class TronGridProvider(Provider):
    """TronGrid official node API. It has no public label database, so it only
    contributes account metadata: it flags smart-contract addresses. Useful for
    classifying whether a TRON address is a contract, not for naming owners."""
    name = "trongrid"
    networks = ("tron",)
    key_header = "TRON-PRO-API-KEY"
    DEFAULT_BASE = "https://api.trongrid.io"

    def endpoint(self, address, network):
        return f"{self.base}/v1/accounts/{address}"

    def _parse(self, data, address, network):
        rows = data.get("data") if isinstance(data, dict) else None
        if not rows:
            return None
        acc = rows[0]
        # A contract account carries type/contract fields on TronGrid.
        is_contract = (acc.get("type") == "Contract"
                       or "contract" in acc
                       or acc.get("is_contract") is True)
        if not is_contract:
            return None
        url = f"https://tronscan.org/#/address/{address}"
        return self.record(address, network, "", "TRON smart contract",
                           "entity", url, "low")


class EtherscanProvider(Provider):
    """EVM verified-contract names via the Etherscan v2 unified API. A single
    API key works across all supported chains (chainid parameter). Returns the
    verified ContractName when available (e.g. 'UniswapV2Router02'); EOAs and
    unverified contracts yield nothing (the free tier has no private name tags).
    Etherscan takes the key as a query parameter, not a header."""
    name = "etherscan"
    networks = ("ethereum", "bsc", "polygon", "arbitrum", "optimism", "base",
                "avalanche", "gnosis", "celo", "fantom", "linea", "scroll")
    DEFAULT_BASE = "https://api.etherscan.io/v2/api"
    CHAIN_ID = {"ethereum": 1, "bsc": 56, "polygon": 137, "arbitrum": 42161,
                "optimism": 10, "base": 8453, "avalanche": 43114,
                "gnosis": 100, "celo": 42220, "fantom": 250, "linea": 59144,
                "scroll": 534352}

    def endpoint(self, address, network):
        cid = self.CHAIN_ID.get(network, 1)
        return (f"{self.base}?chainid={cid}&module=contract"
                f"&action=getsourcecode&address={address}&apikey={self.api_key}")

    def _parse(self, data, address, network):
        res = data.get("result") if isinstance(data, dict) else None
        if not isinstance(res, list) or not res:
            return None
        name = (res[0].get("ContractName") or "").strip()
        if not name:
            return None
        dom = {"ethereum": "etherscan.io", "bsc": "bscscan.com",
               "polygon": "polygonscan.com", "arbitrum": "arbiscan.io",
               "optimism": "optimistic.etherscan.io",
               "base": "basescan.org"}.get(network, "etherscan.io")
        url = f"https://{dom}/address/{address}"
        cat = classify(name)
        return self.record(address, network, name,
                           f"{name} (verified contract)",
                           cat if cat != "entity" else "defi", url, "medium")


class GoPlusProvider(Provider):
    """GoPlus Security address_security API (free, keyless, rate-limited). It
    returns 0/1 risk flags per address, fed by SlowMist / BlockSec. Only the
    strong flags are mapped (by severity); weak ones such as blacklist_doubt are
    ignored, and a clean address yields nothing. TRON is left out: the endpoint
    accepts it but does not actually recognise TRON addresses (flags nothing)."""
    name = "goplus"
    networks = ("ethereum", "bsc", "polygon", "arbitrum", "optimism",
                "avalanche", "base", "fantom", "gnosis", "linea", "zksync",
                "celo", "cronos")
    key_header = "Authorization"
    DEFAULT_BASE = "https://api.gopluslabs.io/api/v1/address_security"
    CHAIN_ID = {"ethereum": 1, "bsc": 56, "polygon": 137, "arbitrum": 42161,
                "optimism": 10, "avalanche": 43114, "base": 8453,
                "fantom": 250, "gnosis": 100, "linea": 59144, "zksync": 324,
                "celo": 42220, "cronos": 25}
    # (category, flags) in severity order; the first group with a flag set wins.
    FLAG_GROUPS = (
        ("sanctioned", ("sanctioned",)),
        ("hack", ("stealing_attack", "cybercrime")),
        ("scam", ("phishing_activities", "fake_kyc", "blackmail_activities",
                  "honeypot_related_address", "financial_crime",
                  "money_laundering", "number_of_malicious_contracts_created",
                  "malicious_mining_activities")),
        ("darknet", ("darkweb_transactions",)),
        ("mixer", ("mixer",)),
    )

    def endpoint(self, address, network):
        return f"{self.base}/{address}?chain_id={self.CHAIN_ID[network]}"

    @staticmethod
    def _is_set(v):
        # flags are "0"/"1" strings; number_of_malicious_contracts_created is a count
        try:
            return int(v) > 0
        except (TypeError, ValueError):
            return False

    def _parse(self, data, address, network):
        res = data.get("result") if isinstance(data, dict) else None
        if not isinstance(res, dict):
            return None
        category, hit = None, []
        for cat, flags in self.FLAG_GROUPS:
            for f in flags:
                if self._is_set(res.get(f)):
                    category = category or cat
                    hit.append(f)
        if not hit:
            return None
        label = "GoPlus: " + ", ".join(hit)
        src = (res.get("data_source") or "").strip()
        if src:
            label += f" ({src})"
        return self.record(address, network, "", label, category,
                           "https://gopluslabs.io", "medium")


# Registry: provider name -> class. Add a new provider by subclassing Provider
# and adding one entry here (then a row in config/providers.csv).
PROVIDER_CLASSES = {
    "tronscan": TronscanProvider,
    "trongrid": TronGridProvider,
    "etherscan": EtherscanProvider,
    "goplus": GoPlusProvider,
}


# --- CSV config loading -----------------------------------------------------
def _read_csv_rows(path):
    """Yield dict rows from a CSV, skipping blank lines and '#' comments."""
    with open(path, encoding="utf-8") as f:
        lines = [ln for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    yield from csv.DictReader(lines)


def load_providers(config_path):
    """Build enabled provider instances from providers.csv."""
    if not os.path.exists(config_path):
        sys.stderr.write(f"! provider config not found: {config_path}\n")
        return []
    active = []
    for row in _read_csv_rows(config_path):
        name = (row.get("provider") or "").strip().lower()
        if name not in PROVIDER_CLASSES:
            if name:
                sys.stderr.write(f"  ? unknown provider '{name}', skipping\n")
            continue
        if (row.get("enabled") or "").strip().lower() not in ("true", "1", "yes", "y"):
            continue
        try:
            sleep = float(row.get("sleep") or 0.25)
        except ValueError:
            sleep = 0.25
        active.append(PROVIDER_CLASSES[name](
            api_key=(row.get("api_key") or "").strip(),
            base_url=(row.get("base_url") or "").strip(),
            sleep=sleep,
        ))
    return active


def providers_for(network, active):
    return [p for p in active if p.supports(network)]


# --- address -> network detection -------------------------------------------
def detect_network(addr):
    a = addr.strip()
    if a.startswith("0x") and len(a) == 42:
        return "ethereum"       # EVM; caller may override with --network
    if a.startswith("T") and len(a) == 34:
        return "tron"
    if a.startswith(("bc1", "1", "3")):
        return "bitcoin"
    return None


# --- targets (addresses to enrich) ------------------------------------------
def targets_from_args(args):
    """Yield (address, network) from --addresses / --input CSV / --from-data."""
    if args.addresses:
        for a in args.addresses.split(","):
            a = a.strip()
            if a:
                yield a, (args.network or detect_network(a))
        return

    if args.from_data:
        path = os.path.join(DATA, f"{args.network}.json") if args.network \
            else os.path.join(DATA, "_all.json")
        if not os.path.exists(path) and os.path.exists(path + ".gz"):
            path += ".gz"          # build.py gzips files larger than 50 MB
        if not os.path.exists(path):
            sys.stderr.write(f"! {path} not found; run build.py first\n")
            return
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8") as f:
            rows = json.load(f)
        seen = 0
        for r in rows:
            if args.network and r["network"] != args.network:
                continue
            if r.get("entity"):        # already has an owner label
                continue
            yield r["address"], r["network"]
            seen += 1
            if args.limit and seen >= args.limit:
                break
        return

    # default: address CSV
    path = args.input or CONFIG_ADDRESSES
    if not os.path.exists(path):
        sys.stderr.write(f"! address file not found: {path}\n")
        return
    for row in _read_csv_rows(path):
        addr = (row.get("address") or "").strip()
        if not addr:
            continue
        net = (row.get("network") or "").strip() or args.network \
            or detect_network(addr)
        yield addr, net


# --- I/O for the enriched output --------------------------------------------
def load_existing_keys():
    keys = set()
    if os.path.exists(ENRICHED_FILE):
        with open(ENRICHED_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    keys.add((r["address"].lower(), r["network"], r["source"]))
                except (json.JSONDecodeError, KeyError):
                    continue
    return keys


def append_records(records):
    os.makedirs(os.path.dirname(ENRICHED_FILE), exist_ok=True)
    with open(ENRICHED_FILE, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# --- self-test (offline; verifies parsing without network) ------------------
def selftest():
    ok = True

    def check(name, cond):
        nonlocal ok
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        ok = ok and cond

    ts = TronscanProvider()
    r = ts._parse({"publicTag": "Binance-Hot 3"}, "TXaddr", "tron")
    check("tronscan exchange tag", r and r["category"] == "exchange"
          and r["entity"] == "Binance")
    r = ts._parse({"addressTag": "USDT Scam Report"}, "TXaddr", "tron")
    check("tronscan scam tag", r and r["category"] == "scam")
    check("tronscan empty -> None", ts._parse({}, "TXaddr", "tron") is None)

    tg = TronGridProvider()
    r = tg._parse({"data": [{"type": "Contract", "address": "41.."}]}, "T", "tron")
    check("trongrid contract flagged", r and "contract" in r["label"].lower())
    check("trongrid normal account -> None",
          tg._parse({"data": [{"address": "41.."}]}, "T", "tron") is None)

    es = EtherscanProvider()
    r = es._parse({"status": "1", "result": [{"ContractName": "UniswapV2Router02"}]},
                  "0xabc", "ethereum")
    check("etherscan verified contract", r and r["entity"] == "UniswapV2Router02")
    check("etherscan EOA -> None",
          es._parse({"status": "1", "result": [{"ContractName": ""}]},
                    "0xabc", "ethereum") is None)

    gp = GoPlusProvider()
    r = gp._parse({"code": 1, "result": {
        "stealing_attack": "1", "phishing_activities": "1",
        "blacklist_doubt": "1", "cybercrime": "0", "sanctioned": "0",
        "data_source": "SlowMist,BlockSec"}}, "0xabc", "ethereum")
    check("goplus flagged -> hack + label", r and r["category"] == "hack"
          and r["label"] == "GoPlus: stealing_attack, phishing_activities "
                            "(SlowMist,BlockSec)")
    r = gp._parse({"result": {"sanctioned": "1", "phishing_activities": "1"}},
                  "0xabc", "ethereum")
    check("goplus sanctioned outranks scam", r and r["category"] == "sanctioned")
    r = gp._parse({"result": {"number_of_malicious_contracts_created": "3"}},
                  "0xabc", "ethereum")
    check("goplus malicious-contract count -> scam", r and r["category"] == "scam")
    check("goplus clean / weak flags only -> None",
          gp._parse({"result": {"blacklist_doubt": "1", "gas_abuse": "1",
                                "stealing_attack": "0", "data_source": ""}},
                    "0xabc", "ethereum") is None)

    # config parsing
    import io
    rows = list(csv.DictReader(
        [ln for ln in io.StringIO(
            "# comment\nprovider,api_key,enabled,base_url,sleep\n"
            "tronscan,,true,,0.1\ntrongrid,,false,,\n")
         if ln.strip() and not ln.lstrip().startswith("#")]))
    check("providers.csv parsed (2 rows)", len(rows) == 2)

    print("\nSelf-test:", "OK" if ok else "FAILURES")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Enrich attribution via provider APIs (CSV-driven)")
    ap.add_argument("--config", default=CONFIG_PROVIDERS,
                    help="provider CSV (default config/providers.csv)")
    ap.add_argument("--input", help="address CSV/TXT (default config/addresses.csv)")
    ap.add_argument("--addresses", help="comma-separated addresses (overrides --input)")
    ap.add_argument("--from-data", action="store_true",
                    help="enrich addresses already in data/ that lack an entity")
    ap.add_argument("--network", help="force a network slug for all targets")
    ap.add_argument("--limit", type=int, default=0, help="max addresses (0 = no limit)")
    ap.add_argument("--dry-run", action="store_true", help="print, do not write")
    ap.add_argument("--selftest", action="store_true",
                    help="verify parsing offline and exit")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(selftest())

    active = load_providers(args.config)
    if not active:
        sys.exit("No enabled providers. Edit config/providers.csv "
                 "(set enabled=true and add an api_key where needed).")
    print("Enabled providers:", ", ".join(p.name for p in active))

    existing = load_existing_keys()
    new_records, hits, calls = [], 0, 0
    for address, network in targets_from_args(args):
        if not network:
            sys.stderr.write(f"  ? cannot detect network for {address}, skipping\n")
            continue
        for prov in providers_for(network, active):
            if (address.lower(), network, prov.name) in existing:
                continue
            calls += 1
            rec = prov.fetch(address, network)
            time.sleep(prov.sleep)
            if rec:
                hits += 1
                existing.add((address.lower(), network, prov.name))
                new_records.append(rec)
                print(f"  + [{prov.name}] {network} {address[:12]}… "
                      f"-> {rec['entity'] or rec['label']} ({rec['category']})")

    print(f"\n{calls} API calls, {hits} labels found, "
          f"{len(new_records)} new records.")
    if args.dry_run:
        print("(dry-run: nothing written)")
    elif new_records:
        append_records(new_records)
        print(f"Appended to {os.path.relpath(ENRICHED_FILE, ROOT)} "
              f"— run build.py to fold into the dataset.")


if __name__ == "__main__":
    main()
