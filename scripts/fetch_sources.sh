#!/usr/bin/env bash
# Fetch / update all upstream attribution sources into ./sources
# Usage: bash scripts/fetch_sources.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/sources"
mkdir -p "$SRC"
cd "$SRC"

clone_or_pull () {
  local dir="$1" url="$2"
  if [ -d "$dir/.git" ]; then
    echo "↻ updating $dir"
    git -C "$dir" pull -q --ff-only || echo "  (pull skipped)"
  else
    echo "⬇ cloning $dir"
    git clone --depth 1 -q "$url" "$dir"
  fi
}

# EVM entity / exchange / protocol labels (~144k addresses, 9 EVM chains)
clone_or_pull ethlabels https://github.com/dawsbot/eth-labels.git

# Curated CEX hot-wallet addresses (Ethereum mainnet)
clone_or_pull cexlist   https://github.com/tradezon/cex-list.git

# Scam / phishing / dark-list addresses
clone_or_pull mew       https://github.com/MyEtherWallet/ethereum-lists.git

# OFAC sanctioned addresses — needs the auto-generated `lists` branch
clone_or_pull ofac      https://github.com/0xB10C/ofac-sanctioned-digital-currency-addresses.git
git -C ofac remote set-branches origin '*' 2>/dev/null || true
git -C ofac fetch -q --depth 1 origin lists:lists 2>/dev/null || git -C ofac fetch -q origin lists 2>/dev/null || true
git -C ofac checkout -q lists 2>/dev/null || echo "  (OFAC lists branch already checked out)"
git -C ofac pull -q --ff-only 2>/dev/null || true

# DefiLlama-Adapters — official proof-of-reserves wallets per exchange.
# Huge repo: use a partial + sparse checkout of just the CEX adapters
# and the shared bitcoin address book (~4 MB instead of the full repo).
DL_PATHS=(
  projects/helper/bitcoin-book
  # registry of ~100 exchanges' static PoR configs + per-exchange files
  cex
  # older per-exchange adapters still living under projects/
  projects/binance projects/bitfinex projects/bitget projects/bitrue-cex
  projects/bitstamp projects/coinbase-btc projects/gate-io projects/gemini
  projects/huobi projects/kucoin projects/okex
)
  projects/TradeOgre projects/arkham-exchange projects/backpack
  projects/biconomy-cex projects/bigone projects/binance projects/bing-cex
  projects/bitfinex projects/bitget projects/bitkan projects/bitkub-cex
  projects/bitlo-cex projects/bitmark projects/bitmex projects/bitomato
  projects/bitrue-cex projects/bitstamp projects/bitunix-cex projects/bitvavo
  projects/bitvenus projects/blofin-cex projects/btse projects/bybit
  projects/bydfi projects/bytedex-cex projects/cake-defi projects/cex-io
  projects/coin8-cex projects/coindcx projects/coinex projects/coinsquare
  projects/coinstore projects/coinw projects/crypto-com projects/deribit
  projects/exmo projects/fastex projects/flipster projects/gate-io
  projects/gate-us projects/gemini projects/grovex projects/hashkey
  projects/hashkey-exchange projects/hibt projects/hotcoin projects/huobi
  projects/indodax projects/korbit projects/kucoin projects/latoken
  projects/lbank-exchange projects/levex projects/mexc-cex projects/nbx
  projects/nexo-cex projects/niza projects/nonkyc projects/okcoin
  projects/okex projects/orangex-cex projects/osl projects/ourbit
  projects/p2pb2b projects/phemex projects/pionex-cex projects/poloniex-cex
  projects/probit projects/robinhood projects/sclite projects/swissborg
  projects/toobit projects/tothemoon projects/valr-cex projects/voyager
  projects/webot projects/weex-cex projects/woo-cex projects/zoomex-cex
  # Coinbase discloses per-chain wallets in separate adapters
  projects/coinbase-btc projects/coinbase-ltc projects/coinbase-xrp
  projects/coinbase-ada
)
if [ -d dl/.git ]; then
  echo "↻ updating dl (DefiLlama-Adapters)"
  git -C dl pull -q --ff-only 2>/dev/null || echo "  (pull skipped)"
  git -C dl sparse-checkout set --cone "${DL_PATHS[@]}"
else
  echo "⬇ cloning dl (DefiLlama-Adapters, sparse)"
  git clone --no-checkout --depth 1 --filter=blob:none -q \
    https://github.com/DefiLlama/DefiLlama-Adapters.git dl
  git -C dl sparse-checkout set --cone "${DL_PATHS[@]}"
  git -C dl checkout -q
fi

# GraphSense TagPacks (MIT) — ~500k curated tags, mostly BTC: exchanges,
# mining pools, coinjoins, ransomware, sextortion, hacks, OFAC
clone_or_pull graphsense https://github.com/graphsense/graphsense-tagpacks.git

# Forta labelled datasets (MIT) — phishing / exploit / malicious contracts
clone_or_pull forta https://github.com/forta-network/labelled-datasets.git

# etherscan-labels (MIT) — scraped Etherscan-family label pages, 7 EVM chains
clone_or_pull etherscanlabels https://github.com/brianleect/etherscan-labels.git

echo "✓ sources ready in $SRC"
