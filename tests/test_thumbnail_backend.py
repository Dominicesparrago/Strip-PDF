from __future__ import annotations

import base64
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname
from unittest.mock import patch

from PIL import Image

from app.backend.api import StudioAPI
from app.backend.image_service import ImageService


ROOT = Path(__file__).resolve().parents[1]


def cache_path(uri: str) -> Path:
    return Path(url2pathname(urlparse(uri).path))


class ThumbnailBackendTests(unittest.TestCase):
    def test_valid_original_remains_imported_when_thumbnail_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "valid.jpg"
            Image.new("RGB", (640, 480), "olive").save(original)
            service = ImageService(root / "cache")

            with patch.object(service, "_write_thumbnail", side_effect=OSError("cache unavailable")):
                result = service.import_paths([original])
            self.assertEqual(result["errors"], [])
            self.assertEqual(len(result["images"]), 1)
            self.assertEqual(result["images"][0]["path"], str(original))
            self.assertIsNone(result["images"][0]["thumbnail"])

            recovered = service.import_paths([original])["images"][0]
            self.assertTrue(cache_path(recovered["thumbnail"]).is_file())

    def test_original_and_cached_display_thumbnail_have_separate_lifecycles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "large photo.png"
            Image.new("RGB", (1920, 1280), "navy").save(original)
            original_bytes = original.read_bytes()
            service = ImageService(root / "cache", thumbnail_size=(160, 160))

            image = service.import_paths([original])["images"][0]
            thumbnail = cache_path(image["thumbnail"])
            self.assertEqual(image["path"], str(original))
            self.assertNotEqual(original, thumbnail)
            self.assertEqual(original.read_bytes(), original_bytes)
            with Image.open(thumbnail) as display:
                self.assertLessEqual(max(display.size), 160)
                self.assertAlmostEqual(display.width / display.height, 1.5, places=2)

            first_modified = thumbnail.stat().st_mtime_ns
            reimported = service.import_paths([original])["images"][0]
            self.assertEqual(reimported["thumbnail"], image["thumbnail"])
            self.assertEqual(thumbnail.stat().st_mtime_ns, first_modified)

    def test_saved_thumbnail_reloads_and_missing_cache_regenerates_from_original(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "portrait.jpg"
            Image.new("RGB", (1080, 1920), "teal").save(original)
            api = StudioAPI(ROOT, root / "app-data")
            try:
                image = api.image_service.import_paths([original])["images"][0]
                api._project["images"] = [image]
                api.image_service = ImageService(root / "app-data" / "thumbnails")
                cached = api.invoke("images.get_thumbnail", {"imageId": image["id"]})
                self.assertTrue(cached["ok"], cached)
                self.assertTrue(cached["data"]["dataUrl"].startswith("data:image/webp;base64,"))

                cache_path(image["thumbnail"]).unlink()
                regenerated = api.invoke("images.get_thumbnail", {"imageId": image["id"]})
                self.assertTrue(regenerated["ok"], regenerated)
                self.assertTrue(regenerated["data"]["dataUrl"].startswith("data:image/webp;base64,"))
                self.assertTrue(cache_path(image["thumbnail"]).is_file())
            finally:
                api.close()

    def test_retry_replaces_damaged_cache_without_modifying_original(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "print-source.jpg"
            Image.new("RGB", (4000, 3000), "maroon").save(original)
            original_bytes = original.read_bytes()
            api = StudioAPI(ROOT, root / "app-data")
            try:
                image = api.image_service.import_paths([original])["images"][0]
                api._project["images"] = [image]
                thumbnail = cache_path(image["thumbnail"])
                thumbnail.write_bytes(b"damaged cache")

                response = api.invoke("images.get_thumbnail", {"imageId": image["id"], "retry": True})
                self.assertTrue(response["ok"], response)
                data_url = response["data"]["dataUrl"]
                with Image.open(BytesIO(base64.b64decode(data_url.split(",", 1)[1]))) as display:
                    display.verify()
                self.assertEqual(original.read_bytes(), original_bytes)
                self.assertTrue(thumbnail.read_bytes().startswith(b"RIFF"))
            finally:
                api.close()

    def test_legacy_id_can_reload_thumbnail_from_original_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "old-project.png"
            Image.new("RGB", (300, 400), "gold").save(original)
            api = StudioAPI(ROOT, root / "app-data")
            try:
                image = api.image_service.import_paths([original])["images"][0]
                image["id"] = "legacy-photo-id"
                image["internalId"] = image["id"]
                image["thumbnail"] = None
                api._project["images"] = [image]
                api.image_service = ImageService(root / "app-data" / "thumbnails")

                response = api.invoke("images.get_thumbnail", {"imageId": image["id"]})
                self.assertTrue(response["ok"], response)
                self.assertTrue(response["data"]["dataUrl"].startswith("data:image/webp;base64,"))
            finally:
                api.close()


if __name__ == "__main__":
    unittest.main()
