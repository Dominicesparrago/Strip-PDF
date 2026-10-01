from __future__ import annotations

import importlib.util
import tempfile
import time
import unittest
from pathlib import Path

from app.backend.api import StudioAPI
from app.backend.strip_geometry import validate_frame_geometry


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(importlib.util.find_spec("PIL") and importlib.util.find_spec("reportlab"),
                     "Pillow and ReportLab are application dependencies")
class FourSourceGeometryWorkflowTests(unittest.TestCase):
    def call(self, api: StudioAPI, method: str, params: dict | None = None) -> dict:
        response = api.invoke(method, params or {})
        self.assertTrue(response["ok"], response)
        data = response["data"]
        if "jobId" not in data:
            return data
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            job = api.invoke("jobs.get", {"jobId": data["jobId"]})
            self.assertTrue(job["ok"], job)
            record = job["data"]
            if record["status"] == "completed":
                return record["result"]
            if record["status"] in {"failed", "cancelled"}:
                self.fail(f"{method} failed: {record}")
            time.sleep(.03)
        self.fail(f"{method} timed out")

    def test_four_different_source_dimensions_keep_one_physical_strip_and_original_quality(self) -> None:
        from PIL import Image

        dimensions = ((1920, 1280), (4000, 3000), (1080, 1920), (3000, 2000))
        colors = ((224, 32, 40), (26, 166, 65), (40, 80, 220), (230, 177, 30))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = []
            for index, (size, color) in enumerate(zip(dimensions, colors), 1):
                path = root / f"IMG_{index:03}.png"
                Image.new("RGB", size, color).save(path)
                paths.append(path)
            api = StudioAPI(ROOT, root / "app-data")
            try:
                imported = self.call(api, "images.import_paths", {"paths": [str(path) for path in paths]})
                self.assertEqual(len(imported["images"]), 4)
                self.assertEqual([(item["width"], item["height"]) for item in imported["images"]], list(dimensions))
                self.assertTrue(all(item["thumbnail"] for item in imported["images"]))
                for item in imported["images"]:
                    thumbnail = self.call(api, "images.get_thumbnail", {"imageId": item["id"]})
                    self.assertTrue(thumbnail["dataUrl"].startswith("data:image/webp;base64,"))

                generated = self.call(api, "strips.generate", {"slotsPerStrip": 4, "incomplete": "empty"})
                project = generated["project"]
                self.assertEqual(len(generated["strips"]), 1)
                self.assertEqual(len(generated["strips"][0]["slots"]), 4)
                self.assertTrue(all(slot["crop"]["mode"] == "contain" and slot["crop"]["zoom"] == 1
                                    and slot["crop"]["offsetX"] == slot["crop"]["offsetY"] == 0
                                    for slot in generated["strips"][0]["slots"]))
                spec = project["stripSpec"]
                self.assertEqual((spec["widthMm"], spec["heightMm"], spec["designWidth"], spec["designHeight"]),
                                 (50.8, 152.4, 600, 1800))
                frames = validate_frame_geometry(project["design"], spec)
                self.assertEqual(len({(frame["x"], frame["width"], frame["height"]) for frame in frames}), 1)
                resolved = project["resolvedFrameLayouts"][generated["strips"][0]["id"]]
                self.assertEqual([round(frame["frameAspectRatio"], 6) for frame in resolved["frames"]],
                                 [1.5, round(4 / 3, 6), round(1080 / 1920, 6), 1.5])
                self.assertTrue(all(frame["visibleSourceFraction"] == 1 for frame in resolved["frames"]))
                self.assertTrue(all(resolved["frames"][index]["y"] + resolved["frames"][index]["height"]
                                    <= resolved["frames"][index + 1]["y"] + 1e-9
                                    for index in range(3)))

                layout = self.call(api, "print.get_layout")
                placement = layout["pages"][0]["placements"][0]
                self.assertEqual((placement["widthMm"], placement["heightMm"]), (50.8, 152.4))

                # Poison the display cache after import. Export must still use
                # the originals referenced by image.path, never these files.
                for item in imported["images"]:
                    thumb_path = api.image_service._thumbnail_by_id[item["id"]]
                    Image.new("RGB", (64, 64), (0, 0, 0)).save(thumb_path, format="WEBP")

                preview = self.call(api, "print.preview", {"page": 0, "dpi": 96})["preview"]
                self.assertTrue(preview["dataUrl"].startswith("data:image/png;base64,"))

                output = root / "four-source.pdf"
                exported = self.call(api, "print.export", {"path": str(output)})
                self.assertEqual(exported["stripCount"], 1)
                self.assertTrue(output.is_file())
                self.assertTrue(output.read_bytes().startswith(b"%PDF"))

                renders = list((root / "app-data" / "render-cache").glob("*.png"))
                self.assertEqual(len(renders), 1)
                with Image.open(renders[0]) as raster:
                    self.assertEqual(raster.size, (600, 1800))
                    for frame, color in zip(resolved["frames"], colors):
                        x = round(frame["x"] + frame["width"] / 2)
                        y = round(frame["y"] + frame["height"] / 2)
                        self.assertEqual(raster.getpixel((x, y)), color)
            finally:
                api.close()


if __name__ == "__main__":
    unittest.main()
