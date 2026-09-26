from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # Allow the dependency-free sorting/grouping tests to run alone.
    Image = None

from app.backend.image_service import ImageService
from app.backend.sorting_service import reorder_images, sort_images
from app.backend.strip_service import generate_strips, normalize_crop, set_slot_crop


def records(count: int) -> list[dict]:
    return [{"id": f"img-{i}", "name": f"image{i}.jpg", "path": f"/photos/image{i}.jpg"} for i in range(1, count + 1)]


class SortingServiceTests(unittest.TestCase):
    def test_smart_sort_uses_numeric_filename_runs(self) -> None:
        images = [
            {"id": "10", "name": "image10.jpg"},
            {"id": "1", "name": "image1.jpg"},
            {"id": "2", "name": "image2.jpg"},
            {"id": "3", "name": "image3.jpg"},
        ]
        self.assertEqual([item["id"] for item in sort_images(images, "smart")], ["1", "2", "3", "10"])

    def test_number_sort_extracts_numeric_sequence_and_places_unmatched_last(self) -> None:
        images = [
            {"id": "x", "name": "behind.jpg"},
            {"id": "10", "name": "photo10.jpg"},
            {"id": "2", "name": "photo2.jpg"},
        ]
        self.assertEqual([item["id"] for item in sort_images(images, "number")], ["2", "10", "x"])

    def test_name_modes_and_date_sort_are_deterministic(self) -> None:
        images = [
            {"id": "b", "name": "b.jpg", "modifiedAt": "2025-01-03T00:00:00Z"},
            {"id": "a", "name": "a.jpg", "modifiedAt": "2025-01-01T00:00:00Z"},
            {"id": "c", "name": "c.jpg"},
        ]
        self.assertEqual([item["id"] for item in sort_images(images, "name_asc")], ["a", "b", "c"])
        self.assertEqual([item["id"] for item in sort_images(images, "name_desc")], ["c", "b", "a"])
        self.assertEqual([item["id"] for item in sort_images(images, "date")], ["a", "b", "c"])

    def test_manual_order_keeps_unspecified_images_in_their_original_order(self) -> None:
        images = records(4)
        self.assertEqual([item["id"] for item in reorder_images(images, ["img-3", "img-1"])], ["img-3", "img-1", "img-2", "img-4"])
        self.assertEqual([item["id"] for item in sort_images(images, "manual")], [item["id"] for item in images])

    def test_unknown_mode_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            sort_images(records(1), "random")


class StripGroupingTests(unittest.TestCase):
    def test_twenty_images_form_five_four_slot_strips(self) -> None:
        result = generate_strips(records(20))
        self.assertEqual(len(result["strips"]), 5)
        self.assertTrue(all(len(strip["slots"]) == 4 for strip in result["strips"]))
        self.assertEqual(result["incompleteImageCount"], 0)

    def test_twenty_three_images_leave_three_empty_slots_by_default(self) -> None:
        result = generate_strips(records(23))
        self.assertEqual(len(result["strips"]), 6)
        self.assertEqual([slot["imageId"] for slot in result["strips"][-1]["slots"]], ["img-21", "img-22", "img-23", None])
        self.assertEqual(result["excludedImageIds"], [])

    def test_duplicate_last_policy_pads_only_the_incomplete_strip(self) -> None:
        result = generate_strips(records(6), incomplete="duplicate_last")
        self.assertEqual([slot["imageId"] for slot in result["strips"][-1]["slots"]], ["img-5", "img-6", "img-6", "img-6"])

    def test_repeat_from_start_policy_cycles_incomplete_images(self) -> None:
        result = generate_strips(records(6), incomplete="repeat_from_start")
        self.assertEqual([slot["imageId"] for slot in result["strips"][-1]["slots"]], ["img-5", "img-6", "img-5", "img-6"])

    def test_exclude_policy_drops_incomplete_group_and_reports_sources(self) -> None:
        result = generate_strips(records(6), incomplete="exclude")
        self.assertEqual(len(result["strips"]), 1)
        self.assertEqual(result["excludedImageIds"], ["img-5", "img-6"])

    def test_crop_state_is_bounded_and_survives_unchanged_regrouping(self) -> None:
        result = generate_strips(records(4))
        strip = result["strips"][0]
        set_slot_crop(result["strips"], strip["id"], 0, {"mode": "cover", "ratio": "4:3", "zoom": 8, "offsetX": -3, "offsetY": 0.25})
        self.assertEqual(strip["slots"][0]["crop"], {"mode": "cover", "ratio": "4:3", "zoom": 4.0, "offsetX": -1.0, "offsetY": 0.25})
        regrouped = generate_strips(records(4), previous_strips=result["strips"])
        self.assertEqual(regrouped["strips"][0]["slots"][0]["crop"]["zoom"], 4.0)
        self.assertEqual(normalize_crop({"ratio": "original"}), {"mode": "cover", "ratio": "original", "zoom": 1.0, "offsetX": 0.0, "offsetY": 0.0})

    def test_invalid_grouping_and_crop_inputs_are_reported(self) -> None:
        with self.assertRaises(ValueError):
            generate_strips(records(1), slots_per_strip=0)
        with self.assertRaises(ValueError):
            generate_strips(records(1), incomplete="silently_shrink")
        with self.assertRaises(ValueError):
            normalize_crop({"zoom": float("nan")})


@unittest.skipIf(Image is None, "Pillow is required for image import tests")
class ImageImportTests(unittest.TestCase):
    def test_batch_import_creates_metadata_and_small_thumbnail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "event1.png"
            cache = Path(directory) / "cache"
            Image.new("RGB", (1200, 900), color=(25, 90, 140)).save(source)
            service = ImageService(thumbnail_dir=cache, thumbnail_size=(128, 128))

            result = service.import_paths([source, source])

            self.assertEqual(len(result["images"]), 1)  # duplicate paths are ignored within a batch
            image = result["images"][0]
            self.assertEqual((image["width"], image["height"]), (1200, 900))
            self.assertEqual(image["status"], "ready")
            self.assertEqual(image["extension"], ".png")
            thumb = service.get_thumbnail(image["id"], as_data_uri=False)
            self.assertTrue(thumb.startswith("file:///"))
            thumbnail_path = Path(thumb.removeprefix("file:///"))
            self.assertTrue(thumbnail_path.is_file())
            with Image.open(thumbnail_path) as preview:
                self.assertLessEqual(max(preview.size), 128)

    def test_unsupported_corrupt_and_missing_inputs_do_not_abort_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = root / "good.jpg"
            corrupt = root / "broken.png"
            unsupported = root / "movie.gif"
            Image.new("RGB", (20, 10), color="red").save(valid)
            corrupt.write_bytes(b"not a PNG")
            unsupported.write_bytes(b"GIF89a")
            service = ImageService(thumbnail_dir=root / "cache")

            result = service.import_paths([valid, corrupt, unsupported, root / "gone.jpg"])

            self.assertEqual(len(result["images"]), 1)
            self.assertEqual([error["status"] for error in result["errors"]], ["corrupt", "unsupported", "missing"])


@unittest.skipIf(Image is not None, "This check applies when Pillow is unavailable")
class MissingPillowTests(unittest.TestCase):
    def test_missing_dependency_returns_structured_service_error(self) -> None:
        result = ImageService().import_paths(["example.jpg"])
        self.assertEqual(result["images"], [])
        self.assertEqual(result["serviceError"]["code"], "dependency_missing")
        self.assertEqual(result["serviceError"]["dependency"], "Pillow")


if __name__ == "__main__":
    unittest.main()
