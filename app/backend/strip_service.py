"""Image grouping and per-slot crop state helpers."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections.abc import Iterable, Mapping, MutableSequence


INCOMPLETE_POLICIES = {"empty", "duplicate_last", "repeat_from_start", "exclude"}
CROP_MODES = {"cover", "contain"}
CROP_RATIOS = {"1:1", "4:3", "16:9", "original"}
DEFAULT_TEMPLATE_ID = "dcapture-clean"
DEFAULT_CROP = {"mode": "cover", "ratio": "original", "zoom": 1.0, "offsetX": 0.0, "offsetY": 0.0}


def _image_id(image: Mapping | str) -> str:
    value = image if isinstance(image, str) else image.get("id", image.get("imageId"))
    if value is None or not str(value):
        raise ValueError("Every image must have an id.")
    return str(value)


def _strip_id(template_id: str, slots_per_strip: int, strip_index: int, slots: list[dict]) -> str:
    fingerprint = {
        "templateId": template_id,
        "slotsPerStrip": slots_per_strip,
        "index": strip_index,
        "imageIds": [slot["imageId"] for slot in slots],
    }
    encoded = json.dumps(fingerprint, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "strip-" + hashlib.sha256(encoded).hexdigest()[:20]


def _previous_crop(previous_strips: Mapping[str, Mapping], strip_id: str, slot_index: int, image_id: str | None) -> dict:
    previous = previous_strips.get(strip_id)
    if not previous or not isinstance(previous.get("slots"), list) or slot_index >= len(previous["slots"]):
        return dict(DEFAULT_CROP)
    old_slot = previous["slots"][slot_index]
    if old_slot.get("imageId") != image_id:
        return dict(DEFAULT_CROP)
    try:
        return normalize_crop(old_slot.get("crop", {}))
    except ValueError:
        return dict(DEFAULT_CROP)


def generate_strips(
    images: Iterable[Mapping | str],
    slots_per_strip: int = 4,
    incomplete: str = "empty",
    template_id: str = DEFAULT_TEMPLATE_ID,
    previous_strips: Iterable[Mapping] | None = None,
) -> dict:
    """Group ordered images into strip records using the selected fill policy.

    The input order is authoritative; sorting belongs to sorting_service.
    Returned keys include the incomplete source ids so callers can report
    excluded images and print validation without re-deriving the grouping.
    """
    if isinstance(slots_per_strip, bool) or not isinstance(slots_per_strip, int) or not 1 <= slots_per_strip <= 100:
        raise ValueError("slots_per_strip must be an integer from 1 to 100.")
    if incomplete not in INCOMPLETE_POLICIES:
        raise ValueError(f"Unsupported incomplete-strip policy: {incomplete}")
    if not isinstance(template_id, str) or not template_id.strip():
        raise ValueError("template_id must be a non-empty string.")

    image_ids = [_image_id(image) for image in images]
    prior_by_id: dict[str, Mapping] = {}
    for strip in previous_strips or ():
        if strip.get("id"):
            prior_by_id[str(strip["id"])] = strip

    complete_count, remainder = divmod(len(image_ids), slots_per_strip)
    group_count = complete_count + (1 if remainder and incomplete != "exclude" else 0)
    strips: list[dict] = []
    for strip_index in range(group_count):
        start = strip_index * slots_per_strip
        group = image_ids[start : start + slots_per_strip]
        if len(group) < slots_per_strip:
            if incomplete == "duplicate_last" and group:
                group.extend([group[-1]] * (slots_per_strip - len(group)))
            elif incomplete == "repeat_from_start" and group:
                original = tuple(group)
                next_index = 0
                while len(group) < slots_per_strip:
                    group.append(original[next_index % len(original)])
                    next_index += 1
            elif incomplete == "empty":
                group.extend([None] * (slots_per_strip - len(group)))

        slots = [{"imageId": image_id, "crop": dict(DEFAULT_CROP)} for image_id in group]
        strip_id = _strip_id(template_id, slots_per_strip, strip_index, slots)
        for slot_index, slot in enumerate(slots):
            slot["crop"] = _previous_crop(prior_by_id, strip_id, slot_index, slot["imageId"])
        strips.append({"id": strip_id, "slots": slots, "templateId": template_id, "designOverride": None})

    incomplete_image_ids = image_ids[complete_count * slots_per_strip :] if remainder else []
    excluded_image_ids = incomplete_image_ids if incomplete == "exclude" else []
    return {
        "strips": strips,
        "slotsPerStrip": slots_per_strip,
        "incompleteHandling": incomplete,
        "incompleteImageIds": incomplete_image_ids,
        "excludedImageIds": list(excluded_image_ids),
        "incompleteImageCount": len(incomplete_image_ids),
    }


def normalize_crop(crop: Mapping | None) -> dict:
    """Validate and bound crop state using the frontend contract's fields."""
    data = dict(crop or {})
    mode = data.get("mode", DEFAULT_CROP["mode"])
    ratio = data.get("ratio", DEFAULT_CROP["ratio"])
    if mode not in CROP_MODES:
        raise ValueError("Crop mode must be 'cover' or 'contain'.")
    if ratio not in CROP_RATIOS:
        raise ValueError("Crop ratio must be 1:1, 4:3, 16:9, or original.")
    try:
        zoom = float(data.get("zoom", DEFAULT_CROP["zoom"]))
        offset_x = float(data.get("offsetX", DEFAULT_CROP["offsetX"]))
        offset_y = float(data.get("offsetY", DEFAULT_CROP["offsetY"]))
    except (TypeError, ValueError) as exc:
        raise ValueError("Crop zoom and offsets must be numeric.") from exc
    if not all(math.isfinite(value) for value in (zoom, offset_x, offset_y)):
        raise ValueError("Crop zoom and offsets must be finite numbers.")
    return {
        "mode": mode,
        "ratio": ratio,
        "zoom": min(4.0, max(1.0, zoom)),
        "offsetX": min(1.0, max(-1.0, offset_x)),
        "offsetY": min(1.0, max(-1.0, offset_y)),
    }


def set_slot_crop(strips: MutableSequence[dict], strip_id: str, slot_index: int, crop: Mapping) -> dict:
    """Update one populated slot's crop state in place and return its strip."""
    if isinstance(slot_index, bool) or not isinstance(slot_index, int) or slot_index < 0:
        raise ValueError("slot_index must be a non-negative integer.")
    for strip in strips:
        if strip.get("id") != strip_id:
            continue
        slots = strip.get("slots")
        if not isinstance(slots, list) or slot_index >= len(slots):
            raise ValueError("The requested strip slot does not exist.")
        if slots[slot_index].get("imageId") is None:
            raise ValueError("An empty strip slot cannot have crop settings.")
        slots[slot_index]["crop"] = normalize_crop(crop)
        return strip
    raise ValueError("The requested strip does not exist.")


def clone_strips(strips: Iterable[Mapping]) -> list[dict]:
    """Deep-copy structured strip state when a caller needs immutable edits."""
    return copy.deepcopy([dict(strip) for strip in strips])


__all__ = ["generate_strips", "normalize_crop", "set_slot_crop", "clone_strips"]
