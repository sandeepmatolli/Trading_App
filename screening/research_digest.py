"""Read-only, audited SAME-DAY campaign digest; never a live trading signal.

Requires a completed batch row to have been recorded by campaign_scan.py record.
Every displayed result is checked against the checkpoint, merged report,
ledger digest, and immutable child archive. Makes no broker, Groww or NSE calls.
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

_SESSION_PATTERN = re.compile(r"^\d{8}T\d{6}_[a-f0-9]{8}$")
_DECISIONS = {"DATA_REJECT", "REJECT", "WATCH", "CANDIDATE"}


def _read(path: Path) -> Dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ScanSafetyError(f"Digest cannot read valid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ScanSafetyError(f"Expected JSON object: {path}")
    return payload


def _atomic_write(path: Path, payload: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def build_same_day_digest(
    state_path: Path,
    ledger_path: Path,
    campaign_root: Path,
    *,
    output_path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> Dict:
    """Verify archived current IST-day campaign research and make an inert digest.

    Requires `campaign_scan.py record` first, and requires no scanner running.
    A CANDIDATE is always labeled manual revalidation required; this function
    deliberately does not consult live prices, event feeds or place any order.
    """
    state_path = Path(state_path)
    ledger_path = Path(ledger_path)
    campaign_root = Path(campaign_root)
    now = now or ist_now()
    if now.tzinfo is None:
        raise ScanSafetyError("Digest requires an aware IST/current-time value")
    now = now.astimezone(IST)
    if (state_path.parent / ".runner.lock").exists():
        raise ScanSafetyError("Batch runner is active; cannot produce a settled digest")
    state = _read(state_path)
    sid = state.get("session_id")
    day = state.get("date_ist")
    if not isinstance(sid, str) or not _SESSION_PATTERN.fullmatch(sid):
        raise ScanSafetyError("Invalid batch session ID")
    if day != now.date().isoformat():
        raise ScanSafetyError("Only today's IST session may produce a same-day digest; use campaign summary for older sessions")
    if not isinstance(state.get("hashes"), dict) or not isinstance(state.get("completed"), dict) or not isinstance(state.get("symbols"), list):
        raise ScanSafetyError("Invalid checkpoint structure")
    source_report = Path(state.get("report_path", "")).resolve()
    if not source_report.is_relative_to((campaign_root / "daily").resolve()):
        raise ScanSafetyError("Digest is restricted to campaign daily sessions")
    folder = Path(state.get("session_dir", "")).resolve()
    if folder.parent != state_path.parent.resolve() or folder.name != sid:
        raise ScanSafetyError("Session archive is not in the expected scanner directory")
    merged = _read(folder / "merged_report.json")
    completed = state["completed"]
    expected = [completed[s] for s in state["symbols"] if s in completed]
    if (
        len(set(state["symbols"])) != len(state["symbols"])
        or any(s not in state["symbols"] for s in completed)
        or merged.get("session_id") != sid
        or merged.get("date_ist") != day
        or merged.get("completed_count") != len(completed)
        or merged.get("results") != expected
        or merged.get("pending_symbols") != [s for s in state["symbols"] if s not in completed]
    ):
        raise ScanSafetyError("Digest checkpoint and merged-report evidence disagree")
    ledger = load_ledger(ledger_path)
    sess = ledger["sessions"].get(sid)
    fingerprint = _digest({
        "session_id": sid, "date_ist": day, "symbols": state["symbols"],
        "hashes": state["hashes"], "session_dir": str(folder),
    })
    if not isinstance(sess, dict) or sess.get("session_fingerprint") != fingerprint:
        raise ScanSafetyError("Session must be recorded in campaign ledger first: run campaign_scan.py record")
    if sess.get("recorded_symbols") != sorted(completed):
        raise ScanSafetyError("Campaign ledger is missing completed symbols: run campaign_scan.py record")

    batches = {b.get("number"): b for b in state.get("batches", []) if isinstance(b, dict)}
    items = []
    for symbol, row in zip([s for s in state["symbols"] if s in completed], expected):
        if not isinstance(row, dict) or row.get("symbol") != symbol or row.get("decision") not in _DECISIONS or _is_pipeline_failure(row):
            raise ScanSafetyError(f"Invalid completed row for digest: {symbol}")
        entry = ledger["entries"].get(symbol)
        if (
            not isinstance(entry, dict)
            or entry.get("source_session_id") != sid
            or entry.get("source_date_ist") != day
            or entry.get("row_sha256") != _digest(row)
            or entry.get("decision_at_scan") != row["decision"]
            or entry.get("historical_only") is not True
        ):
            raise ScanSafetyError(f"Ledger row does not attest checkpoint research: {symbol}")
        batch_number = entry.get("batch_number")
        batch = batches.get(batch_number)
        if (
            not isinstance(batch_number, int) or not isinstance(batch, dict)
            or batch.get("returncode") != 0 or symbol not in batch.get("symbols", [])
            or batch.get("started_at") != entry.get("batch_started_at")
        ):
            raise ScanSafetyError(f"Invalid archived batch provenance: {symbol}")
        archive = folder / f"batch_{batch_number:04d}" / "results.json"
        try:
            saved = json.loads(archive.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ScanSafetyError(f"Missing or unreadable immutable research archive: {archive}: {exc}") from exc
        if not isinstance(saved, list) or len(saved) != len(batch["symbols"]) or sum(x == row for x in saved) != 1:
            raise ScanSafetyError(f"Archived child result differs from digest row: {symbol}")
        quality = row.get("market_data_quality") if isinstance(row.get("market_data_quality"), dict) else {}
        event = row.get("event_profile") if isinstance(row.get("event_profile"), dict) else {}
        technical = row.get("technical_profile") if isinstance(row.get("technical_profile"), dict) else {}
        closed = technical.get("closed_bar_guard") if isinstance(technical.get("closed_bar_guard"), dict) else {}
        candidate_evidence_present = bool(
            row["decision"] == "CANDIDATE" and quality.get("valid") is True
            and quality.get("candidate_eligible") is True and event.get("candidate_eligible") is True
            and closed.get("enabled") is True
        )
        items.append({
            "symbol": symbol,
            "decision_at_scan": row["decision"],
            "review_status": "MANUAL_REVALIDATION_REQUIRED" if row["decision"] == "CANDIDATE" else "RESEARCH_ARCHIVE_ONLY",
            "candidate_evidence_present_at_scan": candidate_evidence_present,
            "source_batch_started_at": entry["batch_started_at"],
            "row_sha256": entry["row_sha256"],
            "data_valid_at_scan": quality.get("valid"),
            "data_candidate_eligible_at_scan": quality.get("candidate_eligible"),
            "event_candidate_eligible_at_scan": event.get("candidate_eligible"),
            "closed_bar_policy_present_at_scan": closed.get("enabled") is True,
        })
    counts = Counter(x["decision_at_scan"] for x in items)
    digest = {
        "schema_version": 1,
        "status": "READ_ONLY_RESEARCH_ARCHIVE_NOT_LIVE_SIGNALS",
        "date_ist": day,
        "session_id": sid,
        "generated_at_ist": now.isoformat(),
        "queue_count": len(state["symbols"]),
        "completed_count": len(items),
        "pending_count": len(state["symbols"]) - len(items),
        "decision_counts_at_scan": dict(counts),
        "candidate_review_count": counts["CANDIDATE"],
        "code_snapshot_sha256": _digest(state["hashes"].get("code", {})),
        "items": items,
        "warning": "All results are archived research, including today's CANDIDATE. Recheck official events, prices, data and closed candles manually before a trade. No live verification or orders occurred.",
    }
    if output_path is None:
        output_path = campaign_root / "digests" / f"{day}_{sid}.json"
    _atomic_write(Path(output_path), digest)
    return {"path": str(Path(output_path).resolve()), "digest": digest}