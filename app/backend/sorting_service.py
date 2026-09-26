"""Deterministic image ordering helpers for Strip Print Studio."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterable, Mapping


_NATURAL_PARTS = re.compile(r"(\d+)")
_NUMBER_PART = re.compile(r"(\d+)")
_SUPPORTED_MODES = {"smart", "number", "name_asc", "name_desc", "date", "manual"}


def natural_key(value: str) -> tuple[tuple[int, object], ...]:
    """Split a string into tagged text and integer runs for natural sorting."""
    parts = _NATURAL_PARTS.split(str(value).casefold())
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in parts if part)


def _filename(image: Mapping) -> str:
    name = image.get("name")
    if name:
        return str(name)
    path = str(image.get("path", ""))
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _identity(image: Mapping) -> str:
    return str(image.get("id", image.get("path", _filename(image))))


def _fallback_key(image: Mapping) -> tuple:
    name = _filename(image)
    return natural_key(name), name.casefold(), name, _identity(image).casefold()


def _number_key(image: Mapping) -> tuple:
    name = _filename(image)
    match = _NUMBER_PART.search(name)
    if match:
        return 0, int(match.group(1)), natural_key(name), name.casefold(), _identity(image).casefold()
    return 1, 0, natural_key(name), name.casefold(), _identity(image).casefold()


def _date_value(image: Mapping) -> float | None:
    value = image.get("modifiedAt")
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            # Naive timestamps from old project data are treated as UTC so
            # their ordering remains stable across host time zones.
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def sort_images(
    images: Iterable[Mapping],
    mode: str = "smart",
    manual_order: Iterable[str] | None = None,
) -> list[dict]:
    """Return copied image records in the requested order.

    `date` sorts oldest to newest, with records lacking a reliable date after
    dated records. `manual` honors ids listed in `manual_order`, then appends
    unlisted records in their incoming order. `smart` is a natural filename
    order, including the expected image1, image2, image10 behavior.
    """
    if mode not in _SUPPORTED_MODES:
        raise ValueError(f"Unsupported image sort mode: {mode}")
    records = [dict(image) for image in images]

    if mode == "manual":
        if manual_order is None:
            return records
        rank: dict[str, int] = {}
        for index, image_id in enumerate(manual_order):
            rank.setdefault(str(image_id), index)
        original_index = {id(image): index for index, image in enumerate(records)}
        return sorted(records, key=lambda image: (rank.get(_identity(image), len(rank)), original_index[id(image)]))

    if mode == "smart":
        return sorted(records, key=_fallback_key)
    if mode == "number":
        return sorted(records, key=_number_key)
    if mode == "name_asc":
        return sorted(records, key=lambda image: (_filename(image).casefold(), _filename(image), _identity(image)))
    if mode == "name_desc":
        return sorted(records, key=lambda image: (_filename(image).casefold(), _filename(image), _identity(image)), reverse=True)

    def date_key(image: Mapping) -> tuple:
        value = _date_value(image)
        # Missing or malformed times fall back after valid dates, using a
        # natural name key to keep ordering deterministic.
        return (value is None, value if value is not None else 0.0, *_fallback_key(image))

    return sorted(records, key=date_key)


def reorder_images(images: Iterable[Mapping], ordered_ids: Iterable[str]) -> list[dict]:
    """Apply an explicit drag/drop order and append any omitted image ids."""
    return sort_images(images, mode="manual", manual_order=ordered_ids)


__all__ = ["natural_key", "sort_images", "reorder_images"]
