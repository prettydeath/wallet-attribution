# wallet-attribution

![Addresses](https://img.shields.io/badge/addresses-634%2C626-2ea44f)
![Networks](https://img.shields.io/badge/networks-48-1f6feb)
![Exchange wallets](https://img.shields.io/badge/exchange%20wallets-383%2C266-e3742f)
![Format](https://img.shields.io/badge/format-CSV%20%2B%20JSON-6f42c1)
![License](https://img.shields.io/badge/license-MIT-blue)

An aggregated, normalized dataset of **attributed cryptocurrency addresses** —
exchanges, sanctioned entities, hackers, ransomware, scams, mixers, mining
pools, gambling, bridges and DeFi protocols —
built entirely from **free, public sources** and stored as plain **CSV + JSON**
per network.

> One schema, many chains. Re-buildable from upstream with two commands.

## What's inside

```
data/
  ethereum.csv / ethereum.json      # per-network files (same nine columns)
  bsc.csv / bsc.json
  bitcoin.csv / ...
  bitcoin.csv.gz / bitcoin.json.gz  # files > 50 MB are stored gzip-compressed
  _all.csv.gz / _all.json.gz        # everything combined (gzip)
  stats.json                        # counts by network / category / source
scripts/
  fetch_sources.sh                  # clone/update the upstream sources
  fetch_onchain.py                  # Ransomwhere + live USDT/USDC blacklists
  build.py                          # normalize sources -> data/
  enrich.py                         # fetch labels live from provider APIs
config/
  providers.csv                     # which APIs to use + your API keys
  addresses.csv                     # which addresses to look up
enriched/
  api_labels.jsonl                  # API-derived labels (committed input)
schema.md                          # field definitions & merge rules
```

Field definitions and the merge logic are in [`schema.md`](schema.md).

## Sources (all free)

| Source | Covers | Category | Link |
|--------|--------|----------|------|
| **eth-labels** | ~144k labelled addresses on 9 EVM chains (ETH, BSC, Base, Arbitrum, Optimism, Avalanche, Gnosis, Celo, WorldChain) | exchange / defi / bridge / entity | https://github.com/dawsbot/eth-labels |
| **cex-list** | Curated CEX hot-wallet addresses (Ethereum) | exchange | https://github.com/tradezon/cex-list |
| **MyEtherWallet/ethereum-lists** | Phishing / scam dark-list | scam | https://github.com/MyEtherWallet/ethereum-lists |
| **OFAC SDN (0xB10C)** | US-sanctioned addresses across BTC, ETH, LTC, BCH, XRP, TRX, BSC, ARB, and more | sanctioned | https://github.com/0xB10C/ofac-sanctioned-digital-currency-addresses |
| **DefiLlama-Adapters** | **Officially-disclosed proof-of-reserves wallets** exchanges publish on their own transparency pages — the `cex/` registry (~100 exchanges: Arkham, BingX, BitMart, BitMEX, MEXC, Crypto.com, Deribit, OSL, Phemex, WOO X, …) plus the older OKX, Binance, Bitget, Gate, KuCoin, HTX, Bitfinex, Coinbase adapters, across 40+ chains incl. BTC/LTC/DOGE/TRON/SOL/XRP/ADA cold wallets; also the address-book entries for FBI-attributed DPRK wallets, seized Silk Road funds and Mt. Gox | exchange / hack / darknet (`confidence: high`) | https://github.com/DefiLlama/DefiLlama-Adapters |
| **GraphSense TagPacks** (MIT) | ~525k curated tags, mostly **Bitcoin**: exchange clusters, mining pools, CoinJoin (Wasabi/Samourai), ransomware, sextortion spam, hacks, terrorism financing, USDT blacklist | exchange / mining / mixer / ransomware / scam / hack / terrorism / frozen | https://github.com/graphsense/graphsense-tagpacks |
| **Forta labelled-datasets** (MIT) | Phishing addresses, exploiter wallets and malicious contracts + their deployers (Ethereum, Optimism) | scam / hack | https://github.com/forta-network/labelled-datasets |
| **Ransomwhere** | Crowdsourced ransomware payment addresses with family names (Locky, Conti, Ryuk, NetWalker…), via its open export API | ransomware | https://ransomwhe.re |
| **USDT / USDC blacklists (on-chain)** | Addresses **currently** frozen by Tether (Ethereum + TRON) and Circle (Ethereum), rebuilt from the contracts' own `AddedBlackList`/`RemovedBlackList` and `Blacklisted`/`UnBlacklisted` events — unfreezes are applied | frozen (`confidence: high`) | Tether / Circle contracts |
| **etherscan-labels** (MIT) | Scraped label pages of Etherscan, BscScan, Polygonscan, Arbiscan, Optimism, Snowtrace, FtmScan | all categories | https://github.com/brianleect/etherscan-labels |

## Rebuild / update

```bash
bash scripts/fetch_sources.sh     # pull latest upstream data into ./sources
                                  # (also runs scripts/fetch_onchain.py)
python3 scripts/build.py          # normalize -> ./data  (needs: pip install pyyaml)
```

`fetch_sources.sh` clones on first run and `git pull`s on later runs, so a refresh
is just re-running both commands. `sources/` is git-ignored — only the normalized
`data/` is versioned, which keeps diffs clean and reviewable.

## Official exchange wallets (proof-of-reserves)

Exchanges publish their own wallet addresses on transparency / proof-of-reserves
pages. Those officially-disclosed wallets are pulled in via the DefiLlama-Adapters
source and tagged `category = exchange`, `confidence = high`,
`source = defillama-cex` — including **cold wallets** on non-EVM chains
(BTC, LTC, DOGE, TRON, Solana, XRP, Cardano…) that the label datasets miss.
This is the cleanest free way to attribute an exchange's *own* declared wallets.
Refreshing is automatic: `fetch_sources.sh` re-pulls the adapters, `build.py`
re-parses them.

## On-chain sources (`fetch_onchain.py`)

`scripts/fetch_onchain.py` (run by `fetch_sources.sh`) snapshots data that lives
on-chain or behind open APIs into `sources/onchain/`:

- **Stablecoin blacklists.** Every blacklist / un-blacklist event of USDT
  (Ethereum, TRON) and USDC (Ethereum) is replayed in order, so the result is the
  *current* frozen set: an address Circle unfroze in 2025 (e.g. the Tornado.Cash
  router after its OFAC delisting) is not included.
- **Ransomwhere** export.

No API key is needed. Ethereum logs use the keyless Blockscout API (about ten
calls per run; it allows ~10 requests per ~40-minute window per IP, and the
script waits out the limit). With a free Etherscan key in the `ETHERSCAN_API_KEY`
environment variable the script uses Etherscan V2 instead, which is faster. Use the
environment variable rather than `config/providers.csv` if you push this repo.
TRON events come from TronGrid. A failed refresh keeps the previous snapshot.

## Live API enrichment (CSV-driven)

The static datasets above cover *known* wallets. To attribute addresses they
miss — especially **TRON exchange deposit/hot wallets** tagged on Tronscan
(`Binance-Hot`, `OKX`, `Bybit`, …) — use the enricher. It is configured with two
plain CSV files, so there are no environment variables and no long command lines.

**1. Put your API keys in `config/providers.csv`** and flip `enabled` to `true`:

```csv
provider,api_key,enabled,base_url,sleep
tronscan,YOUR_TRONSCAN_KEY,true,,0.25
trongrid,,false,,0.25
etherscan,YOUR_ETHERSCAN_KEY,false,,0.25
```

**2. List the addresses in `config/addresses.csv`** (network optional — auto-detected):

```csv
address,network
TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t,tron
0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045,ethereum
```

**3. Run:**

```bash
python3 scripts/enrich.py            # reads both CSVs, appends results
python3 scripts/build.py             # fold the new labels into data/
```

Built-in providers:

| Provider (`providers.csv`) | Networks | What it returns | API key |
|----------|----------|-----------------|---------|
| `tronscan` | TRON | Public address tags incl. exchange names (`Binance-Hot`, …) — the strongest free TRON attribution | recommended (free at tronscan.org) |
| `trongrid` | TRON | Account metadata — flags smart-contract addresses (no public labels) | optional |
| `etherscan` | ETH, BSC, Polygon, Arbitrum, Optimism, Base, … | Verified-contract names via the Etherscan **v2** unified API (one key, all chains) | required |
| `goplus` | 30+ EVM chains (ETH, BSC, Polygon, Arbitrum, Optimism, Base, …) | Risk flags from GoPlus Security (SlowMist / BlockSec data): sanctioned, hack, scam, darknet, mixer. Clean addresses yield no label | none (keyless, rate-limited) |

Handy overrides (optional):

```bash
python3 scripts/enrich.py --addresses TR7...,TX...      # inline instead of the CSV
python3 scripts/enrich.py --from-data --network tron --limit 200  # enrich gaps in data/
python3 scripts/enrich.py --dry-run                     # print, write nothing
python3 scripts/enrich.py --selftest                    # verify parsing offline (no network)
```

Results are appended (de-duplicated) to `enriched/api_labels.jsonl`, which is
committed and re-read by `build.py`.

### Add another provider

Subclass `Provider` in `scripts/enrich.py`, set `name` and `networks`, and
implement `endpoint()` + `_parse()` (a pure function from raw JSON to a
normalized record). Add one entry to the `PROVIDER_CLASSES` map and one row to
`config/providers.csv` — that's it. Good candidates: Blockchair (BTC/LTC/DOGE
tags), other Etherscan-family scanners, Arkham, OKLink.

## Coverage notes (honest limitations)

- **EVM chains are richest** — most labels come from `eth-labels`.
- **Bitcoin** is now well covered (~484k addresses) thanks to GraphSense TagPacks —
  but most of its exchange tags come from address **clustering** (`confidence: medium`)
  and the dataset is largely historical (many tags date from 2013–2020).
- **LTC / BCH / XRP** still carry mainly **OFAC sanctioned** addresses plus
  proof-of-reserves exchange wallets.
- `mixer` on Bitcoin includes CoinJoin **output** addresses (Wasabi / Samourai):
  they show the coins were mixed, not that the owner is a mixer operator.
- **TRON** entity labels are thin in public repos. For USDT-TRC20 exchange
  attribution, enrich against the **Tronscan** API (it tags `Binance-Hot`, `OKX`,
  `Bybit`, …). A `TRONSCAN_API_KEY` is free.
- **Dogecoin** has no public labelled list yet — enrich via **Blockchair** or Arkham.
- Public lists cover exchanges' **main hot/cold wallets**, not the per-user deposit
  addresses an exchange issues to each customer — those are only recoverable through
  on-chain **tracing**, not from a static list.

## How to extend it

Add a `load_<source>()` function in `scripts/build.py` that calls `add(...)` with the
nine-field schema, then list its fetch in `scripts/fetch_sources.sh`. The merge/dedup
logic handles overlaps automatically. Good candidates to add: Tronscan tag enrichment,
Blockchair labels, a private `data/manual.csv` for your own forensic attributions.

To add a **category**: give it a rank in `CATEGORY_PRIORITY`, map label slugs to it
in `SLUG_CATEGORY` (and/or a keyword tuple in `categorize()`), and describe it in
[`schema.md`](schema.md).

## Licensing

The **code** (scripts and tooling) is released under the MIT License — see
[`LICENSE`](LICENSE).

The **data** under `data/` aggregates third-party datasets, each of which retains
its own upstream license (see the Sources table above). Review those before any
redistribution or commercial use. The OFAC SDN data is US-government public record.
