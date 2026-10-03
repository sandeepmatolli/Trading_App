# Trading_App

AI-assisted NSE equity swing-trading **research and manual decision-support** pipeline. V1 produces research output only; it does not place, modify, or cancel broker orders. A saved `CANDIDATE` is not a live trading signal.

## Current pipeline

```text
Screener Pro CSV → Core-50 validation → fundamental/event pre-filter
                   ↓ current-day selected NSE queue
Groww instrument lookup + chunked 1H and 15m OHLCV
                   ↓ data-quality, requested-lookback and closed-candle gates
1H → derived Daily → closed Weekly/Monthly; 15m → fixed session-aligned 75m
                   ↓ corporate-action review + non-repainting SMC + official NSE events
DATA_REJECT / REJECT / WATCH / CANDIDATE → manual human review
                   ↓ optional bounded multi-day research campaign
Archived checkpoint / merged report / immutable child results / campaign ledger
                   ↓ optional audited same-day digest and historical report
```

Financial-sector instruments (banks, NBFCs and insurers) are deferred until their dedicated fundamentals model is implemented. Price discontinuities can truncate technical history. Old research results are historical only and require fresh review before any manual trade.

## Key entrypoints

| Script | Purpose | External calls? |
|---|---|---|
| `prefilter_scan.py` | Validate the configured Screener CSV; fetch official event context; write a selected queue and report. `--run-main` explicitly launches research. | Official source fetches; Groww only with `--run-main` |
| `batch_scan.py` | Bounded, resumable research over a verified current-day report and queue. Defaults to one symbol and one child launch. | Yes, when launched |
| `campaign_scan.py` | `record`, `prepare`, and `summary` for multi-day progress; historical decisions are not refreshed. | No Groww calls; `prepare` uses already generated pre-filter inputs |
| `research_digest.py` | Verify one recorded **same-IST-day** campaign session against its saved evidence. | No |
| `campaign_history.py` | Audit the campaign's recorded history across older scanner sessions. | No |
| `request_budget.py` | Estimate baseline Groww historical-candle request windows; not a quota, ceiling or authorization to scan. | No |
| `main.py` | Run the selected symbols through the existing deep research pipeline. | Yes |

## Install and offline verification

Use a supported Python 3.9 environment and install the pinned requirements (see `requirements.txt`). Keep API credentials in a local `.env`, never in tracked source or screenshots. Start from `.env.example` and enter your own credentials. Do not commit `.env`.

```powershell
cd C:\Users\sande\Desktop\trading_app
python -m pip install -r requirements.txt
python -m pytest -q
python request_budget.py --symbol-count 1
```

The offline pytest workflow at `.github/workflows/offline-tests.yml` runs on `dev2` pushes and pull requests. Offline tests do **not** verify live Groww/NSE availability, official quotas, provider responses or trading performance.

## Campaign operating sequence

Use fresh Screener data and required healthy official NSE source evidence for the current IST day. The command below generates a new queue and performs official-source work, but does not launch Groww without `--run-main`:

```powershell
python prefilter_scan.py --max-deep-symbols 2968 --skip-review
python campaign_scan.py prepare
```

`prepare` prints the new campaign report and queue paths. Copy those **exact** paths into the explicit bounded scanner invocation rather than assuming stale defaults:

```powershell
python batch_scan.py --new-run --report "<printed campaign report path>" --queue "<printed campaign queue path>" --batch-size 1 --max-batches 1
python campaign_scan.py record
python campaign_scan.py summary
python research_digest.py
python campaign_history.py
```

Run `campaign_scan.py record` after a completed batch and **before** replacing the shared current-state pointer or preparing a later campaign. Keep `data/output/campaign/campaign.json`, `data/output/larger_scan/` and archived batch results intact. Resuming a current-day session uses the same queue/report without `--new-run`; any code/CSV/report hash change or IST date rollover requires a fresh current-day plan. Do not delete a `.runner.lock` unless you have verified the scanner process is no longer active.

## API guard and scope

The optional Groww SDK guard lives in `market_data/groww_auth.py` and covers instrument lookup and historical-candle SDK calls within one child Python process. Its default gap and call ceiling are disabled, preserving the earlier fetch behavior. This is **not** a global Groww account quota or a replacement for the provider's usage policy. See `docs/GROWW_SDK_GUARD_OPERATIONS.md` and `docs/GROWW_API_MEMO.md`.
