"""Canonical strip specifications and explicit design-coordinate conversion.

The strip specification owns physical and logical dimensions. Source image
pixels never enter this module. Older designs are inferred without rewriting
their element coordinates; conversion to another preset is an explicit action.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from typing import Any


PHOTOBOOTH_2X6_ID = "photobooth-2x6"
PHOTOBOOTH_54X180_ID = "photobooth-54x180"
CUSTOM_ID = "custom"

STRIP_PRESETS: dict[str, dict[str, Any]] = {
    PHOTOBOOTH_2X6_ID: {
        "presetId": PHOTOBOOTH_2X6_ID,
        "widthMm": 50.8,
        "heightMm": 152.4,
        "dpi": 300,
        "designWidth": 600.0,
        "designHeight": 1800.0,
        "orientation": "portrait",
        "frameCount": 4,
    },
    PHOTOBOOTH_54X180_ID: {
        "presetId": PHOTOBOOTH_54X180_ID,
        "widthMm": 54.0,
        "heightMm": 180.0,
        "dpi": 300,
        "designWidth": 600.0,
        "designHeight": 2000.0,
        "orientation": "portrait",
        "frameCount": 4,
    },
}


def _positive(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive finite number.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a positive finite number.") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be a positive finite number.")
    return number


def _nonnegative(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a non-negative finite number.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a non-negative finite number.") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{name} must be a non-negative finite number.")
    return number


def default_strip_spec() -> dict[str, Any]:
    """Return a detached 2 by 6 inch, four-frame specification."""
    return copy.deepcopy(STRIP_PRESETS[PHOTOBOOTH_2X6_ID])


def normalize_strip_spec(source: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate one JSON-compatible strip specification.

    A named preset has fixed dimensions. Custom dimensions are accepted only
    through the custom preset so stale print settings cannot mutate a preset.
    """
    data = dict(source or {})
    preset_id = str(data.get("presetId") or PHOTOBOOTH_2X6_ID)
    if preset_id in STRIP_PRESETS:
        spec = copy.deepcopy(STRIP_PRESETS[preset_id])
    elif preset_id == CUSTOM_ID:
        spec = {
            "presetId": CUSTOM_ID,
            "widthMm": _positive(data.get("widthMm"), "widthMm"),
            "heightMm": _positive(data.get("heightMm"), "heightMm"),
            "designWidth": _positive(data.get("designWidth"), "designWidth"),
            "designHeight": _positive(data.get("designHeight"), "designHeight"),
            "dpi": _positive(data.get("dpi", 300), "dpi"),
            "frameCount": data.get("frameCount", 4),
        }
        if isinstance(spec["frameCount"], bool) or not isinstance(spec["frameCount"], int) or not 1 <= spec["frameCount"] <= 100:
            raise ValueError("frameCount must be an integer from 1 to 100.")
        spec["orientation"] = "portrait" if spec["heightMm"] > spec["widthMm"] else "landscape" if spec["widthMm"] > spec["heightMm"] else "square"
    else:
        raise ValueError(f"Unsupported strip preset: {preset_id}")
    return spec


def legacy_strip_spec(design: Mapping[str, Any] | None,
                      print_settings: Mapping[str, Any] | None = None,
                      physical: Mapping[str, Any] | None = None,
                      frame_count: int | None = None) -> dict[str, Any]:
    """Describe old saved work exactly, without changing its coordinates.

    Existing print settings take priority over legacy artboard millimeters
    because they were the dimensions used for earlier PDF output. Template
    metadata takes priority when there are no project print settings.
    """
    design = design if isinstance(design, Mapping) else {}
    board = design.get("artboard") if isinstance(design.get("artboard"), Mapping) else {}
    settings = print_settings if isinstance(print_settings, Mapping) else {}
    physical = physical if isinstance(physical, Mapping) else {}
    width_mm = settings.get("stripWidthMm", physical.get("widthMm", board.get("widthMm", 50.8)))
    height_mm = settings.get("stripHeightMm", physical.get("heightMm", board.get("heightMm", 152.4)))
    count = frame_count
    if count is None:
        elements = design.get("elements") if isinstance(design.get("elements"), list) else []
        count = sum(isinstance(e, Mapping) and e.get("type") == "image_frame" for e in elements) or 4
    return normalize_strip_spec({
        "presetId": CUSTOM_ID,
        "widthMm": width_mm,
        "heightMm": height_mm,
        "designWidth": board.get("width", 600),
        "designHeight": board.get("height", 1800),
        "dpi": settings.get("resolutionDpi", 300),
        "frameCount": count,
    })


