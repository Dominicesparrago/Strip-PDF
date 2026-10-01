"""Shared geometry renderers for PDF export and page preview.

Strip inputs are dictionaries keyed by the placement's ``stripId``. A strip
must provide a composed raster via ``renderPath`` (or one of the accepted path
aliases) or explicitly set ``blank: true``. Strip composition remains the
strip/template renderer's responsibility; this module places that exact output
at the physical dimensions provided by ``print_layout``.

Runtime dependencies: ReportLab for PDF creation and Pillow for PNG preview.
Both are imported lazily so layout-only use remains available in lean/test
environments. JPEG files are handed to ReportLab by path to avoid an
application-side decode/re-encode pass.
"""

from __future__ import annotations

import base64
import io
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .print_layout import PAGE_HEIGHT_MM, PAGE_WIDTH_MM, calculate_layout, mm_to_points, page_dimensions


_PATH_KEYS = ("renderPath", "renderedPath", "previewPath", "imagePath", "assetPath", "path")


def _strip_records(strips: Any) -> dict[str, dict[str, Any]]:
    if strips is None:
        return {}
    if isinstance(strips, Mapping):
        if any(key in strips for key in ("id", "stripId", *_PATH_KEYS, "slots")):
            item = dict(strips)
            return {str(item.get("id", item.get("stripId", "1"))): item}
        records: dict[str, dict[str, Any]] = {}
        for key, value in strips.items():
            if isinstance(value, Mapping):
                item = dict(value)
                item.setdefault("id", str(key))
            else:
                item = {"id": str(key), "renderPath": value}
            records[str(item.get("id", item.get("stripId", key)))] = item
        return records
    records = {}
    for index, value in enumerate(strips):
        if isinstance(value, Mapping):
            item = dict(value)
        else:
            item = {"renderPath": value}
        item.setdefault("id", str(index + 1))
        records[str(item.get("id", item.get("stripId")))] = item
    return records


def _asset_for(strip: Mapping[str, Any]) -> Any:
    for key in _PATH_KEYS:
        value = strip.get(key)
        if value is not None:
            return value
    value = strip.get("image")
    if isinstance(value, (str, bytes, bytearray, os.PathLike)):
        return value
    return None


def _asset_reader(asset: Any) -> Any:
    from reportlab.lib.utils import ImageReader

    if isinstance(asset, (bytes, bytearray)):
        return ImageReader(io.BytesIO(bytes(asset)))
    path = os.fspath(asset)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    return path


def write_pdf(layout: Mapping[str, Any], strips: Any, output_path: str | os.PathLike[str],
              title: str = "Strip Print Studio", author: str = "Strip Print Studio") -> dict[str, Any]:
    """Write a physical A4 PDF using the supplied shared layout.

    Returns the standard ``{ok, data}`` / ``{ok, error, detail}`` bridge shape.
    Invalid strip-fit geometry blocks PDF output rather than scaling the strips.
    """
    if not isinstance(layout, Mapping):
        return {"ok": False, "error": "A calculated print layout is required."}
    if not bool((layout.get("fit") or {}).get("valid", False)):
        return {"ok": False, "error": "Configured strips do not fit at their physical size.",
                "detail": layout.get("fit", {})}
    pages = layout.get("pages")
    if not isinstance(pages, list) or not pages:
        return {"ok": False, "error": "There are no strips to export."}
    expected_width, expected_height = page_dimensions(str(layout.get("orientation", "landscape")))
    if not _nearly(layout.get("pageWidthMm"), expected_width) or not _nearly(layout.get("pageHeightMm"), expected_height):
        return {"ok": False, "error": "PDF export requires a correctly sized A4 layout."}
    geometry_errors = _layout_geometry_errors(layout)
    if geometry_errors:
        return {"ok": False, "error": "Print geometry would clip a strip or cut guide.",
                "detail": {"placements": geometry_errors}}

    try:
        from reportlab.pdfgen import canvas as pdf_canvas
    except ImportError as exc:
        return {"ok": False, "error": "ReportLab is required for PDF export.",
                "detail": {"dependency": "reportlab", "reason": str(exc)}}

    try:
        target = Path(output_path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        records = _strip_records(strips)
        placements = [placement for page in pages if isinstance(page, Mapping)
                      for placement in page.get("placements", []) if isinstance(placement, Mapping)]
        missing_assets = [str(placement.get("stripId")) for placement in placements
                          if str(placement.get("stripId")) not in records
                          or (_asset_for(records[str(placement.get("stripId"))]) is None
                              and not records[str(placement.get("stripId"))].get("blank"))]
        if missing_assets:
            return {"ok": False, "error": "One or more strips have no rendered image asset.",
                    "detail": {"stripIds": missing_assets}}

        file_descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.stem}.", suffix=".pdf", dir=target.parent)
        os.close(file_descriptor)
        try:
            pdf = pdf_canvas.Canvas(temporary_name,
                                    pagesize=(mm_to_points(expected_width), mm_to_points(expected_height)),
                                    pageCompression=1)
            pdf.setTitle(str(title))
            pdf.setAuthor(str(author))
            pdf.setSubject("A4 photobooth strip print sheet")
            for page in pages:
                pdf.setFillColorRGB(1, 1, 1)
                pdf.rect(0, 0, mm_to_points(expected_width), mm_to_points(expected_height),
                         stroke=0, fill=1)
                for placement in page.get("placements", []):
                    strip_id = str(placement.get("stripId"))
                    strip = records[strip_id]
                    x = float(placement["xMm"])
                    top = float(placement["yMm"])
                    width = float(placement["widthMm"])
                    height = float(placement["heightMm"])
                    if not strip.get("blank"):
                        asset = _asset_reader(_asset_for(strip))
                        pdf.drawImage(asset,
                                      mm_to_points(x),
                                      mm_to_points(expected_height - top - height),
                                      width=mm_to_points(width),
                                      height=mm_to_points(height),
                                      # The raster was rendered for this exact
                                      # physical box. Its integer pixel rounding
                                      # can differ slightly from the box ratio;
                                      # preserving the
                                      # raster ratio here would shrink the
                                      # printed body below its configured size.
                                      preserveAspectRatio=False,
                                      mask="auto")
                    _draw_pdf_guides(pdf, placement.get("cutGuides", []), expected_height)
                pdf.showPage()
            pdf.save()
            os.replace(temporary_name, target)
        except Exception:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise
        return {"ok": True, "data": {
            "path": str(target),
            "pageCount": len(pages),
            "pageWidthMm": expected_width,
            "pageHeightMm": expected_height,
            "stripCount": len(placements),
            "cutGuides": any(bool(p.get("cutGuides")) for p in placements),
        }}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"ok": False, "error": "PDF export failed.",
                "detail": {"reason": str(exc)}}
    except Exception as exc:
        # Keep the PyWebView bridge predictable; preserve a concise reason for
        # diagnostics without exposing a traceback to the frontend.
        return {"ok": False, "error": "PDF export failed.",
                "detail": {"reason": str(exc)}}


