"""Shared, JSON-compatible state shapes and defaults."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4


SCHEMA_VERSION = 1
DEFAULT_TEMPLATE_ID = "dcapture-clean"

DEFAULT_PRINT_SETTINGS = {
    "paper": "A4",
    "orientation": "landscape",
    "stripsPerPage": 5,
    "stripWidthMm": 54.0,
    "stripHeightMm": 72.0,
    "marginMm": 5.0,
    "gapMm": 2.0,
    "cutGuides": False,
    "resolutionDpi": 300,
    "outputQuality": "high",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_design() -> dict:
    """Editable geometry faithful to DCapture's current 1200x1600 result sheet."""
    width, height, gap, slots = 1200.0, 1600.0, 18.0, 4
    frame_width = width - gap * 2
    frame_height = (height - gap * (slots + 1)) / slots
    elements = [
        {
            "id": "background",
            "type": "background",
            "x": 0.0,
            "y": 0.0,
            "width": width,
            "height": height,
            "fill": "#f6f3eb",
            "opacity": 1.0,
            "visible": True,
            "locked": False,
        }
    ]
    for index in range(slots):
        elements.append(
            {
                "id": f"photo-{index + 1}",
                "type": "image_frame",
                "x": gap,
                "y": gap + index * (frame_height + gap),
                "width": frame_width,
                "height": frame_height,
                "fill": "#ffffff",
                "borderColor": "#00000000",
                "borderWidth": 0.0,
                "borderRadius": 0.0,
                "opacity": 1.0,
                "padding": 0.0,
                "visible": True,
                "locked": False,
                "aspectRatio": "original",
                "cropMode": "cover",
                "slotIndex": index,
            }
        )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "templateId": DEFAULT_TEMPLATE_ID,
        "artboard": {"width": width, "height": height, "widthMm": 54.0, "heightMm": 72.0},
        "elements": elements,
        "pattern": {
            "type": "none",
            "scale": 1.0,
            "spacing": 18.0,
            "opacity": 0.0,
            "color": "#f6f3eb",
            "offsetX": 0.0,
            "offsetY": 0.0,
        },
        "snap": True,
    }


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
        "design": default_design(),
        "printSettings": deepcopy(DEFAULT_PRINT_SETTINGS),
        "pdfSettings": {"resolutionDpi": 300, "cutGuides": False},
        "theme": {"mode": "system"},
    }


def clone_json_data(value):
    """Return a detached copy of JSON-compatible data."""
    return deepcopy(value)