def artboard_for_spec(spec: Mapping[str, Any]) -> dict[str, float]:
    normalized = normalize_strip_spec(spec)
    return {
        "width": normalized["designWidth"],
        "height": normalized["designHeight"],
        "widthMm": normalized["widthMm"],
        "heightMm": normalized["heightMm"],
    }


def normalized_rect(rect: Mapping[str, Any], design_width: float,
                    design_height: float) -> dict[str, float]:
    """Return a design rectangle in 0..1 coordinates."""
    width, height = _positive(design_width, "designWidth"), _positive(design_height, "designHeight")
    values = {}
    for key in ("x", "y", "width", "height"):
        try:
            value = float(rect[key])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{key} must be finite.") from exc
        if not math.isfinite(value):
            raise ValueError(f"{key} must be finite.")
        values[key] = value
    if values["width"] < 0 or values["height"] < 0:
        raise ValueError("Rectangle dimensions cannot be negative.")
    return {
        "x": values["x"] / width,
        "y": values["y"] / height,
        "width": values["width"] / width,
        "height": values["height"] / height,
    }


_FRAME_RATIOS = {"1:1": 1.0, "4:3": 4 / 3, "16:9": 16 / 9}


def _frame_slot_index(element: Mapping[str, Any], fallback: int = 0) -> int:
    """Resolve a frame's slot without depending on its presentation ID."""
    value = element.get("slotIndex")
    if isinstance(value, bool):
        return fallback
    try:
        if value is not None:
            return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        pass
    suffix = "".join(character for character in str(element.get("id", "")) if character.isdigit())
    try:
        return max(0, int(suffix) - 1) if suffix else fallback
    except (TypeError, ValueError, OverflowError):
        return fallback


def _source_dimensions(image: Mapping[str, Any] | None) -> tuple[float, float] | None:
    if not isinstance(image, Mapping):
        return None
    try:
        width = float(image.get("width", image.get("widthPx")))
        height = float(image.get("height", image.get("heightPx")))
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(value) and value > 0 for value in (width, height)):
        return None
    return width, height


def source_aspect_ratio(image: Mapping[str, Any] | None) -> float | None:
    """Return the imported source ratio, never a thumbnail or frame ratio."""
    dimensions = _source_dimensions(image)
    if dimensions:
        return dimensions[0] / dimensions[1]
    try:
        ratio = float(image.get("aspectRatio")) if isinstance(image, Mapping) else 0.0
    except (TypeError, ValueError, OverflowError):
        return None
    return ratio if math.isfinite(ratio) and ratio > 0 else None


def _image_map(images: Mapping[str, Mapping[str, Any]] | list[Mapping[str, Any]] | None) -> dict[str, Mapping[str, Any]]:
    if isinstance(images, Mapping):
        return {str(key): value for key, value in images.items() if isinstance(value, Mapping)}
    return {
        str(item.get("id")): item
        for item in images or ()
        if isinstance(item, Mapping) and item.get("id") is not None
    }


def _slot_for_frame(strip: Mapping[str, Any] | None, slot_index: int) -> Mapping[str, Any]:
    slots = strip.get("slots") if isinstance(strip, Mapping) else None
    if not isinstance(slots, list) or slot_index < 0 or slot_index >= len(slots):
        return {}
    return slots[slot_index] if isinstance(slots[slot_index], Mapping) else {}


