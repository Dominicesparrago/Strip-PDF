"""Print readiness validation and source-image resolution estimates."""

from __future__ import annotations

import math
import os
from typing import Any, Iterable, Mapping

from .print_layout import (
    PAGE_HEIGHT_MM,
    PAGE_WIDTH_MM,
    PrintSettingsError,
    calculate_fit,
    normalize_settings,
)


def estimate_effective_dpi(source_width_px: float, source_height_px: float,
                           frame_width_mm: float, frame_height_mm: float,
                           crop: Mapping[str, Any] | None = None) -> float | None:
    """Estimate the limiting effective DPI after a cover crop.

    ``crop`` may provide normalized ``width``/``height`` fractions or a
    ``zoom`` value. This is a conservative estimate for fit/cover behavior;
    actual pixel resampling inside the strip renderer can vary slightly.
    """
    try:
        sw, sh = float(source_width_px), float(source_height_px)
        fw, fh = float(frame_width_mm), float(frame_height_mm)
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(value) for value in (sw, sh, fw, fh)) or min(sw, sh, fw, fh) <= 0:
        return None
    visible_w = visible_h = 1.0
    if crop:
        try:
            visible_w = float(crop.get("width", 1.0))
            visible_h = float(crop.get("height", 1.0))
        except (TypeError, ValueError, OverflowError):
            visible_w = visible_h = 1.0
        zoom = crop.get("zoom")
        if zoom is not None:
            try:
                zoom_value = max(1.0, float(zoom))
                if visible_w >= 1.0:
                    visible_w = 1.0 / zoom_value
                if visible_h >= 1.0:
                    visible_h = 1.0 / zoom_value
            except (TypeError, ValueError, OverflowError):
                pass
    visible_w = min(1.0, max(1e-9, visible_w))
    visible_h = min(1.0, max(1e-9, visible_h))
    return min(sw * visible_w / fw, sh * visible_h / fh) * 25.4


def _records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        # Both keyed maps and one record object are useful at the API boundary.
        if any(key in value for key in ("id", "imageId", "path", "width", "widthPx", "slots")):
            return [dict(value)]
        result = []
        for key, item in value.items():
            if isinstance(item, Mapping):
                record = dict(item)
                record.setdefault("id", str(key))
                result.append(record)
        return result
    try:
        return [dict(item) for item in value if isinstance(item, Mapping)]
    except TypeError:
        return []


def _frame_size(slot: Mapping[str, Any], slot_index: int,
                strip: Mapping[str, Any], template: Mapping[str, Any] | None,
                settings: Mapping[str, Any]) -> tuple[float, float] | None:
    frame = slot.get("frame") if isinstance(slot.get("frame"), Mapping) else {}
    direct_w = slot.get("frameWidthMm", frame.get("widthMm", slot.get("widthMm")))
    direct_h = slot.get("frameHeightMm", frame.get("heightMm", slot.get("heightMm")))
    if direct_w is not None and direct_h is not None:
        try:
            return float(direct_w), float(direct_h)
        except (TypeError, ValueError, OverflowError):
            return None

    strip_w = strip.get("stripWidthMm", settings.get("stripWidthMm"))
    strip_h = strip.get("stripHeightMm", settings.get("stripHeightMm"))
    template_data = template or {}
    override = strip.get("designOverride") if isinstance(strip.get("designOverride"), Mapping) else None
    design = override if override is not None else template_data.get("design", template_data)
    artboard = design.get("artboard", {}) if isinstance(design, Mapping) else {}
    artboard_w = artboard.get("width", artboard.get("widthMm")) if isinstance(artboard, Mapping) else None
    artboard_h = artboard.get("height", artboard.get("heightMm")) if isinstance(artboard, Mapping) else None
    elements = design.get("elements", []) if isinstance(design, Mapping) else []
    photo_frames = [element for element in elements if isinstance(element, Mapping)
                    and any(word in str(element.get("type", "")).lower() for word in ("photo", "frame", "image"))]
    if (artboard_w and artboard_h and slot_index < len(photo_frames)
            and float(artboard_w) > 0 and float(artboard_h) > 0):
        element = photo_frames[slot_index]
        try:
            return (float(element["width"]) / float(artboard_w) * float(strip_w),
                    float(element["height"]) / float(artboard_h) * float(strip_h))
        except (KeyError, TypeError, ValueError, OverflowError):
            pass
    return None


