# Schema

Every record — in both the per-network CSV and JSON files — has the same nine fields.

| Field          | Type   | Description                                                                 |
|----------------|--------|-----------------------------------------------------------------------------|
| `address`      | string | The wallet/contract address, original casing (EVM matched case-insensitively). |
| `network`      | string | Chain slug: `ethereum`, `bsc`, `polygon`, `base`, `arbitrum`, `optimism`, `avalanche`, `gnosis`, `celo`, `worldchain`, `tron`, `bitcoin`, `litecoin`, `bitcoin-cash`, `dogecoin`, `xrp`, … |
| `entity`       | string | Owning entity / project, e.g. `Binance`, `Uniswap`, `OFAC SDN`.             |
| `label`        | string | Human-readable name tag, e.g. `Binance 14`, `Tornado.Cash Router`.          |
| `category`     | string | One of: `exchange`, `sanctioned`, `scam`, `mixer`, `bridge`, `defi`, `entity`. |
| `source`       | string | Upstream dataset(s). Multiple joined with `+` when records were merged.      |
| `source_url`   | string | Link to the upstream dataset.                                               |
| `confidence`   | string | `high` (curated / official lists) or `medium` (scraped labels).             |
| `last_updated` | string | ISO date the record was (re)built.                                          |

## Uniqueness & merge rules

The primary key is `(address, network)` — EVM addresses are lower-cased for matching.
When the same address appears in more than one source, records are **merged**, not duplicated:

- The **higher-priority category** wins, in this order:
  `sanctioned > scam > exchange = mixer > bridge > defi > entity`.
- Missing `entity` / `label` fields are back-filled from the other record.
- All contributing sources are kept in `source`, joined with `+`.

So a Binance address that is also in the curated CEX list keeps `category = exchange`
and `source = cex-list+eth-labels`.

## Category definitions

- **exchange** — centralised-exchange hot/cold wallets (Binance, Coinbase, OKX…).
- **sanctioned** — on the US OFAC SDN list.
- **scam** — phishing / fraud / dark-list addresses.
- **mixer** — mixers / tumblers (Tornado.Cash, Wasabi…).
- **bridge** — cross-chain bridges.
- **defi** — DeFi protocol contracts (DEXes, lending, staking…).
- **entity** — a known named entity that doesn't fit the buckets above.
