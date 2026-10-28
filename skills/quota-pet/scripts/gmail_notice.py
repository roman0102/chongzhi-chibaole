#!/usr/bin/env python3
"""Private, metadata-only Gmail notification bridge. Does not access Gmail.

An explicitly authorized Codex task obtains a plan, fetches every matching
Gmail page and message's metadata, then supplies one complete JSON batch on
stdin. This module never reads credentials, messages, or attachments itself,
never creates a schedule, and never changes Gmail read/unread state.

Commands (global options precede the command):
  python gmail_notice.py --state-dir PRIVATE init --account LINK_ID --enable
  python gmail_notice.py --state-dir PRIVATE plan
  python gmail_notice.py --state-dir PRIVATE ingest < metadata.json
  python gmail_notice.py --state-dir PRIVATE disable

State lives in PRIVATE/live/ or PRIVATE/demo/. collector.json is collector
owned; snapshot.json is its UI projection; ack.json is UI owned. Updates are
atomic. Missing/uninitialized state is disabled. Re-enabling starts a fresh
baseline. Existing pending reminders survive fetch failures and stale data.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import uuid

VERSION = 1
OVERLAP_SECONDS = 120
STALE_SECONDS = 900
DISPLAY_LIMIT = 5
MAX_SEEN_IDS = 50_000
MAX_INPUT_BYTES = 4 * 1024 * 1024
REASONS = {"disabled", "missing", "stale", "clock_skew", "invalid", "fetch_failed",
           "incomplete", "scope_mismatch", "baseline_pending", "connecting", "unauthorized"}


class MailNoticeError(Exception):
    """Only stable reason codes are exposed; no source payload is echoed."""


def _number(value):
    try:
        return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)
    except (TypeError, OverflowError):
        return False


def _now(value=None):
    value = time.time() if value is None else value
    if not _number(value) or value < 0:
        raise MailNoticeError("clock_skew")
    return float(value)


def _load(path):
    if not path.exists():
        return None
    if path.stat().st_size > 16 * 1024 * 1024:
        raise MailNoticeError("invalid")
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise MailNoticeError("invalid") from exc
    if not isinstance(result, dict):
        raise MailNoticeError("invalid")
    return result


def _atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle, temporary = tempfile.mkstemp(prefix=".mail-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as output:
            json.dump(value, output, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def _lock(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        deadline = time.monotonic() + 5
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    raise MailNoticeError("connecting") from None
                time.sleep(0.02)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _scope_valid(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def _notice(scope, sequence):
    return f"{scope}.{sequence}"


def _ack_sequence(ack, scope, mode, key="acknowledged_seq"):
    if not ack or ack.get("scope_id") != scope or ack.get("mode") != mode:
        return 0
    value = ack.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _safe_ack(path):
    try:
        return _load(path)
    except (OSError, MailNoticeError):
        return None


def _metadata(item):
    if not isinstance(item, dict) or set(item) != {"message_id", "sender", "subject", "received_at"}:
        raise MailNoticeError("invalid")
    identifier, sender, subject, received = (item[key] for key in ("message_id", "sender", "subject", "received_at"))
    if not isinstance(identifier, str) or not identifier or len(identifier) > 512:
        raise MailNoticeError("invalid")
    if not isinstance(sender, str) or not isinstance(subject, str) or len(sender) > 2000 or len(subject) > 4000:
        raise MailNoticeError("invalid")
    if not _number(received) or received < 0:
        raise MailNoticeError("invalid")
    # No raw message ID is persisted. Only short display metadata survives.
    return hashlib.sha256(identifier.encode("utf-8")).hexdigest(), {
        "sender": " ".join(sender.split())[:160],
        "subject": " ".join(subject.split())[:320],
        "received_at": float(received),
    }


def _view_batches(batches, scope, acknowledged=0):
    retained = [batch for batch in batches if batch["seq"] > acknowledged]
    count = sum(batch["count"] for batch in retained)
    items = [item for batch in retained for item in batch["items"]]
    items.sort(key=lambda item: (-item["received_at"], item["sender"], item["subject"]))
    seq = max((batch["seq"] for batch in retained), default=0)
    return {"notice_id": _notice(scope, seq) if seq else None, "new_count": count,
            "items": items[:DISPLAY_LIMIT], "pending": count > 0}


def _trim_display_metadata(batches):
    """Keep at most five metadata records total, not five per pending round."""
    remaining = DISPLAY_LIMIT
    for batch in sorted(batches, key=lambda value: value["seq"], reverse=True):
        batch["items"] = batch["items"][:remaining]
        remaining -= len(batch["items"])


class MailNoticeStore:
    """Single collector's state machine. Clock injection is for deterministic tests."""
    def __init__(self, state_dir, mode="live"):
        if mode not in ("live", "demo"):
            raise MailNoticeError("invalid")
        self.mode = mode
        self.directory = Path(state_dir).expanduser().resolve() / mode
        self.collector_path = self.directory / "collector.json"
        self.snapshot_path = self.directory / "snapshot.json"
        self.ack_path = self.directory / "ack.json"
        self.lock_path = self.directory / "collector.lock"

    def _read(self):
        state = _load(self.collector_path)
        if state is not None and (state.get("version") != VERSION or state.get("mode") != self.mode
                                  or not _scope_valid(state.get("scope_id"))):
            raise MailNoticeError("invalid")
        return state

    def _save(self, state):
        ack = _safe_ack(self.ack_path)
        acknowledged = _ack_sequence(ack, state["scope_id"], self.mode)
        state["pending_batches"] = [b for b in state["pending_batches"] if b["seq"] > acknowledged]
        _trim_display_metadata(state["pending_batches"])
        snapshot = {"version": VERSION, "enabled": state["enabled"], "connected": state["connected"],
                    "observed_at": state["observed_at"], "mode": self.mode, "scope_id": state["scope_id"],
                    "reason": state["reason"], "pending_batches": state["pending_batches"]}
        snapshot.update(_view_batches(snapshot["pending_batches"], state["scope_id"]))
        # Collector is committed first. A retry can always republish its snapshot.
        _atomic(self.collector_path, state)
        _atomic(self.snapshot_path, snapshot)
        return {"snapshot_path": str(self.snapshot_path), **{k: snapshot[k] for k in
                ("version", "enabled", "connected", "mode", "notice_id", "new_count", "reason")}}

    def init(self, account, enable=False, now=None):
        now = _now(now)
        if not isinstance(account, str) or not account.strip() or len(account) > 500 or any(ord(c) < 32 for c in account):
            raise MailNoticeError("invalid")
        with _lock(self.lock_path):
            old = self._read()
            if old and old["account"] == account and old["enabled"] == bool(enable):
                return self._save(old)
            state = {"version": VERSION, "mode": self.mode, "account": account,
                     "scope_id": uuid.uuid4().hex, "enabled": bool(enable), "enabled_at": now,
                     "baseline_complete": False, "last_success_at": None, "seen": [],
                     "notice_seq": 0, "pending_batches": [], "active_round": None,
                     "observed_at": now, "connected": False,
                     "reason": "baseline_pending" if enable else "disabled"}
            return self._save(state)

    def disable(self, now=None):
        now = _now(now)
        with _lock(self.lock_path):
            state = self._read()
            if state is None:
                return {"snapshot_path": str(self.snapshot_path), "enabled": False,
                        "connected": False, "mode": self.mode, "reason": "disabled"}
            state.update(enabled=False, connected=False, observed_at=now, reason="disabled",
                         pending_batches=[], active_round=None)
            return self._save(state)

    def plan(self, now=None):
        now = _now(now)
        with _lock(self.lock_path):
            state = self._read()
            result = {"version": VERSION, "snapshot_path": str(self.snapshot_path), "mode": self.mode,
                      "enabled": bool(state and state["enabled"]), "ready": False}
            if state is None or not state["enabled"]:
                return dict(result, reason="disabled")
            start = state["last_success_at"] if state["baseline_complete"] else state["enabled_at"]
            # A backwards local clock must not silently move a committed watermark
            # or turn an uncertain window into a successful empty result.
            if now < start:
                state.update(connected=False, observed_at=now, reason="clock_skew", active_round=None)
                self._save(state)
                return dict(result, reason="clock_skew")
            round_id = uuid.uuid4().hex
            after = max(0, math.floor(start - OVERLAP_SECONDS))
            before = math.floor(now) + 1
            state["active_round"] = {"round_id": round_id, "query_after": after,
                                     "query_before": now, "baseline": not state["baseline_complete"]}
            self._save(state)
            return dict(result, ready=True, account=state["account"], scope_id=state["scope_id"],
                        round_id=round_id, baseline=not state["baseline_complete"],
                        query_after=after, query_before=now,
                        query=f"in:inbox after:{after} before:{before}", reason=None)

    def ingest(self, payload, now=None):
        now = _now(now)
        with _lock(self.lock_path):
            state = self._read()
            if state is None or not state["enabled"]:
                raise MailNoticeError("disabled")
            if not isinstance(payload, dict):
                raise MailNoticeError("invalid")
            round_data = state.get("active_round")
            if (payload.get("version") != VERSION or payload.get("account") != state["account"]
                    or payload.get("scope_id") != state["scope_id"]):
                raise MailNoticeError("scope_mismatch")
            if set(payload) - {"version", "account", "scope_id", "round_id", "complete", "items", "reason"}:
                state.update(connected=False, observed_at=now, reason="invalid", active_round=None)
                return self._save(state)
            if payload.get("round_id") == state.get("last_completed_round_id") and payload.get("round_id"):
                return dict(self._save(state), duplicate=True)
            if not round_data or payload.get("round_id") != round_data["round_id"]:
                raise MailNoticeError("scope_mismatch")
            if now < round_data["query_before"]:
                state.update(connected=False, observed_at=now, reason="clock_skew", active_round=None)
                return self._save(state)
            if payload.get("complete") is not True:
                reason = payload.get("reason")
                reason = reason if reason in ("unauthorized", "fetch_failed", "incomplete") else "incomplete"
                if payload.get("items") not in (None, []):
                    reason = "invalid"  # Failed rounds must not submit partial payloads.
                state.update(connected=False, observed_at=now, reason=reason, active_round=None)
                return self._save(state)
            try:
                items = payload.get("items")
                if not isinstance(items, list) or len(items) > 5000:
                    raise MailNoticeError("invalid")
                normalized = [_metadata(item) for item in items]
            except MailNoticeError:
                state.update(connected=False, observed_at=now, reason="invalid", active_round=None)
                return self._save(state)
            seen = set(state["seen"])
            fresh = []
            # Order and duplicates do not affect results. Metadata arriving after
            # the plan's cutoff is left unseen for the next overlapping query.
            normalized.sort(key=lambda pair: (pair[1]["received_at"], pair[0], pair[1]["sender"], pair[1]["subject"]))
            for identifier, item in normalized:
                if item["received_at"] > round_data["query_before"] or identifier in seen:
                    continue
                seen.add(identifier)
                if not round_data["baseline"] and item["received_at"] >= state["enabled_at"]:
                    fresh.append(item)
            if len(seen) > MAX_SEEN_IDS:
                # Never forget IDs silently. At this conservative bound an
                # operator can explicitly disable/re-enable a fresh baseline.
                # No partial batch or watermark is committed in the meantime.
                state.update(connected=False, observed_at=now, reason="incomplete", active_round=None)
                return self._save(state)
            if fresh:
                state["notice_seq"] += 1
                fresh.sort(key=lambda item: (-item["received_at"], item["sender"], item["subject"]))
                state["pending_batches"].append({"seq": state["notice_seq"], "count": len(fresh),
                                                 "items": fresh[:DISPLAY_LIMIT]})
            state.update(seen=sorted(seen), baseline_complete=True,
                         last_success_at=round_data["query_before"], active_round=None,
                         last_completed_round_id=round_data["round_id"],
                         connected=True, observed_at=now, reason=None)
            return self._save(state)


