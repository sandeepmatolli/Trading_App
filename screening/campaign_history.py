"""Offline audit of recorded campaign history across old scanner sessions.

No live data access, decision recomputation, prioritization, or broker orders.
An old decision is *always* historical, even when it was CANDIDATE at scan time.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
import os
from pathlib import Path
import re
from typing import Dict, Optional
from uuid import uuid4

from screening.batch_runner import IST, ScanSafetyError, _is_pipeline_failure, ist_now
from screening.campaign import _digest, load_ledger

SESSION_RE = re.compile(r"^\d{8}T\d{6}_[0-9a-f]{8}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DECISIONS = {"DATA_REJECT", "REJECT", "WATCH", "CANDIDATE"}


def _read_object(path: Path) -> Dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ScanSafetyError(f"Historical audit cannot read JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ScanSafetyError(f"Historical audit expects an object: {path}")
    return payload


def _atomic_write(path: Path, payload: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def build_campaign_history_report(
    ledger_path: Path,
    campaign_root: Path,
    *,
    output_path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> Dict:
    """Verify *all recorded* sessions against saved merged/child evidence.

    Unlike same-day digest, this does NOT require the shared current_state.json:
    its pointer is allowed to move to the next day's session. Since old full
    checkpoints are not preserved by V1, the original batch-start timestamp and
    complete input fingerprint remain LEDGER-ATTESTED, not independently
    reconstructed. Archived row content and row SHA are independently verified.

    Refuse a partially recorded session if its current merged report includes
    additional completed rows. Call `campaign_scan.py record` before rollover.
    """
    ledger_path = Path(ledger_path).resolve()
    campaign_root = Path(campaign_root).resolve()
    scan_root = (campaign_root.parent / "larger_scan").resolve()
    generated_at = now or ist_now()
    if generated_at.tzinfo is None:
        raise ScanSafetyError("Historical audit needs a timezone-aware current time")
    generated_at = generated_at.astimezone(IST)
    if (scan_root / ".runner.lock").exists():
        raise ScanSafetyError("Batch scanner is active; history report requires settled archives")

    ledger = load_ledger(ledger_path)
    entries = ledger["entries"]
    sessions = ledger["sessions"]
    if any(not isinstance(k, str) or not isinstance(v, dict) for k, v in entries.items()):
        raise ScanSafetyError("Campaign ledger has invalid symbol entries")
    if any(not isinstance(k, str) or not isinstance(v, dict) for k, v in sessions.items()):
        raise ScanSafetyError("Campaign ledger has invalid sessions")

    report_sessions = []
    report_items = []
    seen_symbols = set()
    for sid in sorted(sessions):
        info = sessions[sid]
        if not SESSION_RE.fullmatch(sid):
            raise ScanSafetyError(f"Invalid historical session ID: {sid}")
        day = info.get("date_ist")
        try:
            valid_day = datetime.strptime(day, "%Y-%m-%d").date()
        except (TypeError, ValueError) as exc:
            raise ScanSafetyError(f"Invalid recorded IST date: {sid}") from exc
        if sid[:8] != valid_day.strftime("%Y%m%d"):
            raise ScanSafetyError(f"Session ID and recorded IST date disagree: {sid}")
        folder = Path(str(info.get("session_dir", ""))).resolve()
        if folder != scan_root / sid:
            raise ScanSafetyError(f"Session archive is outside expected scanner folder: {sid}")
        if not isinstance(info.get("hashes"), dict) or not SHA256_RE.fullmatch(str(info.get("session_fingerprint", ""))):
            raise ScanSafetyError(f"Missing original session snapshot attestation: {sid}")
        recorded = info.get("recorded_symbols")
        if (not isinstance(recorded, list) or any(not isinstance(s, str) for s in recorded)
                or recorded != sorted(set(recorded))):
            raise ScanSafetyError(f"Invalid recorded-symbol manifest: {sid}")
        session_entries = {s: e for s, e in entries.items() if e.get("source_session_id") == sid}
        if recorded != sorted(session_entries):
            raise ScanSafetyError(f"Ledger entries and recorded-symbol manifest disagree: {sid}")
        merged = _read_object(folder / "merged_report.json")
        rows = merged.get("results")
        pending = merged.get("pending_symbols")
        if not isinstance(rows, list) or not isinstance(pending, list):
            raise ScanSafetyError(f"Invalid archived merged report: {sid}")
        if (any(not isinstance(row, dict) for row in rows)
                or any(not isinstance(row.get("symbol"), str)
                       or not isinstance(row.get("decision"), str)
                       or row["decision"] not in DECISIONS for row in rows)
                or any(not isinstance(s, str) for s in pending)):
            raise ScanSafetyError(f"Invalid historical research row or pending symbol: {sid}")
        row_symbols = [row["symbol"] for row in rows]
        if (
            merged.get("session_id") != sid
            or merged.get("date_ist") != day
            or merged.get("completed_count") != len(rows)
            or not isinstance(merged.get("queue_count"), int)
            or isinstance(merged.get("queue_count"), bool)
            or merged["queue_count"] != len(rows) + len(pending)
            or len(set(row_symbols)) != len(rows)
            or len(set(pending)) != len(pending)
            or any(not isinstance(s, str) for s in row_symbols + pending)
            or set(row_symbols).intersection(pending)
            or sorted(row_symbols) != recorded
            or dict(Counter(row["decision"] for row in rows)) != merged.get("decision_counts")
        ):
            raise ScanSafetyError(f"Merged report and recorded campaign manifest disagree: {sid}; record latest completion first")

        for row in rows:
            symbol = row["symbol"]
            if symbol in seen_symbols or row.get("decision") not in DECISIONS or _is_pipeline_failure(row):
                raise ScanSafetyError(f"Duplicate/invalid historical completed result: {symbol}")
            seen_symbols.add(symbol)
            entry = session_entries[symbol]
            batch_number = entry.get("batch_number")
            if (
                entry.get("symbol") != symbol
                or entry.get("source_date_ist") != day
                or entry.get("decision_at_scan") != row["decision"]
                or entry.get("historical_only") is not True
                or entry.get("row_sha256") != _digest(row)
                or not isinstance(batch_number, int)
                or isinstance(batch_number, bool)
                or batch_number < 1
            ):
                raise ScanSafetyError(f"Ledger entry disagrees with archived research: {symbol}")
            started_at = entry.get("batch_started_at")
            try:
                started = datetime.fromisoformat(started_at)
                if started.tzinfo is None or started.astimezone(IST).date() != valid_day:
                    raise ValueError("start date mismatch")
            except (TypeError, ValueError) as exc:
                raise ScanSafetyError(f"Invalid ledger-attested batch timestamp: {symbol}") from exc
            archive = (folder / f"batch_{batch_number:04d}" / "results.json").resolve()
            if not archive.is_relative_to(folder):
                raise ScanSafetyError(f"Archived batch path escapes session directory: {symbol}")
            try:
                archived = json.loads(archive.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ScanSafetyError(f"Cannot read archived child result for {symbol}: {archive}: {exc}") from exc
            if not isinstance(archived, list) or sum(1 for x in archived if isinstance(x, dict) and x.get("symbol") == symbol) != 1 or row not in archived:
                raise ScanSafetyError(f"Archived child result mismatch: {symbol}")
            report_items.append({
                "symbol": symbol,
                "source_session_id": sid,
                "source_date_ist": day,
                "decision_at_scan": row["decision"],
                "review_status": "HISTORICAL_MANUAL_REVALIDATION_REQUIRED" if row["decision"] == "CANDIDATE" else "HISTORICAL_RESEARCH_ONLY",
                "row_sha256": entry["row_sha256"],
                "batch_number": batch_number,
                "batch_started_at_as_recorded_in_ledger": started_at,
                "archived_row_verified": True,
            })
        report_sessions.append({
            "session_id": sid,
            "date_ist": day,
            "recorded_count": len(rows),
            "queue_count_at_last_merge": merged["queue_count"],
            "pending_at_last_merge": len(pending),
            "decision_counts_at_scan": dict(Counter(row["decision"] for row in rows)),
            "original_session_fingerprint_as_recorded_in_ledger": info["session_fingerprint"],
            "archive_rows_verified": True,
        })
    if seen_symbols != set(entries):
        raise ScanSafetyError("Ledger contains orphan entries not present in audited sessions")
    counts = Counter(x["decision_at_scan"] for x in report_items)
    report = {
        "schema_version": 1,
        "status": "OFFLINE_HISTORICAL_RESEARCH_NOT_CURRENT_SIGNALS",
        "generated_at_ist": generated_at.isoformat(),
        "session_count": len(report_sessions),
        "historical_record_count": len(report_items),
        "decision_counts_at_scan": dict(counts),
        "historical_candidate_count_revalidation_required": counts["CANDIDATE"],
        "sessions": report_sessions,
        "items": report_items,
        "limitations": [
            "No current price/event/closed-candle freshness was checked; no investment signal or trade was produced.",
            "Earlier full checkpoints are not retained by V1; batch times and input fingerprint are ledger attestations, while completed row contents are verified against merged and child archives.",
            "Unrecorded sessions are not discoverable from the campaign ledger alone; run campaign_scan.py record after each scanning day before creating the next daily checkpoint.",
        ],
    }
    allowed_dir = (campaign_root / "reports").resolve()
    destination = (Path(output_path) if output_path is not None else allowed_dir / "historical_campaign.json").resolve()
    if destination.parent != allowed_dir or destination.suffix.lower() != ".json":
        raise ScanSafetyError("Historical output must be a .json file directly under campaign/reports")
    _atomic_write(destination, report)
    return {"path": str(destination), "report": report}
