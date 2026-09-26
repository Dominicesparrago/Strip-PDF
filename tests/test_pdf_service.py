from __future__ import annotations

import importlib.util
import base64
import re
import tempfile
import unittest
import zlib
from pathlib import Path

from app.backend.pdf_service import render_preview_png, write_pdf
from app.backend.print_layout import calculate_layout
from app.backend.validation_service import validate_print


class PdfServiceTests(unittest.TestCase):
    def test_pdf_export_blocks_invalid_fit_without_scaling(self) -> None:
        layout = calculate_layout({"stripsPerPage": 6}, 6)
        result = write_pdf(layout, [], "unused.pdf")

        self.assertFalse(result["ok"])
        self.assertIn("physical size", result["error"])

    def test_pdf_export_blocks_cut_guides_clipped_by_page_edge(self) -> None:
        layout = calculate_layout({"stripsPerPage": 1, "cutGuides": True}, 1)
        layout["pages"][0]["placements"][0]["cutGuides"][0]["x1Mm"] = -0.1
        result = write_pdf(layout, [{"id": "1", "blank": True}], "unused.pdf")

        self.assertFalse(result["ok"])
        self.assertIn("clip", result["error"])

    @unittest.skipUnless(importlib.util.find_spec("reportlab") and importlib.util.find_spec("PIL"), "ReportLab and Pillow are application dependencies")
    def test_pdf_has_a4_landscape_pages_and_partial_last_page(self) -> None:
        settings = {"stripsPerPage": 5, "cutGuides": True}
        layout = calculate_layout(settings, 13)
        strips = [{"id": str(index), "blank": True} for index in range(1, 14)]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "print.pdf"
            result = write_pdf(layout, strips, target)

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["data"]["pageCount"], 3)
            self.assertEqual(result["data"]["stripCount"], 13)
            payload = target.read_bytes().decode("latin-1")
            match = re.search(r"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", payload)
            self.assertIsNotNone(match)
            self.assertAlmostEqual(float(match.group(1)), 297.0 * 72 / 25.4, places=3)
            self.assertAlmostEqual(float(match.group(2)), 210.0 * 72 / 25.4, places=3)
            self.assertEqual(len(re.findall(r"/Type\s*/Page\b", payload)), 3)

    @unittest.skipUnless(importlib.util.find_spec("reportlab") and importlib.util.find_spec("PIL"), "ReportLab and Pillow are application dependencies")
    def test_pdf_places_raster_at_exact_configured_physical_size(self) -> None:
        from PIL import Image

        settings = {"stripsPerPage": 1, "stripWidthMm": 54, "stripHeightMm": 72}
        layout = calculate_layout(settings, 1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "rounded-pixel-ratio.png"
            Image.new("RGB", (638, 850), "white").save(asset)
            target = root / "physical-size.pdf"
            result = write_pdf(layout, [{"id": "1", "renderPath": str(asset)}], target)
            self.assertTrue(result["ok"], result)

            document = target.read_bytes()
            page = next(obj for obj in re.finditer(rb"\d+\s+\d+\s+obj(.*?)endobj", document, re.S)
                        if re.search(rb"/Type\s*/Page\b", obj.group(1)))
            content_id = re.search(rb"/Contents\s+(\d+)\s+\d+\s+R", page.group(1)).group(1)
            content = re.search(rb"\b" + content_id + rb"\s+\d+\s+obj(.*?)stream\r?\n(.*?)endstream",
                                document, re.S)
            self.assertIsNotNone(content)
            filters, stream = content.group(1), content.group(2).rstrip(b"\r\n")
            if b"/ASCII85Decode" in filters:
                stream = base64.a85decode(stream, adobe=True)
            if b"/FlateDecode" in filters:
                stream = zlib.decompress(stream)
            image_matrix = re.search(
                rb"([\d.]+)\s+0\s+0\s+([\d.]+)\s+[\d.]+\s+[\d.]+\s+cm\s+/FormXob\.[^\s]+\s+Do",
                stream,
            )
            self.assertIsNotNone(image_matrix)
            self.assertAlmostEqual(float(image_matrix.group(1)), 54 * 72 / 25.4, places=3)
            self.assertAlmostEqual(float(image_matrix.group(2)), 72 * 72 / 25.4, places=3)

    def test_preview_returns_dependency_error_or_uses_shared_page_geometry(self) -> None:
        layout = calculate_layout({}, 1)
        result = render_preview_png(layout, [{"id": "1", "blank": True}], dpi=96)

        if importlib.util.find_spec("PIL") is None:
            self.assertFalse(result["ok"])
            self.assertIn("Pillow", result["error"])
        else:
            self.assertTrue(result["ok"], result)
            self.assertEqual((result["data"]["widthPx"], result["data"]["heightPx"]), (1123, 794))
            self.assertTrue(result["data"]["dataUrl"].startswith("data:image/png;base64,"))

    def test_validation_returns_six_strip_fit_error_details(self) -> None:
        settings = {"stripsPerPage": 6}
        layout = calculate_layout(settings, 6)
        result = validate_print(layout, settings, strips=[{"id": str(i)} for i in range(6)],
                               template={"id": "builtin", "valid": True})

        self.assertEqual(result["status"], "ERROR")
        fit = next(check for check in result["checks"] if check["id"] == "strip_fit")
        self.assertEqual(fit["status"], "ERROR")
        self.assertEqual(fit["detail"]["requestedCount"], 6)
        self.assertEqual(fit["detail"]["maximumCount"], 5)
        self.assertGreater(fit["detail"]["overflowMm"], 0)

    def test_validation_flags_low_resolution_and_unresolved_images(self) -> None:
        settings = {"stripsPerPage": 1}
        strips = [{"id": "s1", "slots": [{
            "imageId": "missing-photo",
            "frameWidthMm": 50,
            "frameHeightMm": 60,
        }]}]
        layout = calculate_layout(settings, strips)
        images = [{"id": "low", "width": 400, "height": 400, "status": "ok"}]
        strips[0]["slots"][0]["imageId"] = "low"
        result = validate_print(layout, settings, strips, images,
                               template={"id": "builtin", "valid": True})

        dpi = next(check for check in result["checks"] if check["id"] == "source_resolution")
        self.assertEqual(dpi["status"], "WARNING")
        self.assertLess(dpi["detail"]["images"][0]["effectiveDpi"], 300)
        strips[0]["slots"][0]["imageId"] = "missing-photo"
        result = validate_print(layout, settings, strips, images,
                               template={"id": "builtin", "valid": True})
        sources = next(check for check in result["checks"] if check["id"] == "source_images")
        self.assertEqual(sources["status"], "ERROR")

    def test_validation_warns_when_grouping_leaves_or_excludes_source_photos(self) -> None:
        settings = {"stripsPerPage": 1}
        layout = calculate_layout(settings, 1)
        result = validate_print(
            layout,
            settings,
            strips=[{"id": "s1", "slots": [{"imageId": "photo-1"}]}],
            images=[{"id": "photo-1", "width": 2000, "height": 2000}],
            template={"id": "builtin", "valid": True},
            incomplete_image_ids=["photo-2"],
            excluded_image_ids=["photo-2"],
        )

        check = next(item for item in result["checks"] if item["id"] == "incomplete_groups")
        self.assertEqual(check["status"], "WARNING")
        self.assertEqual(check["detail"]["groups"][-1]["incompleteImageIds"], ["photo-2"])
        self.assertEqual(check["detail"]["groups"][-1]["excludedImageIds"], ["photo-2"])


if __name__ == "__main__":
    unittest.main()
