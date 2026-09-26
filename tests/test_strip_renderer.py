from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from app.backend.models import default_design
from app.backend.strip_renderer import artboard_transform, render_one


class ArtboardTransformTests(unittest.TestCase):
    def test_custom_physical_ratio_letterboxes_instead_of_stretching_design(self) -> None:
        scale_x, scale_y, offset_x, offset_y = artboard_transform(600, 1000, 1200, 1600)

        self.assertEqual(scale_x, scale_y)
        self.assertEqual((offset_x, offset_y), (0.0, 100.0))
        self.assertEqual((1200 * scale_x, 1600 * scale_y), (600.0, 800.0))


@unittest.skipUnless(importlib.util.find_spec("PIL"), "Pillow is an application dependency")
class StripRendererTests(unittest.TestCase):
    def test_physical_raster_matches_configured_mm_and_dpi(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "photo.png"
            Image.new("RGB", (1600, 900), "red").save(source)
            target = Path(directory) / "strip.png"
            strip = {"id": "test-strip", "slots": [{"imageId": "photo-1", "crop": {"mode": "cover", "zoom": 1, "offsetX": 0, "offsetY": 0}}]}
            images = [{"id": "photo-1", "path": str(source), "width": 1600, "height": 900}]
            render_one(strip, images, default_design(), {"stripWidthMm": 54, "stripHeightMm": 72, "resolutionDpi": 300}, target)
            with Image.open(target) as result:
                self.assertEqual(result.size, (round(54 * 300 / 25.4), round(72 * 300 / 25.4)))
                self.assertEqual(result.mode, "RGB")
                self.assertEqual(result.getpixel((2, 2)), (246, 243, 235))


if __name__ == "__main__":
    unittest.main()