def _dpi_estimates(strips: list[dict[str, Any]], images: list[dict[str, Any]],
                   template: Mapping[str, Any] | None,
                   settings: Mapping[str, Any]) -> tuple[list[dict[str, Any]], int]:
    image_map: dict[str, dict[str, Any]] = {}
    for image in images:
        image_id = image.get("id", image.get("imageId"))
        if image_id is not None:
            image_map[str(image_id)] = image
    estimates: list[dict[str, Any]] = []
    assessed = 0
    for strip in strips:
        slots = strip.get("slots", [])
        if not isinstance(slots, Iterable) or isinstance(slots, (str, bytes, Mapping)):
            continue
        for slot_index, slot_value in enumerate(slots):
            if not isinstance(slot_value, Mapping):
                continue
            slot = dict(slot_value)
            image = slot.get("image") if isinstance(slot.get("image"), Mapping) else None
            image = dict(image) if image else image_map.get(str(slot.get("imageId", "")))
            if image is None:
                continue
            frame = _frame_size(slot, slot_index, strip, template, settings)
            source_w = image.get("widthPx", image.get("width"))
            source_h = image.get("heightPx", image.get("height"))
            if frame is None or source_w is None or source_h is None:
                continue
            dpi = estimate_effective_dpi(source_w, source_h, frame[0], frame[1],
                                          slot.get("crop") if isinstance(slot.get("crop"), Mapping) else None)
            if dpi is None:
                continue
            assessed += 1
            estimates.append({
                "imageId": image.get("id", image.get("imageId", slot.get("imageId"))),
                "stripId": strip.get("id", strip.get("stripId")),
                "slotIndex": slot_index,
                "effectiveDpi": round(dpi, 1),
            })
    return estimates, assessed


