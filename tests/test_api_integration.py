from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.backend.api import StudioAPI
from app.backend.drop_service import extract_dropped_paths
from app.backend.models import DEFAULT_PRINT_SETTINGS


ROOT = Path(__file__).resolve().parents[1]


class ApiIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.api = StudioAPI(ROOT, self.temp.name)

    def tearDown(self) -> None:
        self.api.close()
        self.temp.cleanup()

    def call(self, method: str, params: dict | None = None) -> dict:
        result = self.api.invoke(method, params or {})
        self.assertTrue(result["ok"], result)
        return result["data"]

    def test_pywebview_drop_payload_extracts_deduplicated_full_paths(self) -> None:
        event = {"dataTransfer": {"files": [
            {"name": "image1.jpg", "pywebviewFullPath": r"C:\Photos\image1.jpg"},
            {"name": "image2.png", "pywebviewFullPath": r"C:\Photos\image2.png"},
            {"name": "duplicate.jpg", "pywebviewFullPath": r"C:\Photos\image1.jpg"},
            {"name": "no-path.jpg"},
        ]}}
        self.assertEqual(extract_dropped_paths(event), [r"C:\Photos\image1.jpg", r"C:\Photos\image2.png"])
        self.assertEqual(extract_dropped_paths({"domTransfer": {"files": [
            {"pywebviewFullPath": r"C:\Photos\legacy.jpg"},
        ]}}), [r"C:\Photos\legacy.jpg"])
        self.assertEqual(extract_dropped_paths({"dataTransfer": {"files": []}}), [])

    def test_initial_state_loads_dcapture_clean_and_explicit_physical_default(self) -> None:
        state = self.call("app.get_state")
        project = state["project"]
        self.assertEqual(project["selectedTemplateId"], "dcapture-clean")
        self.assertEqual(project["printSettings"]["stripWidthMm"], 54.0)
        self.assertEqual(project["printSettings"]["stripHeightMm"], 72.0)
        self.assertEqual(project["design"]["artboard"]["width"], 1200)
        self.assertEqual(project["design"]["artboard"]["height"], 1600)
        self.assertEqual(sum(item["type"] == "image_frame" for item in project["design"]["elements"]), 4)
        self.assertFalse(self.api._has_recoverable_work())

    def test_default_five_strip_layout_and_invalid_six_strip_fit_are_reported(self) -> None:
        layout = self.call("print.get_layout")
        self.assertTrue(layout["fit"]["valid"])
        self.assertEqual(layout["fit"]["maximumCount"], 5)
        updated = self.call("print.update_settings", {"settings": {"stripsPerPage": 6}})
        self.assertFalse(updated["fit"]["valid"])
        self.assertEqual(updated["fit"]["requestedCount"], 6)
        self.assertEqual(updated["fit"]["maximumCount"], 5)
        self.assertGreater(updated["fit"]["overflowMm"], 0)

    def test_design_update_undo_redo_and_theme_mode(self) -> None:
        design = self.call("design.get")["design"]
        background = design["elements"][0]
        self.call("design.update", {"label": "Set background", "changes": {"elements": [{"id": background["id"], "fill": "#112233"}]}})
        self.assertEqual(self.call("design.get")["design"]["elements"][0]["fill"], "#112233")
        self.call("design.undo")
        self.assertEqual(self.call("design.get")["design"]["elements"][0]["fill"], "#f6f3eb")
        self.call("design.redo")
        self.assertEqual(self.call("design.get")["design"]["elements"][0]["fill"], "#112233")
        theme = self.call("theme.set", {"mode": "dark"})
        self.assertEqual(theme, {"mode": "dark", "effective": "dark"})
        self.assertEqual(self.call("app.get_state")["project"]["theme"]["mode"], "dark")

    def test_theme_preference_survives_new_project_and_application_restart(self) -> None:
        self.call("theme.set", {"mode": "dark"})
        self.call("project.new")
        self.assertEqual(self.call("app.get_state")["project"]["theme"]["mode"], "dark")
        self.assertFalse(self.api.project_service.is_dirty)

        self.api.close()
        restarted = StudioAPI(ROOT, self.temp.name)
        try:
            self.assertEqual(restarted.invoke("app.get_state", {})["data"]["theme"]["mode"], "dark")
        finally:
            restarted.close()

    def test_deleting_the_active_custom_template_keeps_design_valid(self) -> None:
        custom = self.call("templates.save_as", {"name": "Active custom"})["template"]
        self.call("templates.load", {"templateId": custom["templateId"]})
        deleted = self.call("templates.delete", {"templateId": custom["templateId"]})
        self.assertTrue(deleted["deleted"])
        self.assertEqual(deleted["project"]["selectedTemplateId"], "dcapture-clean")
        validation = self.call("print.validate")["validation"]
        template_check = next(check for check in validation["checks"] if check["id"] == "template")
        self.assertNotEqual(template_check["status"], "ERROR")

    def test_clean_saved_shutdown_does_not_offer_recovery(self) -> None:
        path = Path(self.temp.name) / "clean-project.stripproj"
        self.call("project.save_as", {"path": str(path)})
        self.api._on_closing()
        self.assertEqual(self.call("session.get_recovery"), {"available": False})

    def test_add_decoration_and_pattern_elements_with_history(self) -> None:
        before = len(self.call("design.get")["design"]["elements"])
        added = self.call("design.add_element", {"type": "pattern", "patternType": "grid"})
        self.assertEqual(added["element"]["type"], "pattern")
        self.assertEqual(added["element"]["patternType"], "grid")
        self.assertEqual(len(added["design"]["elements"]), before + 1)
        self.call("design.undo")
        self.assertEqual(len(self.call("design.get")["design"]["elements"]), before)
        self.call("design.redo")
        self.assertEqual(len(self.call("design.get")["design"]["elements"]), before + 1)
        duplicate = self.call("design.update", {"changes": {"duplicateElementId": "photo-3"}})
        copied_frame = duplicate["design"]["elements"][-1]
        self.assertEqual(copied_frame["type"], "image_frame")
        self.assertEqual(copied_frame["slotIndex"], 2)
        invalid = self.api.invoke("design.add_element", {"type": "image_frame"})
        self.assertFalse(invalid["ok"])

    def test_grouping_empty_slots_preserves_strip_physical_settings(self) -> None:
        project = self.api._project
        project["images"] = [{"id": f"img-{index}", "path": f"missing-{index}.jpg", "name": f"image{index}.jpg", "width": 2000, "height": 1000, "status": "ready"} for index in range(1, 6)]
        project["imageOrder"] = [image["id"] for image in project["images"]]
        self.api._commit_project(project)
        grouped = self.call("strips.generate", {"slotsPerStrip": 4, "incomplete": "empty"})
        self.assertEqual(len(grouped["strips"]), 2)
        self.assertIsNone(grouped["strips"][1]["slots"][1]["imageId"])
        layout = self.call("print.get_layout")
        self.assertEqual(layout["pages"][0]["placements"][1]["widthMm"], 54.0)
        self.assertEqual(layout["pages"][0]["placements"][1]["heightMm"], 72.0)

    def test_internal_frame_ratio_change_keeps_physical_strip_body(self) -> None:
        project = self.api._project
        project["strips"] = [{"id": "strip-1", "slots": []}]
        self.api._commit_project(project)
        self.call("design.update", {"changes": {"elements": [{"id": "photo-1", "aspectRatio": "1:1"}]}})
        layout = self.call("print.get_layout")
        placement = layout["pages"][0]["placements"][0]
        self.assertEqual((placement["widthMm"], placement["heightMm"]), (54.0, 72.0))

    def test_crop_changes_participate_in_design_undo_redo(self) -> None:
        project = self.api._project
        project["images"] = [{"id": "img-1", "name": "image1.jpg", "path": "missing.jpg", "width": 1200, "height": 800, "status": "missing"}]
        project["imageOrder"] = ["img-1"]
        self.api._commit_project(project)
        grouped = self.call("strips.generate", {"slotsPerStrip": 1, "incomplete": "empty"})
        strip_id = grouped["strips"][0]["id"]
        self.call("strips.set_crop", {"stripId": strip_id, "slotIndex": 0, "crop": {"zoom": 2, "offsetX": 0.5}})
        self.assertEqual(self.api._project["strips"][0]["slots"][0]["crop"]["zoom"], 2)
        self.call("design.undo")
        self.assertEqual(self.api._project["strips"][0]["slots"][0]["crop"]["zoom"], 1)
        self.call("design.redo")
        self.assertEqual(self.api._project["strips"][0]["slots"][0]["crop"]["zoom"], 2)

    def test_resolution_validation_uses_the_active_design_geometry(self) -> None:
        project = self.api._project
        project["images"] = [{"id": "low", "name": "image1.jpg", "path": None, "width": 500, "height": 500, "status": "ready"}]
        project["strips"] = [{"id": "strip-1", "slots": [{"imageId": "low", "crop": {"zoom": 1}}]}]
        self.api._commit_project(project)
        self.call("design.update", {"changes": {"elements": [{"id": "photo-1", "width": 10, "height": 10}]}})

        validation = self.call("print.validate")["validation"]
        resolution = next(item for item in validation["checks"] if item["id"] == "source_resolution")
        self.assertEqual(resolution["status"], "PASS")

    def test_saved_custom_template_keeps_physical_metadata_without_project_photos(self) -> None:
        saved = self.call("templates.save_as", {"name": "Custom print strip"})["template"]
        self.assertEqual(saved["physical"], {"widthMm": 54.0, "heightMm": 72.0})
        self.assertEqual(saved["slotsPerStrip"], 4)
        self.assertNotIn("images", saved)
        loaded = self.call("templates.load", {"templateId": saved["templateId"]})
        self.assertEqual(loaded["project"]["printSettings"]["stripWidthMm"], 54.0)

    def test_project_save_open_and_recovery_round_trip(self) -> None:
        path = Path(self.temp.name) / "roundtrip.stripproj"
        saved = self.call("project.save_as", {"path": str(path)})
        self.assertTrue(path.is_file())
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(document["kind"], "project")
        reopened = self.call("project.open", {"path": str(path)})
        self.assertEqual(reopened["project"]["selectedTemplateId"], "dcapture-clean")
        project = self.api._project
        project["name"] = "Recovery test"
        self.api.project_service.autosave(project)
        self.assertTrue(self.call("session.get_recovery")["available"])
        restored = self.call("session.restore")
        self.assertEqual(restored["project"]["name"], "Recovery test")

    def test_api_rejects_unknown_methods_as_structured_errors(self) -> None:
        result = self.api.invoke("python.exec", {})
        self.assertFalse(result["ok"])
        self.assertIn("Unsupported method", result["error"])


if __name__ == "__main__":
    unittest.main()
