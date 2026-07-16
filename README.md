# Payer-Library-Prototype

A searchable/sortable capability matrix of insurance payers for SimplePractice clinicians and practice admins — can we bill this payer electronically, check eligibility, or receive ERAs?

Open `index.html` directly in a browser (double-click works - no build step, no server, no other files to load).

If `data/source.csv` changes, regenerate in two steps:

```
python3 scripts/rank_payers.py --write   # recompute SizeRank / Credentialing / Patient Cost Estimates
python3 scripts/build_data.py            # re-embed the data into index.html
```
