from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from app.backend.models import default_design
from app.backend.strip_geometry import resolve_frame_layout
from app.backend.strip_renderer import _draw_shape, _fit_image, _ratio_rect, artboard_transform, render_one, render_strips
from app.backend.pdf_service import write_pdf
from app.backend.print_layout import calculate_layout


class ArtboardTransformTests(unittest.TestCase):
    def test_custom_physical_ratio_letterboxes_instead_of_stretching_design(self) -> None:
        scale_x, scale_y, offset_x, offset_y = artboard_transform(600, 1000, 1200, 1600)

        self.assertEqual(scale_x, scale_y)
        self.assertEqual((offset_x, offset_y), (0.0, 100.0))
        self.assertEqual((1200 * scale_x, 1600 * scale_y), (600.0, 800.0))


@unittest.skipUnless(importlib.util.find_spec("PIL"), "Pillow is an application dependency")
class StripRendererTests(unittest.TestCase):
    def test_original_source_ratio_resolves_the_frame_and_keeps_full_source_visible(self) -> None:
        design = default_design()
        strip = {"slots": [
            {"imageId": str(index), "crop": {"mode": "contain", "ratio": "original", "zoom": 1,
                                               "offsetX": 0, "offsetY": 0}}
            for index in range(4)
        ]}
        images = [{"id": str(index), "width": 1536, "height": 1024} for index in range(4)]
        layout = resolve_frame_layout(design, strip, images)
        self.assertTrue(all(abs(frame["frameAspectRatio"] - 1.5) < 1e-9 for frame in layout["frames"]))
        self.assertTrue(all(frame["visibleSourceBounds"] == {"left": 0.0, "top": 0.0, "right": 1.0, "bottom": 1.0, "fraction": 1.0}
                          for frame in layout["frames"]))
        self.assertTrue(all(layout["frames"][index]["y"] + layout["frames"][index]["height"]
                            <= layout["frames"][index + 1]["y"] + 1e-9
                            for index in range(3)))

    def test_original_render_keeps_both_horizontal_source_edges_visible(self) -> None:
        from PIL import Image, ImageDraw

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "landscape-1536x1024.png"
            source = Image.new("RGB", (1536, 1024), (0, 210, 0))
            draw = ImageDraw.Draw(source)
            draw.rectangle((0, 0, 511, 1023), fill=(220, 30, 30))
            draw.rectangle((1024, 0, 1535, 1023), fill=(30, 60, 220))
            source.save(source_path)
            source.close()

            strip = {"id": "original-landscape", "slots": [
                {"imageId": "landscape", "crop": {"mode": "contain", "ratio": "original",
                                                       "zoom": 1, "offsetX": 0, "offsetY": 0}}
                for _ in range(4)
            ]}
            images = [{"id": "landscape", "path": str(source_path), "width": 1536, "height": 1024}]
            target = root / "rendered.png"
            render_one(strip, images, default_design(),
                       {"stripWidthMm": 50.8, "stripHeightMm": 152.4, "resolutionDpi": 300}, target)

            layout = resolve_frame_layout(default_design(), strip, images)
            frame = layout["frames"][0]
            with Image.open(target) as rendered:
                self.assertEqual(rendered.size, (600, 1800))
                sample_y = round(frame["y"] + frame["height"] / 2)
                self.assertEqual(rendered.getpixel((round(frame["x"] + 4), sample_y)), (220, 30, 30))
                self.assertEqual(rendered.getpixel((round(frame["x"] + frame["width"] - 5), sample_y)), (30, 60, 220))
                self.assertEqual(rendered.getpixel((round(frame["x"] + frame["width"] / 2), sample_y)), (0, 210, 0))

    def test_ratio_rect_compatibility_helper_uses_source_ratio_for_original(self) -> None:
        frame = (18, 24, 582, 420)
        result = _ratio_rect(frame, {"aspectRatio": "original"}, {"width": 1920, "height": 1280})
        self.assertEqual(result, (18, 34, 582, 410))

    def test_cover_fills_fixed_frame_for_portrait_and_landscape_sources(self) -> None:
        from PIL import Image

        for dimensions in ((30, 60), (60, 30)):
            source = Image.new("RGBA", dimensions, (48, 120, 208, 255))
            result = _fit_image(source, (24, 18), {"mode": "cover", "zoom": 1}, {})
            self.assertEqual(result.size, (24, 18))
            self.assertEqual(result.getpixel((0, 0)), (48, 120, 208, 255))
            self.assertEqual(result.getpixel((23, 17)), (48, 120, 208, 255))
            result.close(); source.close()

    def test_border_stroke_uses_the_element_corner_radius(self) -> None:
        from PIL import Image

        canvas = Image.new("RGBA", (120, 120), (0, 0, 0, 0))
        element = {"x": 0, "y": 0, "width": 100, "height": 100, "fill": "#ff0000",
                   "borderColor": "#000000", "borderWidth": 4, "borderRadius": 24, "opacity": 1}
        _draw_shape(canvas, element, (10, 10, 110, 110), "shape")
        self.assertEqual(canvas.getpixel((10, 10))[3], 0)
        self.assertEqual(canvas.getpixel((60, 10))[:3], (0, 0, 0))
        self.assertEqual(canvas.getpixel((18, 18))[:3], (255, 0, 0))
        canvas.close()

    def test_contain_offset_positions_image_inside_free_space(self) -> None:
        from PIL import Image

        source = Image.new("RGBA", (8, 4), (255, 0, 0, 255))
        for x in range(4, 8):
            for y in range(4): source.putpixel((x, y), (0, 0, 255, 255))
        element = {"cropMode": "contain", "fill": "#ffffff"}
        top = _fit_image(source, (8, 8), {"zoom": 1, "offsetY": -1}, element)
        bottom = _fit_image(source, (8, 8), {"zoom": 1, "offsetY": 1}, element)
        self.assertEqual(top.getpixel((1, 1))[:3], (255, 0, 0))
        self.assertEqual(bottom.getpixel((1, 1))[:3], (255, 255, 255))
        self.assertEqual(bottom.getpixel((1, 6))[:3], (255, 0, 0))
        top.close(); bottom.close(); source.close()

    def test_contain_zoom_scales_and_clips_image_to_frame(self) -> None:
        from PIL import Image

        source = Image.new("RGBA", (8, 4), (20, 100, 220, 255))
        normal = _fit_image(source, (8, 8), {"zoom": 1}, {"cropMode": "contain", "fill": "#ffffff"})
        zoomed = _fit_image(source, (8, 8), {"zoom": 2}, {"cropMode": "contain", "fill": "#ffffff"})
        self.assertEqual(normal.size, (8, 8))
        self.assertEqual(zoomed.size, (8, 8))
        self.assertEqual(normal.getpixel((0, 0))[:3], (255, 255, 255))
        self.assertEqual(zoomed.getpixel((0, 0))[:3], (20, 100, 220))
        normal.close(); zoomed.close(); source.close()

    def test_physical_raster_matches_configured_mm_and_dpi(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "photo.png"
            Image.new("RGB", (1600, 900), "red").save(source)
            target = Path(directory) / "strip.png"
            strip = {"id": "test-strip", "slots": [{"imageId": "photo-1", "crop": {"mode": "cover", "zoom": 1, "offsetX": 0, "offsetY": 0}}]}
            images = [{"id": "photo-1", "path": str(source), "width": 1600, "height": 900}]
            render_one(strip, images, default_design(), {"stripWidthMm": 50.8, "stripHeightMm": 152.4, "resolutionDpi": 300}, target)
            with Image.open(target) as result:
                self.assertEqual(result.size, (round(50.8 * 300 / 25.4), round(152.4 * 300 / 25.4)))
                self.assertEqual(result.mode, "RGB")
                self.assertEqual(result.getpixel((2, 2)), (246, 243, 235))

    def test_structured_text_renders_inside_strip_override_and_flows_into_pdf(self) -> None:
        from PIL import Image

        settings = {"stripWidthMm": 54, "stripHeightMm": 72, "resolutionDpi": 96, "stripsPerPage": 1}
        baseline = {"artboard": {"width": 120, "height": 160}, "elements": [
            {"id": "background", "type": "background", "x": 0, "y": 0, "width": 120, "height": 160, "fill": "#ffffff"},
        ]}
        override = {"artboard": {"width": 120, "height": 160}, "elements": [
            {"id": "background", "syncKey": "background:background", "type": "background", "x": 0, "y": 0, "width": 120, "height": 160, "fill": "#ffffff"},
            {"id": "title", "syncKey": "logical-title", "type": "text", "text": "Foundation Week", "content": "Foundation Week",
             "x": 8, "y": 18, "width": 104, "height": 60, "fontFamily": "Arial", "fontSize": 23,
             "fontWeight": "700", "fontStyle": "normal", "textAlign": "center", "verticalAlign": "middle",
             "fill": "#cc2244", "opacity": 1, "letterSpacing": 0.5, "lineHeight": 1.2},
        ]}
        strips = [{"id": "with-text", "slots": [], "designOverride": override},
                  {"id": "without-text", "slots": [], "designOverride": None}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets = render_strips(strips, [], baseline, settings, root / "renders")
            with Image.open(assets[0]["renderPath"]) as rendered:
                rgb = rendered.convert("RGB")
                red_ink = sum(1 for red, green, blue in rgb.getdata() if red > 120 and red > green * 1.4 and red > blue * 1.1)
                self.assertGreater(red_ink, 20)
                rgb.close()
            with Image.open(assets[1]["renderPath"]) as rendered:
                rgb = rendered.convert("RGB")
                self.assertFalse(any(red > 120 and red > green * 1.4 and red > blue * 1.1 for red, green, blue in rgb.getdata()))
                rgb.close()

            pdf = root / "text-output.pdf"
            exported = write_pdf(calculate_layout(settings, [{"id": "with-text"}]), [{"id": "with-text", "renderPath": assets[0]["renderPath"]}], pdf)
            self.assertTrue(exported["ok"], exported)
            self.assertTrue(pdf.read_bytes().startswith(b"%PDF"))
            self.assertEqual(exported["data"]["pageCount"], 1)


if __name__ == "__main__":
    unittest.main()
