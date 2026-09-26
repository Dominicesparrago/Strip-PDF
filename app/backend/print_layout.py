"""Physical A4 print layout calculations.

All public geometry is in millimeters and uses a top-left page origin. The
layout object returned here is the single source of placement geometry for
both the PDF writer and raster preview renderer.

Defaults: A4 landscape, five 54 x 72 mm strips, 5 mm margins, and 2 mm gaps.
The 54 x 72 mm body is a configurable assumed physical size matching the
initial 3:4 DCapture Clean artboard; DCapture supplies raster dimensions, not
authoritative millimeters. Template metadata may override this default.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping


PAGE_WIDTH_MM = 297.0
PAGE_HEIGHT_MM = 210.0
MM_PER_INCH = 25.4
POINTS_PER_INCH = 72.0
PT_PER_MM = POINTS_PER_INCH / MM_PER_INCH

DEFAULT_SETTINGS: dict[str, Any] = {
    "paper": "A4",
    "orientation": "landscape",
    "stripsPerPage": 5,
    "stripWidthMm": 54.0,
    "stripHeightMm": 72.0,
    "marginMm": 5.0,
    "gapMm": 2.0,
    "cutGuides": False,
    "resolutionDpi": 300,
}


class PrintSettingsError(ValueError):
    """Raised when physical print settings cannot describe a valid layout."""


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise PrintSettingsError(f"{name} must be a number.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PrintSettingsError(f"{name} must be a number.") from exc
    if not math.isfinite(number):
        raise PrintSettingsError(f"{name} must be finite.")
    return number


def normalize_settings(settings: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return validated settings with defaults filled in.

    Only A4 landscape is currently supported. Unknown settings are retained so
    application-specific output options can travel with the normalized data.
    """
    supplied = dict(settings or {})
    normalized = {**DEFAULT_SETTINGS, **supplied}
    if str(normalized.get("paper", "A4")).upper() != "A4":
        raise PrintSettingsError("Only A4 paper is supported.")
    if str(normalized.get("orientation", "landscape")).lower() != "landscape":
        raise PrintSettingsError("Only landscape orientation is supported.")

    count_value = normalized.get("stripsPerPage")
    if isinstance(count_value, bool):
        raise PrintSettingsError("stripsPerPage must be a positive whole number.")
    try:
        count = int(count_value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PrintSettingsError("stripsPerPage must be a positive whole number.") from exc
    if count <= 0 or float(count_value) != count:
        raise PrintSettingsError("stripsPerPage must be a positive whole number.")
    normalized["stripsPerPage"] = count

    for key in ("stripWidthMm", "stripHeightMm"):
        value = _finite_number(normalized.get(key), key)
        if value <= 0:
            raise PrintSettingsError(f"{key} must be greater than zero.")
        normalized[key] = value
    for key in ("marginMm", "gapMm"):
        value = _finite_number(normalized.get(key), key)
        if value < 0:
            raise PrintSettingsError(f"{key} cannot be negative.")
        normalized[key] = value

    resolution = _finite_number(normalized.get("resolutionDpi", 300), "resolutionDpi")
    if resolution <= 0:
        raise PrintSettingsError("resolutionDpi must be greater than zero.")
    normalized["resolutionDpi"] = resolution
    normalized["paper"] = "A4"
    normalized["orientation"] = "landscape"
    normalized["cutGuides"] = bool(normalized.get("cutGuides", False))
    return normalized


def calculate_fit(settings: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Calculate whether the configured strip count fits without scaling."""
    normalized = normalize_settings(settings)
    margin = normalized["marginMm"]
    gap = normalized["gapMm"]
    strip_width = normalized["stripWidthMm"]
    strip_height = normalized["stripHeightMm"]
    count = normalized["stripsPerPage"]
    available_width = max(0.0, PAGE_WIDTH_MM - 2 * margin)
    available_height = max(0.0, PAGE_HEIGHT_MM - 2 * margin)
    required_width = count * strip_width + max(0, count - 1) * gap
    overflow = max(0.0, required_width - available_width)
    height_fits = strip_height <= available_height + 1e-9
    if strip_width > available_width + 1e-9 or not height_fits:
        maximum = 0
    else:
        maximum = max(0, int(math.floor((available_width + gap + 1e-9) / (strip_width + gap))))
    valid = count <= maximum and height_fits
    required_height = strip_height
    vertical_overflow = max(0.0, required_height - available_height)
    return {
        "valid": valid,
        "requestedCount": count,
        "maximumCount": maximum,
        "availableWidthMm": _round(available_width),
        "requiredWidthMm": _round(required_width),
        "overflowMm": _round(overflow),
        "availableHeightMm": _round(available_height),
        "requiredHeightMm": _round(required_height),
        "verticalOverflowMm": _round(vertical_overflow),
    }


def _round(value: float) -> float:
    rounded = round(float(value), 6)
    return 0.0 if rounded == 0 else rounded


def _normalise_strips(strips: int | Iterable[Any] | None) -> list[dict[str, Any]]:
    if strips is None:
        return []
    if isinstance(strips, bool):
        raise PrintSettingsError("strips must be a list or a non-negative integer count.")
    if isinstance(strips, int):
        if strips < 0:
            raise PrintSettingsError("Strip count cannot be negative.")
        return [{"id": str(index + 1)} for index in range(strips)]
    result: list[dict[str, Any]] = []
    try:
        iterator = iter(strips)
    except TypeError as exc:
        raise PrintSettingsError("strips must be a list or a non-negative integer count.") from exc
    for index, strip in enumerate(iterator):
        if isinstance(strip, Mapping):
            item = dict(strip)
            strip_id = item.get("id", item.get("stripId", str(index + 1)))
        else:
            item = {"value": strip}
            strip_id = str(strip) if strip is not None else str(index + 1)
        item["id"] = str(strip_id)
        result.append(item)
    return result


def cut_guide_lines(x_mm: float, y_mm: float, width_mm: float, height_mm: float,
                    offset_mm: float = 1.0, length_mm: float = 3.0,
                    clearances_mm: Mapping[str, float] | None = None,
                    shared_sides: Iterable[str] = ()) -> list[dict[str, float]]:
    """Return 8 corner crop-mark segments just outside a strip's bounds.

    Coordinates use the layout's top-left origin. The marks do not enter the
    strip rectangle, so they cannot cover design content.
    """
    x, y, width, height = map(float, (x_mm, y_mm, width_mm, height_mm))
    offset, length = float(offset_mm), float(length_mm)
    if offset < 0 or length <= 0:
        raise ValueError("Cut guide offset must be non-negative and length must be positive.")
    clearances = dict(clearances_mm or {})
    shared = set(shared_sides)

    def dimensions(side: str) -> tuple[float, float] | None:
        clearance = clearances.get(side, math.inf)
        try:
            clearance = max(0.0, float(clearance))
        except (TypeError, ValueError, OverflowError):
            return None
        # Two neighboring strips share a gap: each strip may use only its
        # half, so facing marks cannot overlap the neighboring strip or mark.
        maximum_reach = clearance / 2 if side in shared else clearance
        if maximum_reach <= 1e-9:
            return None
        local_offset = min(offset, maximum_reach / 3)
        local_length = min(length, maximum_reach - local_offset)
        if local_length <= 1e-9:
            return None
        return local_offset, local_length

    left_dimensions = dimensions("left")
    right_dimensions = dimensions("right")
    top_dimensions = dimensions("top")
    bottom_dimensions = dimensions("bottom")
    left, right = x, x + width
    top, bottom = y, y + height
    lines: list[tuple[float, float, float, float]] = []
    if left_dimensions:
        local_offset, local_length = left_dimensions
        lines.extend(((left - local_offset - local_length, top, left - local_offset, top),
                      (left - local_offset - local_length, bottom, left - local_offset, bottom)))
    if right_dimensions:
        local_offset, local_length = right_dimensions
        lines.extend(((right + local_offset, top, right + local_offset + local_length, top),
                      (right + local_offset, bottom, right + local_offset + local_length, bottom)))
    if top_dimensions:
        local_offset, local_length = top_dimensions
        lines.extend(((left, top - local_offset - local_length, left, top - local_offset),
                      (right, top - local_offset - local_length, right, top - local_offset)))
    if bottom_dimensions:
        local_offset, local_length = bottom_dimensions
        lines.extend(((left, bottom + local_offset, left, bottom + local_offset + local_length),
                      (right, bottom + local_offset, right, bottom + local_offset + local_length)))
    return [{"x1Mm": _round(x1), "y1Mm": _round(y1), "x2Mm": _round(x2), "y2Mm": _round(y2)}
            for x1, y1, x2, y2 in lines]


def calculate_layout(settings: Mapping[str, Any] | None = None,
                      strips: int | Iterable[Any] | None = None) -> dict[str, Any]:
    """Create deterministic A4 pages and strip placements in physical mm.

    ``strips`` may be a count, identifiers, or dictionaries with an ``id``.
    The final page is partial when needed; every placement keeps the exact
    configured strip dimensions. The returned ``fit`` check describes the
    configured strips-per-page count independent of how many strips exist.
    """
    normalized = normalize_settings(settings)
    strip_records = _normalise_strips(strips)
    fit = calculate_fit(normalized)
    count_per_page = normalized["stripsPerPage"]
    strip_width = normalized["stripWidthMm"]
    strip_height = normalized["stripHeightMm"]
    margin = normalized["marginMm"]
    gap = normalized["gapMm"]

    start_y = margin + (PAGE_HEIGHT_MM - 2 * margin - strip_height) / 2
    pages: list[dict[str, Any]] = []
    for start in range(0, len(strip_records), count_per_page):
        chunk = strip_records[start:start + count_per_page]
        # Center each page's actual group. A partial final page remains the
        # same size but is centered in its own printable area.
        group_width = len(chunk) * strip_width + max(0, len(chunk) - 1) * gap
        start_x = margin + (PAGE_WIDTH_MM - 2 * margin - group_width) / 2
        placements: list[dict[str, Any]] = []
        for position, strip in enumerate(chunk):
            x = start_x + position * (strip_width + gap)
            placement: dict[str, Any] = {
                "stripId": strip["id"],
                "xMm": _round(x),
                "yMm": _round(start_y),
                "widthMm": _round(strip_width),
                "heightMm": _round(strip_height),
            }
            if normalized["cutGuides"]:
                left_clearance = x if position == 0 else gap
                right_clearance = PAGE_WIDTH_MM - (x + strip_width) if position == len(chunk) - 1 else gap
                clearances = {
                    "left": left_clearance,
                    "right": right_clearance,
                    "top": start_y,
                    "bottom": PAGE_HEIGHT_MM - (start_y + strip_height),
                }
                shared_sides = set()
                if position > 0:
                    shared_sides.add("left")
                if position < len(chunk) - 1:
                    shared_sides.add("right")
                placement["cutGuides"] = cut_guide_lines(
                    x, start_y, strip_width, strip_height,
                    clearances_mm=clearances,
                    shared_sides=shared_sides,
                )
            placements.append(placement)
        pages.append({"index": len(pages), "placements": placements})

    return {
        "pageWidthMm": PAGE_WIDTH_MM,
        "pageHeightMm": PAGE_HEIGHT_MM,
        "paper": "A4",
        "orientation": "landscape",
        "settings": normalized,
        "fit": fit,
        "pages": pages,
    }


def mm_to_points(value_mm: float) -> float:
    """Convert millimeters to PDF points at the PDF boundary."""
    return float(value_mm) * PT_PER_MM
