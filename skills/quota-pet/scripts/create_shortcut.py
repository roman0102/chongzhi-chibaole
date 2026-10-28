#!/usr/bin/env python3
"""Create or update a Windows shortcut for this skin, without starting the pet.

Example: python scripts/create_shortcut.py --skin assets/seinorin/skin.json

Every run rebuilds launcher arguments from the supplied options and skin.
Unspecified options use launcher defaults; old shortcut arguments are never
silently inherited. Use the complete intended configuration when updating.
An existing shortcut is replaceable only when it already targets this exact
skill's quota_pet.py through Python. A matching .ico must be supplied by the
skin or --icon. Optional square PNG conversion requires Pillow.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
from ctypes import wintypes
import hashlib
import io
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import uuid


class ShortcutError(Exception):
    """A concise error suitable for the helper's JSON output."""


_MARKER = "[quota-pet-shortcut:v1]"
_POLICY = "rebuild_from_options_and_skin"
_MAX_IMAGE_BYTES = 20 * 1024 * 1024

# Static bridge only. All user-controlled data travels over JSON stdin.
# No shortcut target is executed and no PowerShell expression is built from it.
_BRIDGE = r'''
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json
$shell = New-Object -ComObject WScript.Shell
function Read-Link([string]$path) {
    if (-not [System.IO.File]::Exists($path)) { return @{exists=$false} }
    $link = $shell.CreateShortcut($path)
    return @{exists=$true; target=$link.TargetPath; args=$link.Arguments;
        working_directory=$link.WorkingDirectory; icon=$link.IconLocation;
        description=$link.Description; window_style=$link.WindowStyle}
}
function Same-Link($left, $right) {
    foreach ($key in @('target','args','working_directory','icon','description','window_style')) {
        if ([string]$left[$key] -cne [string]$right.$key) { return $false }
    }
    return $true
}
$temporary = $null
try {
    $current = Read-Link $request.path
    if ($request.operation -eq 'read') {
        $current | ConvertTo-Json -Compress -Depth 5
        exit 0
    }
    if ($request.operation -ne 'write') { throw 'Unsupported bridge operation.' }
    if ($current.exists -ne [bool]$request.before.exists -or
        ($current.exists -and -not (Same-Link $current $request.before))) {
        throw 'Shortcut changed during preparation; nothing was overwritten.'
    }
    $temporary = [System.IO.Path]::Combine([System.IO.Path]::GetDirectoryName($request.path),
        '.quota-pet-' + [guid]::NewGuid().ToString('N') + '.lnk')
    $link = $shell.CreateShortcut($temporary)
    $link.TargetPath = [string]$request.desired.target
    $link.Arguments = [string]$request.desired.args
    $link.WorkingDirectory = [string]$request.desired.working_directory
    $link.IconLocation = [string]$request.desired.icon
    $link.Description = [string]$request.desired.description
    $link.WindowStyle = [int]$request.desired.window_style
    $link.Save()
    $prepared = Read-Link $temporary
    if (-not (Same-Link $prepared $request.desired)) {
        throw 'Prepared shortcut did not pass readback verification.'
    }
    $latest = Read-Link $request.path
    if ($latest.exists -ne [bool]$request.before.exists -or
        ($latest.exists -and -not (Same-Link $latest $request.before))) {
        throw 'Shortcut changed during preparation; nothing was overwritten.'
    }
    if ($current.exists) {
        # Windows PowerShell 5.1 otherwise coerces $null to an invalid empty
        # backup filename when binding this .NET string parameter.
        [System.IO.File]::Replace($temporary, [string]$request.path,
            [System.Management.Automation.Language.NullString]::Value)
    } else {
        [System.IO.File]::Move($temporary, [string]$request.path)
    }
    $temporary = $null
    Read-Link $request.path | ConvertTo-Json -Compress -Depth 5
} catch {
    @{error=$_.Exception.Message} | ConvertTo-Json -Compress -Depth 3
    exit 1
} finally {
    if ($null -ne $temporary -and [System.IO.File]::Exists($temporary)) {
        [System.IO.File]::Delete($temporary)
    }
}
'''


