# enriched/

`api_labels.jsonl` holds attribution records fetched live from provider APIs
(Tronscan, TronGrid, Etherscan, …) by [`../scripts/enrich.py`](../scripts/enrich.py).

One JSON object per line, using the same nine-field schema as the rest of the
dataset (see [`../schema.md`](../schema.md)). This file **is** version-controlled
(unlike `sources/`) because it is your own accumulated, API-derived attribution —
`build.py` folds it into `data/` on every rebuild.

Records are de-duplicated by `(address, network, source)`, so re-running
`enrich.py` only appends genuinely new findings.
