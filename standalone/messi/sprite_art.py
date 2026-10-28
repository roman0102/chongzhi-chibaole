"""Optional local sprite sheets. Pillow is imported only when a skin is used."""
from __future__ import annotations

import json
import math
from pathlib import Path
from skin_events import EXTRA_ACTIONS, SkinEvents


STATES = ("full", "round", "normal", "hungry", "empty", "unknown")
ACTIONS = ("idle", "walk-right", "walk-left", "happy", "cry", "wave")


class SkinError(ValueError):
    """A skin could not be prepared; callers must not silently replace it."""


class AnimationClip:
    """Prepared frames from a row or explicit boxes; time is in seconds."""

    def __init__(self, frames, milliseconds, row):
        self.frames = tuple(frames)
        self.ms = milliseconds
        self.row = row
        self.loop = True

    def frame_index(self, seconds):
        if (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                or not math.isfinite(seconds)):
            raise SkinError("Animation time must be a finite number of seconds.")
        index = math.floor(max(0, seconds) * 1000 / self.ms)
        return index % len(self.frames) if self.loop else min(index, len(self.frames)-1)


class AnimationSheet:
    def __init__(self, path, clips, crop_box, cell_size):
        self.path = path
        self.clips = clips
        self.crop_box = crop_box
        self.cell_size = cell_size
        self.display_size = clips["idle"].frames[0].size

    def clip(self, action):
        return self.clips.get(action, self.clips["idle"])


class SpriteSkin:
    """Prepared PIL frames, with one lazily cached PhotoImage set per Tk root."""

    def __init__(self, path, name, frames, state_indices, crop_box, cell_size, animations=None):
        self.path = path
        self.name = name
        self.frames = frames
        self.state_indices = state_indices
        self.crop_box = crop_box
        self.cell_size = cell_size
        self.display_size = frames[0].size
        self._interpreter = None
        self._transparent_key = None
        self._photos = []
        self.animations = animations or {}
        self._clip_photos = {}
        self.presentation = SkinEvents()

    def frame_at(self, state, phase=0, action="idle", action_phase=None):
        """Return the prepared PIL frame without requiring a display."""
        if state not in self.state_indices:
            raise SkinError(f"Skin has no state: {state}")
        if state in self.animations:
            clip = self.animations[state].clip(action)
            return clip.frames[clip.frame_index(phase if action_phase is None else action_phase)]
        return self.frames[self.state_indices[state]]

    @staticmethod
    def _make_photos(frames, canvas, transparent_key):
        from PIL import ImageTk
        if transparent_key:
            # Windows Tk colour-key transparency cannot composite fractional
            # alpha with the desktop. Adapt copies only; leave assets untouched.
            frames = [frame.copy() for frame in frames]
            for frame in frames:
                frame.putalpha(frame.getchannel("A").point(lambda alpha: 255 if alpha >= 96 else 0))
        return [ImageTk.PhotoImage(frame, master=canvas) for frame in frames]

    def draw(self, canvas, state, phase, feeding=False, palette=None, transparent_key=None,
             action="idle", action_phase=None):
        """Draw at logical x=140, baseline<=220; UI adds its own window offset."""
        if state not in self.state_indices:
            raise SkinError(f"Skin has no state: {state}")
        if self._interpreter is not canvas.tk or self._transparent_key != transparent_key:
            self._photos = []
            self._clip_photos = {}
            self._interpreter = canvas.tk
            self._transparent_key = transparent_key
        if state in self.animations:
            clip = self.animations[state].clip(action)
            if clip not in self._clip_photos:
                self._clip_photos[clip] = self._make_photos(clip.frames, canvas, transparent_key)
            frame_index = clip.frame_index(phase if action_phase is None else action_phase)
            photo = self._clip_photos[clip][frame_index]
        else:
            if not self._photos:
                self._photos = self._make_photos(self.frames, canvas, transparent_key)
            photo = self._photos[self.state_indices[state]]
        canvas.delete("pet")
        # Only move upward: even at the lowest point the status card stays clear.
        bob = (1 + math.sin(phase * 2.1)) * .8
        if feeding:
            bob += abs(math.sin(phase * 5)) * 1.5
        canvas.create_image(140, 220-bob, anchor="s",
                            image=photo, tags="pet")
        if feeding:
            accent = (palette or {}).get("accent", "#D9A844")
            for index, (x, y) in enumerate(((34, 91), (248, 57), (246, 143))):
                size = 4 + math.sin(phase * 5 + index) * 1.2
                points = []
                for point in range(8):
                    angle = point * math.pi / 4
                    radius = size if point % 2 == 0 else size * .30
                    points.extend((x + math.cos(angle)*radius,
                                   y + math.sin(angle)*radius - bob))
                canvas.create_polygon(*points, fill=accent, outline="", tags="pet")