def _finite_or(value: Any, fallback: float, *, minimum: float | None = None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    if not math.isfinite(number) or (minimum is not None and number < minimum):
        return fallback
    return number


def _visible_source_bounds(source_ratio: float, frame_ratio: float,
                           crop: Mapping[str, Any], crop_mode: str) -> dict[str, float]:
    """Describe the normalized source rectangle visible in a resolved frame."""
    zoom = min(4.0, max(1.0, _finite_or(crop.get("zoom", 1.0), 1.0, minimum=1.0)))
    offset_x = min(1.0, max(-1.0, _finite_or(crop.get("offsetX", 0.0), 0.0)))
    offset_y = min(1.0, max(-1.0, _finite_or(crop.get("offsetY", 0.0), 0.0)))
    if crop_mode == "contain":
        # At the default zoom the complete source is visible. A later zoom is
        # an intentional user crop and can exceed the frame on either axis.
        drawn_width = min(1.0, source_ratio / frame_ratio)
        drawn_height = min(1.0, frame_ratio / source_ratio)
        width = min(1.0, 1.0 / (drawn_width * zoom))
        height = min(1.0, 1.0 / (drawn_height * zoom))
    elif source_ratio > frame_ratio:
        width, height = frame_ratio / source_ratio / zoom, 1.0 / zoom
    else:
        width, height = 1.0 / zoom, source_ratio / frame_ratio / zoom
    width = min(1.0, max(0.0, width))
    height = min(1.0, max(0.0, height))
    left_free, top_free = max(0.0, 1.0 - width), max(0.0, 1.0 - height)
    center_x = 0.5 + offset_x * left_free / 2
    center_y = 0.5 + offset_y * top_free / 2
    left = min(max(0.0, center_x - width / 2), 1.0 - width)
    top = min(max(0.0, center_y - height / 2), 1.0 - height)
    return {
        "left": left, "top": top,
        "right": left + width, "bottom": top + height,
        "fraction": width * height,
    }


def resolve_frame_layout(
    design: Mapping[str, Any],
    strip: Mapping[str, Any] | None = None,
    images: Mapping[str, Mapping[str, Any]] | list[Mapping[str, Any]] | None = None,
    *,
    adaptive: bool = True,
) -> dict[str, Any]:
    """Resolve image-frame boxes from strip geometry and per-slot sources.

    The design rectangles provide the structural content region: padding,
    configured gaps, and the location of the image stack. Populated Original
    frames use their own source ratio. All frames share the maximum available
    stack width and are uniformly reduced when their combined heights exceed
    the available content height. This function is the backend geometry
    boundary used by rendering and the API's Designer snapshot.

    ``adaptive=False`` is the legacy escape hatch. It returns the saved frame
    rectangles exactly, so opening an old project never rewrites its layout.
    """
    if not isinstance(design, Mapping):
        raise ValueError("Strip design is missing.")
    board = design.get("artboard") if isinstance(design.get("artboard"), Mapping) else {}
    board_width = _finite_or(board.get("width"), 0.0, minimum=1e-9)
    board_height = _finite_or(board.get("height"), 0.0, minimum=1e-9)
    if board_width <= 0 or board_height <= 0:
        raise ValueError("Strip design artboard dimensions must be positive.")

    image_map = _image_map(images)
    records: list[dict[str, Any]] = []
    for order, item in enumerate(design.get("elements", []) if isinstance(design.get("elements"), list) else []):
        if not isinstance(item, Mapping) or item.get("type") != "image_frame":
            continue
        x = _finite_or(item.get("x"), 0.0)
        y = _finite_or(item.get("y"), 0.0)
        width = _finite_or(item.get("width"), 1.0, minimum=1e-9)
        height = _finite_or(item.get("height"), 1.0, minimum=1e-9)
        slot_index = _frame_slot_index(item, order)
        slot = _slot_for_frame(strip, slot_index)
        image_id = slot.get("imageId")
        image = slot.get("image") if isinstance(slot.get("image"), Mapping) else None
        if image is None and image_id is not None:
            image = image_map.get(str(image_id))
        source_ratio = source_aspect_ratio(image)
        slot_crop = slot.get("crop") if isinstance(slot.get("crop"), Mapping) else {}
        ratio_mode = str(item.get("aspectRatio") or slot_crop.get("ratio", "original"))
        if ratio_mode not in _FRAME_RATIOS and ratio_mode != "original":
            ratio_mode = "original"
        crop = slot_crop
        crop_mode = str(crop.get("mode") or item.get("cropMode") or ("contain" if ratio_mode == "original" else "cover"))
        if crop_mode not in {"cover", "contain"}:
            crop_mode = "contain" if ratio_mode == "original" else "cover"
        fallback_ratio = width / height
        target_ratio = _FRAME_RATIOS.get(ratio_mode)
        if target_ratio is None:
            target_ratio = source_ratio if source_ratio is not None else fallback_ratio
        records.append({
            "element": item,
            "order": order,
            "slotIndex": slot_index,
            "imageId": str(image_id) if image_id is not None else None,
            "sourceAspectRatio": source_ratio,
            "ratioMode": ratio_mode,
            "cropMode": crop_mode,
            "crop": dict(crop),
            "base": {"x": x, "y": y, "width": width, "height": height},
            "targetRatio": target_ratio,
        })
    if not records:
        return {
            "mode": "adaptive" if adaptive else "legacy",
            "artboard": {"width": board_width, "height": board_height},
            "content": {"x": 0.0, "y": 0.0, "width": board_width, "height": board_height, "gaps": []},
            "scale": 1.0,
            "frames": [],
        }

    ordered = sorted(records, key=lambda record: (record["slotIndex"], record["base"]["y"], record["order"]))
    layout_meta = design.get("imageLayout") if isinstance(design.get("imageLayout"), Mapping) else {}
    default_left = min(record["base"]["x"] for record in ordered)
    default_right = max(record["base"]["x"] + record["base"]["width"] for record in ordered)
    default_top = min(record["base"]["y"] for record in ordered)
    default_bottom = max(record["base"]["y"] + record["base"]["height"] for record in ordered)
    content_left = _finite_or(layout_meta.get("left"), default_left, minimum=0.0)
    content_right_padding = _finite_or(layout_meta.get("right"), board_width - default_right, minimum=0.0)
    content_top = _finite_or(layout_meta.get("top"), default_top, minimum=0.0)
    content_bottom_padding = _finite_or(layout_meta.get("bottom"), board_height - default_bottom, minimum=0.0)
    content_left = min(content_left, board_width)
    content_right_padding = min(content_right_padding, board_width - content_left)
    content_top = min(content_top, board_height)
    content_bottom_padding = min(content_bottom_padding, board_height - content_top)
    content_width = max(1e-9, board_width - content_left - content_right_padding)
    configured_frame_width = _finite_or(layout_meta.get("frameWidth"), content_width, minimum=1e-9)
    base_frame_width = min(content_width, configured_frame_width)

    actual_gaps = [
        max(0.0, ordered[index + 1]["base"]["y"] -
            (ordered[index]["base"]["y"] + ordered[index]["base"]["height"]))
        for index in range(len(ordered) - 1)
    ]
    configured_gap = layout_meta.get("gap")
    if configured_gap is not None:
        gap = _finite_or(configured_gap, actual_gaps[0] if actual_gaps else 0.0, minimum=0.0)
        gaps = [gap] * (len(ordered) - 1)
    else:
        gaps = actual_gaps
    available_height = max(0.0, board_height - content_top - content_bottom_padding - sum(gaps))
    required_height = sum(base_frame_width / record["targetRatio"] for record in ordered)
    scale = min(1.0, available_height / required_height) if required_height > 0 else 1.0
    scale = max(0.0, scale)
    frame_width = base_frame_width * scale
    frame_heights = [frame_width / record["targetRatio"] for record in ordered]
    image_stack_height = sum(frame_heights) + sum(gaps)
    # ``available_height`` excludes the configured gaps for scale fitting;
    # alignment places the complete frame stack, including those gaps, inside
    # the padded content region.
    unused_height = max(0.0, available_height - sum(frame_heights))
    vertical_align = str(layout_meta.get("verticalAlign", "middle")).lower()
    if vertical_align in {"top", "start"}:
        stack_offset = 0.0
    elif vertical_align in {"bottom", "end"}:
        stack_offset = unused_height
    else:
        stack_offset = unused_height / 2
    stack_top = content_top + stack_offset

    frames: list[dict[str, Any]] = []
    cursor_y = stack_top
    for index, record in enumerate(ordered):
        base = record["base"]
        if adaptive:
            box = {
                "x": content_left + max(0.0, (content_width - frame_width) / 2),
                "y": cursor_y,
                "width": frame_width,
                "height": frame_heights[index],
            }
            cursor_y += frame_heights[index] + (gaps[index] if index < len(gaps) else 0.0)
        else:
            box = dict(base)
        ratio = box["width"] / box["height"] if box["height"] > 0 else record["targetRatio"]
        visible = {"left": 0.0, "top": 0.0, "right": 1.0, "bottom": 1.0, "fraction": 1.0}
        if record["sourceAspectRatio"] is not None:
            visible = _visible_source_bounds(record["sourceAspectRatio"], ratio,
                                              record["crop"], record["cropMode"])
        frames.append({
            "elementId": str(record["element"].get("id", "")),
            "slotIndex": record["slotIndex"],
            "imageId": record["imageId"],
            "x": box["x"], "y": box["y"], "width": box["width"], "height": box["height"],
            "frameAspectRatio": ratio,
            "ratioMode": record["ratioMode"],
            "sourceAspectRatio": record["sourceAspectRatio"],
            "cropMode": record["cropMode"],
            "visibleSourceBounds": visible,
            "visibleSourceFraction": visible["fraction"],
            "populated": record["sourceAspectRatio"] is not None,
        })
    return {
        "mode": "adaptive" if adaptive else "legacy",
        "artboard": {"width": board_width, "height": board_height},
        "content": {
            "x": content_left, "y": content_top, "width": content_width,
            "height": available_height, "gaps": gaps,
            "frameWidth": frame_width,
            "rightPadding": content_right_padding, "bottomPadding": content_bottom_padding,
        },
        "scale": scale,
        "requiredImageHeight": required_height,
        "imageStackHeight": image_stack_height,
        "frames": frames,
    }


def apply_frame_layout(design: Mapping[str, Any], layout: Mapping[str, Any]) -> dict[str, Any]:
    """Return a renderable design with canonical resolved frame rectangles."""
    result = copy.deepcopy(dict(design))
    by_id = {
        str(item.get("elementId")): item
        for item in layout.get("frames", [])
        if isinstance(item, Mapping) and item.get("elementId") is not None
    }
    for element in result.get("elements", []) if isinstance(result.get("elements"), list) else []:
        if not isinstance(element, dict) or element.get("type") != "image_frame":
            continue
        resolved = by_id.get(str(element.get("id")))
        if resolved:
            for key in ("x", "y", "width", "height"):
                element[key] = float(resolved[key])
    return result


def physical_rect(rect: Mapping[str, Any], spec: Mapping[str, Any]) -> dict[str, float]:
    """Map one design rectangle into physical millimeters."""
    normalized = normalize_strip_spec(spec)
    unit = normalized_rect(rect, normalized["designWidth"], normalized["designHeight"])
    return {
        "xMm": unit["x"] * normalized["widthMm"],
        "yMm": unit["y"] * normalized["heightMm"],
        "widthMm": unit["width"] * normalized["widthMm"],
        "heightMm": unit["height"] * normalized["heightMm"],
    }


def normalize_design_coordinates(design: Mapping[str, Any], target_spec: Mapping[str, Any]) -> dict[str, Any]:
    """Explicitly migrate element rectangles through normalized coordinates.

    This is never called on project load. Call it only when the user selects a
    different strip preset; background, decorations, and frame order survive.
    """
    if not isinstance(design, Mapping):
        raise ValueError("Design must be an object.")
    old_board = design.get("artboard")
    if not isinstance(old_board, Mapping):
        raise ValueError("Design has no artboard.")
    old_width = _positive(old_board.get("width"), "artboard.width")
    old_height = _positive(old_board.get("height"), "artboard.height")
    spec = normalize_strip_spec(target_spec)
    scale_x = spec["designWidth"] / old_width
    scale_y = spec["designHeight"] / old_height
    scale_uniform = math.sqrt(scale_x * scale_y)
    converted = copy.deepcopy(dict(design))
    converted["artboard"] = artboard_for_spec(spec)
    for element in converted.get("elements", []):
        if not isinstance(element, dict) or not all(key in element for key in ("x", "y", "width", "height")):
            continue
        unit = normalized_rect(element, old_width, old_height)
        element["x"] = unit["x"] * spec["designWidth"]
        element["y"] = unit["y"] * spec["designHeight"]
        element["width"] = unit["width"] * spec["designWidth"]
        element["height"] = unit["height"] * spec["designHeight"]
        # Distances attached to text and decorations must follow the same
        # coordinate change as their boxes. Dimensionless line-height and
        # rotation values intentionally stay as authored.
        for key, factor in (
            ("fontSize", scale_y), ("letterSpacing", scale_x),
            ("borderRadius", scale_uniform), ("borderWidth", scale_uniform),
            ("padding", scale_uniform), ("strokeWidth", scale_uniform),
            ("shadowBlur", scale_uniform), ("shadowOffsetX", scale_x),
            ("shadowOffsetY", scale_y),
        ):
            if key in element and isinstance(element[key], (int, float)) and not isinstance(element[key], bool):
                if not math.isfinite(float(element[key])):
                    raise ValueError(f"{key} must be finite.")
                element[key] = float(element[key]) * factor
    pattern = converted.get("pattern")
    if isinstance(pattern, dict):
        for key, factor in (("spacing", scale_uniform), ("offsetX", scale_x), ("offsetY", scale_y)):
            if key in pattern and isinstance(pattern[key], (int, float)) and not isinstance(pattern[key], bool):
                if not math.isfinite(float(pattern[key])):
                    raise ValueError(f"pattern.{key} must be finite.")
                pattern[key] = float(pattern[key]) * factor
    return converted


def validate_frame_geometry(design: Mapping[str, Any], spec: Mapping[str, Any],
                            required_count: int | None = None) -> list[dict[str, Any]]:
    """Return frames by slot after checking bounds and required slot coverage.

    A user-created duplicate may intentionally display the same slot twice,
    so only the built-in default's initial four-frame count is fixed.
    """
    if not isinstance(design, Mapping):
        raise ValueError("Strip design is missing.")
    normalized = normalize_strip_spec(spec)
    board = design.get("artboard")
    if not isinstance(board, Mapping):
        raise ValueError("Strip design has no artboard.")
    width, height = _positive(board.get("width"), "artboard.width"), _positive(board.get("height"), "artboard.height")
    if not math.isclose(width, normalized["designWidth"], abs_tol=1e-6) or not math.isclose(height, normalized["designHeight"], abs_tol=1e-6):
        raise ValueError("Strip design dimensions do not match its strip specification.")
    elements = design.get("elements")
    if not isinstance(elements, list):
        raise ValueError("Strip design has no elements.")
    frames: list[dict[str, Any]] = []
    covered_indices: set[int] = set()
    for item in elements:
        if not isinstance(item, Mapping) or item.get("type") != "image_frame":
            continue
        index = item.get("slotIndex")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValueError("Image frames need non-negative slot indices.")
        rect = normalized_rect(item, width, height)
        if (rect["width"] <= 0 or rect["height"] <= 0 or
            rect["x"] < -1e-9 or rect["y"] < -1e-9 or
            rect["x"] + rect["width"] > 1 + 1e-9 or
            rect["y"] + rect["height"] > 1 + 1e-9):
            raise ValueError("Image frame must stay inside the strip artboard.")
        frames.append(dict(item))
        covered_indices.add(index)
    count = normalized["frameCount"] if required_count is None else required_count
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("Required frame count is invalid.")
    if any(index not in covered_indices for index in range(count)):
        raise ValueError(f"Strip design needs image frames for slots 1 through {count}.")
    return sorted(frames, key=lambda item: item["slotIndex"])


def legacy_dcapture_design() -> dict[str, Any]:
    """Reconstruct the original 3:4 built-in for old projects lacking design data."""
    width, height, gap = 1200.0, 1600.0, 18.0
    frame_width = width - 2 * gap
    frame_height = (height - 5 * gap) / 4
    elements: list[dict[str, Any]] = [{
        "id": "background", "type": "background", "x": 0.0, "y": 0.0,
        "width": width, "height": height, "fill": "#f6f3eb",
        "opacity": 1.0, "visible": True, "locked": False,
    }]
    for index in range(4):
        elements.append({
            "id": f"photo-{index + 1}", "type": "image_frame",
            "x": gap, "y": gap + index * (frame_height + gap),
            "width": frame_width, "height": frame_height,
            "fill": "#ffffff", "borderColor": "#00000000",
            "borderWidth": 0.0, "borderRadius": 0.0, "opacity": 1.0,
            "padding": 0.0, "visible": True, "locked": False,
            "aspectRatio": "original", "cropMode": "cover", "slotIndex": index,
        })
    return {
        "schemaVersion": 1, "templateId": "dcapture-clean",
        "artboard": {"width": width, "height": height, "widthMm": 54.0, "heightMm": 72.0},
        "elements": elements,
        "pattern": {"type": "none", "scale": 1.0, "spacing": gap,
                    "opacity": 0.0, "color": "#f6f3eb", "offsetX": 0.0, "offsetY": 0.0},
        "snap": True,
    }


def make_1x4_design(spec: Mapping[str, Any] | None = None, *,
                    side_padding: float = 18.0, top_padding: float = 18.0,
                    bottom_padding: float = 18.0, gap: float = 18.0,
                    template_id: str = "dcapture-clean") -> dict[str, Any]:
    """Create four equal locked frames independent of source photo dimensions."""
    normalized = normalize_strip_spec(spec)
    if normalized["frameCount"] != 4:
        raise ValueError("The 1x4 design requires a four-frame strip specification.")
    width, height = normalized["designWidth"], normalized["designHeight"]
    sides = _nonnegative(side_padding, "side_padding")
    top, bottom = _nonnegative(top_padding, "top_padding"), _nonnegative(bottom_padding, "bottom_padding")
    spacing = _nonnegative(gap, "gap")
    frame_width = width - 2 * sides
    frame_height = (height - top - bottom - 3 * spacing) / 4
    if frame_width <= 0 or frame_height <= 0:
        raise ValueError("Frame padding and gap leave no room for photos.")
    elements: list[dict[str, Any]] = [{
        "id": "background", "type": "background", "x": 0.0, "y": 0.0,
        "width": width, "height": height, "fill": "#f6f3eb",
        "opacity": 1.0, "visible": True, "locked": False,
    }]
    for index in range(4):
        elements.append({
            "id": f"photo-{index + 1}", "type": "image_frame",
            "x": sides, "y": top + index * (frame_height + spacing),
            "width": frame_width, "height": frame_height,
            "fill": "#ffffff", "borderColor": "#00000000",
            "borderWidth": 0.0, "borderRadius": 0.0, "opacity": 1.0,
            "padding": 0.0, "visible": True, "locked": True,
            "aspectRatio": "original", "cropMode": "contain", "slotIndex": index,
        })
    return {
        "schemaVersion": 1,
        "templateId": template_id,
        "artboard": artboard_for_spec(normalized),
        "elements": elements,
        "imageLayout": {
            "left": sides, "right": sides, "top": top, "bottom": bottom,
            "gap": spacing, "verticalAlign": "middle",
        },
        "pattern": {
            "type": "none", "scale": 1.0, "spacing": spacing,
            "opacity": 0.0, "color": "#f6f3eb", "offsetX": 0.0, "offsetY": 0.0,
        },
        "snap": True,
    }
