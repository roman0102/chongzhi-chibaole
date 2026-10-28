"""Optional skin-specific event gestures; never changes account or work state."""
import math

EXTRA_ACTIONS = ("trophy", "ears", "depleted", "mail", "bobo")


class SkinEvents:
    def __init__(self, data=None):
        data = {} if data is None else data
        if not isinstance(data, dict):
            raise ValueError("Skin presentation must be an object.")
        self.actions = {}
        for key in ("full", "high", "empty", "waiting", "mail"):
            value = data.get(key)
            if value is not None:
                if value not in EXTRA_ACTIONS:
                    raise ValueError(f"Invalid presentation action for {key}.")
                self.actions[key] = value
        self.high_min = data.get("high_min", 80)
        if (isinstance(self.high_min, bool) or not isinstance(self.high_min, (int, float))
                or not math.isfinite(self.high_min) or not 0 < self.high_min < 100):
            raise ValueError("presentation.high_min must be between 0 and 100.")
        self.bubbles = self._texts(data.get("bubbles", {}), 80)
        self.labels = self._texts(data.get("labels", {}), 32)
        hold = data.get("hold", [])
        if not isinstance(hold, list) or any(action not in EXTRA_ACTIONS for action in hold):
            raise ValueError("Presentation hold must list known gesture names.")
        self.hold = set(hold)

    @staticmethod
    def _texts(value, limit):
        if not isinstance(value, dict):
            raise ValueError("Presentation text maps must be objects.")
        if any(key not in EXTRA_ACTIONS or not isinstance(text, str)
               or not 1 <= len(text) <= limit or any(ord(c) < 32 for c in text)
               for key, text in value.items()):
            raise ValueError("Invalid presentation text or action name.")
        return dict(value)

    def quota_action(self, remaining):
        if remaining == 0:
            return self.actions.get("empty", "idle")
        if remaining == 100:
            return self.actions.get("full", "idle")
        if remaining is not None and self.high_min <= remaining < 100:
            return self.actions.get("high", "idle")
        return "idle"

    def select(self, base_action, remaining, work_state, *, mailing=False, previewing=False,
               quota_preview=False):
        if previewing:
            return base_action
        # Deliberate quota previews show the selected pose even while Codex works.
        # The real work state stays untouched and is still shown in the card.
        if quota_preview and self.actions:
            return self.quota_action(remaining)
        if work_state == "waiting":
            return self.actions.get("waiting", base_action)
        if work_state == "error":
            return base_action
        if mailing:
            return self.actions.get("mail", base_action)
        if remaining == 0 and "empty" in self.actions:
            return self.actions["empty"]
        if base_action == "idle" and remaining is not None:
            if remaining == 100:
                return self.actions.get("full", base_action)
            if self.high_min <= remaining < 100:
                return self.actions.get("high", base_action)
        return base_action

    def validate_clips(self, animations, states):
        required = set(self.actions.values()) | set(self.bubbles) | set(self.labels) | self.hold
        for state in states:
            if required and (state not in animations or not required <= set(animations[state].clips)):
                raise ValueError(f"Presentation gestures need matching animation clips in state {state}.")