class MailNoticeReader:
    """UI reader. Local acknowledgement never marks anything read in Gmail."""
    def __init__(self, file, mode="live"):
        if mode not in ("live", "demo"):
            raise ValueError("mode must be live or demo")
        self.file = Path(file).expanduser().resolve()
        self.mode = mode
        self.ack_path = self.file.with_name("ack.json")
        self.lock_path = self.file.with_name("ack.lock")
        self.collector_lock_path = self.file.with_name("collector.lock")
        self._last = None

    def poll(self, now=None):
        empty = {"version": VERSION, "enabled": False, "connected": False, "observed_at": 0,
                 "mode": self.mode, "notice_id": None, "new_count": 0, "items": [],
                 "reason": "missing", "pending": False}
        try:
            now = _now(now)
            snapshot = _load(self.file)
            if snapshot is None:
                if self._last is not None:
                    return dict(self._last, connected=False, reason="missing")
                return empty
            if (snapshot.get("version") != VERSION or snapshot.get("mode") != self.mode
                    or not _scope_valid(snapshot.get("scope_id")) or not isinstance(snapshot.get("enabled"), bool)
                    or not isinstance(snapshot.get("connected"), bool) or not _number(snapshot.get("observed_at"))):
                raise MailNoticeError("invalid")
            batches = snapshot.get("pending_batches")
            if not isinstance(batches, list):
                raise MailNoticeError("invalid")
            for batch in batches:
                if (not isinstance(batch, dict) or not isinstance(batch.get("seq"), int) or batch["seq"] <= 0
                        or not isinstance(batch.get("count"), int) or batch["count"] <= 0
                        or not isinstance(batch.get("items"), list) or len(batch["items"]) > DISPLAY_LIMIT):
                    raise MailNoticeError("invalid")
                for item in batch["items"]:
                    if (not isinstance(item, dict) or set(item) != {"sender", "subject", "received_at"}
                            or not isinstance(item["sender"], str) or not isinstance(item["subject"], str)
                            or not _number(item["received_at"])):
                        raise MailNoticeError("invalid")
            acknowledged = _ack_sequence(_safe_ack(self.ack_path), snapshot["scope_id"], self.mode)
            result = {key: snapshot[key] for key in ("version", "enabled", "connected", "observed_at", "mode")}
            result.update(_view_batches(batches, snapshot["scope_id"], acknowledged))
            result["reason"] = snapshot.get("reason") if snapshot.get("reason") in REASONS else None
            if not result["enabled"]:
                result.update(connected=False, notice_id=None, new_count=0, items=[], pending=False, reason="disabled")
            elif now - result["observed_at"] > STALE_SECONDS:
                result.update(connected=False, reason="stale")
            elif result["observed_at"] - now > OVERLAP_SECONDS:
                result.update(connected=False, reason="clock_skew")
            self._last = result
            return result
        except (OSError, MailNoticeError, KeyError, TypeError, ValueError):
            if self._last is not None:
                return dict(self._last, connected=False, reason="invalid")
            return dict(empty, reason="invalid")

    def _mark(self, notice_id, key):
        # Prefer the notice actually shown to the user, not a newer one that
        # might arrive between a click and this call.
        current = self._last if self._last is not None else self.poll()
        notice_id = notice_id if notice_id is not None else current.get("notice_id")
        if not isinstance(notice_id, str) or not re.fullmatch(r"[0-9a-f]{32}\.[1-9][0-9]*", notice_id):
            return False
        scope, sequence_text = notice_id.split(".")
        sequence = int(sequence_text)
        try:
            # Scope changes and UI writes share the collector lock. Re-check
            # the current scope only after acquiring it, so a delayed click
            # from a previous account cannot replace the new account's ack.
            # Always acquire collector before ack; never reverse this order.
            with _lock(self.collector_lock_path):
                snapshot = _load(self.file)
                if (not snapshot or not snapshot.get("enabled") or snapshot.get("mode") != self.mode
                        or snapshot.get("scope_id") != scope):
                    return False
                maximum = max((batch["seq"] for batch in snapshot.get("pending_batches", [])), default=0)
                if sequence > maximum:
                    return False
                with _lock(self.lock_path):
                    previous = _safe_ack(self.ack_path)
                    value = {"version": VERSION, "scope_id": scope, "mode": self.mode,
                             "acknowledged_seq": _ack_sequence(previous, scope, self.mode),
                             "announced_seq": _ack_sequence(previous, scope, self.mode, "announced_seq")}
                    if key == "announced_seq" and sequence <= max(value["announced_seq"], value["acknowledged_seq"]):
                        return False
                    value[key] = max(value[key], sequence)
                    value["updated_at"] = time.time()
                    _atomic(self.ack_path, value)
            return True
        except (OSError, MailNoticeError, KeyError, TypeError, ValueError):
            return False

    def ack(self, notice_id=None):
        return self._mark(notice_id, "acknowledged_seq")

    def claim_notice(self, notice_id=None):
        """Return True once per notice, persistently; does not clear pending."""
        return self._mark(notice_id, "announced_seq")


