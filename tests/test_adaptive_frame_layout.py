from __future__ import annotations

import math
import unittest

from app.backend.models import default_design
from app.backend.strip_geometry import default_strip_spec, resolve_frame_layout


def strip_for(images: list[tuple[int, int]], *, mode: str = "contain", ratio: str = "original") -> tuple[dict, list[dict]]:
    strip = {"id": "adaptive-strip", "slots": [
        {"imageId": str(index), "crop": {"mode": mode, "ratio": ratio, "zoom": 1,
                                           "offsetX": 0, "offsetY": 0}}
        for index in range(4)
    ]}
    records = [{"id": str(index), "width": width, "height": height}
               for index, (width, height) in enumerate(images)]
    return strip, records


class AdaptiveFrameLayoutTests(unittest.TestCase):
    def assert_frames_fit(self, layout: dict) -> None:
        frames = layout["frames"]
        self.assertEqual(len(frames), 4)
        for index, frame in enumerate(frames):
            self.assertGreater(frame["width"], 0)
            self.assertGreater(frame["height"], 0)
            self.assertGreaterEqual(frame["x"], -1e-9)
            self.assertGreaterEqual(frame["y"], -1e-9)
            self.assertLessEqual(frame["x"] + frame["width"], 600 + 1e-9)
            self.assertLessEqual(frame["y"] + frame["height"], 1800 + 1e-9)
            if index:
                self.assertGreaterEqual(frame["y"], frames[index - 1]["y"] + frames[index - 1]["height"] - 1e-9)

    def test_four_three_to_two_sources_use_their_true_ratio_and_no_crop(self) -> None:
        strip, images = strip_for([(1536, 1024)] * 4)
        layout = resolve_frame_layout(default_design(), strip, images)
        self.assert_frames_fit(layout)
        self.assertEqual(layout["mode"], "adaptive")
        self.assertTrue(all(math.isclose(frame["frameAspectRatio"], 1.5, rel_tol=1e-9)
                            for frame in layout["frames"]))
        self.assertTrue(all(frame["visibleSourceFraction"] == 1 for frame in layout["frames"]))
        self.assertEqual((default_strip_spec()["widthMm"], default_strip_spec()["heightMm"]), (50.8, 152.4))

    def test_four_three_to_four_sources_keep_the_requested_ratio(self) -> None:
        for ratio, dimensions, expected in (
            ("4:3", (1536, 1024), 4 / 3),
            ("16:9", (1920, 1080), 16 / 9),
            ("1:1", (1080, 1080), 1.0),
        ):
            with self.subTest(ratio=ratio):
                strip, images = strip_for([dimensions] * 4, ratio=ratio)
                design = default_design()
                for element in design["elements"]:
                    if element.get("type") == "image_frame":
                        element["aspectRatio"] = ratio
                        element["cropMode"] = "cover"
                layout = resolve_frame_layout(design, strip, images)
                self.assert_frames_fit(layout)
                self.assertTrue(all(math.isclose(frame["frameAspectRatio"], expected, rel_tol=1e-9)
                                    for frame in layout["frames"]))

    def test_square_stack_uniformly_reduces_width_instead_of_cropping(self) -> None:
        strip, images = strip_for([(1080, 1080)] * 4)
        layout = resolve_frame_layout(default_design(), strip, images)
        self.assert_frames_fit(layout)
        self.assertLess(layout["scale"], 1)
        self.assertTrue(all(frame["visibleSourceFraction"] == 1 for frame in layout["frames"]))
        self.assertAlmostEqual(layout["frames"][0]["width"], layout["frames"][0]["height"])
        self.assertAlmostEqual(layout["frames"][0]["x"], (600 - layout["frames"][0]["width"]) / 2)

    def test_unused_vertical_space_follows_layout_alignment(self) -> None:
        strip, images = strip_for([(1536, 1024)] * 4)
        design = default_design()
        design["imageLayout"]["verticalAlign"] = "top"
        top = resolve_frame_layout(design, strip, images)
        design["imageLayout"]["verticalAlign"] = "bottom"
        bottom = resolve_frame_layout(design, strip, images)
        self.assertEqual(top["frames"][0]["y"], 18)
        self.assertGreater(bottom["frames"][0]["y"], top["frames"][0]["y"])
        self.assertAlmostEqual(bottom["frames"][-1]["y"] + bottom["frames"][-1]["height"], 1782)

    def test_explicit_designer_frame_width_keeps_original_ratio(self) -> None:
        strip, images = strip_for([(1536, 1024)] * 4)
        design = default_design()
        design["imageLayout"]["frameWidth"] = 500
        layout = resolve_frame_layout(design, strip, images)
        self.assertTrue(all(math.isclose(frame["width"], 500, rel_tol=1e-9)
                            for frame in layout["frames"]))
        self.assertTrue(all(math.isclose(frame["height"], 500 / 1.5, rel_tol=1e-9)
                            for frame in layout["frames"]))

    def test_mixed_sources_keep_individual_ratios_and_fit_without_overlap(self) -> None:
        strip, images = strip_for([(1536, 1024), (4000, 3000), (1920, 1080), (1080, 1920)])
        layout = resolve_frame_layout(default_design(), strip, images)
        self.assert_frames_fit(layout)
        expected = [1.5, 4 / 3, 16 / 9, 1080 / 1920]
        for frame, ratio in zip(layout["frames"], expected):
            self.assertAlmostEqual(frame["frameAspectRatio"], ratio)
            self.assertEqual(frame["visibleSourceFraction"], 1)
        self.assertEqual(len({round(frame["width"], 9) for frame in layout["frames"]}), 1)

    def test_fixed_cover_mode_can_crop_while_original_mode_cannot(self) -> None:
        strip, images = strip_for([(1920, 1080)] * 4, mode="cover", ratio="1:1")
        fixed_design = default_design()
        for element in fixed_design["elements"]:
            if element["type"] == "image_frame":
                element["aspectRatio"] = "1:1"
        fixed = resolve_frame_layout(fixed_design, strip, images)
        self.assertTrue(all(frame["frameAspectRatio"] == 1 for frame in fixed["frames"]))
        self.assertTrue(all(frame["visibleSourceFraction"] < 1 for frame in fixed["frames"]))

        original_strip, original_images = strip_for([(1920, 1080)] * 4, mode="contain")
        original = resolve_frame_layout(default_design(), original_strip, original_images)
        self.assertTrue(all(math.isclose(frame["frameAspectRatio"], 16 / 9, rel_tol=1e-9)
                            for frame in original["frames"]))
        self.assertTrue(all(frame["visibleSourceFraction"] == 1 for frame in original["frames"]))

    def test_empty_slot_uses_saved_placeholder_ratio_until_source_is_assigned(self) -> None:
        strip, images = strip_for([(1536, 1024), (1080, 1920), (1080, 1080), (1080, 1080)])
        strip["slots"][3]["imageId"] = None
        layout = resolve_frame_layout(default_design(), strip, images)
        self.assert_frames_fit(layout)
        self.assertFalse(layout["frames"][3]["populated"])
        self.assertAlmostEqual(layout["frames"][3]["frameAspectRatio"], 564 / 427.5)

    def test_legacy_mode_returns_saved_geometry_exactly(self) -> None:
        strip, images = strip_for([(1536, 1024)] * 4)
        design = default_design()
        layout = resolve_frame_layout(design, strip, images, adaptive=False)
        for element, frame in zip((item for item in design["elements"] if item["type"] == "image_frame"), layout["frames"]):
            self.assertEqual((frame["x"], frame["y"], frame["width"], frame["height"]),
                             (element["x"], element["y"], element["width"], element["height"]))
        self.assertEqual(layout["mode"], "legacy")


if __name__ == "__main__":
    unittest.main()
