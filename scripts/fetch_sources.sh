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
  projects/binance projects/bitfinex projects/bitget projects/gate-io
  projects/huobi projects/kucoin projects/coinbase-btc projects/coinbase-ltc
  projects/coinbase-xrp projects/coinbase-ada projects/bitstamp
  projects/gemini projects/bitrue-cex projects/okex
)
if [ -d dl/.git ]; then
  echo "↻ updating dl (DefiLlama-Adapters)"
  git -C dl pull -q --ff-only 2>/dev/null || echo "  (pull skipped)"
else
  echo "⬇ cloning dl (DefiLlama-Adapters, sparse)"
  git clone --no-checkout --depth 1 --filter=blob:none -q \
    https://github.com/DefiLlama/DefiLlama-Adapters.git dl
  git -C dl sparse-checkout set --cone "${DL_PATHS[@]}"
  git -C dl checkout -q
fi

echo "✓ sources ready in $SRC"
