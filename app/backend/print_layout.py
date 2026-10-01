"""Physical A4 placement in millimeters, shared by preview and PDF.

Strip bodies keep their canonical physical dimensions. Only their count and
page positions change when paper, margins, or gaps change. Strips stay upright.
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
    "stripsPerPage": "auto",
    "stripWidthMm": 50.8,
    "stripHeightMm": 152.4,
    "marginMm": 5.0,
    "gapMm": 3.0,
    "cutGuides": True,
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

    Unknown settings are retained for application-specific output options.
    """
    supplied = dict(settings or {})
    normalized = {**DEFAULT_SETTINGS, **supplied}
    if str(normalized.get("paper", "A4")).upper() != "A4":
        raise PrintSettingsError("Only A4 paper is supported.")
    orientation = str(normalized.get("orientation", "landscape")).lower()
    if orientation not in {"landscape", "portrait"}:
        raise PrintSettingsError("A4 orientation must be landscape or portrait.")

    count_value = normalized.get("stripsPerPage")
    if isinstance(count_value, str) and count_value.lower() == "auto":
        normalized["stripsPerPage"] = "auto"
    else:
        if isinstance(count_value, bool):
            raise PrintSettingsError("stripsPerPage must be auto or a positive whole number.")
        try:
            count = int(count_value)
            exact = float(count_value) == count
        except (TypeError, ValueError, OverflowError) as exc:
            raise PrintSettingsError("stripsPerPage must be auto or a positive whole number.") from exc
        if count <= 0 or not exact:
            raise PrintSettingsError("stripsPerPage must be auto or a positive whole number.")
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
    normalized["orientation"] = orientation
    normalized["cutGuides"] = bool(normalized.get("cutGuides", False))
    page_width, page_height = page_dimensions(orientation)
    if normalized["marginMm"] * 2 >= min(page_width, page_height):
        raise PrintSettingsError("Page margins leave no printable area.")
    return normalized


def page_dimensions(orientation: str) -> tuple[float, float]:
    """A4 dimensions without rotating any strip on the page."""
    return (PAGE_HEIGHT_MM, PAGE_WIDTH_MM) if orientation == "portrait" else (PAGE_WIDTH_MM, PAGE_HEIGHT_MM)


def calculate_fit(settings: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Calculate whether the configured strip count fits without scaling."""
    normalized = normalize_settings(settings)
    margin = normalized["marginMm"]
    gap = normalized["gapMm"]
    strip_width = normalized["stripWidthMm"]
    strip_height = normalized["stripHeightMm"]
    requested = normalized["stripsPerPage"]
    page_width, page_height = page_dimensions(normalized["orientation"])
    available_width = page_width - 2 * margin
    available_height = page_height - 2 * margin
    columns = max(0, int(math.floor((available_width + gap + 1e-9) / (strip_width + gap))))
    rows = max(0, int(math.floor((available_height + gap + 1e-9) / (strip_height + gap))))
    maximum = columns * rows
    automatic = requested == "auto"
    effective = maximum if automatic else min(requested, maximum)
    valid = maximum > 0 and (automatic or requested <= maximum)
    count_in_row = columns if automatic else min(requested, max(1, columns))
    required_width = count_in_row * strip_width + max(0, count_in_row - 1) * gap
    required_rows = 1 if automatic else math.ceil(requested / max(1, columns))
    required_height = required_rows * strip_height + max(0, required_rows - 1) * gap
    overflow = max(0.0, required_width - available_width)
    vertical_overflow = max(0.0, required_height - available_height)
    return {
        "valid": valid,
        "auto": automatic,
        "requestedCount": requested,
        "effectiveCount": effective,
        "maximumCount": maximum,
        "columns": columns,
        "rows": rows,
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
    count_per_page = fit["effectiveCount"]
    strip_width = normalized["stripWidthMm"]
    strip_height = normalized["stripHeightMm"]
    margin = normalized["marginMm"]
    gap = normalized["gapMm"]
    page_width, page_height = page_dimensions(normalized["orientation"])
    pages: list[dict[str, Any]] = []
    if count_per_page == 0:
        return {"pageWidthMm": page_width, "pageHeightMm": page_height,
                "paper": "A4", "orientation": normalized["orientation"],
                "settings": normalized, "fit": fit, "pages": pages}
    columns = fit["columns"]
    for start in range(0, len(strip_records), count_per_page):
        chunk = strip_records[start:start + count_per_page]
        row_count = math.ceil(len(chunk) / columns)
        group_height = row_count * strip_height + (row_count - 1) * gap
        start_y = (page_height - group_height) / 2
        placements: list[dict[str, Any]] = []
        for position, strip in enumerate(chunk):
            row, column = divmod(position, columns)
            items_in_row = min(columns, len(chunk) - row * columns)
            group_width = items_in_row * strip_width + (items_in_row - 1) * gap
            x = (page_width - group_width) / 2 + column * (strip_width + gap)
            y = start_y + row * (strip_height + gap)
            placement: dict[str, Any] = {
                "stripId": strip["id"],
                "xMm": _round(x),
                "yMm": _round(y),
                "widthMm": _round(strip_width),
                "heightMm": _round(strip_height),
            }
            if normalized["cutGuides"]:
                left_clearance = x if column == 0 else gap
                right_clearance = page_width - (x + strip_width) if column == items_in_row - 1 else gap
                clearances = {
                    "left": left_clearance,
                    "right": right_clearance,
                    "top": y if row == 0 else gap,
                    "bottom": page_height - (y + strip_height) if row == row_count - 1 else gap,
                }
                shared_sides = set()
                if column > 0:
                    shared_sides.add("left")
                if column < items_in_row - 1:
                    shared_sides.add("right")
                if row > 0:
                    shared_sides.add("top")
                if row < row_count - 1:
                    shared_sides.add("bottom")
                placement["cutGuides"] = cut_guide_lines(
                    x, y, strip_width, strip_height,
                    clearances_mm=clearances,
                    shared_sides=shared_sides,
                )
            placements.append(placement)
        pages.append({"index": len(pages), "placements": placements})

    return {
        "pageWidthMm": page_width,
        "pageHeightMm": page_height,
        "paper": "A4",
        "orientation": normalized["orientation"],
        "settings": normalized,
        "fit": fit,
        "pages": pages,
    }


def mm_to_points(value_mm: float) -> float:
    """Convert millimeters to PDF points at the PDF boundary."""
    return float(value_mm) * PT_PER_MM
