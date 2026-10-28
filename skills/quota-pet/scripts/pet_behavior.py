"""Small, time-based desktop movements, with no Tk or quota-reader dependency."""
from __future__ import annotations

import math
import random
from collections import deque
from skin_events import EXTRA_ACTIONS


def clamp_position(x, y, work_area, size):
    left, top, right, bottom = work_area
    width, height = size
    return (min(max(x, left), max(left, right-width)),
            min(max(y, top), max(top, bottom-height)))


class HoverState:
    """Keep a card open across short gaps between the pet and the card."""
    def __init__(self, delay=.25):
        self.visible = False
        self.leave_at = None
        self.delay = delay

    def update(self, now, over):
        if over:
            self.visible, self.leave_at = True, None
        elif self.visible:
            if self.leave_at is None:
                self.leave_at = now + self.delay
            elif now >= self.leave_at:
                self.visible, self.leave_at = False, None
        return self.visible


class PetBehavior:
    """Real work state selects gestures; only idle wandering is random."""
    def __init__(self, now, rng=None):
        self.rng = rng or random.Random()
        self.action = "idle"
        self.action_started = now
        self.action_until = now
        self.next_walk = now + self.rng.uniform(5, 9)
        self.last_tick = now
        self.target = None
        self.position = None
        self.expected_position = None
        self.speed = 32.
        self.direction = "walk-right"
        self.work_state = "unknown"
        self.work_event_until = 0
        self.last_event_key = None
        self.preview_until = 0
        self.preview_action = None
        self.mail_wave_until = 0
        self.mail_notices = deque(maxlen=128)

    def notify_mail(self, notice_id, now):
        """A local notification gesture; never changes the real work state."""
        if not notice_id or notice_id in self.mail_notices:
            return False
        self.mail_notices.append(notice_id)
        self.mail_wave_until = now + 4
        self.target = None
        return True

    def clear_mail_gesture(self):
        self.mail_wave_until = 0

    def _action(self, name, now, duration=0):
        if name != self.action:
            self.action, self.action_started = name, now
        self.action_until = now + duration

    def play(self, action, now, duration=3.2):
        if action not in ("happy", "cry", "wave") + EXTRA_ACTIONS:
            raise ValueError("Unknown preview gesture")
        self.target = None
        self.preview_action, self.preview_until = action, now+duration
        self.action_started = now
        self._action(action, now, duration)
        self.next_walk = now + duration + self.rng.uniform(4, 8)

    def set_work_state(self, state, now, event_key=None):
        if state not in ("working", "waiting", "completed", "error", "idle", "unknown"):
            state = "unknown"
        changed = state != self.work_state
        token = (state, event_key) if event_key is not None else None
        new_event = token is not None and token != self.last_event_key
        if changed or (new_event and state in ("completed", "error")):
            self.target = None
            terminal = state in ("completed", "error")
            duplicate = terminal and token is not None and token == self.last_event_key
            self.work_event_until = now+3.2 if terminal and not duplicate else 0
            if state == "working":
                self.next_walk = now
        self.work_state = state
        if token is not None and state in ("completed", "error"):
            self.last_event_key = token

    def stop_walk(self, now):
        self.target = None
        if self.action.startswith("walk-") and self.work_state == "idle":
            self._action("idle", now)
        self.next_walk = max(self.next_walk, now + 1.2)

    def tick(self, now, position, work_area, size, remaining, *, paused=False,
             roaming=True, feeding=False):
        dt = min(.12, max(0, now-self.last_tick))
        self.last_tick = now
        if self.position is None or tuple(position) != self.expected_position:
            self.position = tuple(map(float, position))
        x, y = clamp_position(*self.position, work_area, size)
        previewing = now < self.preview_until
        mailing = now < self.mail_wave_until and self.work_state not in ("waiting", "error")
        can_walk = (not paused and roaming and not previewing and not mailing
                    and self.work_state in ("idle", "working"))
        if not can_walk:
            self.stop_walk(now)
        if can_walk and self.target is None and now >= self.next_walk:
            dx = self.rng.choice((-1, 1)) * self.rng.uniform(65, 155)
            dy = self.rng.uniform(-32, 32)
            target = clamp_position(x+dx, y+dy, work_area, size)
            # A walk chosen toward an edge turns back into the same work area.
            if math.dist((x, y), target) < 20:
                target = clamp_position(x-dx, y-dy, work_area, size)
            self.target = target if math.dist((x, y), target) >= 4 else None
            self.next_walk = now + self.rng.uniform(8, 18)
            if self.target:
                self.speed = self.rng.uniform(25, 38)
                self.direction = "walk-right" if self.target[0] >= x else "walk-left"
        if self.target is not None and can_walk:
            tx, ty = clamp_position(*self.target, work_area, size)
            distance = math.hypot(tx-x, ty-y)
            step = self.speed * dt
            if distance <= step:
                x, y = tx, ty
                self.target = None
                self.next_walk = now + self.rng.uniform(2, 5) if self.work_state == "working" else now+self.rng.uniform(8, 18)
            elif distance:
                x, y = x + (tx-x)*step/distance, y + (ty-y)*step/distance
        if previewing:
            action = self.preview_action
        elif self.work_state == "waiting":
            action = "wave"
        elif self.work_state == "error":
            action = "cry" if now < self.work_event_until else "idle"
        elif mailing:
            action = "wave"
        elif self.work_state == "working":
            action = self.direction
        elif self.work_state in ("completed", "error") and now < self.work_event_until:
            action = "happy" if self.work_state == "completed" else "cry"
        else:
            action = self.direction if self.target is not None else "idle"
        self._action(action, now)
        self.position = clamp_position(x, y, work_area, size)
        self.expected_position = tuple(round(v) for v in self.position)
        return self.expected_position