def _image_path(path, image_name):
    if (not isinstance(image_name, str) or not image_name or
            any(char in image_name for char in ("/", "\\", ":", "\x00")) or
            Path(image_name).name != image_name or image_name in (".", "..") or
            Path(image_name).is_absolute()):
        raise SkinError("Skin 'image' must be a filename in the same directory as the JSON.")
    image_path = (path.parent / image_name).resolve()
    if image_path.parent != path.parent:
        raise SkinError("Skin 'image' must stay in the same directory as the JSON.")
    if not image_path.is_file():
        raise SkinError(f"Skin image is missing: {image_path}")
    return image_path


def _pillow():
    try:
        from PIL import Image
        return Image
    except ImportError as exc:
        raise SkinError("This skin needs Pillow. Install it with 'python -m pip install Pillow' "
                        "using the Python runtime that starts the pet. The default pig needs no Pillow.") from exc


def _load_static(path, name, manifest):
    image_name = manifest.get("image")
    if (not isinstance(image_name, str) or not image_name or
            Path(image_name).name != image_name or image_name in (".", "..") or
            Path(image_name).is_absolute()):
        raise SkinError("Skin 'image' must be a filename in the same directory as the JSON.")
    columns, rows = manifest.get("columns"), manifest.get("rows")
    if any(type(value) is not int or not 1 <= value <= 32 for value in (columns, rows)):
        raise SkinError("Skin 'columns' and 'rows' must be integers from 1 to 32.")
    indices = manifest.get("states")
    if not isinstance(indices, dict) or any(key not in indices for key in STATES):
        raise SkinError("Skin 'states' must include full, round, normal, hungry, empty and unknown.")
    count = columns * rows
    if any(type(indices[key]) is not int or not 0 <= indices[key] < count for key in STATES):
        raise SkinError(f"Skin state indices must be integers from 0 to {count-1}.")
    image_path = _image_path(path, image_name)
    Image = _pillow()
    try:
        with Image.open(image_path) as source:
            if source.width % columns or source.height % rows:
                raise SkinError("Skin image dimensions must divide evenly into its columns and rows.")
            if "A" not in source.getbands() and "transparency" not in source.info:
                raise SkinError("Skin image must include transparency (for example, an RGBA PNG).")
            sheet = source.convert("RGBA")
            sheet.load()
        cell_width, cell_height = sheet.width // columns, sheet.height // rows
        if not cell_width or not cell_height:
            raise SkinError("Skin cells must have non-zero dimensions.")
        cells, bounds = [], []
        for index in range(count):
            x, y = (index % columns)*cell_width, (index // columns)*cell_height
            cell = sheet.crop((x, y, x+cell_width, y+cell_height))
            cells.append(cell)
            alpha = cell.getchannel("A")
            bounds.append(alpha.point(lambda value: 255 if value > 8 else 0).getbbox())
            # Ignore stray near-transparent background noise only in memory.
            cell.putalpha(alpha.point(lambda value: value if value > 8 else 0))
        if any(bounds[indices[state]] is None for state in STATES):
            raise SkinError("Every referenced skin state must contain visible pixels.")
        visible = [box for box in bounds if box is not None]
        crop_box = (min(box[0] for box in visible), min(box[1] for box in visible),
                    max(box[2] for box in visible), max(box[3] for box in visible))
        width, height = crop_box[2]-crop_box[0], crop_box[3]-crop_box[1]
        scale = min(220/width, 212/height)
        size = (max(1, min(220, round(width*scale))),
                max(1, min(212, round(height*scale))))
        resample = Image.Resampling.LANCZOS
        frames = [cell.crop(crop_box).resize(size, resample) for cell in cells]
    except SkinError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise SkinError(f"Cannot decode skin image '{image_path}': {exc}") from exc
    return SpriteSkin(path, name.strip(), frames,
                      {key: indices[key] for key in STATES}, crop_box,
                      (cell_width, cell_height))


def _animation_config(path, config):
    if not isinstance(config, dict):
        raise SkinError("Each animation must be an object or a reference to another quota state.")
    image_path = _image_path(path, config.get("image"))
    if image_path.suffix.lower() not in (".png", ".webp"):
        raise SkinError("Animation images must be PNG or WebP files.")
    columns, rows = config.get("columns"), config.get("rows")
    if any(type(value) is not int or not 1 <= value <= 32 for value in (columns, rows)):
        raise SkinError("Animation 'columns' and 'rows' must be integers from 1 to 32.")
    edges = config.get("row_edges")
    if edges is not None:
        if (not isinstance(edges, list) or len(edges) != rows + 1
                or any(type(edge) is not int for edge in edges)
                or edges[0] != 0 or any(left >= right for left, right in zip(edges, edges[1:]))):
            raise SkinError("Animation row_edges must contain rows+1 strictly increasing integers starting at zero.")
        edges = tuple(edges)
    clips = config.get("clips")
    if not isinstance(clips, dict) or "idle" not in clips:
        raise SkinError("Animation 'clips' must include an idle clip.")
    if any(key not in ACTIONS + EXTRA_ACTIONS for key in clips):
        raise SkinError("Unknown animation clip name.")
    normalized = []
    for action in ACTIONS + EXTRA_ACTIONS:
        if action not in clips:
            continue
        clip = clips[action]
        if not isinstance(clip, dict):
            raise SkinError(f"Animation clip '{action}' must be an object.")
        row, frames, ms = clip.get("row"), clip.get("frames"), clip.get("ms")
        boxes = None
        if "boxes" in clip:
            boxes = clip["boxes"]
            if (not isinstance(boxes, list) or not 1 <= len(boxes) <= 256
                    or any(not isinstance(box, list) or len(box) != 4
                           or any(type(value) is not int for value in box)
                           or not (0 <= box[0] < box[2] and 0 <= box[1] < box[3])
                           for box in boxes)):
                raise SkinError(f"Animation clip '{action}' boxes must be integer [x1,y1,x2,y2] rectangles with positive dimensions.")
            if "frames" in clip and (type(frames) is not int or frames != len(boxes)):
                raise SkinError(f"Animation clip '{action}' frames must match its boxes count.")
            frames, row = len(boxes), None
            boxes = tuple(tuple(box) for box in boxes)
        else:
            if type(row) is not int or not 0 <= row < rows:
                raise SkinError(f"Animation clip '{action}' row is outside the sheet.")
            if type(frames) is not int or not 1 <= frames <= columns:
                raise SkinError(f"Animation clip '{action}' frames must fit within one row.")
        if type(ms) is not int or not 1 <= ms <= 60000:
            raise SkinError(f"Animation clip '{action}' ms must be an integer from 1 to 60000.")
        normalized.append((action, row, frames, ms, boxes))
    return image_path, columns, rows, tuple(normalized), edges


def _load_animation(config):
    image_path, columns, rows, definitions, row_edges = config
    has_grid = any(boxes is None for _action, _row, _frames, _ms, boxes in definitions)
    has_boxes = any(boxes is not None for _action, _row, _frames, _ms, boxes in definitions)
    Image = _pillow()
    try:
        with Image.open(image_path) as source:
            if source.format not in ("PNG", "WEBP"):
                raise SkinError("Animation image contents must be PNG or WebP.")
            if source.width * source.height > 16777216:
                raise SkinError("Animation sheet exceeds the 16 megapixel size limit.")
            if has_grid and (source.width % columns or (row_edges is None and source.height % rows)):
                raise SkinError("Animation image dimensions must divide evenly into its columns and rows.")
            if row_edges is not None and row_edges[-1] != source.height:
                raise SkinError("Animation row_edges must end at the image height.")
            if "A" not in source.getbands() and "transparency" not in source.info:
                raise SkinError("Animation image must include transparency.")
            sheet = source.convert("RGBA")
            sheet.load()
        if row_edges is None:
            row_edges = tuple(row * (sheet.height // rows) for row in range(rows + 1))
        cell_width = sheet.width // columns
        cell_height = max(bottom - top for top, bottom in zip(row_edges, row_edges[1:]))
        if has_grid and (not cell_width or not cell_height):
            raise SkinError("Animation cells must have non-zero dimensions.")
        # Include only frames declared by clips. Unused padding cells in native
        # 8-column sprite sheets do not affect the shared crop or scale.
        cells, bounds, clip_keys = {}, [], {}
        for action, row, frame_count, _ms, boxes in definitions:
            locations = boxes if boxes is not None else tuple(
                (column * cell_width, row_edges[row], (column + 1) * cell_width, row_edges[row + 1])
                for column in range(frame_count))
            keys = []
            for column, rectangle in enumerate(locations):
                if not (0 <= rectangle[0] < rectangle[2] <= sheet.width
                        and 0 <= rectangle[1] < rectangle[3] <= sheet.height):
                    raise SkinError(f"Animation clip '{action}' box {column} is outside the image bounds.")
                key = ("box", rectangle) if boxes is not None else ("grid", row, column)
                keys.append(key)
                if key in cells:
                    continue
                raw = sheet.crop(rectangle)
                # Generated sheets can have uneven row heights. Pad each cell
                # upward so all rows share a baseline before computing bounds.
                if boxes is None:
                    cell = Image.new("RGBA", (cell_width, cell_height))
                    cell.paste(raw, (0, cell_height - raw.height))
                else:
                    cell = raw
                alpha = cell.getchannel("A")
                box = alpha.point(lambda value: 255 if value > 8 else 0).getbbox()
                if box is None:
                    raise SkinError(f"Animation clip '{action}', frame {column} has no visible pixels.")
                cell.putalpha(alpha.point(lambda value: value if value > 8 else 0))
                # Explicit boxes identify complete actors but can have different
                # external margins. Remove those margins, preserving each
                # actor's actual width/height rather than stretching each frame.
                cells[key] = cell.crop(box) if has_boxes else cell
                bounds.append(box)
            clip_keys[action] = keys
        if has_boxes:
            cell_width = max(cell.width for cell in cells.values())
            cell_height = max(cell.height for cell in cells.values())
            for key, cell in cells.items():
                padded = Image.new("RGBA", (cell_width, cell_height))
                padded.paste(cell, ((cell_width - cell.width) // 2, cell_height - cell.height))
                cells[key] = padded
            crop_box = (0, 0, cell_width, cell_height)
        else:
            crop_box = (min(box[0] for box in bounds), min(box[1] for box in bounds),
                        max(box[2] for box in bounds), max(box[3] for box in bounds))
        width, height = crop_box[2] - crop_box[0], crop_box[3] - crop_box[1]
        scale = min(220 / width, 212 / height)
        size = (max(1, min(220, round(width * scale))), max(1, min(212, round(height * scale))))
        cells = {key: cell.crop(crop_box).resize(size, Image.Resampling.LANCZOS)
                 for key, cell in cells.items()}
        # Left-walk frames are already authored facing left. Never mirror them
        # a second time, and never wrap a clip into the next sheet row.
        clips = {action: AnimationClip([cells[key] for key in clip_keys[action]], ms, row)
                 for action, row, _frame_count, ms, _boxes in definitions}
        return AnimationSheet(image_path, clips, crop_box, (cell_width, cell_height))
    except SkinError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise SkinError(f"Cannot decode animation image '{image_path}': {exc}") from exc


def load_skin(manifest_path):
    """Prepare static, animated or mixed skins before opening any Tk window.

    `animations` is keyed by quota state. Values contain image/grid/clips, or
    reference another animated state by name. Missing actions fall back to idle.
    Missing animated states use the legacy static image when it is provided.
    Each sheet has one uniform scale and a maximum 220x212 display. Explicit
    clip boxes use alpha-trimmed, bottom-centred frames with shared padding.
    """
    path = Path(manifest_path).expanduser().resolve()
    try:
        if path.stat().st_size > 1024 * 1024:
            raise SkinError("Skin JSON exceeds the 1 MiB size limit.")
        manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SkinError(f"Cannot read skin JSON '{path}': {exc}") from exc
    if not isinstance(manifest, dict):
        raise SkinError("Skin JSON must be an object.")
    name = manifest.get("name")
    if not isinstance(name, str) or not name.strip():
        raise SkinError("Skin 'name' must be a non-empty string.")
    has_static = any(key in manifest for key in ("image", "columns", "rows", "states"))
    skin = _load_static(path, name, manifest) if has_static or "animations" not in manifest else None
    if "animations" not in manifest:
        if manifest.get("presentation"):
            raise SkinError("Skin presentation requires animation clips.")
        return skin
    definitions = manifest["animations"]
    if not isinstance(definitions, dict) or any(state not in STATES for state in definitions):
        raise SkinError("Skin 'animations' must map known quota states to animation sheets.")
    if skin is None and any(state not in definitions for state in STATES):
        raise SkinError("An animation-only skin must define all six quota states.")
    animations, prepared = {}, {}

    def resolve(state, visiting):
        if state in animations:
            return animations[state]
        if state in visiting:
            raise SkinError(f"Animation state reference cycle includes '{state}'.")
        if state not in definitions:
            raise SkinError(f"Animation references missing state '{state}'.")
        definition = definitions[state]
        if isinstance(definition, str):
            if definition not in STATES:
                raise SkinError(f"Animation references unknown quota state '{definition}'.")
            animation = resolve(definition, visiting | {state})
        else:
            config = _animation_config(path, definition)
            if config not in prepared:
                prepared[config] = _load_animation(config)
            animation = prepared[config]
        animations[state] = animation
        return animation

    for state in definitions:
        resolve(state, set())
    if skin is None:
        representative = animations["normal"]
        frame = representative.clips["idle"].frames[0]
        skin = SpriteSkin(path, name.strip(), [frame], {state: 0 for state in STATES},
                          representative.crop_box, representative.cell_size)
    skin.animations = animations
    try:
        skin.presentation = SkinEvents(manifest.get("presentation"))
        skin.presentation.validate_clips(animations, STATES)
    except ValueError as exc:
        raise SkinError(str(exc)) from exc
    for animation in set(animations.values()):
        for action in skin.presentation.hold:
            animation.clips[action].loop = False
    # Leave headroom for a speech bubble; older skins keep their original size.
    if skin.presentation.bubbles:
        from PIL import Image
        for animation in set(animations.values()):
            for clip in animation.clips.values():
                scale = min(1, 170 / max(frame.height for frame in clip.frames))
                clip.frames = tuple(frame.resize((max(1, round(frame.width * scale)),
                                                   max(1, round(frame.height * scale))),
                                                  Image.Resampling.LANCZOS)
                                    for frame in clip.frames)
            animation.display_size = animation.clips["idle"].frames[0].size
    return skin
