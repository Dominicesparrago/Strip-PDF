from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from app.backend.strip_geometry import (
    artboard_for_spec,
    default_strip_spec,
    legacy_dcapture_design,
    legacy_strip_spec,
    make_1x4_design,
    normalize_design_coordinates,
    normalize_strip_spec,
    normalized_rect,
    physical_rect,
    validate_frame_geometry,
)
from app.backend.template_service import TemplateService


ROOT = Path(__file__).resolve().parents[1]


class StripGeometryTests(unittest.TestCase):
    def test_default_two_by_six_spec_has_one_to_three_ratio(self) -> None:
        spec = default_strip_spec()
        self.assertEqual(spec["presetId"], "photobooth-2x6")
        self.assertEqual((spec["widthMm"], spec["heightMm"]), (50.8, 152.4))
        self.assertEqual((spec["designWidth"], spec["designHeight"]), (600, 1800))
        self.assertEqual(spec["frameCount"], 4)
        self.assertTrue(math.isclose(spec["widthMm"] / spec["heightMm"], 1 / 3))
        self.assertTrue(math.isclose(spec["designWidth"] / spec["designHeight"], 1 / 3))

    def test_four_predefined_frames_are_equal_and_do_not_use_source_dimensions(self) -> None:
        spec = default_strip_spec()
        design = make_1x4_design(spec)
        frames = validate_frame_geometry(design, spec)
        self.assertEqual([frame["slotIndex"] for frame in frames], [0, 1, 2, 3])
        self.assertEqual(len({(frame["x"], frame["width"], frame["height"]) for frame in frames}), 1)
        self.assertTrue(all(frame["locked"] and frame["cropMode"] == "contain" for frame in frames))
        for source_width, source_height in ((1920, 1280), (4000, 3000), (1080, 1920), (3000, 2000)):
            self.assertEqual((frames[0]["width"], frames[0]["height"]), (564, 427.5))
            self.assertNotEqual((frames[0]["width"], frames[0]["height"]), (source_width, source_height))

    def test_design_rectangle_maps_to_physical_millimeters(self) -> None:
        spec = default_strip_spec()
        rect = {"x": 60, "y": 180, "width": 300, "height": 900}
        self.assertEqual(normalized_rect(rect, 600, 1800),
                         {"x": .1, "y": .1, "width": .5, "height": .5})
        mapped = physical_rect(rect, spec)
        self.assertAlmostEqual(mapped["xMm"], 5.08)
        self.assertAlmostEqual(mapped["yMm"], 15.24)
        self.assertAlmostEqual(mapped["widthMm"], 25.4)
        self.assertAlmostEqual(mapped["heightMm"], 76.2)

    def test_legacy_work_is_described_without_changing_its_coordinates(self) -> None:
        old_design = {
            "artboard": {"width": 1200, "height": 1600, "widthMm": 54, "heightMm": 72},
            "elements": [
                {"id": "background", "type": "background", "x": 0, "y": 0, "width": 1200, "height": 1600},
                {"id": "photo-1", "type": "image_frame", "slotIndex": 0,
                 "x": 18, "y": 18, "width": 1164, "height": 377.5},
            ],
        }
        original = json.dumps(old_design, sort_keys=True)
        spec = legacy_strip_spec(old_design, {"stripWidthMm": 54, "stripHeightMm": 72})
        self.assertEqual(spec["presetId"], "custom")
        self.assertEqual((spec["designWidth"], spec["designHeight"]), (1200, 1600))
        self.assertEqual((spec["widthMm"], spec["heightMm"]), (54, 72))
        self.assertEqual(json.dumps(old_design, sort_keys=True), original)
        mapped = physical_rect(old_design["elements"][1], spec)
        self.assertAlmostEqual(mapped["xMm"], 18 / 1200 * 54)
        self.assertAlmostEqual(mapped["heightMm"], 377.5 / 1600 * 72)

    def test_missing_legacy_dcapture_design_can_be_reconstructed(self) -> None:
        design = legacy_dcapture_design()
        spec = legacy_strip_spec(design, {"stripWidthMm": 54, "stripHeightMm": 72})
        self.assertEqual((spec["designWidth"], spec["designHeight"]), (1200, 1600))
        self.assertEqual((spec["widthMm"], spec["heightMm"]), (54, 72))
        frames = validate_frame_geometry(design, spec)
        self.assertEqual(len(frames), 4)
        self.assertEqual((frames[0]["x"], frames[0]["y"], frames[0]["width"], frames[0]["height"]),
                         (18, 18, 1164, 377.5))
        self.assertFalse(frames[0]["locked"])

    def test_explicit_migration_preserves_order_and_relative_positions(self) -> None:
        old = {
            "artboard": {"width": 1200, "height": 1600},
            "elements": [
                {"id": "background", "type": "background", "x": 0, "y": 0, "width": 1200, "height": 1600, "fill": "#112233"},
                {"id": "photo-1", "type": "image_frame", "slotIndex": 0, "x": 120, "y": 160, "width": 600, "height": 400},
                {"id": "flower", "type": "decoration", "x": 900, "y": 1200,
                 "width": 120, "height": 160, "borderWidth": 8, "borderRadius": 12},
                {"id": "title", "type": "text", "x": 120, "y": 1100,
                 "width": 900, "height": 100, "fontSize": 40, "letterSpacing": 4, "text": "Hello"},
            ],
            "pattern": {"spacing": 24, "offsetX": 4, "offsetY": 8},
        }
        migrated = normalize_design_coordinates(old, default_strip_spec())
        self.assertEqual(migrated["artboard"], artboard_for_spec(default_strip_spec()))
        self.assertEqual([item["id"] for item in migrated["elements"]], ["background", "photo-1", "flower", "title"])
        self.assertEqual(migrated["elements"][1]["slotIndex"], 0)
        self.assertEqual(migrated["elements"][0]["fill"], "#112233")
        self.assertEqual((migrated["elements"][1]["x"], migrated["elements"][1]["y"]), (60, 180))
        self.assertEqual(migrated["elements"][3]["fontSize"], 45)
        self.assertEqual(migrated["elements"][3]["letterSpacing"], 2)
        self.assertEqual(migrated["elements"][3]["text"], "Hello")
        self.assertAlmostEqual(migrated["elements"][2]["borderWidth"], 8 * math.sqrt(.5 * 1.125))
        self.assertEqual(migrated["pattern"]["offsetX"], 2)
        self.assertEqual(migrated["pattern"]["offsetY"], 9)
        self.assertEqual((old["elements"][1]["x"], old["elements"][1]["y"]), (120, 160))

    def test_builtin_dcapture_clean_matches_default_spec(self) -> None:
        template = json.loads((ROOT / "templates" / "builtin" / "dcapture-clean.json").read_text(encoding="utf-8"))
        spec = normalize_strip_spec(template["stripSpec"])
        self.assertEqual(spec, default_strip_spec())
        self.assertEqual(template["design"]["artboard"], artboard_for_spec(spec))
        self.assertEqual(len(validate_frame_geometry(template["design"], spec)), 4)

    def test_legacy_custom_template_loads_with_inferred_spec_without_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            builtin_dir = root / "builtin"
            custom_dir = root / "custom"
            builtin_dir.mkdir()
            custom_dir.mkdir()
            path = custom_dir / ("custom-" + "a" * 32 + ".json")
            old = {
                "kind": "template", "schemaVersion": 1,
                "templateId": "custom-" + "a" * 32, "name": "Old Strip",
                "design": {"artboard": {"width": 1200, "height": 1600}, "elements": []},
                "physical": {"widthMm": 54, "heightMm": 72},
            }
            path.write_text(json.dumps(old), encoding="utf-8")
            original = path.read_bytes()
            loaded = TemplateService(builtin_dir, custom_dir).load_template(old["templateId"])
            self.assertEqual((loaded["stripSpec"]["designWidth"], loaded["stripSpec"]["designHeight"]), (1200, 1600))
            self.assertEqual((loaded["stripSpec"]["widthMm"], loaded["stripSpec"]["heightMm"]), (54, 72))
            self.assertEqual(path.read_bytes(), original)

    def test_54_by_180_and_custom_specs_are_supported(self) -> None:
        preset = normalize_strip_spec({"presetId": "photobooth-54x180"})
        self.assertEqual((preset["widthMm"], preset["heightMm"]), (54, 180))
        custom = normalize_strip_spec({"presetId": "custom", "widthMm": 60, "heightMm": 170,
                                       "designWidth": 600, "designHeight": 1700, "frameCount": 4})
        self.assertEqual((custom["widthMm"], custom["heightMm"]), (60, 170))
        self.assertEqual(custom["orientation"], "portrait")

    def test_invalid_frame_and_nonfinite_values_are_rejected(self) -> None:
        spec = default_strip_spec()
        design = make_1x4_design(spec)
        design["elements"][1]["x"] = float("nan")
        with self.assertRaisesRegex(ValueError, "finite"):
            validate_frame_geometry(design, spec)
        design["elements"][1]["x"] = 18
        design["elements"][1]["width"] = 999
        with self.assertRaisesRegex(ValueError, "inside"):
            validate_frame_geometry(design, spec)
        with self.assertRaisesRegex(ValueError, "positive"):
            normalize_strip_spec({"presetId": "custom", "widthMm": float("inf"),
                                  "heightMm": 100, "designWidth": 600, "designHeight": 1000})

    def test_duplicate_frame_can_share_slot_but_missing_slot_is_rejected(self) -> None:
        spec = default_strip_spec()
        design = make_1x4_design(spec)
        duplicated = dict(design["elements"][2], id="duplicate-photo-2", x=20)
        design["elements"].append(duplicated)
        self.assertEqual(len(validate_frame_geometry(design, spec)), 5)
        design["elements"][4]["slotIndex"] = 1
        with self.assertRaisesRegex(ValueError, "slots 1 through 4"):
            validate_frame_geometry(design, spec)


if __name__ == "__main__":
    unittest.main()