def export_pdf(settings: Mapping[str, Any] | None, strips: Any,
               output_path: str | os.PathLike[str], **metadata: Any) -> dict[str, Any]:
    """Calculate layout once, then pass it unchanged to the PDF renderer."""
    try:
        records = _strip_records(strips)
        layout = calculate_layout(settings, list(records.values()))
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": "Print layout settings are invalid.",
                "detail": {"reason": str(exc)}}
    return write_pdf(layout, records, output_path, **metadata)


def render_preview_png(layout: Mapping[str, Any], strips: Any, page_index: int = 0,
                       dpi: float = 96.0, output_path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Render a local PNG preview from the same layout geometry as PDF export.

    The result includes a base64 data URL suitable for a WebView ``img`` source;
    pass ``output_path`` to persist a cache image and receive its path too.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        return {"ok": False, "error": "Pillow is required for PNG preview rendering.",
                "detail": {"dependency": "Pillow", "reason": str(exc)}}
    try:
        pages = layout.get("pages", [])
        if page_index < 0 or page_index >= len(pages):
            raise ValueError("Preview page index is out of range.")
        scale = float(dpi) / 25.4
        if not (scale > 0 and scale < 100):
            raise ValueError("Preview DPI is outside the supported range.")
        page_width_mm = float(layout["pageWidthMm"])
        page_height_mm = float(layout["pageHeightMm"])
        page_width_px = max(1, round(page_width_mm * scale))
        page_height_px = max(1, round(page_height_mm * scale))
        page_image = Image.new("RGB", (page_width_px, page_height_px), "white")
        draw = ImageDraw.Draw(page_image)
        records = _strip_records(strips)
        asset_cache: dict[str, Any] = {}
        page = pages[page_index]
        for placement in page.get("placements", []):
            strip_id = str(placement.get("stripId"))
            if strip_id not in records:
                raise FileNotFoundError(f"Missing strip renderer data for strip {strip_id}.")
            strip = records[strip_id]
            x, top = float(placement["xMm"]), float(placement["yMm"])
            width, height = float(placement["widthMm"]), float(placement["heightMm"])
            x_px, top_px = round(x * scale), round(top * scale)
            width_px, height_px = max(1, round(width * scale)), max(1, round(height * scale))
            if not strip.get("blank"):
                asset = _asset_for(strip)
                if asset is None:
                    raise FileNotFoundError(f"Strip {strip_id} has no rendered image asset.")
                cache_key = str(asset) if not isinstance(asset, (bytes, bytearray)) else f"bytes:{id(asset)}"
                if cache_key not in asset_cache:
                    if isinstance(asset, (bytes, bytearray)):
                        source = Image.open(io.BytesIO(bytes(asset)))
                    else:
                        path = os.fspath(asset)
                        if not os.path.isfile(path):
                            raise FileNotFoundError(path)
                        source = Image.open(path)
                    source.load()
                    asset_cache[cache_key] = source.convert("RGBA")
                source_image = asset_cache[cache_key]
                fitted = source_image.resize((width_px, height_px), Image.Resampling.LANCZOS)
                page_image.paste(fitted, (x_px, top_px), fitted)
            for guide in placement.get("cutGuides", []):
                line_width = max(1, round(0.2 * scale))
                draw.line((round(float(guide["x1Mm"]) * scale), round(float(guide["y1Mm"]) * scale),
                           round(float(guide["x2Mm"]) * scale), round(float(guide["y2Mm"]) * scale)),
                          fill="black", width=line_width)

        output = io.BytesIO()
        page_image.save(output, format="PNG", optimize=True)
        data = output.getvalue()
        result_data: dict[str, Any] = {
            "pageIndex": page_index,
            "pageCount": len(pages),
            "widthPx": page_width_px,
            "heightPx": page_height_px,
            "dpi": float(dpi),
            "dataUrl": "data:image/png;base64," + base64.b64encode(data).decode("ascii"),
        }
        if output_path is not None:
            target = Path(output_path).expanduser().resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            result_data["path"] = str(target)
        return {"ok": True, "data": result_data}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"ok": False, "error": "PDF page preview could not be rendered.",
                "detail": {"reason": str(exc)}}


