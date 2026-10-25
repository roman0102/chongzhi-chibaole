#!/usr/bin/env python3
"""Read-only work status from Codex desktop's local compatibility IPC.

This is a private desktop protocol, verified against Windows 26.1021.3705.0,
not the public app-server API. No model turn, owner registration, approval
response, credential access, log reading, or settings write is performed.
The desktop sends a full initial stream snapshot. It is decoded transiently
and immediately reduced to an allowlisted status projection. Chat text,
tool arguments/results, file names, and request parameters are not retained,
logged, sent to the callback, or written to disk. Incremental content patches
are discarded. A connection loss is unknown, never assumed completion.

Usage: WorkStatusObserver(callback, thread_id=None).start(); observer.stop()
The default target is CODEX_THREAD_ID. scope="desktop" discovers the recent
50 unarchived local IDs using a read-only ID-only SQLite query. Only chats
currently owned by the running desktop can be followed. The callback runs
on the worker thread. Remote/cloud chats are outside this adapter's scope.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import struct
import threading
import time
import uuid
from typing import Any, Callable

_ENDPOINT = r"\\.\pipe\codex-ipc"
_MAX_FRAME = 256 * 1024 * 1024  # Same bound as the installed desktop IPC reader.
_SOURCE = "codex-desktop-ipc"
_STATES = ("working", "waiting", "completed", "error", "idle", "unknown")
_WAIT_METHODS = {
    "item/commandExecution/requestApproval": "approval",
    "item/fileChange/requestApproval": "approval",
    "item/permissions/requestApproval": "approval",
    "item/tool/requestUserInput": "input",
    "item/tool/requestOptionPicker": "input",
    "item/tool/requestSetupCodexContextPicker": "input",
    "mcpServer/elicitation/request": "input",
    "item/plan/requestImplementation": "input",
}


class _ProtocolError(Exception):
    """Only fixed, non-content-bearing messages may be raised here."""


def _choice(*choices: str):
    return lambda value: value if isinstance(value, str) and value in choices else None


def _opaque(value):
    return value if isinstance(value, str) and len(value) <= 200 else None


_TURN = {"turnId": _opaque,
         "status": _choice("inProgress", "completed", "failed", "interrupted"),
         "items": [{"type": lambda x: "sleep" if x == "sleep" else "other"}]}
_SCHEMA = {
    "resumeState": _choice("needs_resume", "resuming", "resumed"),
    "threadRuntimeStatus": {
        "type": _choice("active", "idle", "systemError", "notLoaded"),
        "activeFlags": [_choice("waitingOnApproval", "waitingOnUserInput")],
    },
    "hasUnreadTurn": lambda x: x is True,
    "requests": [{"method": lambda x: _WAIT_METHODS.get(x) if isinstance(x, str) else None}],
    "turns": [_TURN],
    "turnHistory": {"kind": _choice("canonical"), "history": {
        "islands": [{"entries": [{"value": _opaque}],
                     "newerBoundary": {"status": _choice("exhausted", "unknown", "available")}}],
        "entitiesByKey": {"*": _TURN},
    }},
}


def _project(value: Any, schema: Any = _SCHEMA) -> Any:
    """Return status-only data; no unlisted field survives this boundary."""
    if callable(schema):
        return schema(value)
    if isinstance(schema, list):
        return [_project(x, schema[0]) for x in value] if isinstance(value, list) else []
    if not isinstance(value, dict):
        return {}
    if "*" in schema:
        return {k: _project(v, schema["*"]) for k, v in value.items()
                if isinstance(k, str) and len(k) <= 200}
    return {k: _project(value[k], spec) for k, spec in schema.items() if k in value}


def _schema_at(path: list[Any]) -> Any:
    schema = _SCHEMA
    for part in path:
        if isinstance(schema, list):
            if not isinstance(part, int) or isinstance(part, bool) or part < 0:
                return None
            schema = schema[0]
        elif isinstance(schema, dict):
            if not isinstance(part, str):
                return None
            schema = schema.get(part, schema.get("*"))
            if schema is None:
                return None
        else:
            return None
    return schema


def _apply_patches(state: dict, patches: Any) -> dict:
    """Apply only allowlisted Immer patches; reject invalid metadata paths."""
    if not isinstance(patches, list):
        raise _ProtocolError("unsupported_patch_format")
    for patch in patches:
        if not isinstance(patch, dict):
            raise _ProtocolError("unsupported_patch_format")
        path, operation = patch.get("path"), patch.get("op")
        if not isinstance(path, list) or operation not in ("add", "replace", "remove"):
            raise _ProtocolError("unsupported_patch_format")
        schema = _schema_at(path)
        if schema is None:
            continue  # Discard chat text and other non-status patches immediately.
        value = _project(patch.get("value"), schema) if operation != "remove" else None
        if not path:
            if operation == "remove":
                raise _ProtocolError("removed_status_root")
            state = value
            continue
        container = state
        for part in path[:-1]:
            try:
                container = container[part]
            except (KeyError, IndexError, TypeError):
                raise _ProtocolError("status_patch_requires_snapshot") from None
        key = path[-1]
        try:
            if isinstance(container, list):
                if not isinstance(key, int) or key < 0 or key > len(container):
                    raise _ProtocolError("status_patch_requires_snapshot")
                if operation == "add":
                    container.insert(key, value)
                elif operation == "remove":
                    container.pop(key)
                else:
                    container[key] = value
            elif isinstance(container, dict):
                if operation == "remove":
                    container.pop(key, None)
                else:
                    container[key] = value
            else:
                raise _ProtocolError("status_patch_requires_snapshot")
        except (KeyError, IndexError, TypeError):
            raise _ProtocolError("status_patch_requires_snapshot") from None
    return state


def _latest_turns(state: dict) -> tuple[dict | None, dict | None]:
    turns = state.get("turns", [])
    history = state.get("turnHistory", {})
    if history.get("kind") == "canonical":
        h = history.get("history", {})
        islands = h.get("islands", [])
        island = islands[-1] if islands else {}
        if island.get("newerBoundary", {}).get("status") == "exhausted":
            entities = h.get("entitiesByKey", {})
            turns = [entities.get(e.get("value")) for e in island.get("entries", [])]
            turns = [t for t in turns if isinstance(t, dict)]
        else:
            turns = [h.get("entitiesByKey", {}).get(e.get("value"))
                     for island in islands for e in island.get("entries", [])]
            turns = [t for t in turns if isinstance(t, dict)]
    latest = turns[-1] if turns else None
    control = next((t for t in reversed(turns) if t.get("turnId") is not None or
                    t.get("status") not in ("completed", "failed")), None)
    return latest, control


def _snapshot(state: dict | None, connected: bool, reason: str | None = None) -> dict:
    native = "unknown"
    detail = None
    latest = None
    if state is not None:
        runtime = state.get("threadRuntimeStatus", {})
        flags = runtime.get("activeFlags", [])
        methods = [r.get("method") for r in state.get("requests", [])]
        latest, control = _latest_turns(state)
        recognized = (state.get("resumeState") in ("needs_resume", "resuming", "resumed") or
                      runtime.get("type") in ("active", "idle", "systemError") or
                      bool(latest and latest.get("status") is not None))
        if state.get("resumeState") == "needs_resume" and runtime.get("type") == "notLoaded":
            recognized = False
        needs_resume = state.get("resumeState") == "needs_resume" or state.get("resumeState") is None
        if needs_resume:
            active = runtime.get("type") == "active"
            waiting = active and any(f in ("waitingOnApproval", "waitingOnUserInput") for f in flags)
            detail = ("approval" if "waitingOnApproval" in flags else "input") if waiting else None
            failed = runtime.get("type") == "systemError"
        else:
            items = control.get("items", []) if control else []
            sleeping = bool(items and items[-1].get("type") == "sleep")
            active = state.get("resumeState") == "resuming" or bool(
                control and control.get("status") == "inProgress" and not sleeping)
            waiting = any(m in ("approval", "input") for m in methods)
            detail = ("approval" if "approval" in methods else "input") if waiting else None
            failed = bool(latest and latest.get("status") == "failed")
        completed = ((state.get("hasUnreadTurn") and not (latest and latest.get("status") == "interrupted"))
                     or state.get("_completion_until", 0) > time.monotonic())
        native = ("unknown" if not recognized else "waiting" if waiting else "error" if failed
                  else "working" if active else "completed" if completed else "idle")
        if not recognized:
            reason = reason or "status_fields_unavailable"
    result = {"state": native, "observed_at": time.time(), "connected": connected,
              "detail_kind": detail, "counts": {key: int(key == native) for key in _STATES},
              "source": _SOURCE, "scope": "current_chat", "reason": reason}
    if latest and latest.get("turnId") and native in ("completed", "error"):
        result["event_id"] = hashlib.sha256(latest["turnId"].encode()).hexdigest()[:24]
    return result


def _recent_local_ids() -> list[str]:
    """Read IDs only. Never query titles, messages, rollout paths, or logs."""
    directory = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    path = directory / "state_5.sqlite"
    if not path.is_file():
        return []
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.5)) as db:
            db.execute("PRAGMA query_only=ON")
            rows = db.execute("SELECT id FROM threads WHERE archived=0 ORDER BY updated_at DESC LIMIT 50").fetchall()
        return [row[0] for row in rows if isinstance(row[0], str) and
                re.fullmatch(r"[A-Za-z0-9-]{16,80}", row[0])]
    except (sqlite3.Error, OSError, ValueError):
        return []


def _aggregate(states: list[dict | None], connected: bool, reason: str | None = None) -> dict:
    values = [_snapshot(state, connected) for state in states]
    counts = {key: sum(value["state"] == key for value in values) for key in _STATES}
    if not values:
        values = [_snapshot(None, connected, reason or "no_owned_local_chats")]
        counts["unknown"] = 1
    priority = ("waiting", "error", "working", "completed", "unknown", "idle")
    winner = next(value for key in priority for value in values if value["state"] == key)
    result = dict(winner, counts=counts, scope="desktop", observed_at=time.time(), reason=reason)
    if not states:
        result["reason"] = reason or "no_owned_local_chats"
    result["observed_chat_count"] = len(states)
    result["discovery_limit"] = 50
    return result


class _WindowsPipe:
    """An existing named-pipe client only; no server and no ownership claims."""
    def __init__(self, stop: threading.Event):
        if os.name != "nt":
            raise _ProtocolError("windows_desktop_required")
        from ctypes import wintypes as w
        self._w, self._stop = w, stop
        self._k = k = ctypes.WinDLL("kernel32", use_last_error=True)

        class Overlapped(ctypes.Structure):
            _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                        ("Offset", w.DWORD), ("OffsetHigh", w.DWORD), ("hEvent", w.HANDLE)]

        self._overlapped = Overlapped
        k.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
        k.CreateFileW.restype = w.HANDLE
        k.CloseHandle.argtypes = [w.HANDLE]
        k.CloseHandle.restype = w.BOOL
        k.PeekNamedPipe.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.c_void_p,
                                   ctypes.POINTER(w.DWORD), ctypes.c_void_p]
        k.PeekNamedPipe.restype = w.BOOL
        for name in ("ReadFile", "WriteFile"):
            fn = getattr(k, name)
            fn.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.POINTER(Overlapped)]
            fn.restype = w.BOOL
        k.CreateEventW.argtypes = [ctypes.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR]
        k.CreateEventW.restype = w.HANDLE
        k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        k.WaitForSingleObject.restype = w.DWORD
        k.CancelIoEx.argtypes = [w.HANDLE, ctypes.POINTER(Overlapped)]
        k.CancelIoEx.restype = w.BOOL
        k.GetOverlappedResult.argtypes = [w.HANDLE, ctypes.POINTER(Overlapped), ctypes.POINTER(w.DWORD), w.BOOL]
        k.GetOverlappedResult.restype = w.BOOL
        self._handle = k.CreateFileW(_ENDPOINT, 0xC0000000, 0, None, 3, 0x40000000, None)
        if self._handle == ctypes.c_void_p(-1).value:
            self._handle = None
            raise _ProtocolError("desktop_pipe_unavailable")

    def _transfer(self, data: bytes | None, count: int, allow_stopped: bool = False) -> bytes | int:
        w, k = self._w, self._k
        ov = self._overlapped()
        ov.hEvent = k.CreateEventW(None, True, False, None)
        if not ov.hEvent:
            raise _ProtocolError("pipe_io_unavailable")
        buffer = ctypes.create_string_buffer(data) if data is not None else ctypes.create_string_buffer(count)
        transferred = w.DWORD()
        fn = k.WriteFile if data is not None else k.ReadFile
        try:
            ok = fn(self._handle, buffer, count, ctypes.byref(transferred), ctypes.byref(ov))
            if not ok:
                if ctypes.get_last_error() != 997:
                    raise _ProtocolError("desktop_pipe_disconnected")
                deadline = time.monotonic() + 2
                while k.WaitForSingleObject(ov.hEvent, 50) == 258:
                    if (self._stop.is_set() and not allow_stopped) or time.monotonic() >= deadline:
                        k.CancelIoEx(self._handle, ctypes.byref(ov))
                        k.GetOverlappedResult(self._handle, ctypes.byref(ov), ctypes.byref(transferred), True)
                        raise _ProtocolError("pipe_io_cancelled")
                if not k.GetOverlappedResult(self._handle, ctypes.byref(ov), ctypes.byref(transferred), False):
                    raise _ProtocolError("desktop_pipe_disconnected")
            return transferred.value if data is not None else buffer.raw[:transferred.value]
        finally:
            k.CloseHandle(ov.hEvent)

    def send(self, message: dict, allow_stopped: bool = False):
        body = json.dumps(message, separators=(",", ":")).encode()
        data = struct.pack("<I", len(body)) + body
        written = self._transfer(data, len(data), allow_stopped)
        if written != len(data):
            raise _ProtocolError("incomplete_pipe_write")

    def read_available(self) -> bytes:
        available = self._w.DWORD()
        if not self._k.PeekNamedPipe(self._handle, None, 0, None, ctypes.byref(available), None):
            raise _ProtocolError("desktop_pipe_disconnected")
        if not available.value:
            return b""
        return self._transfer(None, min(available.value, 1024 * 1024))

    def close(self):
        if self._handle is not None:
            self._k.CancelIoEx(self._handle, None)
            self._k.CloseHandle(self._handle)
            self._handle = None


class WorkStatusObserver:
    """Observe one owned local chat; callback receives only normalized metadata.

    start() is idempotent while running. stop() joins the worker. No work is
    started/resumed and no approval is ever answered. Missing target, missing
    owner, incompatible protocol, and disconnect all produce unknown.
    """
    def __init__(self, callback: Callable[[dict], None], thread_id: str | None = None,
                 scope: str = "thread"):
        if scope not in ("thread", "desktop"):
            raise ValueError("scope must be 'thread' or 'desktop'")
        self._callback = callback
        self._thread_id = thread_id if thread_id is not None else os.environ.get("CODEX_THREAD_ID")
        self._scope = scope
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._last_fingerprint = None
        self._largest_frame_bytes = 0  # Size only, useful for local resource diagnostics.

    def start(self):
        if self._worker and self._worker.is_alive():
            return self
        self._stop.clear()
        self._worker = threading.Thread(target=self._run, name="codex-work-status", daemon=False)
        self._worker.start()
        return self

    def stop(self):
        self._stop.set()
        worker = self._worker
        if worker and worker is not threading.current_thread():
            worker.join(timeout=5)

    def _emit(self, state: dict | None, connected: bool, reason: str | None = None,
              heartbeat: bool = False):
        result = _snapshot(state, connected, reason)
        if self._scope == "desktop":
            result["scope"] = "desktop"
        self._emit_result(result, heartbeat)

    def _emit_result(self, result: dict, heartbeat: bool = False):
        fingerprint = {k: v for k, v in result.items() if k != "observed_at"}
        if heartbeat or fingerprint != self._last_fingerprint:
            self._last_fingerprint = fingerprint
            try:
                self._callback(result)
            except Exception:
                pass  # Never leak callback exceptions or server payloads to logs.

    def _run(self):
        if not self._thread_id and self._scope == "thread":
            self._emit(None, False, "current_chat_unavailable")
            return
        if self._thread_id and not re.fullmatch(r"[A-Za-z0-9-]{16,80}", self._thread_id):
            self._emit(None, False, "invalid_chat_identifier")
            return
        if os.name != "nt":
            self._emit(None, False, "windows_desktop_required")
            return
        self._emit(None, False, "connecting")
        while not self._stop.is_set():
            try:
                self._session()
            except _ProtocolError as exc:
                if not self._stop.is_set():
                    self._emit(None, False, str(exc))
            except Exception:
                if not self._stop.is_set():
                    self._emit(None, False, "desktop_protocol_unavailable")
            if self._stop.wait(3):
                break

    def _session(self):
        pipe = _WindowsPipe(self._stop)
        client_id = "initializing-client"
        initialize_id = str(uuid.uuid4())
        pending = bytearray()
        owned = {}  # thread ID -> status-only projection and stream bookkeeping.
        discovery = {}  # request ID -> (thread ID, deadline).
        attempted = {}
        candidates = []
        initialized = False
        initialize_deadline = time.monotonic() + 5
        discover_at = 0.0
        heartbeat_at = time.monotonic() + 5

        def request(method, params, request_id, version):
            pipe.send({"type": "request", "requestId": request_id,
                       "sourceClientId": client_id, "version": version,
                       "method": method, "params": params, "timeoutMs": 3000})

        def follow(thread_id, entry, value, allow_stopped=False):
            pipe.send({"type": "broadcast", "method": "thread-stream-following-changed",
                       "sourceClientId": client_id, "targetClientIds": [entry["owner"]], "version": 1,
                       "params": {"conversationId": thread_id, "hostId": "local", "following": value}},
                      allow_stopped=allow_stopped)

        def emit(reason=None, heartbeat=False):
            if self._scope == "desktop":
                result = _aggregate([entry["state"] for entry in owned.values()], True, reason)
                self._emit_result(result, heartbeat)
            else:
                entry = owned.get(self._thread_id)
                self._emit(entry["state"] if entry else None, True,
                           reason or ("current_chat_owner_unavailable" if entry is None else None), heartbeat)

        def track_transition(entry, previous_turn):
            state = entry["state"]
            latest, _ = _latest_turns(state)
            if (previous_turn and latest and previous_turn.get("turnId") == latest.get("turnId")
                    and latest.get("turnId") is not None and previous_turn.get("status") == "inProgress"
                    and latest.get("status") == "completed"):
                entry["completion_until"] = time.monotonic() + 8
            if not latest or latest.get("status") != "completed":
                entry["completion_until"] = 0
            state["_completion_until"] = entry.get("completion_until", 0)

        try:
            request("initialize", {"clientType": "quota-pet-observer"}, initialize_id, 0)
            while not self._stop.is_set():
                chunk = pipe.read_available()
                if chunk:
                    pending.extend(chunk)
                while len(pending) >= 4:
                    size = struct.unpack_from("<I", pending)[0]
                    self._largest_frame_bytes = max(self._largest_frame_bytes, size)
                    if not 0 < size <= _MAX_FRAME:
                        raise _ProtocolError("unsupported_frame_size")
                    if len(pending) < size + 4:
                        break
                    if size > 1024 * 1024:
                        # Move the large complete frame instead of copying its
                        # entire byte buffer. Only its small trailing frame(s)
                        # are copied; no second large snapshot is queued here.
                        frame = pending
                        pending = frame[size + 4:]
                        del frame[size + 4:]
                        del frame[:4]
                        message = json.loads(frame)
                        frame.clear()
                        frame = None
                    else:
                        message = json.loads(pending[4:size + 4])
                        del pending[:size + 4]
                    if not isinstance(message, dict):
                        raise _ProtocolError("unsupported_message")
                    kind = message.get("type")
                    if kind == "client-discovery-request":
                        # Explicit refusal: never become a handler for any request.
                        pipe.send({"type": "client-discovery-response", "requestId": message.get("requestId"),
                                   "response": {"canHandle": False}})
                    elif kind == "response" and message.get("requestId") == initialize_id:
                        if message.get("resultType") != "success":
                            raise _ProtocolError("desktop_initialize_rejected")
                        client_id = message.get("result", {}).get("clientId")
                        if not isinstance(client_id, str):
                            raise _ProtocolError("unsupported_initialize_response")
                        initialized = True
                    elif kind == "response" and message.get("requestId") in discovery:
                        thread_id, _ = discovery.pop(message["requestId"])
                        owner_id = message.get("handledByClientId")
                        if message.get("resultType") == "success" and isinstance(owner_id, str):
                            now = time.monotonic()
                            old = owned.get(thread_id)
                            if old is None or old["owner"] != owner_id:
                                if old is not None:
                                    follow(thread_id, old, False)
                                entry = {"owner": owner_id, "state": None, "revision": None,
                                         "resync": False, "deadline": now + 15, "last_snapshot": now}
                                owned[thread_id] = entry
                                follow(thread_id, entry, True)
                        else:
                            old = owned.pop(thread_id, None)
                            if old is not None:
                                follow(thread_id, old, False)
                            if old is not None or self._scope == "thread":
                                emit("current_chat_owner_unavailable")
                    elif kind == "broadcast":
                        method = message.get("method")
                        params = message.get("params", {})
                        if method == "client-status-changed" and params.get("status") == "disconnected":
                            departed = params.get("clientId")
                            for thread_id, entry in list(owned.items()):
                                if entry["owner"] == departed:
                                    del owned[thread_id]
                                    attempted.pop(thread_id, None)
                            discover_at = 0
                            emit("desktop_owner_disconnected")
                        elif method == "ipc-connection-reset":
                            raise _ProtocolError("desktop_connection_reset")
                        elif method == "thread-archived":
                            thread_id = params.get("conversationId")
                            entry = owned.pop(thread_id, None)
                            if entry:
                                follow(thread_id, entry, False)
                                emit()
                        elif method == "thread-stream-following-status-requested":
                            thread_id = params.get("conversationId")
                            entry = owned.get(thread_id)
                            if entry and params.get("hostId") == "local" and message.get("sourceClientId") == entry["owner"]:
                                follow(thread_id, entry, True)
                        elif method == "thread-stream-state-changed":
                            thread_id = params.get("conversationId")
                            entry = owned.get(thread_id)
                            if entry and params.get("hostId") == "local" and message.get("sourceClientId") == entry["owner"]:
                                change = params.get("change", {})
                                previous_turn = _latest_turns(entry["state"])[0] if entry["state"] else None
                                previous_turn = dict(previous_turn) if previous_turn else None
                                if change.get("type") == "snapshot":
                                    raw = change.get("conversationState")
                                    if not isinstance(raw, dict):
                                        raise _ProtocolError("unsupported_snapshot")
                                    entry["state"] = _project(raw)
                                    raw = None
                                    track_transition(entry, previous_turn)
                                    entry["revision"] = change.get("revision")
                                    entry["resync"] = False
                                    entry["deadline"] = None
                                    emit()
                                elif change.get("type") == "patches":
                                    if (entry["state"] is None or entry["resync"] or
                                            change.get("baseRevision") != entry["revision"]):
                                        entry["resync"] = True
                                    else:
                                        try:
                                            entry["state"] = _apply_patches(entry["state"], change.get("patches"))
                                            track_transition(entry, previous_turn)
                                            entry["revision"] = change.get("revision")
                                        except _ProtocolError:
                                            entry["resync"] = True
                                    if entry["resync"]:
                                        entry["state"] = None
                                    emit("resynchronizing" if entry["resync"] else None)
                                change = None
                        params = None
                    # Full source payload has no surviving reference after this.
                    message = None
                now = time.monotonic()
                if not initialized:
                    if now >= initialize_deadline:
                        raise _ProtocolError("desktop_initialize_timeout")
                    self._stop.wait(0.05)
                    continue
                if now >= discover_at:
                    ids = _recent_local_ids() if self._scope == "desktop" else []
                    # Ownership may move without the old desktop client exiting.
                    # A same-owner discovery result does not request a snapshot.
                    ids.extend(owned)
                    if self._thread_id:
                        ids.insert(0, self._thread_id)
                    in_flight = {value[0] for value in discovery.values()}
                    for thread_id in dict.fromkeys(ids):
                        interval = 30 if thread_id in owned else 10
                        if (thread_id not in candidates and thread_id not in in_flight
                                and now - attempted.get(thread_id, -60) >= interval):
                            candidates.append(thread_id)
                    discover_at = now + 10
                for request_id, (thread_id, deadline) in list(discovery.items()):
                    if now >= deadline:
                        del discovery[request_id]
                        old = owned.pop(thread_id, None)
                        if old is not None:
                            follow(thread_id, old, False)
                        if old is not None or self._scope == "thread":
                            emit("current_chat_owner_unavailable")
                while candidates and len(discovery) < 4:
                    thread_id = candidates.pop(0)
                    request_id = str(uuid.uuid4())
                    request("thread-owner-discovery", {"hostId": "local", "conversationId": thread_id}, request_id, 1)
                    discovery[request_id] = (thread_id, now + 4)
                    attempted[thread_id] = now
                for thread_id, entry in list(owned.items()):
                    if entry["deadline"] is not None and now >= entry["deadline"]:
                        follow(thread_id, entry, False)
                        del owned[thread_id]
                        emit("desktop_status_timeout")
                        continue
                    if entry["resync"] and now - entry["last_snapshot"] >= 1:
                        follow(thread_id, entry, True)
                        entry["last_snapshot"] = now
                        if entry["deadline"] is None:
                            entry["deadline"] = now + 5
                if now >= heartbeat_at:
                    # Existing pipe and owner-disconnect broadcasts provide liveness;
                    # heartbeat does not repeatedly request full conversation snapshots.
                    emit(heartbeat=True)
                    heartbeat_at = now + 5
                if not chunk:
                    self._stop.wait(0.05)
        finally:
            cleanup_deadline = time.monotonic() + 2
            for thread_id, entry in owned.items():
                if time.monotonic() >= cleanup_deadline:
                    break  # Closing the pipe also removes every follower subscription.
                try:
                    follow(thread_id, entry, False, allow_stopped=True)
                except Exception:
                    pass
            pipe.close()
            pending.clear()
            owned.clear()


def main():
    parser = argparse.ArgumentParser(description="Print current-chat status metadata from the running Windows Codex desktop.")
    parser.add_argument("--seconds", type=float, default=10, help="Bounded observation duration (default: 10 seconds).")
    parser.add_argument("--scope", choices=("thread", "desktop"), default="thread",
                        help="Current CODEX_THREAD_ID or owned recent local desktop chats.")
    args = parser.parse_args()
    if not 0 < args.seconds <= 3600:
        parser.error("--seconds must be between 0 and 3600")
    observer = WorkStatusObserver(lambda value: print(json.dumps(value, ensure_ascii=False), flush=True),
                                  scope=args.scope).start()
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()


if __name__ == "__main__":
    main()