def validate_print(layout: Mapping[str, Any] | None,
                   settings: Mapping[str, Any] | None = None,
                   strips: Any = None,
                   images: Any = None,
                   template: Mapping[str, Any] | None = None,
                   dpi_threshold: float = 300.0,
                   incomplete_image_ids: Iterable[Any] | None = None,
                   excluded_image_ids: Iterable[Any] | None = None) -> dict[str, Any]:
    """Return structured PASS/WARNING/ERROR print checks.

    Source image dictionaries should include pixel ``width``/``height`` and
    strip slots should reference them using ``imageId``. For precise DPI checks,
    supply per-slot ``frameWidthMm``/``frameHeightMm`` or a design with an
    artboard and photo-frame elements.
    """
    layout = dict(layout or {})
    settings = dict(settings or layout.get("settings") or {})
    strip_records = _records(strips)
    image_records = _records(images)
    checks: list[dict[str, Any]] = []

    def add(check_id: str, status: str, message: str, detail: Any = None) -> None:
        check = {"id": check_id, "status": status, "message": message}
        if detail is not None:
            check["detail"] = detail
        checks.append(check)

    page_width = layout.get("pageWidthMm", PAGE_WIDTH_MM)
    page_height = layout.get("pageHeightMm", PAGE_HEIGHT_MM)
    orientation = str(layout.get("orientation", settings.get("orientation", "landscape"))).lower()
    expected_width, expected_height = ((PAGE_HEIGHT_MM, PAGE_WIDTH_MM) if orientation == "portrait"
                                       else (PAGE_WIDTH_MM, PAGE_HEIGHT_MM))
    page_ok = (str(layout.get("paper", settings.get("paper", "A4"))).upper() == "A4"
               and orientation in {"landscape", "portrait"}
               and _near(page_width, expected_width) and _near(page_height, expected_height))
    add("page_size", "PASS" if page_ok else "ERROR",
        f"Page is A4 {orientation}." if page_ok else "The print layout must use A4 dimensions.",
        {"pageWidthMm": page_width, "pageHeightMm": page_height})

    try:
        normalized = normalize_settings(settings)
        fit = dict(layout.get("fit") or calculate_fit(normalized))
        add("print_settings", "PASS", "Print settings use valid physical dimensions.")
        add("margin_gap", "PASS", "Page margins and strip gaps are valid.",
            {"marginMm": normalized["marginMm"], "gapMm": normalized["gapMm"]})
    except (PrintSettingsError, TypeError, ValueError) as exc:
        normalized = None
        fit = dict(layout.get("fit") or {})
        add("print_settings", "ERROR", "Print settings are invalid.", {"reason": str(exc)})
        add("margin_gap", "ERROR", "Page margins or strip gaps are invalid.", {"reason": str(exc)})

    if fit:
        fit_ok = bool(fit.get("valid"))
        detail = {key: fit.get(key) for key in (
            "requestedCount", "maximumCount", "availableWidthMm", "requiredWidthMm", "overflowMm",
            "availableHeightMm", "requiredHeightMm", "verticalOverflowMm") if key in fit}
        add("strip_fit", "PASS" if fit_ok else "ERROR",
            "Configured strips fit at their physical size." if fit_ok else
            f"{fit.get('requestedCount', 'Requested')} strips cannot fit while preserving the configured strip size.",
            detail)
    else:
        add("strip_fit", "ERROR", "A fit calculation is unavailable.")

    clipping: list[dict[str, Any]] = []
    pages = layout.get("pages", [])
    margin = normalized["marginMm"] if normalized else 0.0
    for page_index, page in enumerate(pages if isinstance(pages, list) else []):
        if not isinstance(page, Mapping):
            continue
        for placement in page.get("placements", []):
            if not isinstance(placement, Mapping):
                continue
            try:
                x = float(placement["xMm"])
                y = float(placement["yMm"])
                width = float(placement["widthMm"])
                height = float(placement["heightMm"])
            except (KeyError, TypeError, ValueError, OverflowError):
                clipping.append({"page": page_index, "stripId": placement.get("stripId"), "reason": "invalid_geometry"})
                continue
            if (not all(math.isfinite(value) for value in (x, y, width, height))
                    or width <= 0 or height <= 0
                    or x < margin - 1e-6 or y < margin - 1e-6
                    or x + width > float(page_width) - margin + 1e-6
                    or y + height > float(page_height) - margin + 1e-6):
                clipping.append({
                    "page": page_index,
                    "stripId": placement.get("stripId"),
                    "xMm": x, "yMm": y, "widthMm": width, "heightMm": height,
                })
    add("clipping", "ERROR" if clipping else "PASS",
        "All strip placements stay within the printable page area." if not clipping else
        "One or more strip placements clip the printable page area.",
        {"placements": clipping} if clipping else None)

    guide_clipping: list[dict[str, Any]] = []
    for page_index, page in enumerate(pages if isinstance(pages, list) else []):
        if not isinstance(page, Mapping):
            continue
        for placement in page.get("placements", []):
            if not isinstance(placement, Mapping):
                continue
            for guide_index, guide in enumerate(placement.get("cutGuides", [])):
                if not isinstance(guide, Mapping):
                    continue
                try:
                    points = ((float(guide["x1Mm"]), float(guide["y1Mm"])),
                              (float(guide["x2Mm"]), float(guide["y2Mm"])))
                except (KeyError, TypeError, ValueError, OverflowError):
                    guide_clipping.append({"page": page_index, "stripId": placement.get("stripId"),
                                           "guideIndex": guide_index, "reason": "invalid_geometry"})
                    continue
                if any(not math.isfinite(x) or not math.isfinite(y) or x < 0 or y < 0
                       or x > float(page_width) or y > float(page_height) for x, y in points):
                    guide_clipping.append({"page": page_index, "stripId": placement.get("stripId"),
                                           "guideIndex": guide_index, "points": points})
    add("cut_guides", "ERROR" if guide_clipping else "PASS",
        "Cut guides stay within the page." if not guide_clipping else "One or more cut guides clip the page edge.",
        {"guides": guide_clipping} if guide_clipping else None)

    missing: list[dict[str, Any]] = []
    known_image_ids = {str(image.get("id", image.get("imageId"))) for image in image_records
                       if image.get("id", image.get("imageId")) is not None}
    for image in image_records:
        image_id = image.get("id", image.get("imageId"))
        path = image.get("path")
        status = str(image.get("status", "")).lower()
        exists = True if path is None else os.path.isfile(os.fspath(path))
        if status in {"missing", "invalid", "error", "unsupported", "corrupt", "unreadable"} or image.get("valid") is False or not exists:
            missing.append({"imageId": image_id, "path": path, "status": status or "missing"})
    for strip in strip_records:
        slots = strip.get("slots", [])
        if not isinstance(slots, list):
            continue
        for slot_index, slot in enumerate(slots):
            if not isinstance(slot, Mapping):
                continue
            image_id = slot.get("imageId")
            if image_id not in (None, "") and slot.get("image") is None and str(image_id) not in known_image_ids:
                missing.append({"imageId": image_id,
                                "stripId": strip.get("id", strip.get("stripId")),
                                "slotIndex": slot_index,
                                "status": "unresolved_reference"})
    add("source_images", "ERROR" if missing else "PASS",
        "All referenced source images are available." if not missing else
        "One or more source images are missing or invalid.",
        {"images": missing} if missing else {"checked": len(image_records)})

    incomplete: list[dict[str, Any]] = []
    for strip in strip_records:
        slots = strip.get("slots", [])
        empty_indexes = [index for index, slot in enumerate(slots)
                         if slot is None or (isinstance(slot, Mapping)
                                             and slot.get("imageId") in (None, "")
                                             and slot.get("image") is None)] if isinstance(slots, list) else []
        if strip.get("incomplete") or empty_indexes:
            incomplete.append({"stripId": strip.get("id", strip.get("stripId")), "emptySlots": empty_indexes})
    incomplete_sources = list(dict.fromkeys(str(value) for value in (incomplete_image_ids or []) if value not in (None, "")))
    excluded_sources = list(dict.fromkeys(str(value) for value in (excluded_image_ids or []) if value not in (None, "")))
    if incomplete_sources or excluded_sources:
        incomplete.append({"incompleteImageIds": incomplete_sources, "excludedImageIds": excluded_sources})
    add("incomplete_groups", "WARNING" if incomplete else "PASS",
        "Some strips contain empty slots or photos were left out by the grouping policy." if incomplete else "Strip groups are complete.",
        {"groups": incomplete} if incomplete else None)

    if template is not None:
        template_invalid = template.get("valid") is False or str(template.get("status", "")).lower() in {"invalid", "error"}
        add("template", "ERROR" if template_invalid else "PASS",
            "Template data is valid." if not template_invalid else "The selected template is invalid.",
            {"templateId": template.get("id", template.get("templateId"))} if template_invalid else None)
    else:
        add("template", "WARNING", "Template validity was not supplied for validation.")

    estimates, assessed = _dpi_estimates(strip_records, image_records, template, normalized or settings)
    low_resolution = [estimate for estimate in estimates if estimate["effectiveDpi"] < float(dpi_threshold)]
    if low_resolution:
        add("source_resolution", "WARNING", "Some source photos may print below the recommended resolution.", {
            "thresholdDpi": float(dpi_threshold), "images": low_resolution,
        })
    elif assessed:
        add("source_resolution", "PASS", "Estimated source resolution meets the recommended threshold.", {
            "thresholdDpi": float(dpi_threshold), "assessedPlacements": assessed,
        })
    elif image_records:
        add("source_resolution", "WARNING", "Source DPI could not be estimated for the supplied images.", {
            "reason": "Provide source pixel dimensions and per-slot physical frame dimensions.",
        })
    else:
        add("source_resolution", "WARNING", "Source DPI was not assessed because image metadata was not supplied.")

    overall = "ERROR" if any(check["status"] == "ERROR" for check in checks) else (
        "WARNING" if any(check["status"] == "WARNING" for check in checks) else "PASS")
    return {"status": overall, "checks": checks}


def _near(first: Any, second: float, tolerance: float = 1e-6) -> bool:
    try:
        return math.isclose(float(first), float(second), abs_tol=tolerance, rel_tol=0.0)
    except (TypeError, ValueError, OverflowError):
        return False