def _draw_pdf_guides(pdf: Any, guides: Any, page_height_mm: float) -> None:
    if not guides:
        return
    pdf.saveState()
    pdf.setStrokeColorRGB(0, 0, 0)
    pdf.setLineWidth(0.2)
    for guide in guides:
        x1 = mm_to_points(float(guide["x1Mm"]))
        y1 = mm_to_points(page_height_mm - float(guide["y1Mm"]))
        x2 = mm_to_points(float(guide["x2Mm"]))
        y2 = mm_to_points(page_height_mm - float(guide["y2Mm"]))
        pdf.line(x1, y1, x2, y2)
    pdf.restoreState()


def _layout_geometry_errors(layout: Mapping[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    margin = 0.0
    page_width = float(layout.get("pageWidthMm", PAGE_WIDTH_MM))
    page_height = float(layout.get("pageHeightMm", PAGE_HEIGHT_MM))
    settings = layout.get("settings")
    if isinstance(settings, Mapping):
        try:
            margin = max(0.0, float(settings.get("marginMm", 0.0)))
        except (TypeError, ValueError, OverflowError):
            pass
    for page_index, page in enumerate(layout.get("pages", [])):
        if not isinstance(page, Mapping):
            errors.append({"page": page_index, "reason": "invalid_page"})
            continue
        for placement in page.get("placements", []):
            if not isinstance(placement, Mapping):
                errors.append({"page": page_index, "reason": "invalid_placement"})
                continue
            try:
                x = float(placement["xMm"])
                y = float(placement["yMm"])
                width = float(placement["widthMm"])
                height = float(placement["heightMm"])
            except (KeyError, TypeError, ValueError, OverflowError):
                errors.append({"page": page_index, "stripId": placement.get("stripId"),
                               "reason": "invalid_strip_geometry"})
                continue
            if (not all(map(math.isfinite, (x, y, width, height))) or width <= 0 or height <= 0
                    or x < margin - 1e-6 or y < margin - 1e-6
                    or x + width > page_width - margin + 1e-6
                    or y + height > page_height - margin + 1e-6):
                errors.append({"page": page_index, "stripId": placement.get("stripId"),
                               "reason": "strip_clipped", "xMm": x, "yMm": y,
                               "widthMm": width, "heightMm": height})
            for guide_index, guide in enumerate(placement.get("cutGuides", [])):
                try:
                    points = ((float(guide["x1Mm"]), float(guide["y1Mm"])),
                              (float(guide["x2Mm"]), float(guide["y2Mm"])))
                except (KeyError, TypeError, ValueError, OverflowError):
                    errors.append({"page": page_index, "stripId": placement.get("stripId"),
                                   "guideIndex": guide_index, "reason": "invalid_guide_geometry"})
                    continue
                if any(not math.isfinite(px) or not math.isfinite(py) or px < 0 or py < 0
                       or px > page_width or py > page_height for px, py in points):
                    errors.append({"page": page_index, "stripId": placement.get("stripId"),
                                   "guideIndex": guide_index, "reason": "guide_clipped", "points": points})
    return errors


def _nearly(value: Any, expected: float) -> bool:
    try:
        return abs(float(value) - expected) <= 1e-6
    except (TypeError, ValueError, OverflowError):
        return False
