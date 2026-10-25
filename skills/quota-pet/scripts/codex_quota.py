#!/usr/bin/env python3
"""Read real Codex usage limits through the installed CLI's app-server.

Standard library only. No API key, credential-file access, model turn, credit
purchase, rate-limit reset, or settings write is performed by this script.
The CLI uses its existing sign-in. Protocol reference:
https://learn.chatgpt.com/docs/app-server#6-rate-limits-chatgpt
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Any

_MAX_LINE_BYTES = 2 * 1024 * 1024


class QuotaReadError(Exception):
    """An intentionally safe error that contains no raw server output."""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _empty(bucket: str) -> dict[str, Any]:
    return {
        "remaining_percent": None,
        "used_percent": None,
        "window_minutes": None,
        "resets_at": None,
        "credits": {"balance": None, "has_credits": None, "unlimited": None},
        "limit_id": bucket,
        "label": "Codex" if bucket == "codex" else bucket,
        "fetched_at": _now(),
        "window_selection": "most_restrictive_remaining",
        "selected_window": None,
        "windows": [],
        "error": None,
    }


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _balance(value: Any) -> int | float | str | None:
    number = _number(value)
    if number is not None:
        return number
    # Balances may be decimal strings. Preserve their precision without passing
    # through arbitrary server strings, identifiers, or diagnostic messages.
    if isinstance(value, str) and len(value) <= 80 and re.fullmatch(r"-?\d+(?:\.\d+)?", value):
        return value
    return None


def _display_name(value: Any, fallback: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return fallback
    return "".join(c for c in value if c.isprintable())[:120]


def normalize_rate_limits(result: Any, bucket: str = "codex") -> dict[str, Any]:
    """Select one bucket; retain unknown values as None and credits separately.

    `rateLimitsByLimitId` is authoritative when it is a nonempty mapping. Never
    silently use another bucket. The old single-bucket `rateLimits` response may
    lack a limitId; only the default Codex bucket may use that legacy response.
    """
    snapshot = _empty(bucket)
    if not isinstance(result, dict):
        snapshot["error"] = "invalid_response: the CLI did not return a quota object"
        return snapshot
    buckets = result.get("rateLimitsByLimitId")
    limits = None
    if isinstance(buckets, dict) and buckets:
        candidate = buckets.get(bucket)
        if isinstance(candidate, dict):
            limits = candidate
    else:
        legacy = result.get("rateLimits")
        if isinstance(legacy, dict):
            legacy_id = legacy.get("limitId")
            if legacy_id == bucket or (legacy_id is None and bucket == "codex"):
                limits = legacy
    if limits is None:
        snapshot["error"] = "bucket_unavailable: the requested quota bucket was not returned"
        return snapshot

    snapshot["label"] = _display_name(limits.get("limitName"), snapshot["label"])
    credits = limits.get("credits")
    if isinstance(credits, dict):
        snapshot["credits"] = {
            "balance": _balance(credits.get("balance")),
            "has_credits": credits.get("hasCredits") if isinstance(credits.get("hasCredits"), bool) else None,
            "unlimited": credits.get("unlimited") if isinstance(credits.get("unlimited"), bool) else None,
        }
    for kind in ("primary", "secondary"):
        window = limits.get(kind)
        if not isinstance(window, dict):
            continue
        used = _number(window.get("usedPercent"))
        # A negative used percentage is malformed, not extra quota. Percentages
        # above 100 can represent exhaustion: remaining is clamped to zero.
        if used is not None and used < 0:
            used = None
        duration = _number(window.get("windowDurationMins"))
        reset = _number(window.get("resetsAt"))
        snapshot["windows"].append({
            "kind": kind,
            "remaining_percent": max(0, min(100, 100 - used)) if used is not None else None,
            "used_percent": used,
            "window_minutes": duration if duration is not None and duration > 0 else None,
            "resets_at": reset if reset is not None and reset >= 0 else None,
        })
    known = [w for w in snapshot["windows"] if w["remaining_percent"] is not None]
    if known:
        selected = min(known, key=lambda w: w["remaining_percent"])
        snapshot["selected_window"] = selected["kind"]
        for key in ("remaining_percent", "used_percent", "window_minutes", "resets_at"):
            snapshot[key] = selected[key]
    # With no reported percentage, remain unknown even if credits are present.
    return snapshot


def _checked_executable(value: str | os.PathLike[str]) -> str | None:
    candidate = Path(value).expanduser()
    if candidate.is_file() and (os.name == "nt" or os.access(candidate, os.X_OK)
                                or candidate.suffix.lower() in (".js", ".mjs", ".cjs")):
        return str(candidate.resolve())
    located = shutil.which(str(value))
    return str(Path(located).resolve()) if located else None


def _node_executable() -> str | None:
    for name in (("node.exe",) if os.name == "nt" else ("node",)):
        found = shutil.which(name)
        if found and (os.name != "nt" or Path(found).suffix.lower() == ".exe"):
            return str(Path(found).resolve())
    return None


def _resolve_cli_launcher(path: str) -> str | None:
    """Avoid Windows command-shell shims; find the official npm JS entry.

    npm's codex.cmd is not a native executable. Never pass it to CreateProcess
    or construct a cmd.exe command string. Read the adjacent public package
    manifest, then invoke the package's JS entry with node.exe and an argv list.
    Unrecognized wrappers require an explicit native CLI/JS path instead.
    """
    candidate = Path(path)
    if os.name != "nt" or candidate.suffix.lower() not in (".cmd", ".bat", ".ps1"):
        if candidate.suffix.lower() in (".js", ".mjs", ".cjs") and not _node_executable():
            return None
        return str(candidate)
    package_root = candidate.parent / "node_modules" / "@openai" / "codex"
    try:
        manifest_path = package_root / "package.json"
        if manifest_path.stat().st_size > 1024 * 1024:
            return None
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("name") != "@openai/codex":
            return None
        bins = manifest.get("bin")
        entry = bins.get("codex") if isinstance(bins, dict) else bins
        if not isinstance(entry, str):
            return None
        root = package_root.resolve()
        script = (root / entry).resolve()
        if (script.is_relative_to(root) and script.is_file()
                and script.suffix.lower() in (".js", ".mjs", ".cjs")
                and _node_executable()):
            return str(script)
    except (OSError, ValueError, TypeError):
        pass
    return None


def _launch_prefix(executable: str) -> list[str]:
    if Path(executable).suffix.lower() in (".js", ".mjs", ".cjs"):
        node = _node_executable()
        if not node:
            raise QuotaReadError("node_not_found: the npm Codex entry requires Node.js on PATH")
        return [node, executable]
    if os.name == "nt" and Path(executable).suffix.lower() in (".cmd", ".bat", ".ps1"):
        raise QuotaReadError("unsupported_cli_wrapper: supply a native codex.exe or the npm Codex JavaScript entry")
    return [executable]


def discover_codex(codex_path: str | os.PathLike[str] | None = None) -> str:
    """Use explicit path, CODEX_CLI_PATH, PATH, then the Windows app install."""
    explicit = codex_path if codex_path is not None else os.environ.get("CODEX_CLI_PATH")
    if explicit is not None:
        found = _checked_executable(explicit)
        if found:
            resolved = _resolve_cli_launcher(found)
            if resolved:
                return resolved
            raise QuotaReadError("unsupported_cli_wrapper: supply codex.exe, or install Node.js and the official npm Codex package")
        raise QuotaReadError("cli_not_found: --codex or CODEX_CLI_PATH must point to an installed Codex executable")
    for name in (("codex.exe", "codex") if os.name == "nt" else ("codex",)):
        found = shutil.which(name)
        if found:
            resolved = _resolve_cli_launcher(str(Path(found).resolve()))
            if resolved:
                return resolved
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        root = Path(os.environ["LOCALAPPDATA"]) / "OpenAI" / "Codex" / "bin"
        try:
            candidates = sorted(root.glob("*/codex.exe"), key=lambda p: p.stat().st_mtime, reverse=True)
            for candidate in candidates:
                if candidate.is_file():
                    return str(candidate.resolve())
        except OSError:
            pass
    raise QuotaReadError("cli_not_found: install/sign in to Codex, or supply --codex PATH or CODEX_CLI_PATH")


def _read_stdout(stream: Any, messages: queue.Queue[Any]) -> None:
    try:
        while True:
            line = stream.readline(_MAX_LINE_BYTES + 1)
            if not line:
                messages.put(None)
                return
            if len(line) > _MAX_LINE_BYTES:
                messages.put(QuotaReadError("invalid_response: protocol message exceeded the size limit"))
                return
            try:
                message = json.loads(line)
            except (UnicodeError, ValueError):
                continue
            if isinstance(message, dict):
                messages.put(message)
    except (OSError, ValueError):
        messages.put(None)


def _response(messages: queue.Queue[Any], request_id: int, deadline: float) -> Any:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise QuotaReadError("timeout: Codex did not return usage limits before the deadline")
        try:
            message = messages.get(timeout=remaining)
        except queue.Empty:
            raise QuotaReadError("timeout: Codex did not return usage limits before the deadline") from None
        if isinstance(message, QuotaReadError):
            raise message
        if message is None:
            raise QuotaReadError("cli_exited: Codex stopped before returning usage limits; check its sign-in and configuration")
        if message.get("id") != request_id or "method" in message:
            continue
        if "error" in message:
            # Do not expose raw errors: they can contain account identifiers or
            # authentication details. A numeric protocol code is safe to show.
            error = message.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            suffix = f" (code {code})" if isinstance(code, int) and not isinstance(code, bool) else ""
            raise QuotaReadError("quota_unavailable: Codex rejected the read request; check ChatGPT sign-in and CLI compatibility" + suffix)
        if "result" not in message:
            raise QuotaReadError("invalid_response: Codex response has no result")
        return message["result"]


def _stop_process(process: subprocess.Popen[Any]) -> None:
    """Stop only the app-server started by this call, including its child tree."""
    try:
        if process.stdin:
            process.stdin.close()
    except OSError:
        pass
    if process.poll() is None:
        if os.name == "nt":
            taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
            try:
                subprocess.run([str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=3,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.wait(timeout=1)


def read_quota(codex_path: str | os.PathLike[str] | None = None,
               timeout: float = 20, bucket: str = "codex") -> dict[str, Any]:
    """Return one JSON-serializable snapshot; expected failures have `error` set.

    Percentages represent a selected rate-limit window, NOT a monetary balance
    or token count. A missing percentage remains None, never 0. No raw protocol
    response, account identifier, or server diagnostic is returned.
    """
    snapshot = _empty(bucket)
    process = None
    reader = None
    try:
        if not isinstance(bucket, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", bucket):
            raise QuotaReadError("invalid_bucket: use a quota bucket identifier such as codex")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise QuotaReadError("invalid_timeout: timeout must be a positive number of seconds")
        executable = discover_codex(codex_path)
        options: dict[str, Any] = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.DEVNULL}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True
        deadline = time.monotonic() + timeout
        process = subprocess.Popen(_launch_prefix(executable) + ["app-server", "--listen", "stdio://"], **options)
        messages: queue.Queue[Any] = queue.Queue()
        reader = threading.Thread(target=_read_stdout, args=(process.stdout, messages), daemon=True)
        reader.start()

        def send(message: dict[str, Any]) -> None:
            assert process is not None and process.stdin is not None
            process.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8"))
            process.stdin.flush()

        send({"id": 1, "method": "initialize", "params": {"clientInfo": {
            "name": "quota_pet", "title": "重置吃饱了 · 只读额度监测", "version": "1.0.0"}}})
        _response(messages, 1, deadline)
        send({"method": "initialized", "params": {}})
        send({"id": 2, "method": "account/rateLimits/read"})
        snapshot = normalize_rate_limits(_response(messages, 2, deadline), bucket)
    except QuotaReadError as error:
        snapshot["error"] = str(error)
    except (OSError, subprocess.SubprocessError):
        snapshot["error"] = "cli_io_error: unable to start or communicate with the installed Codex CLI"
    finally:
        if process is not None:
            try:
                _stop_process(process)
            except (OSError, subprocess.SubprocessError):
                if snapshot["error"] is None:
                    snapshot["error"] = "cleanup_failed: the temporary Codex app-server may need to be closed"
            if reader is not None:
                reader.join(timeout=0.5)
            if process.stdout and (reader is None or not reader.is_alive()):
                process.stdout.close()
        snapshot["fetched_at"] = _now()
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="Read real Codex remaining usage through the signed-in CLI. No model task is started.")
    parser.add_argument("--json", action="store_true", help="print the snapshot as JSON (unknown values are null)")
    parser.add_argument("--codex", metavar="PATH", help="installed Codex executable; otherwise use CODEX_CLI_PATH, PATH, or Windows app discovery")
    parser.add_argument("--bucket", default="codex", help="quota bucket identifier (default: codex); never substitutes another bucket")
    parser.add_argument("--timeout", type=float, default=20, metavar="SECONDS", help="total protocol timeout, excluding bounded process cleanup (default: 20)")
    args = parser.parse_args()
    snapshot = read_quota(args.codex, args.timeout, args.bucket)
    if args.json:
        print(json.dumps(snapshot, ensure_ascii=False, allow_nan=False))
    elif snapshot["error"]:
        print(snapshot["error"], file=sys.stderr)
    else:
        remaining = snapshot["remaining_percent"]
        value = "unknown" if remaining is None else f"{remaining:g}%"
        print(f"{snapshot['label']}: {value} remaining")
        if snapshot["selected_window"]:
            print(f"Selected window: {snapshot['selected_window']}; duration: {snapshot['window_minutes']} minutes; resets_at: {snapshot['resets_at']}")
        print("Credits are separate from the usage percentage.")
    return 1 if snapshot["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
