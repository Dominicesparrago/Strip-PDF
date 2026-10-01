"""Shared, JSON-compatible state shapes and defaults."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from .strip_geometry import default_strip_spec, make_1x4_design


SCHEMA_VERSION = 1
DEFAULT_TEMPLATE_ID = "dcapture-clean"

DEFAULT_PRINT_SETTINGS = {
    "paper": "A4",
    "orientation": "landscape",
    "stripsPerPage": "auto",
    "stripWidthMm": 50.8,
    "stripHeightMm": 152.4,
    "marginMm": 5.0,
    "gapMm": 3.0,
    "cutGuides": True,
    "resolutionDpi": 300,
    "outputQuality": "high",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_design() -> dict:
    """Default four-frame layout in canonical 2 by 6 inch design space."""
    return make_1x4_design(default_strip_spec(), template_id=DEFAULT_TEMPLATE_ID)


def new_project() -> dict:
    now = utc_now()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "id": str(uuid4()),
        "name": "Untitled Project",
        "path": None,
        "createdAt": now,
        "updatedAt": now,
        "images": [],
        "imageOrder": [],
        "sortMode": "smart",
        "incompleteHandling": "empty",
        "slotsPerStrip": 4,
        "strips": [],
        "selectedTemplateId": DEFAULT_TEMPLATE_ID,
        "frameLayoutMode": "adaptive",
        "stripSpec": default_strip_spec(),
        "design": default_design(),
        "printSettings": deepcopy(DEFAULT_PRINT_SETTINGS),
        "pdfSettings": {"resolutionDpi": 300, "cutGuides": True},
        "theme": {"mode": "system"},
    }


def clone_json_data(value):
    """Return a detached copy of JSON-compatible data."""
    return deepcopy(value)