def default_state_dir():
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData/Local"))
        return base / "CodexQuotaPet/gmail"
    return Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local/state")) / "quota-pet/gmail"


def main():
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--state-dir", default=str(default_state_dir()), help="Private state root; never place it in a shared repository")
    parser.add_argument("--mode", choices=("live", "demo"), default="live")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Bind an account; remains disabled unless --enable is supplied")
    init.add_argument("--account", required=True, help="Private connector link/account ID; not an API key")
    init.add_argument("--enable", action="store_true")
    commands.add_parser("plan", help="Return the exact bounded query and a single-use round token")
    commands.add_parser("ingest", help="Accept one complete metadata batch or failure via JSON stdin")
    commands.add_parser("disable", help="Disable and clear local reminders; does not pause external scheduling")
    args = parser.parse_args()
    try:
        store = MailNoticeStore(args.state_dir, args.mode)
        if args.command == "init":
            result = store.init(args.account, args.enable)
        elif args.command == "plan":
            result = store.plan()
        elif args.command == "disable":
            result = store.disable()
        else:
            raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
            if len(raw) > MAX_INPUT_BYTES:
                raise MailNoticeError("invalid")
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeError) as exc:
                raise MailNoticeError("invalid") from exc
            result = store.ingest(payload)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (MailNoticeError, OSError) as exc:
        reason = str(exc) if isinstance(exc, MailNoticeError) else "invalid"
        print(json.dumps({"version": VERSION, "ok": False, "reason": reason if reason in REASONS else "invalid"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
