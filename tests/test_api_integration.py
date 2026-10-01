from __future__ import annotations

import json
import re
import tempfile
import time
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

    def make_strips(self, count: int) -> list[dict]:
        project = self.api._project
        project["images"] = [{"id": f"sync-image-{index}", "path": None, "name": f"IMG_{index:03}.jpg",
                              "width": 1200, "height": 800, "status": "ready"}
                             for index in range(count)]
        project["imageOrder"] = [image["id"] for image in project["images"]]
        self.api._commit_project(project)
        return self.call("strips.generate", {"slotsPerStrip": 1, "incomplete": "empty"})["strips"]

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
        self.assertEqual(project["stripSpec"]["presetId"], "photobooth-2x6")
        self.assertEqual(project["printSettings"]["stripWidthMm"], 50.8)
        self.assertEqual(project["printSettings"]["stripHeightMm"], 152.4)
        self.assertEqual(project["design"]["artboard"]["width"], 600)
        self.assertEqual(project["design"]["artboard"]["height"], 1800)
        self.assertEqual(sum(item["type"] == "image_frame" for item in project["design"]["elements"]), 4)
        self.assertFalse(any(item["type"] == "text" for item in project["design"]["elements"]))
        self.assertFalse(self.api._has_recoverable_work())

    def test_functional_frontend_has_no_native_browser_dialog_calls(self) -> None:
        frontend = ROOT / "frontend"
        sources = [*frontend.rglob("*.html"), *frontend.rglob("*.js"), *frontend.rglob("*.mjs")]
        native_dialog_call = re.compile(r"\b(?:(?:window|globalThis)\.)?(?:alert|confirm|prompt)\s*\(")
        for path in sources:
            source = path.read_text(encoding="utf-8")
            self.assertNotRegex(source, native_dialog_call, str(path.relative_to(ROOT)))

    def test_published_project_dirty_state_tracks_import_edits_saves_apply_all_and_open(self) -> None:
        from PIL import Image

        self.assertFalse(self.call("app.get_state")["project"]["isDirty"])
        self.call("project.new")
        self.assertFalse(self.call("app.get_state")["project"]["isDirty"])
        image_path = Path(self.temp.name) / "dirty-state.jpg"
        Image.new("RGB", (32, 24), "navy").save(image_path)
        queued = self.call("images.import_paths", {"paths": [str(image_path)]})
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            job = self.call("jobs.get", {"jobId": queued["jobId"]})
            if job["status"] == "completed":
                break
            if job["status"] == "failed":
                self.fail(f"Image import failed while testing dirty state: {job['error']}")
            time.sleep(0.02)
        else:
            self.fail("Image import timed out while testing dirty state")
        self.assertTrue(self.call("app.get_state")["project"]["isDirty"])

        project_path = Path(self.temp.name) / "dirty-state.stripproj"
        self.call("project.save_as", {"path": str(project_path)})
        self.assertFalse(self.call("app.get_state")["project"]["isDirty"])
        background = self.call("design.get")["design"]["elements"][0]
        self.call("design.update", {"changes": {"elements": [{"id": background["id"], "fill": "#143255"}]}})
        self.assertTrue(self.call("app.get_state")["project"]["isDirty"])
        self.call("project.save")
        self.assertFalse(self.call("app.get_state")["project"]["isDirty"])

        self.call("print.update_settings", {"settings": {"cutGuides": True}})
        self.assertTrue(self.call("app.get_state")["project"]["isDirty"])
        self.call("project.save")
        self.assertFalse(self.call("app.get_state")["project"]["isDirty"])

        strips = self.make_strips(2)
        self.call("project.save_as", {"path": str(Path(self.temp.name) / "two-strips.stripproj")})
        text = self.call("design.add_element", {"elementType": "text", "stripId": strips[0]["id"]})["element"]
        self.call("project.save")
        self.assertFalse(self.call("app.get_state")["project"]["isDirty"])
        self.call("design.apply_to_all", {"stripId": strips[0]["id"], "elementId": text["id"]})
        self.assertTrue(self.call("app.get_state")["project"]["isDirty"])

        self.call("project.open", {"path": str(Path(self.temp.name) / "two-strips.stripproj")})
        self.assertFalse(self.call("app.get_state")["project"]["isDirty"])

    def test_image_state_exposes_ordered_display_metadata_without_replacing_internal_identity(self) -> None:
        project = self.api._project
        project["images"] = [
            {"id": "internal-landscape", "path": "C:/Photos/IMG_002.jpg", "name": "IMG_002.jpg", "width": 1200, "height": 800},
            {"id": "internal-portrait", "path": "C:/Photos/IMG_001.jpg", "name": "IMG_001.jpg", "width": 800, "height": 1200},
            {"id": "internal-square", "path": "C:/Photos/IMG_003.jpg", "name": "IMG_003.jpg", "width": 500, "height": 500},
            {"id": "internal-wide", "path": "C:/Photos/pano.jpg", "name": "pano.jpg", "width": 2400, "height": 300},
        ]
        project["imageOrder"] = ["internal-portrait", "internal-landscape", "internal-square", "internal-wide"]
        self.api._commit_project(project)
        state = self.api.invoke("app.get_state", {})["data"]["project"]
        images = state["images"]
        self.assertEqual([image["id"] for image in images], project["imageOrder"])
        self.assertEqual([image["orderIndex"] for image in images], [0, 1, 2, 3])
        self.assertEqual([image["orientation"] for image in images], ["portrait", "landscape", "square", "landscape"])
        self.assertEqual(images[0]["displayName"], "IMG_001.jpg")
        self.assertEqual(images[0]["internalId"], "internal-portrait")
        self.assertEqual(images[3]["previewFit"], "contain")

    def test_default_five_strip_layout_and_invalid_six_strip_fit_are_reported(self) -> None:
        layout = self.call("print.get_layout")
        self.assertTrue(layout["fit"]["valid"])
        self.assertEqual(layout["fit"]["maximumCount"], 5)
        updated = self.call("print.update_settings", {"settings": {"stripsPerPage": 6}})
        self.assertFalse(updated["fit"]["valid"])
        self.assertEqual(updated["fit"]["requestedCount"], 6)
        self.assertEqual(updated["fit"]["maximumCount"], 5)
        self.assertGreater(updated["fit"]["verticalOverflowMm"], 0)

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

    def test_application_state_publishes_undo_redo_availability_transitions(self) -> None:
        state = self.call("app.get_state")
        self.assertFalse(state["history"]["canUndo"])
        self.assertFalse(state["history"]["canRedo"])
        background = state["project"]["design"]["elements"][0]
        changed = self.call("design.update", {"changes": {"elements": [{"id": background["id"], "fill": "#123456"}]}})
        self.assertTrue(changed["history"]["canUndo"])
        self.assertFalse(changed["history"]["canRedo"])
        undone = self.call("design.undo")
        self.assertFalse(undone["history"]["canUndo"])
        self.assertTrue(undone["history"]["canRedo"])
        redone = self.call("design.redo")
        self.assertTrue(redone["history"]["canUndo"])
        self.assertFalse(redone["history"]["canRedo"])

    def test_locked_elements_reject_edits_and_can_be_unlocked(self) -> None:
        background = self.call("design.get")["design"]["elements"][0]
        self.call("design.update", {"changes": {"elements": [{"id": background["id"], "locked": True}]}})
        for changes in (
            {"elements": [{"id": background["id"], "fill": "#ff0000"}]},
            {"deleteElementId": background["id"]},
            {"duplicateElementId": background["id"]},
            {"resetElementId": background["id"]},
        ):
            response = self.api.invoke("design.update", {"changes": changes})
            self.assertFalse(response["ok"], changes)
        self.call("design.update", {"changes": {"elements": [{"id": background["id"], "locked": False}]}})
        edited = self.call("design.update", {"changes": {"elements": [{"id": background["id"], "fill": "#ff0000"}]}})
        self.assertEqual(next(item for item in edited["design"]["elements"] if item["id"] == background["id"])["fill"], "#ff0000")

    def test_locked_image_frame_allows_photo_crop_but_rejects_geometry_edit(self) -> None:
        project = self.api._project
        project["images"] = [{"id": "img-1", "path": "missing.jpg", "name": "photo.jpg", "width": 1200, "height": 800, "status": "missing"}]
        project["imageOrder"] = ["img-1"]
        self.api._commit_project(project)
        strip = self.call("strips.generate", {"slotsPerStrip": 1, "incomplete": "empty"})["strips"][0]
        frame = next(item for item in self.api._project["design"]["elements"] if item["id"] == "photo-1")
        if not frame.get("locked"):
            self.call("design.update", {"changes": {"elements": [{"id": "photo-1", "locked": True}]}})
        response = self.api.invoke("strips.set_crop", {"stripId": strip["id"], "slotIndex": 0,
            "elementId": "photo-1", "crop": {"zoom": 2}})
        self.assertTrue(response["ok"])
        self.assertEqual(response["data"]["strip"]["slots"][0]["crop"]["zoom"], 2)
        geometry = self.api.invoke("design.update", {"changes": {"elements": [{"id": "photo-1", "width": 100}]}})
        self.assertFalse(geometry["ok"])

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
        self.call("design.update", {"changes": {"elements": [{"id": "photo-3", "locked": False}]}})
        duplicate = self.call("design.update", {"changes": {"duplicateElementId": "photo-3"}})
        copied_frame = duplicate["design"]["elements"][-1]
        self.assertEqual(copied_frame["type"], "image_frame")
        self.assertEqual(copied_frame["slotIndex"], 2)
        invalid = self.api.invoke("design.add_element", {"type": "image_frame"})
        self.assertFalse(invalid["ok"])

    def test_text_elements_edit_reset_duplicate_and_persist_in_custom_template(self) -> None:
        strip = self.make_strips(1)[0]
        added = self.call("design.add_element", {"elementType": "text", "stripId": strip["id"]})
        text = added["element"]
        self.assertEqual((text["type"], text["text"], text["content"]), ("text", "Text", "Text"))
        self.assertTrue(text["syncKey"])
        self.assertAlmostEqual(text["x"] + text["width"] / 2, 300)
        self.assertAlmostEqual(text["y"] + text["height"] / 2, 900)

        changed = self.call("design.update", {"stripId": strip["id"], "changes": {"elements": [{
            "id": text["id"], "text": "Foundation Week", "fill": "#2244aa", "fontSize": 78,
            "fontWeight": "700", "fontStyle": "italic", "fontFamily": "Georgia",
            "textAlign": "right", "verticalAlign": "bottom", "letterSpacing": 2.5,
            "lineHeight": 1.35, "x": 31, "y": 42, "width": 710, "height": 120,
        }]}})
        edited = next(item for item in changed["design"]["elements"] if item["id"] == text["id"])
        self.assertEqual((edited["text"], edited["content"], edited["fill"]), ("Foundation Week", "Foundation Week", "#2244aa"))
        self.assertEqual((edited["fontSize"], edited["fontWeight"], edited["textAlign"], edited["verticalAlign"]), (78, "700", "right", "bottom"))

        duplicate = self.call("design.update", {"stripId": strip["id"], "changes": {"duplicateElementId": text["id"]}})
        duplicate_id = duplicate["createdElementId"]
        copied = next(item for item in duplicate["design"]["elements"] if item["id"] == duplicate_id)
        self.assertEqual(copied["text"], "Foundation Week")
        self.assertNotEqual(copied["syncKey"], edited["syncKey"])
        self.call("design.update", {"stripId": strip["id"], "changes": {"deleteElementId": duplicate_id}})

        reset = self.call("design.update", {"stripId": strip["id"], "changes": {"resetElementId": text["id"]}})
        reset_text = next(item for item in reset["design"]["elements"] if item["id"] == text["id"])
        self.assertEqual(reset_text["text"], "Text")
        self.assertEqual(reset_text["fontFamily"], "Arial")
        self.assertEqual(reset_text["syncKey"], text["syncKey"])

        custom = self.call("templates.save_as", {"name": "Text template", "stripId": strip["id"]})["template"]
        self.assertTrue(any(item["type"] == "text" for item in custom["design"]["elements"]))
        self.assertNotIn("images", custom)

    def test_design_apply_to_all_rejects_missing_or_locked_source(self) -> None:
        strip = self.make_strips(2)[0]
        text = self.call("design.add_element", {"elementType": "text", "stripId": strip["id"]})["element"]
        self.call("design.update", {"stripId": strip["id"], "changes": {"elements": [{"id": text["id"], "locked": True}]}})
        locked = self.api.invoke("design.apply_to_all", {"stripId": strip["id"], "elementId": text["id"]})
        missing = self.api.invoke("design.apply_to_all", {"stripId": "deleted-strip", "elementId": text["id"]})
        self.assertFalse(locked["ok"])
        self.assertFalse(missing["ok"])

    def test_apply_to_all_targets_2_5_and_20_strips_in_one_undo_step(self) -> None:
        for count in (2, 5, 20):
            with self.subTest(strip_count=count):
                self.call("project.new")
                strips = self.make_strips(count)
                for index, strip in enumerate(strips):
                    self.call("strips.set_crop", {"stripId": strip["id"], "slotIndex": 0,
                        "crop": {"mode": "contain", "ratio": "original", "zoom": 1.2 + index / 10,
                                 "offsetX": (index % 3) / 4, "offsetY": -(index % 2) / 4}})
                before_slots = {strip["id"]: strip["slots"] for strip in self.api._project["strips"]}
                source_id = strips[0]["id"]
                element = self.call("design.add_element", {"type": "text", "stripId": source_id})["element"]
                self.call("design.update", {"stripId": source_id, "changes": {"elements": [{
                    "id": element["id"], "text": "Shared title", "fill": "#123456", "fontSize": 56,
                }]}})
                before_depth = self.api._history.status()["undoDepth"]
                applied = self.call("design.apply_to_all", {"stripId": source_id, "elementId": element["id"]})
                self.assertEqual(applied["affectedStrips"], count - 1)
                self.assertEqual(applied["created"], count - 1)
                self.assertEqual(applied["updated"], 0)
                self.assertEqual(self.api._history.status()["undoDepth"], before_depth + 1)
                for strip in self.api._project["strips"]:
                    self.assertEqual(strip["slots"], before_slots[strip["id"]])
                    self.assertEqual(sum(item["type"] == "text" for item in strip["designOverride"]["elements"]), 1)
                undone = self.call("design.undo")
                self.assertIsNone(next(strip for strip in undone["project"]["strips"] if strip["id"] == strips[1]["id"])["designOverride"])
                redone = self.call("design.redo")
                self.assertEqual(sum(item["type"] == "text" for item in next(strip for strip in redone["project"]["strips"] if strip["id"] == strips[1]["id"])["designOverride"]["elements"]), 1)

    def test_apply_to_all_updates_corresponding_text_and_skips_locked_targets(self) -> None:
        strips = self.make_strips(3)
        source_id = strips[0]["id"]
        source = self.call("design.add_element", {"type": "text", "stripId": source_id})["element"]
        first = self.call("design.apply_to_all", {"stripId": source_id, "elementId": source["id"]})
        self.assertEqual(first["created"], 2)
        second_strip = self.api._project["strips"][1]
        counterpart = next(item for item in second_strip["designOverride"]["elements"] if item.get("syncKey") == source["syncKey"])
        self.call("design.update", {"stripId": second_strip["id"], "changes": {"elements": [{"id": counterpart["id"], "locked": True}]}})
        self.call("design.update", {"stripId": source_id, "changes": {"elements": [{"id": source["id"], "text": "Updated"}]}})
        applied = self.call("design.apply_to_all", {"stripId": source_id, "elementId": source["id"]})
        self.assertEqual((applied["created"], applied["updated"], applied["skippedLocked"]), (0, 1, 1))
        target = next(strip for strip in applied["project"]["strips"] if strip["id"] == strips[2]["id"])
        self.assertEqual(next(item for item in target["designOverride"]["elements"] if item["syncKey"] == source["syncKey"])["text"], "Updated")

    def test_per_strip_design_override_round_trips_project_save_and_reload(self) -> None:
        strip = self.make_strips(1)[0]
        text = self.call("design.add_element", {"type": "text", "stripId": strip["id"]})["element"]
        self.call("design.update", {"stripId": strip["id"], "changes": {"elements": [{"id": text["id"], "text": "Saved text"}]}})
        path = Path(self.temp.name) / "text-override.stripproj"
        self.call("project.save_as", {"path": str(path)})
        self.call("project.open", {"path": str(path)})
        loaded = self.api._project["strips"][0]["designOverride"]
        self.assertEqual(next(item for item in loaded["elements"] if item["id"] == text["id"])["text"], "Saved text")

    def test_grouping_empty_slots_preserves_strip_physical_settings(self) -> None:
        project = self.api._project
        project["images"] = [{"id": f"img-{index}", "path": f"missing-{index}.jpg", "name": f"image{index}.jpg", "width": 2000, "height": 1000, "status": "ready"} for index in range(1, 6)]
        project["imageOrder"] = [image["id"] for image in project["images"]]
        self.api._commit_project(project)
        grouped = self.call("strips.generate", {"slotsPerStrip": 4, "incomplete": "empty"})
        self.assertEqual(len(grouped["strips"]), 2)
        self.assertIsNone(grouped["strips"][1]["slots"][1]["imageId"])
        layout = self.call("print.get_layout")
        self.assertEqual(layout["pages"][0]["placements"][1]["widthMm"], 50.8)
        self.assertEqual(layout["pages"][0]["placements"][1]["heightMm"], 152.4)

    def test_internal_frame_ratio_change_keeps_physical_strip_body(self) -> None:
        project = self.api._project
        project["strips"] = [{"id": "strip-1", "slots": []}]
        self.api._commit_project(project)
        self.call("design.update", {"changes": {"elements": [{"id": "photo-1", "locked": False}]}})
        self.call("design.update", {"changes": {"elements": [{"id": "photo-1", "aspectRatio": "1:1"}]}})
        layout = self.call("print.get_layout")
        placement = layout["pages"][0]["placements"][0]
        self.assertEqual((placement["widthMm"], placement["heightMm"]), (50.8, 152.4))

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
        self.call("design.update", {"changes": {"elements": [{"id": "photo-1", "locked": False}]}})
        self.call("design.update", {"changes": {"elements": [{"id": "photo-1", "width": 10, "height": 10}]}})

        validation = self.call("print.validate")["validation"]
        resolution = next(item for item in validation["checks"] if item["id"] == "source_resolution")
        self.assertEqual(resolution["status"], "PASS")

    def test_saved_custom_template_keeps_physical_metadata_without_project_photos(self) -> None:
        saved = self.call("templates.save_as", {"name": "Custom print strip"})["template"]
        self.assertEqual(saved["stripSpec"]["widthMm"], 50.8)
        self.assertEqual(saved["stripSpec"]["heightMm"], 152.4)
        self.assertEqual(saved["slotsPerStrip"], 4)
        self.assertNotIn("images", saved)
        loaded = self.call("templates.load", {"templateId": saved["templateId"]})
        self.assertEqual(loaded["project"]["printSettings"]["stripWidthMm"], 50.8)

    def test_legacy_project_geometry_is_preserved_until_explicit_preset_change(self) -> None:
        legacy = dict(self.api._project)
        legacy.pop("stripSpec", None)
        legacy["design"] = {
            "artboard": {"width": 1200, "height": 1600, "widthMm": 54, "heightMm": 72},
            "elements": [
                {"id": "background", "type": "background", "x": 0, "y": 0,
                 "width": 1200, "height": 1600, "fill": "#f6f3eb"},
                {"id": "photo-1", "type": "image_frame", "slotIndex": 0,
                 "x": 120, "y": 160, "width": 960, "height": 400},
            ],
        }
        legacy["printSettings"] = {**legacy["printSettings"], "stripWidthMm": 54, "stripHeightMm": 72}
        project = self.api._commit_project(legacy)
        self.assertEqual(project["stripSpec"]["presetId"], "custom")
        self.assertEqual((project["stripSpec"]["designWidth"], project["stripSpec"]["designHeight"]), (1200, 1600))
        self.assertEqual((project["printSettings"]["stripWidthMm"], project["printSettings"]["stripHeightMm"]), (54, 72))
        self.assertEqual((project["design"]["elements"][1]["x"], project["design"]["elements"][1]["y"]), (120, 160))

        changed = self.call("print.update_settings", {"settings": {"stripPresetId": "photobooth-2x6"}})["project"]
        self.assertEqual(changed["stripSpec"]["presetId"], "photobooth-2x6")
        self.assertEqual((changed["design"]["artboard"]["width"], changed["design"]["artboard"]["height"]), (600, 1800))
        self.assertEqual((changed["design"]["elements"][1]["x"], changed["design"]["elements"][1]["y"]), (60, 180))

    def test_saved_project_with_existing_strip_spec_stays_legacy_without_layout_mode(self) -> None:
        legacy = dict(self.api._project)
        legacy.pop("frameLayoutMode", None)
        saved_frames = {
            element["id"]: (element["x"], element["y"], element["width"], element["height"])
            for element in legacy["design"]["elements"]
            if element.get("type") == "image_frame"
        }
        self.api._commit_project(legacy)

        state = self.call("app.get_state")["project"]
        self.assertEqual(state["frameLayoutMode"], "legacy")
        resolved = state["resolvedFrameLayouts"]["__shared__"]
        self.assertEqual(resolved["mode"], "legacy")
        self.assertEqual(
            {
                frame["elementId"]: (frame["x"], frame["y"], frame["width"], frame["height"])
                for frame in resolved["frames"]
            },
            saved_frames,
        )

    def test_original_frame_resize_persists_ratio_locked_adaptive_width(self) -> None:
        project = self.api._project
        project["images"] = [{"id": "landscape", "path": None, "name": "landscape.jpg",
                              "width": 1536, "height": 1024, "status": "ready"}]
        project["imageOrder"] = ["landscape"]
        project["strips"] = [{"id": "strip-resize", "slots": [
            {"imageId": "landscape", "crop": {"mode": "contain", "ratio": "original",
                                                   "zoom": 1, "offsetX": 0, "offsetY": 0}}
            for _ in range(4)
        ], "designOverride": None}]
        self.api._commit_project(project)

        frame_id = "photo-1"
        self.call("design.update", {"changes": {"elements": [{"id": frame_id, "locked": False}]}})
        self.call("design.update", {"changes": {"elements": [{"id": frame_id, "width": 500, "height": 500 / 1.5}]}})
        state = self.call("app.get_state")["project"]
        self.assertEqual(state["design"]["imageLayout"]["frameWidth"], 500)
        resolved = state["resolvedFrameLayouts"]["strip-resize"]
        self.assertTrue(all(abs(frame["width"] - 500) < 1e-9 for frame in resolved["frames"]))
        self.assertTrue(all(abs(frame["height"] - 500 / 1.5) < 1e-9 for frame in resolved["frames"]))

    def test_legacy_project_without_design_recovers_original_dcapture_layout(self) -> None:
        legacy = dict(self.api._project)
        legacy.pop("stripSpec", None)
        legacy["design"] = None
        legacy["printSettings"] = {**legacy["printSettings"], "stripWidthMm": 54, "stripHeightMm": 72}
        project = self.api._commit_project(legacy)
        self.assertEqual(project["stripSpec"]["presetId"], "custom")
        self.assertEqual((project["design"]["artboard"]["width"], project["design"]["artboard"]["height"]), (1200, 1600))
        self.assertEqual((project["stripSpec"]["widthMm"], project["stripSpec"]["heightMm"]), (54, 72))
        self.assertEqual(len([element for element in project["design"]["elements"] if element["type"] == "image_frame"]), 4)

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
