"""High-resolution, lossless composition of editable strip designs."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

try:
    from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFont, ImageOps
    _PIL_ERROR: ImportError | None = None
except ImportError as exc:  # Keep non-image portions of the application importable.
    Image = ImageChops = ImageColor = ImageDraw = ImageFont = ImageOps = None  # type: ignore[assignment]
    _PIL_ERROR = exc

from app.backend.models import default_design
from app.backend.strip_geometry import resolve_frame_layout


def render_strips(
    strips: list[Mapping[str, Any]],
    images: list[Mapping[str, Any]],
    design: Mapping[str, Any] | None,
    settings: Mapping[str, Any],
    output_dir: str | os.PathLike[str],
    *,
    adaptive_frame_layout: bool | None = None,
) -> list[dict[str, Any]]:
    """Render strips one at a time, avoiding a batch of full-resolution images in memory."""
    if Image is None:
        raise RuntimeError(f"Pillow is required to render strips: {_PIL_ERROR}")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    image_map = {str(item.get("id")): item for item in images if item.get("id") is not None}
    resolved_design = dict(design or default_design())
    dpi = _positive(settings.get("resolutionDpi", 300), "resolutionDpi")
    width_mm = _positive(settings.get("stripWidthMm", 50.8), "stripWidthMm")
    height_mm = _positive(settings.get("stripHeightMm", 152.4), "stripHeightMm")
    width_px = max(1, round(width_mm * dpi / 25.4))
    height_px = max(1, round(height_mm * dpi / 25.4))
    if width_px > 40000 or height_px > 40000 or width_px * height_px > 80_000_000:
        raise ValueError("Configured strip size and output resolution exceed the safe render limit.")

    rendered: list[dict[str, Any]] = []
    for strip in strips:
        strip_id = str(strip.get("id") or "")
        if not strip_id:
            raise ValueError("Every strip needs an ID before it can be rendered.")
        strip_design = strip.get("designOverride") if isinstance(strip.get("designOverride"), Mapping) else resolved_design
        key_data = {
            "strip": strip,
            "design": strip_design,
            "widthPx": width_px,
            "heightPx": height_px,
            "adaptiveFrameLayout": adaptive_frame_layout,
            "sourceStats": _source_fingerprints(strip, image_map),
        }
        key = hashlib.sha256(json.dumps(key_data, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")).hexdigest()
        destination = root / f"{key}.png"
        if not destination.is_file() or destination.stat().st_size == 0:
            _render_one(strip, image_map, strip_design, width_px, height_px, destination, dpi,
                        adaptive_frame_layout=adaptive_frame_layout)
        rendered.append({"id": strip_id, "renderPath": str(destination), "path": str(destination)})
    return rendered


def render_one(
    strip: Mapping[str, Any],
    images: list[Mapping[str, Any]],
    design: Mapping[str, Any] | None,
    settings: Mapping[str, Any],
    output_path: str | os.PathLike[str],
    *,
    adaptive_frame_layout: bool | None = None,
) -> dict[str, Any]:
    """Render one strip to a lossless PNG at the requested physical DPI."""
    if Image is None:
        raise RuntimeError(f"Pillow is required to render strips: {_PIL_ERROR}")
    dpi = _positive(settings.get("resolutionDpi", 300), "resolutionDpi")
    width_px = max(1, round(_positive(settings.get("stripWidthMm", 50.8), "stripWidthMm") * dpi / 25.4))
    height_px = max(1, round(_positive(settings.get("stripHeightMm", 152.4), "stripHeightMm") * dpi / 25.4))
    target = Path(output_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    image_map = {str(item.get("id")): item for item in images if item.get("id") is not None}
    _render_one(strip, image_map, dict(design or default_design()), width_px, height_px, target, dpi,
                adaptive_frame_layout=adaptive_frame_layout)
    return {"id": str(strip.get("id") or ""), "renderPath": str(target), "path": str(target)}


def _render_one(strip: Mapping[str, Any], image_map: Mapping[str, Mapping[str, Any]],
                design: Mapping[str, Any], width_px: int, height_px: int, destination: Path, dpi: float,
                *, adaptive_frame_layout: bool | None = None) -> None:
    if Image is None:
        raise RuntimeError(f"Pillow is required to render strips: {_PIL_ERROR}")
    artboard = design.get("artboard") if isinstance(design.get("artboard"), Mapping) else {}
    artboard_width = _positive(artboard.get("width", 1200), "artboard.width")
    artboard_height = _positive(artboard.get("height", 1600), "artboard.height")
    scale_x, scale_y, offset_x, offset_y = artboard_transform(
        width_px, height_px, artboard_width, artboard_height
    )
    board_scale = scale_x
    elements = design.get("elements") if isinstance(design.get("elements"), list) else []
    output = Image.new("RGBA", (width_px, height_px), _rgba("#ffffff"))
    slots = strip.get("slots") if isinstance(strip.get("slots"), list) else []
    loaded: dict[str, Any] = {}
    frame_layout = resolve_frame_layout(
        design, strip, image_map,
        adaptive=True if adaptive_frame_layout is None else bool(adaptive_frame_layout),
    )
    resolved_frames = {
        str(item.get("elementId")): item
        for item in frame_layout.get("frames", [])
        if isinstance(item, Mapping) and item.get("elementId") is not None
    }

    global_pattern = design.get("pattern") if isinstance(design.get("pattern"), Mapping) else {}
    pattern_drawn = False
    for element in elements:
        if not isinstance(element, Mapping) or element.get("visible", True) is False:
            continue
        kind = str(element.get("type", "shape")).lower()
        render_element = dict(element)
        if kind == "image_frame":
            resolved = resolved_frames.get(str(element.get("id")))
            if resolved:
                for key in ("x", "y", "width", "height"):
                    render_element[key] = resolved[key]
        rect = _element_rect(render_element, scale_x, scale_y, width_px, height_px, offset_x, offset_y)
        if rect[2] <= rect[0] or rect[3] <= rect[1]:
            continue
        try:
            render_element["borderWidth"] = float(element.get("borderWidth", 0) or 0) * board_scale
        except (TypeError, ValueError):
            render_element["borderWidth"] = 0
        if kind == "image_frame":
            _draw_image_frame(output, render_element, rect, slots, image_map, loaded, scale_x, scale_y)
        elif kind == "pattern":
            for key in ("spacing", "scale", "offsetX", "offsetY"):
                if key in element:
                    render_element[key] = float(element[key]) * board_scale
            _draw_pattern(output, render_element, rect)
        elif kind == "text":
            _draw_text(output, render_element, rect, scale_x, scale_y)
        else:
            _draw_shape(output, render_element, rect, kind)
        if kind == "background" and not pattern_drawn and global_pattern.get("type", "none") != "none":
            artboard_rect = (
                round(offset_x), round(offset_y),
                min(width_px, round(offset_x + artboard_width * board_scale)),
                min(height_px, round(offset_y + artboard_height * board_scale)),
            )
            _draw_pattern(output, {
                "type": "pattern",
                "patternType": global_pattern.get("type", "dots"),
                "spacing": float(global_pattern.get("spacing", 18)) * board_scale,
                "scale": float(global_pattern.get("scale", 1)) * board_scale,
                "opacity": global_pattern.get("opacity", 0),
                "color": global_pattern.get("color", "#000000"),
                "offsetX": float(global_pattern.get("offsetX", 0)) * board_scale,
                "offsetY": float(global_pattern.get("offsetY", 0)) * board_scale,
            }, artboard_rect)
            pattern_drawn = True

    temporary_name: str | None = None
    try:
        fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.stem}.", suffix=".png", dir=destination.parent)
        os.close(fd)
        rgb = output.convert("RGB")
        try:
            rgb.save(temporary_name, format="PNG", optimize=True, dpi=(float(dpi), float(dpi)))
        finally:
            rgb.close()
        os.replace(temporary_name, destination)
        temporary_name = None
    finally:
        if temporary_name:
            try: os.unlink(temporary_name)
            except OSError: pass
    output.close()
    for image in loaded.values():
        try:
            image.close()
        except Exception:
            pass


def _draw_shape(output: Any, element: Mapping[str, Any], rect: tuple[int, int, int, int], kind: str) -> None:
    if kind not in {"background", "shape", "decoration", "accent", "border", "photo_frame", "frame"}:
        return
    layer = Image.new("RGBA", output.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    fill = _with_opacity(_rgba(element.get("fill", "#ffffff")), element.get("opacity", 1.0))
    radius = max(0, round(float(element.get("borderRadius", 0) or 0) * min(
        (rect[2] - rect[0]) / max(float(element.get("width", 1)), 1),
        (rect[3] - rect[1]) / max(float(element.get("height", 1)), 1),
    )))
    if radius:
        draw.rounded_rectangle(rect, radius=min(radius, max(0, min(rect[2]-rect[0], rect[3]-rect[1]) // 2)), fill=fill)
    else:
        draw.rectangle(rect, fill=fill)
    _draw_border(draw, element, rect)
    output.alpha_composite(layer)
    layer.close()


def _text_font(element: Mapping[str, Any], size: int) -> Any:
    family = str(element.get("fontFamily", "Arial"))
    if family not in {"Arial", "Georgia", "Courier New"}:
        family = "Arial"
    weight = str(element.get("fontWeight", "400")).lower()
    bold = weight == "bold" or (weight.isdigit() and int(weight) >= 600)
    italic = str(element.get("fontStyle", "normal")) == "italic"
    names = {
        ("Arial", False, False): ("arial.ttf", "Arial.ttf"),
        ("Arial", True, False): ("arialbd.ttf", "Arial Bold.ttf"),
        ("Arial", False, True): ("ariali.ttf", "Arial Italic.ttf"),
        ("Arial", True, True): ("arialbi.ttf", "Arial Bold Italic.ttf"),
        ("Georgia", False, False): ("georgia.ttf",),
        ("Georgia", True, False): ("georgiab.ttf",),
        ("Georgia", False, True): ("georgiai.ttf",),
        ("Georgia", True, True): ("georgiaz.ttf",),
        ("Courier New", False, False): ("cour.ttf",),
        ("Courier New", True, False): ("courbd.ttf",),
        ("Courier New", False, True): ("couri.ttf",),
        ("Courier New", True, True): ("courbi.ttf",),
    }
    candidates = list(names.get((family, bold, italic), ()))
    if os.name == "nt":
        windows = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        candidates = [str(windows / item) for item in candidates]
    else:
        portable = {"Arial": "DejaVuSans", "Georgia": "DejaVuSerif", "Courier New": "DejaVuSansMono"}[family]
        candidates.extend((f"{portable}.ttf", "DejaVuSans.ttf"))
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except (OSError, ValueError):
            continue
    try:
        return ImageFont.truetype(family, size=size)
    except (OSError, ValueError):
        return ImageFont.load_default(size=size)


def _text_advance(font: Any, value: str, letter_spacing: float) -> float:
    try:
        width = float(font.getlength(value))
    except (AttributeError, TypeError, ValueError):
        box = font.getbbox(value)
        width = float(box[2] - box[0])
    return width + max(0, len(value) - 1) * letter_spacing


def _wrap_text(text: str, font: Any, width: float, letter_spacing: float) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        line = ""
        for word in words:
            candidate = word if not line else f"{line} {word}"
            if not line or _text_advance(font, candidate, letter_spacing) <= width:
                line = candidate
                continue
            lines.append(line)
            if _text_advance(font, word, letter_spacing) <= width:
                line = word
                continue
            fragment = ""
            for char in word:
                candidate_fragment = fragment + char
                if fragment and _text_advance(font, candidate_fragment, letter_spacing) > width:
                    lines.append(fragment)
                    fragment = char
                else:
                    fragment = candidate_fragment
            line = fragment
        lines.append(line)
    return lines or [""]


def _draw_text(output: Any, element: Mapping[str, Any], rect: tuple[int, int, int, int],
               scale_x: float, scale_y: float) -> None:
    left, top, right, bottom = rect
    available_width, available_height = max(1, right - left), max(1, bottom - top)
    try:
        font_size = max(1, round(float(element.get("fontSize", 64)) * scale_x))
        letter_spacing = float(element.get("letterSpacing", 0)) * scale_x
        line_height = float(element.get("lineHeight", 1.2))
        opacity = float(element.get("opacity", 1.0))
    except (TypeError, ValueError, OverflowError):
        font_size, letter_spacing, line_height, opacity = 64, 0.0, 1.2, 1.0
    font = _text_font(element, font_size)
    color = _with_opacity(_rgba(element.get("fill", element.get("color", "#000000"))), opacity)
    text = str(element.get("text", element.get("content", "Text")))
    lines = _wrap_text(text, font, available_width, letter_spacing)
    line_step = max(1.0, font_size * line_height * (scale_y / max(scale_x, 1e-9)))
    total_height = line_step * len(lines)
    valign = str(element.get("verticalAlign", "middle"))
    y = top if valign == "top" else bottom - total_height if valign == "bottom" else top + (available_height - total_height) / 2
    align = str(element.get("textAlign", "center"))
    layer = Image.new("RGBA", output.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for line in lines:
        width = _text_advance(font, line, letter_spacing)
        x = left if align == "left" else right - width if align == "right" else left + (available_width - width) / 2
        cursor = x
        for index, char in enumerate(line):
            try:
                draw.text((round(cursor), round(y)), char, font=font, fill=color, anchor="lt")
            except (TypeError, ValueError):
                draw.text((round(cursor), round(y)), char, font=font, fill=color)
            cursor += _text_advance(font, char, 0.0)
            if index + 1 < len(line):
                cursor += letter_spacing
        y += line_step
    clip = Image.new("L", output.size, 0)
    ImageDraw.Draw(clip).rectangle((left, top, right - 1, bottom - 1), fill=255)
    # `paste` with an opaque rectangle mask also copies the transparent layer's
    # black RGB values. Mask the layer alpha itself so only glyph pixels are
    # composited and text stays clipped to its element bounds.
    glyph_alpha = layer.getchannel("A")
    alpha = ImageChops.multiply(glyph_alpha, clip)
    glyph_alpha.close()
    layer.putalpha(alpha)
    output.alpha_composite(layer)
    alpha.close()
    clip.close()
    layer.close()


def _draw_image_frame(output: Any, element: Mapping[str, Any], rect: tuple[int, int, int, int],
                      slots: list[Any], image_map: Mapping[str, Mapping[str, Any]], loaded: dict[str, Any],
                      scale_x: float, scale_y: float) -> None:
    _draw_shape(output, element, rect, "frame")
    slot_index = element.get("slotIndex")
    if slot_index is None:
        suffix = "".join(character for character in str(element.get("id", "")) if character.isdigit())
        slot_index = int(suffix) - 1 if suffix else 0
    try:
        slot_index = int(slot_index)
    except (TypeError, ValueError):
        return
    if slot_index < 0 or slot_index >= len(slots) or not isinstance(slots[slot_index], Mapping):
        return
    slot = slots[slot_index]
    image_id = slot.get("imageId")
    image_record = image_map.get(str(image_id)) if image_id is not None else None
    source_path = image_record.get("path") if image_record else None
    if not source_path or not os.path.isfile(os.fspath(source_path)):
        return

    # Frame geometry has already been resolved by ``resolve_frame_layout``.
    # Fitting the source happens only inside that exact frame rectangle.
    inner = _inset(rect, element.get("padding", 0), element, scale_x, scale_y)
    if inner[2] <= inner[0] or inner[3] <= inner[1]:
        return
    key = os.path.normcase(os.path.abspath(os.fspath(source_path)))
    source = loaded.get(key)
    if source is None:
        try:
            with Image.open(source_path) as opened:
                source = ImageOps.exif_transpose(opened).convert("RGBA")
        except (OSError, ValueError):
            return
        loaded[key] = source
    crop = slot.get("crop") if isinstance(slot.get("crop"), Mapping) else {}
    fitted = _fit_image(source, (inner[2] - inner[0], inner[3] - inner[1]), crop, element)
    try:
        opacity = min(1.0, max(0.0, float(element.get("opacity", 1.0))))
    except (TypeError, ValueError):
        opacity = 1.0
    if opacity < 1.0:
        alpha = fitted.getchannel("A")
        try:
            adjusted_alpha = alpha.point(lambda value: round(value * opacity))
            fitted.putalpha(adjusted_alpha)
            adjusted_alpha.close()
        finally:
            alpha.close()
    if float(element.get("borderRadius", 0) or 0) > 0:
        radius = round(float(element["borderRadius"]) * min(
            (rect[2] - rect[0]) / max(float(element.get("width", 1)), 1),
            (rect[3] - rect[1]) / max(float(element.get("height", 1)), 1),
        ))
        clipped = Image.new("RGBA", fitted.size, (0, 0, 0, 0))
        mask = Image.new("L", fitted.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, fitted.width - 1, fitted.height - 1), radius=max(0, min(radius, min(fitted.size) // 2)), fill=255)
        clipped.paste(fitted, (0, 0), mask)
        output.alpha_composite(clipped, (inner[0], inner[1]))
        clipped.close(); mask.close()
    else:
        output.alpha_composite(fitted, (inner[0], inner[1]))
    fitted.close()
    # Repaint the configured border after the image so it remains visible.
    draw = ImageDraw.Draw(output)
    _draw_border(draw, element, rect)


def _ratio_rect(rect: tuple[int, int, int, int], element: Mapping[str, Any], image: Mapping[str, Any]) -> tuple[int, int, int, int]:
    """Compatibility helper for callers that need a ratio-fit rectangle.

    Normal strip rendering no longer uses this function. The canonical layout
    resolves the frame rectangle before image fitting, including Original
    source ratios and mixed-ratio stacks.
    """
    left, top, right, bottom = rect
    box_w, box_h = right - left, bottom - top
    ratio = str(element.get("aspectRatio", "original"))
    if ratio == "1:1": desired = 1.0
    elif ratio == "4:3": desired = 4 / 3
    elif ratio == "16:9": desired = 16 / 9
    else: desired = None
    if desired is None:
        desired = None
        try:
            width = float(image.get("width"))
            height = float(image.get("height"))
            if width > 0 and height > 0 and math.isfinite(width / height): desired = width / height
        except (AttributeError, TypeError, ValueError, OverflowError):
            pass
        if desired is None: return rect
    if box_w / max(box_h, 1) > desired:
        new_w = max(1, round(box_h * desired))
        left += (box_w - new_w) // 2
        right = left + new_w
    else:
        new_h = max(1, round(box_w / desired))
        top += (box_h - new_h) // 2
        bottom = top + new_h
    return left, top, right, bottom


def _fit_image(source: Any, size: tuple[int, int], crop: Mapping[str, Any], element: Mapping[str, Any]) -> Any:
    target_w, target_h = max(1, size[0]), max(1, size[1])
    fallback_mode = "contain" if str(element.get("aspectRatio", "original")) == "original" else "cover"
    mode = str(crop.get("mode", element.get("cropMode", fallback_mode)))
    try:
        zoom = min(4.0, max(1.0, float(crop.get("zoom", 1.0))))
        offset_x = min(1.0, max(-1.0, float(crop.get("offsetX", 0.0))))
        offset_y = min(1.0, max(-1.0, float(crop.get("offsetY", 0.0))))
        if not all(math.isfinite(value) for value in (zoom, offset_x, offset_y)):
            raise ValueError("Crop values must be finite.")
    except (TypeError, ValueError):
        zoom, offset_x, offset_y = 1.0, 0.0, 0.0
    if mode == "contain":
        base = ImageOps.contain(source, (target_w, target_h), method=Image.Resampling.LANCZOS)
        draw_w = max(1, round(base.width * zoom))
        draw_h = max(1, round(base.height * zoom))
        fitted = base.resize((draw_w, draw_h), Image.Resampling.LANCZOS) if (draw_w, draw_h) != base.size else base.copy()
        base.close()
        canvas = Image.new("RGBA", (target_w, target_h), _rgba(element.get("fill", "#ffffff")))
        left = round((target_w - fitted.width) / 2 + offset_x * abs(target_w - fitted.width) / 2)
        top = round((target_h - fitted.height) / 2 + offset_y * abs(target_h - fitted.height) / 2)
        dest_left, dest_top = max(0, left), max(0, top)
        src_left, src_top = max(0, -left), max(0, -top)
        visible_w = min(target_w - dest_left, fitted.width - src_left)
        visible_h = min(target_h - dest_top, fitted.height - src_top)
        if visible_w > 0 and visible_h > 0:
            visible = fitted.crop((src_left, src_top, src_left + visible_w, src_top + visible_h))
            canvas.alpha_composite(visible, (dest_left, dest_top))
            visible.close()
        fitted.close()
        return canvas

    source_w, source_h = source.size
    target_ratio = target_w / target_h
    source_ratio = source_w / source_h
    if source_ratio > target_ratio:
        crop_h = float(source_h)
        crop_w = crop_h * target_ratio
    else:
        crop_w = float(source_w)
        crop_h = crop_w / target_ratio
    crop_w /= zoom
    crop_h /= zoom
    center_x = source_w / 2 + offset_x * max(0.0, source_w - crop_w) / 2
    center_y = source_h / 2 + offset_y * max(0.0, source_h - crop_h) / 2
    left = min(max(0.0, center_x - crop_w / 2), source_w - crop_w)
    top = min(max(0.0, center_y - crop_h / 2), source_h - crop_h)
    region = source.crop((round(left), round(top), round(left + crop_w), round(top + crop_h)))
    fitted = region.resize((target_w, target_h), Image.Resampling.LANCZOS)
    region.close()
    return fitted


def _draw_pattern(output: Any, element: Mapping[str, Any], rect: tuple[int, int, int, int]) -> None:
    pattern_type = str(element.get("patternType", element.get("typeName", "dots"))).lower()
    if pattern_type == "pattern":
        pattern_type = str(element.get("pattern", "dots")).lower()
    if pattern_type == "none":
        return
    opacity = float(element.get("opacity", 1.0) or 0.0)
    spacing = max(2, round(float(element.get("spacing", 24) or 24)))
    radius = max(1, round(float(element.get("scale", 1) or 1)))
    color = _with_opacity(_rgba(element.get("color", element.get("fill", "#000000"))), opacity)
    layer = Image.new("RGBA", output.size, (0, 0, 0, 0)); draw = ImageDraw.Draw(layer)
    left, top, right, bottom = rect
    if pattern_type == "lines":
        offset_x, offset_y = round(float(element.get("offsetX", 0) or 0)), round(float(element.get("offsetY", 0) or 0))
        first_x = left + offset_x % spacing
        for x in range(first_x, right + 1, spacing): draw.line((x, top, x, bottom), fill=color, width=radius)
    elif pattern_type == "grid":
        offset_x, offset_y = round(float(element.get("offsetX", 0) or 0)), round(float(element.get("offsetY", 0) or 0))
        for x in range(left + offset_x % spacing, right + 1, spacing): draw.line((x, top, x, bottom), fill=color, width=radius)
        for y in range(top + offset_y % spacing, bottom + 1, spacing): draw.line((left, y, right, y), fill=color, width=radius)
    else:
        offset_x, offset_y = round(float(element.get("offsetX", 0) or 0)), round(float(element.get("offsetY", 0) or 0))
        for y in range(top + spacing // 2 + offset_y % spacing, bottom, spacing):
            for x in range(left + spacing // 2 + offset_x % spacing, right, spacing):
                draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=color)
    output.alpha_composite(layer); layer.close()


def _draw_border(draw: Any, element: Mapping[str, Any], rect: tuple[int, int, int, int]) -> None:
    try:
        width = max(0, round(float(element.get("borderWidth", 0) or 0)))
    except (TypeError, ValueError):
        width = 0
    if width:
        color = _with_opacity(_rgba(element.get("borderColor", "#000000")), element.get("opacity", 1.0))
        try:
            radius = max(0, round(float(element.get("borderRadius", 0) or 0) * min(
                (rect[2] - rect[0]) / max(float(element.get("width", 1)), 1),
                (rect[3] - rect[1]) / max(float(element.get("height", 1)), 1),
            )))
        except (TypeError, ValueError):
            radius = 0
        for inset in range(width):
            left, top, right, bottom = rect
            if right - left > 2 * inset and bottom - top > 2 * inset:
                bounds = (left + inset, top + inset, right - 1 - inset, bottom - 1 - inset)
                inner_radius = max(0, min(radius - inset, (bounds[2] - bounds[0]) // 2, (bounds[3] - bounds[1]) // 2))
                if inner_radius:
                    draw.rounded_rectangle(bounds, radius=inner_radius, outline=color)
                else:
                    draw.rectangle(bounds, outline=color)


def _element_rect(element: Mapping[str, Any], scale_x: float, scale_y: float,
                  max_width: int, max_height: int,
                  offset_x: float = 0.0, offset_y: float = 0.0) -> tuple[int, int, int, int]:
    try:
        x, y = float(element.get("x", 0)), float(element.get("y", 0))
        width, height = float(element.get("width", 0)), float(element.get("height", 0))
    except (TypeError, ValueError):
        return (0, 0, 0, 0)
    return (max(0, round(offset_x + x * scale_x)), max(0, round(offset_y + y * scale_y)),
            min(max_width, round(offset_x + (x + width) * scale_x)),
            min(max_height, round(offset_y + (y + height) * scale_y)))


def artboard_transform(output_width: int, output_height: int,
                       artboard_width: float, artboard_height: float) -> tuple[float, float, float, float]:
    """Fit the design artboard inside the physical raster without stretching."""
    width = _positive(output_width, "output_width")
    height = _positive(output_height, "output_height")
    board_width = _positive(artboard_width, "artboard_width")
    board_height = _positive(artboard_height, "artboard_height")
    scale = min(width / board_width, height / board_height)
    return scale, scale, (width - board_width * scale) / 2, (height - board_height * scale) / 2


def _inset(rect: tuple[int, int, int, int], padding: Any, element: Mapping[str, Any],
           px_per_unit_x: float, px_per_unit_y: float) -> tuple[int, int, int, int]:
    try:
        pad = max(0.0, float(padding or 0))
    except (TypeError, ValueError):
        pad, px_per_unit_x, px_per_unit_y = 0, 1, 1
    dx, dy = round(pad * px_per_unit_x), round(pad * px_per_unit_y)
    return rect[0] + dx, rect[1] + dy, rect[2] - dx, rect[3] - dy


def _source_fingerprints(strip: Mapping[str, Any], image_map: Mapping[str, Mapping[str, Any]]) -> list[tuple]:
    result = []
    for slot in strip.get("slots", []) if isinstance(strip.get("slots"), list) else []:
        image_id = slot.get("imageId") if isinstance(slot, Mapping) else None
        image = image_map.get(str(image_id)) if image_id is not None else None
        path = image.get("path") if image else None
        if not path:
            result.append((image_id, None)); continue
        try:
            stat = os.stat(path)
            result.append((image_id, os.path.normcase(os.path.abspath(path)), stat.st_size, stat.st_mtime_ns))
        except OSError:
            result.append((image_id, os.path.normcase(os.path.abspath(path)), "missing"))
    return result


def _rgba(value: Any) -> tuple[int, int, int, int]:
    if ImageColor is None:
        return 255, 255, 255, 255
    try:
        return ImageColor.getcolor(str(value or "#ffffff"), "RGBA")
    except (ValueError, TypeError):
        return 255, 255, 255, 255


def _with_opacity(color: tuple[int, int, int, int], opacity: Any) -> tuple[int, int, int, int]:
    try:
        alpha = min(1.0, max(0.0, float(opacity)))
    except (TypeError, ValueError):
        alpha = 1.0
    return color[0], color[1], color[2], round(color[3] * alpha)


def _positive(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be a number.") from exc
    if not 0 < number < float("inf"):
        raise ValueError(f"{label} must be positive and finite.")
    return number


__all__ = ["artboard_transform", "render_one", "render_strips"]
