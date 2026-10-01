from __future__ import annotations

import importlib.util
import re
import tempfile
import time
import unittest
from pathlib import Path

from app.backend.api import StudioAPI
from app.backend.drop_service import extract_dropped_paths


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(
    importlib.util.find_spec("PIL") and importlib.util.find_spec("reportlab"),
    "Pillow and ReportLab are application dependencies",
)
class RealImagePdfWorkflowTests(unittest.TestCase):
    """Exercise real source files through the bridge services and PDF writer."""

    def setUp(self) -> None:
        from PIL import Image

        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.samples = self.root / "photos"
        self.samples.mkdir()
        for index in range(1, 18):
            suffix = {0: ".jpg", 1: ".png", 2: ".webp"}[index % 3]
            image = Image.new("RGB", (1800, 2400), ((index * 31) % 255, (index * 61) % 255, (index * 97) % 255))
            image.save(self.samples / f"image{index}{suffix}")
        (self.samples / "corrupt.jpg").write_bytes(b"not a valid image")
        (self.samples / "unsupported.gif").write_bytes(b"GIF89a")
        self.api = StudioAPI(ROOT, self.root / "app-data")

    def tearDown(self) -> None:
        self.api.close()
        self.temporary.cleanup()

    def call(self, method: str, params: dict | None = None) -> dict:
        response = self.api.invoke(method, params or {})
        self.assertTrue(response["ok"], response)
        return response["data"]

    def call_job(self, method: str, params: dict | None = None) -> dict:
        result = self.call(method, params)
        job_id = result.get("jobId")
        if not job_id:
            return result
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            job = self.call("jobs.get", {"jobId": job_id})
            if job["status"] == "completed":
                return job["result"]
            if job["status"] in {"failed", "cancelled"}:
                self.fail(f"{method} job failed: {job}")
            time.sleep(0.02)
        self.fail(f"{method} job timed out")

    def test_real_import_design_preview_pdf_and_recovery_workflow(self) -> None:
        paths = sorted(self.samples.glob("image*"), key=lambda path: path.name)
        dropped_paths = extract_dropped_paths({"dataTransfer": {"files": [
            {"pywebviewFullPath": str(path)} for path in paths
        ]}})
        self.assertEqual(len(dropped_paths), 17)

        imported = self.call_job("images.import_paths", {
            "paths": [*dropped_paths, str(self.samples / "corrupt.jpg"), str(self.samples / "unsupported.gif")]
        })
        self.assertEqual(len(imported["images"]), 17)
        self.assertEqual({item["extension"] for item in imported["images"]}, {".jpg", ".png", ".webp"})
        self.assertEqual({item["status"] for item in imported["images"]}, {"ready"})
        self.assertEqual(len(imported["errors"]), 2)

        first = self.call("app.get_state")["project"]["images"][0]
        thumb = self.call("images.get_thumbnail", {"imageId": first["id"]})
        self.assertTrue(thumb["dataUrl"].startswith("data:image/webp;base64,"))

        sorted_project = self.call("images.sort", {"mode": "smart"})["project"]
        self.assertEqual([Path(item["name"]).stem for item in sorted_project["images"]], [f"image{i}" for i in range(1, 18)])
        reverse_ids = [item["id"] for item in reversed(sorted_project["images"])]
        reordered = self.call("images.reorder", {"imageIds": reverse_ids})["project"]
        self.assertEqual([item["id"] for item in reordered["images"]], reverse_ids)
        self.call("images.sort", {"mode": "smart"})

        empty = self.call("strips.generate", {"slotsPerStrip": 4, "incomplete": "empty"})
        self.assertEqual(len(empty["strips"]), 5)
        self.assertIsNone(empty["strips"][-1]["slots"][-1]["imageId"])
        duplicate = self.call("strips.generate", {"slotsPerStrip": 4, "incomplete": "duplicate_last"})
        self.assertEqual(len({slot["imageId"] for slot in duplicate["strips"][-1]["slots"]}), 1)
        repeated = self.call("strips.generate", {"slotsPerStrip": 4, "incomplete": "repeat_from_start"})
        self.assertEqual(len(repeated["strips"][-1]["slots"]), 4)
        excluded = self.call("strips.generate", {"slotsPerStrip": 4, "incomplete": "exclude"})
        self.assertEqual(len(excluded["strips"]), 4)
        self.assertEqual(len(excluded["project"]["excludedImageIds"]), 1)

        clean = self.call("templates.load", {"templateId": "dcapture-clean"})["project"]
        self.assertEqual(clean["design"]["artboard"]["width"], 600)
        self.assertEqual(clean["design"]["artboard"]["height"], 1800)
        self.assertEqual(sum(item["type"] == "image_frame" for item in clean["design"]["elements"]), 4)
        background = next(item for item in clean["design"]["elements"] if item["type"] == "background")
        changed = self.call("design.update", {"changes": {
            "elements": [{"id": background["id"], "fill": "#ddeeff"}],
            "pattern": {"type": "dots", "scale": 2, "spacing": 24, "opacity": 0.3, "color": "#334455", "offsetX": 2, "offsetY": 4},
        }})["design"]
        self.assertEqual(next(item for item in changed["elements"] if item["id"] == background["id"])["fill"], "#ddeeff")
        self.assertEqual(changed["pattern"]["spacing"], 24)
        saved_template = self.call("templates.save_as", {"name": "E2E Custom"})["template"]
        loaded_template = self.call("templates.load", {"templateId": saved_template["templateId"]})["project"]
        self.assertEqual(loaded_template["design"]["pattern"]["type"], "dots")
        duplicate_template = self.call("templates.duplicate", {"templateId": saved_template["templateId"]})["template"]
        renamed = self.call("templates.rename", {"templateId": duplicate_template["templateId"], "name": "E2E Renamed"})["template"]
        self.assertEqual(renamed["name"], "E2E Renamed")
        self.assertTrue(self.call("templates.delete", {"templateId": duplicate_template["templateId"]})["deleted"])
        self.call("templates.reset", {})

        grouped = self.call("strips.generate", {"slotsPerStrip": 4, "incomplete": "empty"})
        first_strip = grouped["strips"][0]
        for ratio in ("1:1", "4:3", "16:9", "original"):
            cropped = self.call("strips.set_crop", {"stripId": first_strip["id"], "slotIndex": 0, "crop": {
                "mode": "cover", "ratio": ratio, "zoom": 1.5, "offsetX": 0.25, "offsetY": -0.15,
            }})
            self.assertEqual(cropped["project"]["strips"][0]["slots"][0]["crop"]["ratio"], ratio)
        self.call("design.undo")
        self.call("design.redo")
        self.call("design.update", {"changes": {"elements": [{"id": "photo-1", "locked": False}]}})
        self.call("design.update", {"changes": {"elements": [{"id": "photo-1", "aspectRatio": "1:1"}]}})
        physical = self.call("print.get_layout")["pages"][0]["placements"][0]
        self.assertEqual((physical["widthMm"], physical["heightMm"]), (50.8, 152.4))

        ids = [item["id"] for item in self.call("app.get_state")["project"]["images"]]
        self.call("images.remove", {"imageIds": ids[:4]})
        self.assertEqual(len(self.call("app.get_state")["project"]["images"]), 13)
        self.call("strips.generate", {"slotsPerStrip": 1, "incomplete": "empty"})
        settings = self.call("print.update_settings", {"settings": {
            "paper": "A4", "orientation": "landscape", "stripsPerPage": 5,
            "stripWidthMm": 54, "stripHeightMm": 72, "marginMm": 5, "gapMm": 2,
            "cutGuides": True, "resolutionDpi": 300,
        }})
        self.assertTrue(settings["fit"]["valid"])
        self.assertEqual(settings["fit"]["maximumCount"], 10)
        invalid = self.call("print.update_settings", {"settings": {"stripsPerPage": 11}})
        self.assertFalse(invalid["fit"]["valid"])
        self.assertEqual((invalid["fit"]["requestedCount"], invalid["fit"]["maximumCount"]), (11, 10))
        self.assertAlmostEqual(invalid["fit"]["verticalOverflowMm"], 20.0)
        self.call("print.update_settings", {"settings": {"stripsPerPage": 5}})

        preview = self.call_job("print.preview", {"page": 0, "dpi": 96})["preview"]
        self.assertEqual((preview["widthPx"], preview["heightPx"]), (1123, 794))
        self.assertTrue(preview["dataUrl"].startswith("data:image/png;base64,"))
        last_preview = self.call_job("print.preview", {"page": 2, "dpi": 96})["preview"]
        self.assertEqual(last_preview["page"], 2)
        layout = self.call("print.get_layout")
        self.assertEqual([len(page["placements"]) for page in layout["pages"]], [5, 5, 3])
        self.assertTrue(all(
            (placement["widthMm"], placement["heightMm"]) == (54.0, 72.0)
            for page in layout["pages"] for placement in page["placements"]
        ))
        self.assertTrue(all(placement["cutGuides"] for page in layout["pages"] for placement in page["placements"]))

        output = self.root / "exports" / "a4-e2e.pdf"
        exported = self.call_job("print.export", {"path": str(output)})
        self.assertTrue(output.is_file())
        self.assertEqual((exported["pageCount"], exported["stripCount"]), (3, 13))
        payload = output.read_bytes().decode("latin-1")
        page_box = re.search(r"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", payload)
        self.assertIsNotNone(page_box)
        self.assertAlmostEqual(float(page_box.group(1)), 297 * 72 / 25.4, places=3)
        self.assertAlmostEqual(float(page_box.group(2)), 210 * 72 / 25.4, places=3)
        self.assertEqual(len(re.findall(r"/Type\s*/Page\b", payload)), 3)

        project_path = self.root / "saved.stripproj"
        self.call("project.save_as", {"path": str(project_path)})
        reopened = self.call("project.open", {"path": str(project_path)})["project"]
        self.assertEqual(len(reopened["images"]), 13)
        for mode in ("light", "dark", "system"):
            self.call("theme.set", {"mode": mode})
            self.assertEqual(self.call("app.get_state")["theme"]["mode"], mode)

        self.call("design.update", {"changes": {"elements": [{"id": "background", "fill": "#ccbbaa"}]}})
        self.api.project_service.flush_autosave(self.api._project)
        self.assertTrue(self.call("session.get_recovery")["available"])
        self.api._on_closing()
        restarted = StudioAPI(ROOT, self.root / "app-data")
        try:
            self.assertTrue(restarted.invoke("session.get_recovery", {})["data"]["available"])
            restored = restarted.invoke("session.restore", {})["data"]["project"]
            self.assertEqual(len(restored["images"]), 13)
        finally:
            restarted.close()


if __name__ == "__main__":
    unittest.main()
