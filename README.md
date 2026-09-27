# Trading_App

AI-assisted NSE swing-trading research pipeline.

This V1 is intentionally **manual-trade only**. It validates Screener Pro
fundamentals, builds a company master, fetches Groww OHLCV, calculates
deterministic SMC-style evidence, adds optional event context, and produces a
research shortlist. It does not place broker orders.

## Project structure

```text
Trading_App/
├── ai/
│   └── ai_validation.py
├── fundamentals/
│   ├── csv_validator.py
│   ├── master_builder.py
│   └── screener_symbols.py
├── market_data/
│   ├── groww_auth.py
│   └── groww_fetch.py
├── news/
│   └── news_engine.py
├── technical/
│   └── smc_engine.py
├── tests/
│   ├── test_csv_validator.py
│   └── test_smc_engine.py
├── .gitignore
├── config.py
├── main.py
├── README.md
└── requirements.txt
# Trading_App

AI-assisted NSE swing-trading research pipeline.

This V1 is intentionally **manual-trade only**. It does not place broker
orders.

## Current architecture

```text
Screener Pro CSV
    ↓
Core-50 validation
    ↓
Company master
    ↓
Selected NSE symbols
    ↓
Groww instrument validation
    ↓
Groww 1H historical candles
    ├── Daily
    │    ├── Weekly
    │    └── Monthly
    │
    └── 1H context

Groww 15m historical candles
    ↓
Session-aligned 75m setup candles

Fundamentals + SMC + event evidence
    ↓
DATA_REJECT / REJECT / WATCH / CANDIDATE
    ↓
Manual human review