from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.backend.persistence import atomic_write_json, read_json_object
from app.backend.project_service import ProjectPathRequiredError, ProjectService
from app.backend.template_service import BuiltInTemplateError, DesignHistory, TemplateService


class PersistenceTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_atomic_json_write_round_trips_and_rejects_nonfinite(self) -> None:
        path = self.root / "nested" / "state.json"
        atomic_write_json(path, {"label": "Café", "items": [1, 2]})
        self.assertEqual(read_json_object(path), {"label": "Café", "items": [1, 2]})

        with self.assertRaises(ValueError):
            atomic_write_json(path, {"bad": float("nan")})
        self.assertEqual(read_json_object(path)["label"], "Café")
        self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_project_save_as_open_preserves_state_and_missing_paths(self) -> None:
        data_dir = self.root / "user-data"
        source = self.root / "photo1.jpg"
        source.write_bytes(b"not decoded by persistence")
        service = ProjectService(data_dir)
        project = service.new_project()
        project.update(
            {
                "name": "Party",
                "images": [
                    {"id": "a", "path": str(source), "name": "photo1.jpg"},
                    {"id": "b", "path": str(self.root / "gone.jpg"), "name": "gone.jpg"},
                ],
                "imageOrder": ["b", "a"],
                "grouping": {"slotsPerStrip": 4, "incomplete": "duplicate_last"},
                "strips": [{"id": "s1", "slots": [{"imageId": "b", "crop": {"zoom": 1.2}}]}],
                "selectedTemplateId": "dcapture-clean",
                "templateOverrides": {"border": {"width": 2}},
                "design": {"elements": [{"id": "frame-1", "x": 12}]},
                "printSettings": {"paper": "A4", "orientation": "landscape", "stripsPerPage": 5},
                "pdfSettings": {"resolutionDpi": 300, "quality": "high"},
            }
        )

        path = self.root / "party.stripproj"
        result = service.save_as_project(path, project)
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(raw["kind"], "project")
        self.assertEqual(raw["schemaVersion"], 1)
        self.assertEqual(raw["imageOrder"], ["b", "a"])
        self.assertEqual(raw["strips"][0]["slots"][0]["crop"], {"zoom": 1.2})
        self.assertEqual(raw["templateOverrides"], {"border": {"width": 2}})
        self.assertEqual(result["path"], str(path.resolve()))

        loaded = ProjectService(data_dir).open_project(path)
        self.assertEqual(loaded["name"], "Party")
        self.assertEqual(loaded["imageOrder"], ["b", "a"])
        self.assertEqual(loaded["strips"], project["strips"])
        self.assertEqual(loaded["design"], project["design"])
        self.assertEqual(loaded["printSettings"], project["printSettings"])
        self.assertEqual(loaded["pdfSettings"], project["pdfSettings"])
        self.assertEqual(loaded["images"][0]["status"], "available")
        self.assertEqual(loaded["images"][1]["status"], "missing")
        self.assertEqual(loaded["missingImagePaths"], [str(self.root / "gone.jpg")])

    def test_save_as_uses_filename_for_default_project_name(self) -> None:
        service = ProjectService(self.root / "user-data")
        path = self.root / "UI-e2e.stripproj"
        saved = service.save_as_project(path)
        self.assertEqual(saved["project"]["name"], "UI-e2e")
        self.assertEqual(ProjectService(self.root / "other-data").open_project(path)["name"], "UI-e2e")

    def test_project_save_requires_path_and_recovery_lifecycle(self) -> None:
        service = ProjectService(self.root / "appdata", autosave_delay=0)
        service.new_project()
        with self.assertRaises(ProjectPathRequiredError):
            service.save_project()

        state = service.project
        state["name"] = "Unsaved"
        state["images"] = [{"id": "x", "path": str(self.root / "missing.png")}]
        service.autosave(state)
        summary = service.get_recovery()
        self.assertTrue(summary["available"])
        self.assertEqual(summary["projectName"], "Unsaved")
        self.assertEqual(summary["imageCount"], 1)

        restored = ProjectService(self.root / "appdata").restore_recovery()
        self.assertEqual(restored["name"], "Unsaved")
        self.assertEqual(restored["missingImagePaths"], [str(self.root / "missing.png")])

        fresh = service.start_new_session()
        self.assertEqual(fresh["images"], [])
        self.assertEqual(service.get_recovery(), {"available": False})

    def test_open_and_save_discard_stale_recovery_and_cancel_old_autosaves(self) -> None:
        service = ProjectService(self.root / "appdata", autosave_delay=60)
        project = service.new_project()
        project["name"] = "Unsaved A"
        service.schedule_autosave(project)
        stale_revision = service._revision

        saved_path = self.root / "project-b.stripproj"
        project_b = {**project, "name": "Project B"}
        service.save_as_project(saved_path, project_b)
        self.assertFalse(service.is_dirty)
        self.assertEqual(service.get_recovery(), {"available": False})
        self.assertTrue(service._write_recovery_snapshot(project, None, stale_revision).get("skipped"))

        # Recreate a valid stale snapshot, then opening another project must
        # replace the active state and remove the old recovery offer.
        service.autosave({**project, "name": "Stale recovery"})
        opened = service.open_project(saved_path)
        self.assertEqual(opened["name"], "Project B")
        self.assertFalse(service.is_dirty)
        self.assertEqual(service.get_recovery(), {"available": False})

    def test_successful_save_removes_recovery_and_marks_project_clean(self) -> None:
        service = ProjectService(self.root / "appdata", autosave_delay=0)
        project = service.new_project()
        project["name"] = "Edited project"
        service.autosave(project)
        self.assertTrue(service.get_recovery()["available"])
        service.save_as_project(self.root / "edited.stripproj", project)
        self.assertFalse(service.is_dirty)
        self.assertEqual(service.get_recovery(), {"available": False})

    def test_corrupt_recovery_is_reported_without_losing_service(self) -> None:
        service = ProjectService(self.root / "appdata")
        service.recovery_path.parent.mkdir(parents=True)
        service.recovery_path.write_text("{broken", encoding="utf-8")
        result = service.get_recovery()
        self.assertFalse(result["available"])
        self.assertTrue(result["corrupt"])
        self.assertIn("Invalid JSON", result["error"])
        self.assertEqual(service.new_project()["images"], [])

    def test_custom_template_lifecycle_never_edits_builtin(self) -> None:
        builtin_dir = self.root / "templates" / "builtin"
        custom_dir = self.root / "user-data" / "templates" / "custom"
        builtin_dir.mkdir(parents=True)
        builtin_file = builtin_dir / "dcapture-clean.json"
        builtin_document = {
            "kind": "template",
            "schemaVersion": 1,
            "id": "dcapture-clean",
            "name": "DCapture Clean",
            "design": {"artboard": {"widthMm": 50, "heightMm": 160}, "elements": []},
        }
        builtin_file.write_text(json.dumps(builtin_document), encoding="utf-8")
        original_text = builtin_file.read_text(encoding="utf-8")
        service = TemplateService(builtin_dir, custom_dir)

        builtin = service.load_template("dcapture-clean")
        self.assertTrue(builtin["builtIn"])
        with self.assertRaises(BuiltInTemplateError):
            service.rename("dcapture-clean", "Changed")
        with self.assertRaises(BuiltInTemplateError):
            service.delete("dcapture-clean")

        custom = service.save_as("My Version", {"templateId": "dcapture-clean", "elements": [{"id": "x"}]})
        self.assertFalse(custom["builtIn"])
        reloaded = service.load_template(custom["templateId"])
        self.assertEqual(reloaded["design"]["elements"], [{"id": "x"}])

        renamed = service.rename(custom["templateId"], "Renamed")
        self.assertEqual(renamed["name"], "Renamed")
        duplicate = service.duplicate("dcapture-clean", "My Copy")
        self.assertEqual(duplicate["design"], builtin["design"])
        self.assertNotEqual(duplicate["templateId"], builtin["templateId"])
        self.assertEqual(service.reset("dcapture-clean")["design"], builtin["design"])
        self.assertEqual(len(service.list_templates()), 3)
        self.assertTrue(service.delete(custom["templateId"])["deleted"])
        self.assertEqual(builtin_file.read_text(encoding="utf-8"), original_text)
        self.assertEqual([item["name"] for item in service.list_templates()], ["DCapture Clean", "My Copy"])

    def test_design_history_is_bounded_structured_and_clears_redo(self) -> None:
        history = DesignHistory({"elements": [{"x": 0}]}, max_entries=2)
        history.commit({"elements": [{"x": 1}]}, "Move right")
        history.commit({"elements": [{"x": 2}]}, "Move right")
        history.commit({"elements": [{"x": 3}]}, "Move right")
        self.assertEqual(history.status()["undoDepth"], 2)
        self.assertEqual(history.undo(), {"elements": [{"x": 2}]})
        self.assertEqual(history.undo(), {"elements": [{"x": 1}]})
        self.assertIsNone(history.undo())
        self.assertEqual(history.redo(), {"elements": [{"x": 2}]})
        history.commit({"elements": [{"x": 20}]}, "New branch")
        self.assertFalse(history.can_redo)
        self.assertEqual(history.current, {"elements": [{"x": 20}]})


if __name__ == "__main__":
    unittest.main()
