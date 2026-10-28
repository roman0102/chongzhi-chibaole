#!/usr/bin/env python3
"""A local Codex quota companion. Python 3.10+ and Tk; optional skins use Pillow."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time

from codex_quota import read_quota
from pet_behavior import HoverState, PetBehavior, clamp_position


def prepare_dpi():
    """Use physical window coordinates on Windows, including mixed-DPI desktops."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except (AttributeError, OSError):
                pass


def window_work_area(root):
    """The current monitor's work area, in physical desktop coordinates."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            class MonitorInfo(ctypes.Structure):
                _fields_ = (("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                            ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD))
            user32 = ctypes.windll.user32
            user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
            user32.GetAncestor.restype = wintypes.HWND
            user32.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
            user32.MonitorFromWindow.restype = wintypes.HANDLE
            user32.GetMonitorInfoW.argtypes = (wintypes.HANDLE, ctypes.POINTER(MonitorInfo))
            user32.GetMonitorInfoW.restype = wintypes.BOOL
            hwnd = user32.GetAncestor(root.winfo_id(), 2)
            info = MonitorInfo(cbSize=ctypes.sizeof(MonitorInfo))
            if user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(info)):
                area = info.rcWork
                return area.left, area.top, area.right, area.bottom
        except (AttributeError, OSError, ValueError):
            pass
    # Leave a conservative dock/taskbar margin where Tk has no work-area API.
    return 0, 0, root.winfo_screenwidth(), max(1, root.winfo_screenheight()-48)


def move_window(root, x, y):
    """Move without Tk's negative-offset-from-right interpretation on Windows."""
    x, y = round(x), round(y)
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user32.GetAncestor.restype = wintypes.HWND
        user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
        user32.SetWindowPos.restype = wintypes.BOOL
        hwnd = user32.GetAncestor(root.winfo_id(), 2)
        if user32.SetWindowPos(hwnd, None, x, y, 0, 0, 0x0015):
            return
    root.geometry(f"+{max(0, x)}+{max(0, y)}")


def stage_for(remaining):
    if remaining is None:
        return "unknown", "饭量还没查到"
    if remaining <= 0:
        return "empty", "饭呢……我的饭呢……"
    if remaining < 20:
        return "hungry", "还有活？先给口饭吧"
    if remaining < 50:
        return "normal", "每一个任务，都是减肥"
    if remaining < 80:
        return "round", "还能干，肚子里有货"
    return "full", "粮草充足，尽管吩咐"


def countdown(reset, now=None):
    if not isinstance(reset, (int, float)) or not math.isfinite(reset):
        return "开饭时间未知"
    seconds = max(0, int(reset - (time.time() if now is None else now)))
    if not seconds:
        return "到开饭时间了，等额度更新"
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60
    if days:
        return f"下次恢复 · {days}天 {hours}小时"
    if hours:
        return f"下次恢复 · {hours}小时 {minutes}分"
    return f"下次恢复 · {max(1, minutes)}分钟"


def window_label(minutes):
    if minutes is None:
        return "额度"
    if minutes == 10080:
        return "周额度"
    if minutes == 300:
        return "5小时额度"
    if minutes and minutes % 1440 == 0:
        return f"{minutes // 1440:g}天额度"
    if minutes and minutes % 60 == 0:
        return f"{minutes // 60:g}小时额度"
    return f"{minutes:g}分钟额度"


WORK_STATES = ("working", "waiting", "completed", "error", "idle", "unknown")


def normalize_work_status(snapshot, now):
    """Small, non-sensitive input contract; a heartbeat expires after 30s."""
    data = snapshot if isinstance(snapshot, dict) else {}
    state, observed = data.get("state"), data.get("observed_at")
    timestamp = None
    if not isinstance(observed, bool) and isinstance(observed, (int, float)):
        try:
            candidate = float(observed)
            if math.isfinite(candidate):
                timestamp = candidate
        except (OverflowError, ValueError):
            pass
    age = now-timestamp if timestamp is not None else None
    valid_time = age is not None and -5 <= age <= 30
    connected = data.get("connected") is True and valid_time and state in WORK_STATES
    reason = None if connected else ("stale" if age is not None and age > 30 else "unavailable")
    counts = data.get("counts") if isinstance(data.get("counts"), dict) else {}
    event_id = data.get("event_id")
    return {"state": state if connected else "unknown", "connected": connected,
            "observed_at": timestamp if valid_time else None,
            "detail_kind": data.get("detail_kind") if data.get("detail_kind") in ("approval", "input") else None,
            "counts": {key: value for key, value in counts.items() if key in WORK_STATES
                       and type(value) is int and value >= 0},
            "source": str(data.get("source") or "unconnected")[:80], "reason": reason,
            "scope": data.get("scope") if data.get("scope") in ("desktop", "current_chat", "thread") else None,
            "event_id": str(event_id)[:128] if isinstance(event_id, (str, int)) and not isinstance(event_id, bool) else None}


