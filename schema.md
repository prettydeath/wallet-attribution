# Schema

Every record — in both the per-network CSV and JSON files — has the same nine fields.

| Field          | Type   | Description                                                                 |
|----------------|--------|-----------------------------------------------------------------------------|
| `address`      | string | The wallet/contract address, original casing (EVM matched case-insensitively). |
| `network`      | string | Chain slug: `ethereum`, `bsc`, `polygon`, `base`, `arbitrum`, `optimism`, `avalanche`, `gnosis`, `celo`, `worldchain`, `tron`, `bitcoin`, `litecoin`, `bitcoin-cash`, `dogecoin`, `xrp`, … |
| `entity`       | string | Owning entity / project, e.g. `Binance`, `Uniswap`, `OFAC SDN`.             |
| `label`        | string | Human-readable name tag, e.g. `Binance 14`, `Tornado.Cash Router`.          |
| `category`     | string | One of the categories below (`exchange`, `sanctioned`, `scam`, `hack`, `ransomware`, `mixer`, `mining`, …). |
| `source`       | string | Upstream dataset(s). Multiple joined with `+` when records were merged.      |
| `source_url`   | string | Link to the upstream dataset.                                               |
| `confidence`   | string | `high` (curated / official lists) or `medium` (scraped labels).             |
| `last_updated` | string | ISO date the record was (re)built.                                          |

## Uniqueness & merge rules

The primary key is `(address, network)` — EVM addresses are lower-cased for matching.
When the same address appears in more than one source, records are **merged**, not duplicated:

- The **higher-priority (more severe) category** wins, in this order:
  `sanctioned > terrorism > ransomware > hack > scam = darknet > extremism = frozen
  > exchange = mixer > gambling = mining = service = bridge > mev = defi > entity`.
  On a tie the record seen first is kept.
- Missing `entity` / `label` fields are back-filled from the other record.
- All contributing sources are kept in `source`, joined with `+`.
- If a source with the same category is `high` confidence, the merged record becomes `high`.
- Strings that are not a valid address for their network (truncated `0x12…ab`
  scrape artefacts, page text) are dropped; an address filed under the wrong
  chain is re-routed by its format.

So a Binance address that is also in the curated CEX list keeps `category = exchange`
and `source = cex-list+eth-labels`.

## Category definitions

| Category | Meaning | Main sources |
|----------|---------|--------------|
| `sanctioned` | On the US OFAC SDN list (or tagged "OFAC Blocked"). | ofac-sdn, graphsense, eth-labels |
| `terrorism` | Terrorism-financing campaigns (e.g. US forfeiture of al-Qaeda wallets). | graphsense |
| `ransomware` | Ransom payment addresses (Locky, Ryuk, …). | ransomwhere, graphsense |
| `hack` | Exploiters, heist wallets, drainers, attributed state hackers (Lazarus / DPRK). | eth-labels, forta, graphsense, defillama |
| `scam` | Phishing, fraud, ponzi, sextortion-spam, dark-list addresses. | mew, forta, graphsense, eth-labels |
| `darknet` | Darknet markets (e.g. seized Silk Road wallets). | defillama |
| `extremism` | Donation addresses of extremist groups. | graphsense |
| `frozen` | Blacklisted by a stablecoin issuer (USDT/USDC) / "Blocked" on Etherscan. Records with source `tether-blacklist` / `circle-blacklist` are the **current** on-chain state (`high`); others are older label snapshots and may since have been unfrozen. | tether-/circle-blacklist, graphsense, eth-labels |
| `exchange` | Centralised-exchange hot/cold/deposit wallets (Binance, Coinbase, OKX…). | defillama, cex-list, eth-labels, graphsense |
| `mixer` | Mixers and CoinJoin coordinators/outputs (Tornado.Cash, Wasabi, Samourai…). | graphsense, eth-labels |
| `gambling` | Online casinos, dice and betting contracts. | graphsense, eth-labels |
| `mining` | Mining pools and payout addresses. | graphsense, eth-labels |
| `service` | Payment processors, custodial wallets, OTC desks, fiat gateways. | graphsense, eth-labels |
| `bridge` | Cross-chain bridges. | eth-labels |
| `mev` | MEV bots and block builders. | eth-labels |
| `defi` | DeFi protocol contracts (DEXes, lending, staking…). | eth-labels, graphsense |
| `entity` | A known named entity that doesn't fit the buckets above. | all |

Labels from free-form datasets (eth-labels, etherscan-labels) are categorised by
`categorize()` in `scripts/build.py`: severe keywords first (OFAC → hack → scam →
mixer), then the exact label-slug table `SLUG_CATEGORY`, then softer keywords
(exchange, gambling, mining, MEV, bridge, DeFi). GraphSense tags map through
`GS_ABUSE` (what the funds were used for — wins) and `GS_CATEGORY`.