def _bridge(request: dict) -> dict:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    powershell = Path(system_root) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    if not powershell.is_file():
        raise ShortcutError("Windows PowerShell is unavailable; the shortcut was not changed.")
    encoded = base64.b64encode(_BRIDGE.encode("utf-16-le")).decode("ascii")
    try:
        process = subprocess.run(
            [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            input=json.dumps(request, ensure_ascii=False).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25,
            creationflags=subprocess.CREATE_NO_WINDOW, shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ShortcutError("The Windows shortcut bridge failed or timed out; verify the destination before retrying.") from exc
    try:
        value = json.loads(process.stdout.decode("utf-8-sig").strip())
    except (ValueError, TypeError, UnicodeError):
        raise ShortcutError("The Windows shortcut bridge did not return valid JSON.") from None
    if not isinstance(value, dict):
        raise ShortcutError("The Windows shortcut bridge returned an invalid result.")
    if process.returncode or value.get("error"):
        raise ShortcutError(str(value.get("error") or "Windows could not create the shortcut."))
    return value


def known_desktop() -> Path:
    """Use the redirected Windows Known Folder, including OneDrive desktops."""
    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    folder = GUID.from_buffer_copy(uuid.UUID("B4BFCC3A-DB2C-424C-B029-7FE99A87C641").bytes_le)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD,
                                           wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    pointer = ctypes.c_wchar_p()
    result = shell32.SHGetKnownFolderPath(ctypes.byref(folder), 0, None, ctypes.byref(pointer))
    if result != 0 or not pointer.value:
        raise ShortcutError("Windows could not locate the Desktop; use --shortcut-path.")
    try:
        return Path(pointer.value).resolve()
    finally:
        ole32.CoTaskMemFree(ctypes.cast(pointer, ctypes.c_void_p))


def _argv(arguments: str) -> list[str]:
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    count = ctypes.c_int()
    # Arguments lacks the executable; prepend one to preserve normal Windows
    # quoting rules for its first (script path) argument.
    pointer = shell32.CommandLineToArgvW("quota-pet " + arguments, ctypes.byref(count))
    if not pointer:
        raise ShortcutError("Existing shortcut arguments could not be parsed.")
    try:
        return [pointer[index] for index in range(1, count.value)]
    finally:
        kernel32.LocalFree(ctypes.cast(pointer, ctypes.c_void_p))


def _same_path(left: str | Path, right: str | Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _owned(existing: dict, launcher: Path) -> bool:
    target = Path(existing.get("target") or "")
    if target.name.lower() not in ("python.exe", "pythonw.exe"):
        return False
    arguments = _argv(existing.get("args") or "")
    return bool(arguments and Path(arguments[0]).is_absolute() and _same_path(arguments[0], launcher))


def _file(path: str | Path, label: str) -> Path:
    try:
        resolved = Path(path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ShortcutError(f"{label} does not exist: {path}") from exc
    if not resolved.is_file():
        raise ShortcutError(f"{label} must be a file: {resolved}")
    return resolved


def _skin(path: str | None) -> tuple[Path | None, dict]:
    if path is None:
        return None, {}
    skin = _file(path, "Skin")
    if skin.stat().st_size > 1024 * 1024:
        raise ShortcutError("Skin JSON is too large.")
    try:
        data = json.loads(skin.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ShortcutError("Skin must be valid UTF-8 JSON.") from exc
    if not isinstance(data, dict):
        raise ShortcutError("Skin must contain a JSON object.")
    return skin, data


def _validate_ico(path: Path) -> Path:
    size = path.stat().st_size
    if size > _MAX_IMAGE_BYTES or size < 22:
        raise ShortcutError("ICO must be a valid icon file no larger than 20 MiB.")
    with path.open("rb") as handle:
        header = handle.read(6)
        reserved, kind, count = struct.unpack("<HHH", header)
        if reserved != 0 or kind != 1 or not 1 <= count <= 256 or size < 6 + 16 * count:
            raise ShortcutError("The supplied .ico does not have a valid Windows icon header.")
        directory = handle.read(16 * count)
    for index in range(count):
        entry = directory[index * 16:(index + 1) * 16]
        length, offset = struct.unpack_from("<II", entry, 8)
        if not length or offset < 6 + 16 * count or offset + length > size:
            raise ShortcutError("The supplied ICO contains an invalid image entry.")
    # Header bounds alone cannot prove that Windows can decode the embedded
    # image. Load it as an in-memory icon, without displaying a window, then
    # immediately release the handle. This keeps the ICO path standard-library
    # only while rejecting corrupt files that would show a generic icon.
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.LoadImageW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.UINT,
                                 ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.DestroyIcon.argtypes = [wintypes.HANDLE]
    user32.DestroyIcon.restype = wintypes.BOOL
    handle = user32.LoadImageW(None, str(path), 1, 0, 0, 0x10 | 0x40)
    if not handle:
        raise ShortcutError("Windows could not decode this ICO; provide a valid character icon.")
    user32.DestroyIcon(handle)
    return path


def _png_to_ico(source: Path) -> Path:
    if source.stat().st_size > _MAX_IMAGE_BYTES:
        raise ShortcutError("PNG must be no larger than 20 MiB.")
    try:
        from PIL import Image
    except ImportError as exc:
        raise ShortcutError("PNG conversion needs Pillow. Install Pillow or supply an existing .ico.") from exc
    try:
        with Image.open(source) as original:
            if original.format != "PNG" or original.width != original.height or not 16 <= original.width <= 4096:
                raise ShortcutError("--icon PNG must be square, between 16 and 4096 pixels per side.")
            if getattr(original, "n_frames", 1) != 1:
                raise ShortcutError("Use a single still PNG for the icon.")
            image = original.convert("RGBA")
            if image.width > 256:
                image = image.resize((256, 256), Image.Resampling.LANCZOS)
            sizes = [(n, n) for n in (16, 24, 32, 48, 64, 128, 256) if n <= image.width]
            output = io.BytesIO()
            image.save(output, format="ICO", sizes=sizes)
            payload = output.getvalue()
    except (OSError, ValueError) as exc:
        raise ShortcutError("The PNG could not be converted to a Windows icon.") from exc
    # A stable content-addressed sibling avoids overwriting unrelated icons.
    digest = hashlib.sha256(payload).hexdigest()[:16]
    destination = source.with_name(f"{source.stem}-quota-pet-{digest}.ico")
    try:
        with destination.open("xb") as handle:
            handle.write(payload)
    except FileExistsError:
        if destination.read_bytes() != payload:
            raise ShortcutError("A different file already occupies the generated icon path.") from None
    except OSError as exc:
        raise ShortcutError("Cannot save the converted ICO next to the PNG; use a writable location or an existing ICO.") from exc
    return _validate_ico(destination.resolve())


def _icon(explicit: str | None, skin: Path | None, data: dict) -> Path:
    if explicit:
        source = _file(explicit, "Icon")
    else:
        relative = data.get("icon")
        if skin is None or not isinstance(relative, str) or not relative.strip():
            raise ShortcutError("No matching character icon. Add a relative .ico 'icon' field to the skin or provide --icon.")
        relative_path = Path(relative)
        if relative_path.is_absolute() or relative_path.suffix.lower() != ".ico":
            raise ShortcutError("Skin 'icon' must be a relative .ico path inside the skin directory.")
        source = _file(skin.parent / relative_path, "Skin icon")
        if not source.is_relative_to(skin.parent):
            raise ShortcutError("Skin 'icon' must stay inside the skin directory.")
    if source.suffix.lower() == ".png":
        return _png_to_ico(source)
    if source.suffix.lower() != ".ico":
        raise ShortcutError("--icon must be an existing .ico or square .png.")
    return _validate_ico(source)


def _pythonw(explicit: str | None) -> Path:
    candidate = Path(explicit or sys.executable).resolve()
    if candidate.name.lower() == "python.exe":
        candidate = candidate.with_name("pythonw.exe")
    if candidate.name.lower() != "pythonw.exe" or not candidate.is_file():
        raise ShortcutError("A pythonw.exe is required. Use --python with the Python installation that runs this skill.")
    return candidate


def _notify(path: Path, created: bool):
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.SHChangeNotify.argtypes = [ctypes.c_long, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    shell32.SHChangeNotify.restype = None
    event = 0x00000002 if created else 0x00002000  # CREATE / UPDATEITEM
    shell32.SHChangeNotify(event, 0x0005 | 0x2000, ctypes.c_wchar_p(str(path)), None)


def _matches(actual: dict, desired: dict) -> bool:
    return (actual.get("exists") is True and
            all(str(actual.get(key)) == str(value) for key, value in desired.items()))


def create_shortcut(options: argparse.Namespace) -> dict:
    if os.name != "nt":
        raise ShortcutError("This helper creates Windows .lnk files and must run on Windows.")
    launcher = _file(Path(__file__).with_name("quota_pet.py"), "Pet launcher")
    skin, skin_data = _skin(options.skin)
    name = options.name if options.name is not None else skin_data.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 12 or any(ord(c) < 32 for c in name):
        raise ShortcutError("Provide --name or a skin name containing 1–12 characters.")
    name = name.strip()
    for option in ("accent", "color"):
        value = getattr(options, option)
        if value is not None and not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
            raise ShortcutError(f"--{option} must be a color such as #9270d6.")
    if options.interval is not None and options.interval < 30:
        raise ShortcutError("--interval must be at least 30 seconds.")
    if options.bucket is not None and (not options.bucket.strip() or options.bucket.startswith("-")):
        raise ShortcutError("--bucket must be a nonempty quota bucket ID.")
    if options.work_scope == "thread" and not options.work_thread:
        raise ShortcutError("A persistent single-chat shortcut requires --work-thread; otherwise use --work-scope desktop.")
    if options.work_thread is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", options.work_thread):
        raise ShortcutError("--work-thread must be a valid chat ID.")
    target = _pythonw(options.python)
    filename = re.sub(r'[<>:"/\\|?*]', "_", name).rstrip(". ") + " · 重置吃饱了.lnk"
    path = (Path(options.shortcut_path).expanduser() if options.shortcut_path else known_desktop() / filename).resolve()
    if path.suffix.lower() != ".lnk" or (path.exists() and not path.is_file()):
        raise ShortcutError("--shortcut-path must name a .lnk file.")
    before = _bridge({"operation": "read", "path": str(path)})
    if before.get("exists") and not _owned(before, launcher):
        raise ShortcutError("An unrelated shortcut already exists at this path; choose another --shortcut-path. It was not overwritten.")
    icon = _icon(options.icon, skin, skin_data)
    arguments = [str(launcher), "--name", name]
    if skin is not None:
        arguments.extend(("--skin", str(skin)))
    for option in ("color", "accent", "interval", "bucket"):
        value = getattr(options, option)
        if value is not None:
            arguments.extend(("--" + option, str(value)))
    if options.codex is not None:
        arguments.extend(("--codex", str(_file(options.codex, "Codex executable"))))
    if options.no_roam:
        arguments.append("--no-roam")
    scope = "thread" if options.work_thread else options.work_scope
    arguments.extend(("--work-scope", scope))
    if options.work_thread:
        arguments.extend(("--work-thread", options.work_thread))
    desired = {"target": str(target), "args": subprocess.list2cmdline(arguments),
               "working_directory": str(launcher.parent), "icon": str(icon) + ",0",
               "description": f"{name} · 重置吃饱了 {_MARKER}", "window_style": 1}
    if _matches(before, desired):
        action = "unchanged"
        actual = before
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        actual = _bridge({"operation": "write", "path": str(path), "before": before, "desired": desired})
        action = "updated" if before.get("exists") else "created"
    verified = _matches(actual, desired) and _argv(actual["args"]) == arguments
    if not verified:
        raise ShortcutError("Shortcut readback did not match the requested configuration; the pet was not started.")
    _notify(path, action == "created")
    return {"path": str(path), "target": actual["target"], "args": actual["args"],
            "argv": arguments, "working_directory": actual["working_directory"],
            "icon": actual["icon"], "action": action, "verified": True,
            "argument_policy": _POLICY, "pet_started": False}


def main() -> int:
    # Windows terminal code pages must not corrupt JSON containing Chinese paths.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", help="Pet name; defaults to the skin JSON name")
    parser.add_argument("--skin", help="Skin JSON; name and a relative .ico icon may be read from it")
    parser.add_argument("--icon", help="Explicit character ICO or existing square PNG; PNG conversion needs Pillow")
    parser.add_argument("--shortcut-path", help="Exact destination .lnk; defaults to the redirected Windows Desktop")
    parser.add_argument("--accent", help="Launcher accent color, e.g. #9270d6; omitted uses launcher default")
    parser.add_argument("--color", help="Procedural pet body color, e.g. #f4b6a5; omitted uses launcher default")
    parser.add_argument("--no-roam", action="store_true", help="Keep the pet in one place; gestures still work")
    parser.add_argument("--interval", type=int, help="Quota refresh interval in seconds, minimum 30; omitted uses launcher default")
    parser.add_argument("--bucket", help="Explicit quota bucket ID; omitted uses launcher default")
    parser.add_argument("--codex", help="Explicit path to the installed Codex executable")
    parser.add_argument("--work-scope", choices=("desktop", "thread"), default="desktop")
    parser.add_argument("--work-thread", help="Explicit chat ID; implies --work-scope thread")
    parser.add_argument("--python", help="Python interpreter for this skill; defaults to sys.executable's pythonw.exe")
    options = parser.parse_args()
    try:
        result = create_shortcut(options)
    except (ShortcutError, OSError) as exc:
        print(json.dumps({"verified": False, "error": str(exc), "pet_started": False}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