class QuotaPet:
    WIDTH, HEIGHT = 300, 348
    KEY = "#ff00ff"

    def __init__(self, root, args, skin=None):
        import tkinter as tk
        import tkinter.font as tkfont
        from pet_art import draw_pet

        self.tk, self.root, self.args, self.draw_pet = tk, root, args, draw_pet
        self.tkfont = tkfont
        self.mail_fonts = {}
        self.skin = skin
        self.gmail_demo = bool(getattr(args, "gmail_demo", False))
        self.gmail_configured = self.gmail_demo or bool(getattr(args, "gmail_status_file", None))
        self.HEIGHT = 464 if self.gmail_configured else 348
        self.gmail_reader = None
        self.gmail_status = {"enabled": False, "connected": False, "pending": False,
                             "new_count": 0, "notice_id": None, "items": [],
                             "mode": "demo" if self.gmail_demo else "live", "reason": "missing"}
        self.next_gmail_poll = 0
        self.gmail_acknowledged = False
        self.gmail_ack_error = False
        self.gmail_announced = set()
        if self.gmail_configured and not self.gmail_demo:
            from gmail_notice import MailNoticeReader
            self.gmail_reader = MailNoticeReader(args.gmail_status_file)
        elif self.gmail_demo:
            self.gmail_status.update(enabled=True, pending=True, new_count=1,
                notice_id="offline-mail-demo", reason=None,
                items=[{"sender": "演示发件人", "subject": "这是一封模拟邮件，用来预览提醒效果", "received_at": None}])
        # Font sizes and Canvas geometry share a stable 96-DPI design space.
        root.tk.call("tk", "scaling", 96 / 72)
        self.events = queue.Queue()
        self.work_events = queue.Queue()
        self.work_observer = None
        self._work_snapshot = {}
        self.work_status = normalize_work_status({}, time.time())
        self.next_work_poll = 0
        self.preview_until = 0
        self.closed = False
        self.busy = False
        self.mode = "demo" if args.demo is not None else ("snapshot" if args.snapshot else "live")
        self.snapshot = {}
        self.remaining = args.demo
        self.last_live = None
        self.feed_until = 0
        self.started = time.monotonic()
        self.last_status = None
        self.next_fetch = 0
        self.demo_index = 0
        self.next_demo = self.started + 3
        self.status_error = None
        self.topmost = tk.BooleanVar(value=True)
        self.roaming = tk.BooleanVar(value=not getattr(args, "no_roam", False))
        self.drag = None
        self.menu_open = False
        self.hover_state = HoverState()
        self.hover = False
        self.pause_until = 0
        self.behavior = PetBehavior(self.started)
        self.action, self.action_started = "idle", self.started
        self.marker_written = 0
        self.last_motion_marker = None
        self.palette = {"body": args.color, "outline": "#42342e", "accent": args.accent}

        root.withdraw()
        root.title(f"{args.name} · 重置吃饱了")
        root.configure(bg=self.KEY)
        root.resizable(False, False)
        self.transparent = False
        try:
            if sys.platform == "win32":
                root.attributes("-transparentcolor", self.KEY)
                self.transparent = True
            elif sys.platform == "darwin":
                root.configure(bg="systemTransparent")
                root.attributes("-transparent", True)
                self.transparent = True
        except tk.TclError:
            self.transparent = False
        background = "systemTransparent" if self.transparent and sys.platform == "darwin" else (self.KEY if self.transparent else "#f5efe3")
        root.configure(bg=background)
        root.overrideredirect(self.transparent)
        root.attributes("-topmost", True)
        x = max(0, root.winfo_screenwidth() - self.WIDTH - 48)
        y = max(0, root.winfo_screenheight() - self.HEIGHT - 96)
        root.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")
        fonts = set(tkfont.families(root))
        self.font = next((f for f in ("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "Arial") if f in fonts), "TkDefaultFont")
        self.name_size = 15
        while self.name_size > 8 and tkfont.Font(root=root, family=self.font, size=self.name_size, weight="bold").measure(args.name) > 150:
            self.name_size -= 1
        self.canvas = tk.Canvas(root, width=self.WIDTH, height=self.HEIGHT, bg=background, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Button-3>", self.show_menu)
        self.canvas.bind("<Enter>", lambda _: self.update_hover(time.monotonic(), True))
        if sys.platform == "darwin":
            self.canvas.bind("<Button-2>", self.show_menu)
            self.canvas.bind("<Control-Button-1>", self.show_menu)
        root.bind("<Escape>", lambda _: self.close())
        root.protocol("WM_DELETE_WINDOW", self.close)

        self.menu = tk.Menu(root, tearoff=False)
        self.menu.add_command(label="重新读取额度快照" if args.snapshot else "刷新真实额度", command=self.return_live)
        demo = tk.Menu(self.menu, tearoff=False)
        for pct, label in ((100, "100% · 胖成球"), (65, "65% · 圆润"), (35, "35% · 正常"), (10, "10% · 饿了"), (0, "0% · 皮包骨")):
            demo.add_command(label=label, command=lambda p=pct: self.set_demo(p))
        self.menu.add_cascade(label="试玩造型（模拟数据）", menu=demo)
        actions = tk.Menu(self.menu, tearoff=False)
        for action, label in (("happy", "笑一笑"), ("cry", "哭一会儿"), ("wave", "招招手")):
            actions.add_command(label=label, command=lambda a=action: self.preview_action(a))
        self.menu.add_cascade(label="动作试玩", menu=actions)
        if self.gmail_configured:
            self.menu.add_command(label="确认邮件提醒（仅本地）", command=self.acknowledge_gmail,
                                  state="disabled")
            self.gmail_ack_menu = self.menu.index("end")
        self.menu.add_checkbutton(label="自由溜达", variable=self.roaming, command=self.toggle_roaming)
        self.menu.add_checkbutton(label="保持置顶", variable=self.topmost, command=lambda: root.attributes("-topmost", self.topmost.get()))
        self.menu.add_separator()
        self.menu.add_command(label=f"退出{args.name}", command=self.close)
        self.menu.bind("<Unmap>", self.menu_closed)
        root.update_idletasks()
        self.work_area = window_work_area(root)
        self.next_work_area = self.started + 2
        move_window(root, *clamp_position(root.winfo_x(), root.winfo_y(),
                                         self.work_area, (self.WIDTH, self.HEIGHT)))
        self.draw_status()
        self.animate()
        root.deiconify()
        self.write_status()
        if args.quit_after:
            root.after(int(args.quit_after * 1000), self.close)

    def on_press(self, event):
        if self.hover and 6 <= event.y <= 31 and 264 <= event.x <= 289:
            self.close()
            return
        self.update_hover(time.monotonic(), True)
        self.behavior.stop_walk(time.monotonic())
        self.drag = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y(), event.x_root, event.y_root)

    def on_drag(self, event):
        if self.drag:
            move_window(self.root, event.x_root-self.drag[0], event.y_root-self.drag[1])

    def on_release(self, event):
        clicked = self.drag and abs(event.x_root-self.drag[2]) + abs(event.y_root-self.drag[3]) < 5
        if self.drag and not clicked:
            self.pause_until = time.monotonic() + 8
            self.work_area = window_work_area(self.root)
            move_window(self.root, *clamp_position(self.root.winfo_x(), self.root.winfo_y(),
                                                  self.work_area, (self.WIDTH, self.HEIGHT)))
        self.drag = None
        if clicked:
            self.show_menu(event)

    def show_menu(self, event):
        self.menu_open = True
        self.behavior.stop_walk(time.monotonic())
        self.update_hover(time.monotonic(), True)
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def menu_closed(self, _event=None):
        self.menu_open = False
        self.pause_until = max(self.pause_until, time.monotonic()+.5)

    def update_hover(self, now, over):
        visible = self.hover_state.update(now, over or self.menu_open or self.drag is not None)
        if visible != self.hover:
            self.hover = visible
            self.canvas.itemconfigure("status", state="normal" if visible else "hidden")
        return visible

    def poll_hover(self, now):
        px, py = self.root.winfo_pointerxy()
        x, y = px-self.canvas.winfo_rootx(), py-self.canvas.winfo_rooty()
        # A small margin stops a walking pet before its opaque pixels reach the
        # pointer. The delayed hide bridges the gap down to the information card.
        box = self.canvas.bbox("pet")
        over = bool(box and box[0]-10 <= x <= box[2]+10 and box[1]-8 <= y <= box[3]+8)
        indicator = self.canvas.bbox("work-indicator")
        over = over or bool(indicator and indicator[0]-4 <= x <= indicator[2]+4 and
                            indicator[1]-4 <= y <= indicator[3]+4)
        mail_badge = self.canvas.bbox("mail-indicator")
        over = over or bool(mail_badge and mail_badge[0]-4 <= x <= mail_badge[2]+4 and
                            mail_badge[1]-4 <= y <= mail_badge[3]+4)
        if self.hover and not over:
            over = any(box and box[0] <= x <= box[2] and box[1] <= y <= box[3]
                       for box in (self.canvas.bbox(item) for item in self.canvas.find_withtag("status")))
        self.update_hover(now, over)

    def toggle_roaming(self):
        self.behavior.stop_walk(time.monotonic())
        self.write_status()

    def preview_action(self, action):
        now = time.monotonic()
        self.behavior.play(action, now)
        self.preview_until = self.behavior.preview_until
        self.action, self.action_started = self.behavior.action, self.behavior.action_started
        self.last_status = None
        self.write_status()

    def set_work_status(self, snapshot, now=None):
        """Accept a heartbeat on the Tk thread; workers use work_events.put()."""
        self._work_snapshot = dict(snapshot) if isinstance(snapshot, dict) else {}
        normalized = normalize_work_status(self._work_snapshot, time.time() if now is None else now)
        visual_keys = ("state", "connected", "detail_kind", "counts", "reason")
        if any(normalized[key] != self.work_status[key] for key in visual_keys):
            self.last_status = None
        self.work_status = normalized
        self.behavior.set_work_state(normalized["state"], time.monotonic(), normalized["event_id"])

    def poll_work_status(self, now):
        while True:
            try:
                self.set_work_status(self.work_events.get_nowait())
            except queue.Empty:
                break
        if now < self.next_work_poll:
            return
        self.next_work_poll = now + 1
        filename = getattr(self.args, "work_status_file", None)
        if filename:
            try:
                path = Path(filename)
                if path.stat().st_size > 65536:
                    raise ValueError("Work snapshot is too large")
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError, UnicodeError):
                data = {"connected": False, "source": "local-file"}
            self.set_work_status(data)
        else:
            # Even an automatic source must keep supplying fresh heartbeats.
            self.set_work_status(self._work_snapshot)

    def poll_gmail(self, now):
        if not self.gmail_configured or now < self.next_gmail_poll:
            return
        self.next_gmail_poll = now + 1
        if self.gmail_reader is not None:
            try:
                status = self.gmail_reader.poll()
            except (OSError, ValueError):
                status = dict(self.gmail_status, connected=False, reason="fetch_failed")
            if status != self.gmail_status:
                if status.get("pending") and status.get("notice_id") != self.gmail_status.get("notice_id"):
                    self.gmail_acknowledged, self.gmail_ack_error = False, False
                self.gmail_status = status
                self.last_status = None
        status = self.gmail_status
        self.menu.entryconfigure(self.gmail_ack_menu, state="normal" if status.get("pending") else "disabled")
        notice = status.get("notice_id")
        eligible = (status.get("pending") and notice and (status.get("connected") or self.gmail_demo)
                    and self.work_status["state"] not in ("waiting", "error") and now >= self.preview_until)
        if eligible and notice not in self.gmail_announced:
            try:
                claimed = self.gmail_demo or self.gmail_reader.claim_notice(notice)
            except (OSError, ValueError):
                claimed = False
            if claimed:
                self.gmail_announced.add(notice)
                self.behavior.notify_mail(notice, now)
                self.last_status = None

    def acknowledge_gmail(self):
        if not self.gmail_status.get("pending"):
            return
        if self.gmail_demo:
            success = True
            self.gmail_status = dict(self.gmail_status, pending=False, new_count=0, items=[])
        else:
            try:
                success = self.gmail_reader.ack(self.gmail_status.get("notice_id"))
            except (OSError, ValueError):
                success = False
        self.gmail_ack_error = not success
        if success:
            self.gmail_acknowledged = True
            self.behavior.clear_mail_gesture()
        self.next_gmail_poll = 0
        self.poll_gmail(time.monotonic())
        self.last_status = None
        self.draw_status()
        self.write_status()

    def gmail_state(self):
        status = self.gmail_status
        if self.gmail_demo:
            return "demo"
        if status.get("reason") == "unauthorized" or not status.get("enabled") and status.get("reason") == "disabled":
            return "unauthorized"
        if not status.get("connected"):
            return "stale" if status.get("reason") == "stale" else "disconnected"
        if status.get("pending"):
            return "new"
        return "confirmed" if self.gmail_acknowledged else "empty"

    def step_behavior(self, now):
        if now >= self.next_work_area:
            self.work_area = window_work_area(self.root)
            self.next_work_area = now + 2
        current = (self.root.winfo_x(), self.root.winfo_y())
        position = self.behavior.tick(now, current, self.work_area, (self.WIDTH, self.HEIGHT),
                                     self.remaining, paused=self.hover or self.drag is not None or
                                     self.menu_open or now < self.pause_until,
                                     roaming=self.roaming.get(), feeding=now < self.feed_until)
        if position != current and self.drag is None:
            move_window(self.root, *position)
        self.action, self.action_started = self.behavior.action, self.behavior.action_started
        marker = (self.hover, self.action, self.roaming.get(), position,
                  self.work_status["state"], now < self.preview_until,
                  self.gmail_state() if self.gmail_configured else None,
                  self.gmail_status.get("new_count", 0), self.gmail_ack_error)
        if marker != self.last_motion_marker and now-self.marker_written >= 1:
            self.last_motion_marker = marker
            self.write_status()

    def set_demo(self, percent):
        old = self.remaining
        self.mode = "demo"
        self.remaining = percent
        self.status_error = None
        if old is not None and percent > old:
            self.feed_until = time.monotonic() + 3
        self.last_status = None
        self.write_status()

    def return_live(self):
        self.mode = "snapshot" if self.args.snapshot else "live"
        self.remaining = None
        self.snapshot = {}
        self.status_error = None
        self.next_fetch = 0
        self.last_status = None
        self.write_status()

    def fetch(self):
        self.busy = True
        self.next_fetch = time.monotonic() + self.args.interval

        def worker():
            try:
                if self.args.snapshot:
                    data = json.loads(Path(self.args.snapshot).read_text(encoding="utf-8-sig"))
                    value = data.get("remaining_percent")
                    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100):
                        raise ValueError("Invalid remaining_percent")
                    result = data
                else:
                    result = read_quota(codex_path=self.args.codex, timeout=20, bucket=self.args.bucket)
            except Exception:
                result = {"remaining_percent": None, "error": "读取失败，请检查运行环境"}
            self.events.put(result)

        # Allow read_quota's bounded finally block to close its own app-server
        # even if the user closes the window while a query is in flight.
        threading.Thread(target=worker, daemon=False).start()

    def process_events(self):
        while True:
            try:
                data = self.events.get_nowait()
            except queue.Empty:
                return
            self.busy = False
            if self.mode == "demo":
                continue
            self.snapshot = data
            self.status_error = data.get("error")
            self.remaining = data.get("remaining_percent") if not self.status_error else None
            if self.remaining is not None:
                if self.last_live is not None and self.remaining > self.last_live + 1:
                    self.feed_until = time.monotonic() + 4
                self.last_live = self.remaining
            self.last_status = None
            self.write_status()

    def write_status(self):
        if self.args.ready_file:
            marker = {"pid": os.getpid(), "mode": self.mode, "visible": not self.closed,
                      "remaining_percent": self.remaining, "error": self.status_error,
                      "skin": str(self.skin.path) if self.skin else None,
                      "hover": self.hover, "action": self.action,
                      "roaming": self.roaming.get(),
                      "work_state": self.work_status["state"],
                      "work_connected": self.work_status["connected"],
                      "work_scope": self.work_status["scope"],
                      "work_source": self.work_status["source"],
                      "work_counts": self.work_status["counts"],
                      "action_preview": time.monotonic() < self.preview_until,
                      "position": [self.root.winfo_x(), self.root.winfo_y()]}
            if self.gmail_configured:
                marker["gmail"] = {"state": self.gmail_state(), "connected": bool(self.gmail_status.get("connected")),
                                   "pending": bool(self.gmail_status.get("pending")),
                                   "count": self.gmail_status.get("new_count", 0),
                                   "demo": self.gmail_demo, "ack_error": self.gmail_ack_error}
            # A bad optional marker path must not turn a walk into 12 writes/s.
            self.marker_written = time.monotonic()
            try:
                Path(self.args.ready_file).write_text(json.dumps(marker, ensure_ascii=False), encoding="utf-8")
            except OSError:
                print("Could not write the optional readiness marker.", file=sys.stderr)

    def animate(self):
        if self.closed:
            return
        now = time.monotonic()
        self.process_events()
        self.poll_work_status(now)
        self.poll_gmail(now)
        if self.args.demo_cycle and self.mode == "demo" and now >= self.next_demo:
            self.demo_index = (self.demo_index + 1) % 5
            self.set_demo((100, 65, 35, 10, 0)[self.demo_index])
            self.next_demo = now + 3
        if self.mode != "demo" and not self.busy and now >= self.next_fetch:
            self.fetch()
        self.poll_hover(now)
        self.step_behavior(now)
        if self.skin:
            self.skin.draw(self.canvas, stage_for(self.remaining)[0], now-self.started,
                           feeding=now < self.feed_until, palette=self.palette,
                           transparent_key=self.KEY if self.transparent and sys.platform == "win32" else None,
                           action=self.action, action_phase=now-self.action_started)
        else:
            self.draw_pet(self.canvas, self.remaining, now-self.started,
                          feeding=now < self.feed_until, palette=self.palette)
        # The drawing uses a 280px logical canvas; center it within the window.
        self.canvas.move("pet", 10, 5)
        self.canvas.tag_lower("pet")
        status_key = (self.mode, self.remaining, self.status_error, int(time.time()) // 10,
                      now < self.feed_until, self.work_status["state"], now < self.preview_until)
        if status_key != self.last_status:
            self.draw_status()
            self.last_status = status_key
        self.root.after(80, self.animate)

    def round_rect(self, x1, y1, x2, y2, radius=18, **kwargs):
        points = (x1+radius,y1,x2-radius,y1,x2,y1,x2,y1+radius,x2,y2-radius,x2,y2,x2-radius,y2,x1+radius,y2,x1,y2,x1,y2-radius,x1,y1+radius,x1,y1)
        return self.canvas.create_polygon(points, smooth=True, splinesteps=16, **kwargs)

    def text(self, x, y, text, size=10, color="#65564d", bold=False, anchor="center"):
        return self.canvas.create_text(x,y,text=text,font=(self.font,size,"bold" if bold else "normal"),fill=color,anchor=anchor,tags="status")

    def draw_status(self):
        self.canvas.delete("status")
        stage, message = stage_for(self.remaining)
        self.round_rect(15,235,285,336, fill="#e3d9ca", outline="", tags="status")
        self.round_rect(12,232,282,333, fill="#fff9eb", outline="#42342e", width=2, tags="status")
        self.text(28,254,self.args.name,self.name_size,"#42342e",True,"w")
        pct = "—" if self.remaining is None else f"{self.remaining:g}%"
        self.text(266,254,pct,18,"#d46c45" if stage in ("empty","hungry") else "#557654",True,"e")
        if self.mode == "demo":
            subtitle = "试玩中 · 模拟数据"
        elif self.mode == "snapshot":
            subtitle = "额度快照 · 非实时"
        elif self.status_error:
            subtitle = "额度未知 · 点击菜单重试"
        elif self.remaining is None:
            subtitle = "额度未提供百分比" if self.snapshot else "正在查询真实额度…"
        else:
            subtitle = f"{window_label(self.snapshot.get('window_minutes'))}剩余 · 每{self.args.interval:g}秒查询"
        self.text(28,276,subtitle,9,anchor="w")
        self.text(148,298,self.work_label(),10,"#42342e")
        if self.mode == "demo":
            footer = "点击菜单回到额度快照" if self.args.snapshot else "点击菜单回到真实额度"
        elif self.status_error:
            footer = "先看看 Codex 是否已登录"
        elif self.remaining is None:
            footer = "未知状态不会当作零额度"
        elif self.mode == "snapshot":
            stamp = self.snapshot.get("fetched_at") or "时间未知"
            footer = "采样 " + str(stamp)[:19].replace("T", " ") + (" UTC" if str(stamp).endswith("Z") or str(stamp).endswith("+00:00") else "")
        else:
            footer = countdown(self.snapshot.get("resets_at"))
        self.text(148,320,footer,8)
        self.canvas.create_oval(264,6,289,31,fill="#fff9eb",outline="#42342e",width=1,tags="status")
        self.text(276,18,"×",13,"#42342e")
        credits = self.snapshot.get("credits") or {}
        balance = credits.get("balance")
        if self.mode != "demo" and not self.status_error and (credits.get("unlimited") or credits.get("has_credits")):
            label = "备用粮 ∞" if credits.get("unlimited") else (f"备用点数 {balance}" if balance is not None else "有备用点数")
            self.round_rect(17,12,160,34,10,fill="#e7edd6",outline="#42342e",tags="status")
            self.text(88,23,label,8)
        self.canvas.itemconfigure("status", state="normal" if self.hover else "hidden")
        self.draw_work_indicator()
        self.draw_gmail()

    def clipped_text(self, x, y, value, size=9, bold=False, color="#65564d", width=236):
        text = " ".join(str(value or "").split())[:2048]
        key = (size, bold)
        if key not in self.mail_fonts:
            self.mail_fonts[key] = self.tkfont.Font(root=self.root, family=self.font, size=size, weight="bold" if bold else "normal")
        font = self.mail_fonts[key]
        if font.measure(text) > width:
            low, high = 0, len(text)
            while low < high:
                middle = (low+high+1)//2
                if font.measure(text[:middle]+"…") <= width:
                    low = middle
                else:
                    high = middle-1
            text = text[:low]+"…"
        return self.canvas.create_text(x,y,text=text,font=font,fill=color,anchor="w",tags=("status","mail-status"))

    def draw_gmail(self):
        self.canvas.delete("mail-indicator")
        if not self.gmail_configured:
            return
        status, state = self.gmail_status, self.gmail_state()
        pending = bool(status.get("pending"))
        count = status.get("new_count", 0)
        count_label = str(min(count, 99)) + ("+" if count > 99 else "")
        fill = "#d9c9e8" if self.gmail_demo else ("#f4ce72" if pending else "#e8e9df")
        outline = "#bf6658" if self.gmail_ack_error else "#685a4f"
        self.canvas.create_rectangle(225,211,251,227,fill=fill,outline=outline,width=1,tags="mail-indicator")
        self.canvas.create_line(225,211,238,220,251,211,fill=outline,width=1,tags="mail-indicator")
        if pending:
            self.canvas.create_oval(242,196,262,216,fill="#bf6658",outline="",tags="mail-indicator")
            self.canvas.create_text(252,206,text=count_label,font=(self.font,7,"bold"),fill="#ffffff",tags="mail-indicator")
        if self.gmail_demo:
            self.canvas.create_text(236,185,text="邮件演示",font=(self.font,8),fill="#76518b",tags="mail-indicator")
        self.round_rect(15,347,285,455,fill="#e3d9ca",outline="",tags=("status","mail-status"))
        self.round_rect(12,344,282,452,fill="#fff9eb",outline="#42342e",width=2,tags=("status","mail-status"))
        if self.gmail_demo:
            title = f"邮件演示 · {count_label} 条待确认" if pending else "邮件演示 · 已确认本地提醒"
        else:
            title = {"new": f"Gmail · {count_label} 条待确认", "empty": "Gmail · 暂无新邮件",
                     "confirmed": "Gmail · 已确认本地提醒", "unauthorized": "Gmail · 未授权或未启用",
                     "stale": "Gmail · 检查结果已过期", "disconnected": "Gmail · 暂未连接"}[state]
        self.clipped_text(28,365,title,10,True,"#42342e")
        items = status.get("items") or []
        if pending and items:
            item = items[0]
            self.clipped_text(28,389,"来自 " + str(item.get("sender") or "未知发件人"),9)
            self.clipped_text(28,410,item.get("subject") or "（无主题）",10,color="#42342e")
        else:
            lines = {"demo": ("这是离线演示，没有连接邮箱", "仅清除了这次模拟提醒"),
                     "empty": ("已连接，当前没有新邮件提醒", ""),
                     "confirmed": ("只确认了本地提醒", "Gmail 邮件状态没有改变"),
                     "unauthorized": ("尚未启用已授权的邮件检查", "确认连接后才会显示真实邮件"),
                     "stale": ("正在等待新的邮件检查结果", "失联不会清除已有提醒"),
                     "disconnected": ("尚未收到可用的邮件检查结果", "连接恢复后会继续检查")}
            first, second = lines.get(state, ("新邮件提醒", ""))
            self.clipped_text(28,389,first)
            self.clipped_text(28,410,second)
        footer = "菜单确认仅清本地提醒，不标为已读"
        if self.gmail_ack_error:
            footer = "本地确认失败 · 提醒仍保留，请重试"
        elif self.gmail_demo:
            footer = "模拟数据 · 不读取或修改真实 Gmail"
        elif pending and not status.get("connected"):
            footer = f"连接不可用 · 保留 {count_label} 条待确认提醒"
        self.clipped_text(28,436,footer,8,color="#9b5b4d" if self.gmail_ack_error else "#65564d")
        self.canvas.itemconfigure("mail-status",state="normal" if self.hover else "hidden")

    def work_label(self):
        if time.monotonic() < self.preview_until:
            name = {"happy": "笑一笑", "cry": "哭一会儿", "wave": "招招手"}.get(self.behavior.preview_action, "动作")
            return f"动作试玩 · {name}"
        state = self.work_status["state"]
        if state == "waiting":
            return {"approval": "等待你批准", "input": "等待你回复"}.get(self.work_status["detail_kind"], "等待你处理")
        if state == "working":
            count = self.work_status["counts"].get("working", 0)
            return "正在工作" if count < 2 else f"正在工作 · {min(count, 99)}{'+' if count > 99 else ''} 个任务"
        return {"completed": "本次工作已完成", "error": "任务出错 · 请查看 Codex",
                "idle": "当前没有进行中的任务", "unknown": "工作状态未连接" +
                (" · 已过期" if self.work_status["reason"] == "stale" else "")}[state]

    def draw_work_indicator(self):
        self.canvas.delete("work-indicator")
        previewing = time.monotonic() < self.preview_until
        colors = {"working": "#4c8cc4", "waiting": "#d5a13b", "completed": "#65a46e",
                  "error": "#d96964", "idle": "#97ab8e", "unknown": "#a8a8a8"}
        color = "#a17ab6" if previewing else colors[self.work_status["state"]]
        self.canvas.create_oval(273,213,283,223,fill=color,outline="#42342e",width=1,tags="work-indicator")
        if previewing:
            self.canvas.create_text(278,203,text="试玩",font=(self.font,8),fill="#76518b",tags="work-indicator")

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.work_observer is not None:
            self.work_observer.stop()
        self.write_status()
        self.root.destroy()


def color(value):
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise argparse.ArgumentTypeError("Use a color such as #f4b6a5")
    return value


def percent(value):
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 100:
        raise argparse.ArgumentTypeError("Demo percentage must be 0–100")
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", help="Pet name; defaults to the skin name or 饭桶")
    parser.add_argument("--skin", help="Optional local sprite skin JSON; requires Pillow")
    parser.add_argument("--color", type=color, default="#f4b6a5", help="Body color")
    parser.add_argument("--accent", type=color, default="#dc786c", help="Ear and nose color")
    parser.add_argument("--codex", help="Explicit path to codex executable")
    parser.add_argument("--bucket", default="codex", help="Exact quota bucket ID")
    parser.add_argument("--interval", type=int, default=300, help="Read-only refresh interval, default 300 seconds; minimum 30")
    parser.add_argument("--demo", type=percent, help="Clearly labeled offline preview, 0–100")
    parser.add_argument("--demo-cycle", action="store_true", help="Cycle all five demo shapes")
    parser.add_argument("--no-roam", action="store_true", help="Keep the pet in one place; gestures still work")
    parser.add_argument("--snapshot", help="Read a sanitized quota snapshot JSON instead of the live account")
    parser.add_argument("--work-status-file", help="Optional local work-status heartbeat JSON; expires after 30 seconds")
    parser.add_argument("--work-scope", choices=("desktop", "thread"), default="desktop", help="Observe active local desktop chats, or one chat")
    parser.add_argument("--work-thread", help="Observe this chat ID; implies --work-scope thread")
    mail = parser.add_mutually_exclusive_group()
    mail.add_argument("--gmail-status-file", help="Optional local Gmail notice JSON from an authorized external check")
    mail.add_argument("--gmail-demo", action="store_true", help="Offline simulated mail notice; never connects to Gmail")
    parser.add_argument("--background", action="store_true", help="Launch detached; no console window on Windows")
    parser.add_argument("--ready-file", help="Optional local readiness marker for testing")
    parser.add_argument("--quit-after", type=float, help="Close after N seconds; for smoke testing")
    args = parser.parse_args()
    if args.interval < 30:
        parser.error("--interval must be at least 30 seconds")
    if args.quit_after is not None and (not math.isfinite(args.quit_after) or args.quit_after <= 0):
        parser.error("--quit-after must be finite and positive")
    if args.demo_cycle and args.demo is None:
        args.demo = 100
    if args.demo is not None and args.snapshot:
        parser.error("Choose either --demo or --snapshot")
    if args.work_thread:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", args.work_thread):
            parser.error("--work-thread must be a valid chat ID")
        args.work_scope = "thread"
    skin = None
    if args.skin:
        from sprite_art import SkinError, load_skin
        try:
            skin = load_skin(args.skin)
        except SkinError as exc:
            parser.error(str(exc))
        args.skin = str(skin.path)
    args.name = args.name if args.name is not None else (skin.name if skin else "饭桶")
    if len(args.name) > 12:
        parser.error("Keep the pet name within 12 characters")
    if args.snapshot:
        args.snapshot = str(Path(args.snapshot).resolve())
    if args.work_status_file:
        args.work_status_file = str(Path(args.work_status_file).resolve())
    if args.gmail_status_file:
        args.gmail_status_file = str(Path(args.gmail_status_file).resolve())
    if args.ready_file:
        args.ready_file = str(Path(args.ready_file).resolve())
    if args.codex and Path(args.codex).is_file():
        args.codex = str(Path(args.codex).resolve())
    try:
        import tkinter as tk
    except ImportError:
        print("Tk is missing. Use a Python installation with tkinter, or the Codex bundled Python runtime.", file=sys.stderr)
        return 2
    if args.background:
        executable = Path(sys.executable)
        if sys.platform == "win32" and executable.with_name("pythonw.exe").exists():
            executable = executable.with_name("pythonw.exe")
        command = [str(executable), str(Path(__file__).resolve()), "--name", args.name,
                   "--color", args.color, "--accent", args.accent,
                   "--bucket", args.bucket, "--interval", str(args.interval),
                   "--work-scope", args.work_scope]
        for option, value in (("--codex", args.codex), ("--snapshot", args.snapshot),
                              ("--skin", args.skin),
                              ("--work-status-file", args.work_status_file),
                              ("--work-thread", args.work_thread),
                              ("--gmail-status-file", args.gmail_status_file),
                              ("--ready-file", args.ready_file), ("--demo", args.demo),
                              ("--quit-after", args.quit_after)):
            if value is not None:
                command.extend((option, str(value)))
        if args.demo_cycle:
            command.append("--demo-cycle")
        if args.no_roam:
            command.append("--no-roam")
        if args.gmail_demo:
            command.append("--gmail-demo")
        kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "cwd": str(Path(__file__).parent)}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        else:
            kwargs["start_new_session"] = True
        process = subprocess.Popen(command, **kwargs)
        print(json.dumps({"pid": process.pid, "status": "starting", "exit": "Click × or use the pet menu"}))
        return 0
    try:
        prepare_dpi()
        root = tk.Tk()
        pet = QuotaPet(root, args, skin=skin)
        if not args.work_status_file:
            from codex_work_status import WorkStatusObserver
            pet.work_observer = WorkStatusObserver(pet.work_events.put,
                                                   thread_id=args.work_thread,
                                                   scope=args.work_scope)
            pet.work_observer.start()
        root.mainloop()
    except tk.TclError:
        print("A working desktop display and Tk are required. This is a local desktop pet, not a headless service.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
