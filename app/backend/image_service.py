"""Image import validation and small, disk-backed thumbnail generation.

This module deliberately returns plain dictionaries so it can be used by the
PyWebView bridge without coupling it to the project's persistence models.
"""

from __future__ import annotations

import base64
import hashlib
import os
import tempfile
import threading
import uuid
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

try:
    from PIL import Image, ImageOps, UnidentifiedImageError

    _DecompressionBombError = Image.DecompressionBombError
    _DecompressionBombWarning = Image.DecompressionBombWarning
except (ImportError, OSError):  # Keep the rest of the application importable.
    Image = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]
    UnidentifiedImageError = OSError  # type: ignore[assignment,misc]
    _DecompressionBombError = Warning
    _DecompressionBombWarning = Warning


SUPPORTED_FORMATS = {".jpg", ".jpeg", ".jpe", ".png", ".webp"}
_ID_NAMESPACE = uuid.UUID("2f294baa-6a42-5f5e-98ed-b244d6fd17ce")


def default_thumbnail_dir() -> Path:
    """Return the application's local thumbnail cache directory."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "StripPrintStudio" / "thumbnails"
    return Path.home() / ".local" / "share" / "StripPrintStudio" / "thumbnails"


def _path_id(path: Path) -> str:
    # normcase is significant on Windows and harmless on case-sensitive hosts.
    canonical = os.path.normcase(str(path.resolve(strict=False)))
    return str(uuid.uuid5(_ID_NAMESPACE, canonical))


def _file_uri(path: Path) -> str:
    return path.resolve().as_uri()


class ImageService:
    """Validates imports and writes bounded thumbnails to a disk cache.

    Full-resolution Pillow images are opened only within a short context and
    never retained on this object. `thumbnail` in each record is a local file
    URI; `get_thumbnail` can return a data URI for bridge consumers that need
    an inline browser resource.
    """

    def __init__(
        self,
        thumbnail_dir: str | os.PathLike[str] | None = None,
        thumbnail_size: tuple[int, int] = (320, 320),
        thumbnail_quality: int = 78,
    ) -> None:
        if len(thumbnail_size) != 2 or min(thumbnail_size) <= 0:
            raise ValueError("thumbnail_size must contain two positive dimensions")
        if not 1 <= thumbnail_quality <= 100:
            raise ValueError("thumbnail_quality must be between 1 and 100")
        self.thumbnail_dir = Path(thumbnail_dir) if thumbnail_dir else default_thumbnail_dir()
        self.thumbnail_size = (int(thumbnail_size[0]), int(thumbnail_size[1]))
        self.thumbnail_quality = int(thumbnail_quality)
        self._thumbnail_by_id: dict[str, Path] = {}
        self._cache_lock = threading.Lock()

    def import_paths(self, paths: Iterable[str | os.PathLike[str]], *, refresh_thumbnails: bool = False) -> dict:
        """Import a batch, returning valid images and per-path rejections.

        Rejections never abort the rest of the batch. The returned `images`
        records use the frontend contract's image shape.
        """
        if Image is None:
            return {
                "images": [],
                "errors": [],
                "serviceError": {
                    "code": "dependency_missing",
                    "dependency": "Pillow",
                    "message": "Image processing is unavailable because Pillow is not installed.",
                },
            }

        images: list[dict] = []
        errors: list[dict] = []
        seen_paths: set[str] = set()

        for raw_path in paths:
            try:
                path = Path(raw_path).expanduser().resolve(strict=False)
            except (TypeError, ValueError, OSError):
                errors.append(self._error_record(raw_path, "invalid", "Invalid image path."))
                continue

            normalized = os.path.normcase(str(path))
            if normalized in seen_paths:
                continue
            seen_paths.add(normalized)

            suffix = path.suffix.lower()
            if suffix not in SUPPORTED_FORMATS:
                errors.append(self._error_record(path, "unsupported", "Unsupported image format."))
                continue
            if not path.is_file():
                errors.append(self._error_record(path, "missing", "Image file does not exist."))
                continue

            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", _DecompressionBombWarning)
                    with Image.open(path) as source:
                        source_format = (source.format or "").upper()
                        # Pillow's verify() must run before reading EXIF or
                        # dimensions; those accesses may seek the file and make
                        # verify() reject an otherwise valid image.
                        source.verify()
                    with Image.open(path) as source:
                        width, height = source.size
                        try:
                            orientation = source.getexif().get(274, 1)
                        except (AttributeError, OSError, ValueError):
                            orientation = 1
                        if orientation in {5, 6, 7, 8}:
                            width, height = height, width
                        # Extension checks alone are insufficient: reject e.g. a
                        # renamed GIF rather than claiming to import it as JPEG.
                        expected = {".jpg": {"JPEG"}, ".jpeg": {"JPEG"}, ".jpe": {"JPEG"}, ".png": {"PNG"}, ".webp": {"WEBP"}}
                        if source_format not in expected[suffix]:
                            errors.append(self._error_record(path, "unsupported", "File contents do not match a supported image format."))
                            continue
                if width <= 0 or height <= 0:
                    errors.append(self._error_record(path, "corrupt", "Image has invalid dimensions."))
                    continue

                stat = path.stat()
                image_id = _path_id(path)
                try:
                    thumbnail_path = self._write_thumbnail(path, image_id, stat.st_mtime_ns, stat.st_size, refresh=refresh_thumbnails)
                except (OSError, ValueError, _DecompressionBombError, _DecompressionBombWarning):
                    # A cache failure must not turn a valid original into a
                    # rejected import. The card can surface FAILED and Retry.
                    thumbnail_path = None
                    self._thumbnail_by_id.pop(image_id, None)
                modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
                ratio = round(width / height, 6)
                orientation = "landscape" if width > height else "portrait" if height > width else "square"
                record = {
                    "id": image_id,
                    "internalId": image_id,
                    "path": str(path),
                    "name": path.name,
                    "originalName": path.name,
                    "displayName": path.name,
                    "extension": suffix,
                    "width": int(width),
                    "height": int(height),
                    "aspectRatio": ratio,
                    "orientation": orientation,
                    "previewFit": "contain",
                    "warning": None,
                    "modifiedAt": modified,
                    "status": "ready",
                    "thumbnail": _file_uri(thumbnail_path) if thumbnail_path else None,
                }
                if thumbnail_path:
                    self._thumbnail_by_id[image_id] = thumbnail_path
                images.append(record)
            except (UnidentifiedImageError, OSError, ValueError, _DecompressionBombError, _DecompressionBombWarning) as exc:
                # Do not expose Pillow or filesystem internals in the message.
                _ = exc
                errors.append(self._error_record(path, "corrupt", "Image could not be read or is damaged."))

        return {"images": images, "errors": errors}

    def get_thumbnail(self, image_id: str, *, as_data_uri: bool = True) -> str | None:
        """Return a cached thumbnail as a data URI or local file URI."""
        path = self._thumbnail_by_id.get(str(image_id))
        if path is None or not path.is_file():
            return None
        if not as_data_uri:
            return _file_uri(path)
        try:
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError:
            return None
        return f"data:image/webp;base64,{encoded}"

    def _write_thumbnail(self, source_path: Path, image_id: str, mtime_ns: int, file_size: int, *, refresh: bool = False) -> Path:
        fingerprint = f"{image_id}:{mtime_ns}:{file_size}:{self.thumbnail_size[0]}x{self.thumbnail_size[1]}"
        cache_key = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
        destination = self.thumbnail_dir / f"{cache_key}.webp"
        with self._cache_lock:
            if destination.is_file() and not refresh:
                return destination
            self.thumbnail_dir.mkdir(parents=True, exist_ok=True)
            temporary_name: str | None = None
            frame = None
            try:
                # Reopen after verify. `thumbnail()` allows supported decoders
                # (notably JPEG) to reduce their decode size before loading all
                # pixels. EXIF orientation is applied after the reduction.
                with Image.open(source_path) as source:
                    source.thumbnail(self.thumbnail_size, Image.Resampling.LANCZOS)
                    frame = ImageOps.exif_transpose(source)
                    if frame.mode not in {"RGB", "RGBA"}:
                        converted = frame.convert("RGBA" if "A" in frame.getbands() else "RGB")
                        frame.close()
                        frame = converted
                # WebP keeps transparency and is compact for browser thumbnails.
                with tempfile.NamedTemporaryFile(dir=self.thumbnail_dir, suffix=".webp", delete=False) as temp:
                    temporary_name = temp.name
                frame.save(temporary_name, format="WEBP", quality=self.thumbnail_quality, method=4)
                os.replace(temporary_name, destination)
                return destination
            finally:
                if frame is not None:
                    frame.close()
                if temporary_name and os.path.exists(temporary_name):
                    try:
                        os.unlink(temporary_name)
                    except OSError:
                        pass

    @staticmethod
    def _error_record(path: object, status: str, message: str) -> dict:
        try:
            display_path = str(path)
            name = Path(display_path).name
            extension = Path(display_path).suffix.lower()
        except (TypeError, ValueError, OSError):
            display_path, name, extension = str(path), "", ""
        return {"path": display_path, "name": name, "extension": extension, "status": status, "error": message}


def thumbnail_data_uri_from_uri(uri: str) -> str | None:
    """Utility for a bridge/service that retained only the record's file URI."""
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    if os.name == "nt" and parsed.netloc:
        path = Path(f"//{parsed.netloc}{url2pathname(parsed.path)}")
    elif os.name == "nt":
        path = Path(url2pathname(unquote(parsed.path)))
    else:
        path = Path(unquote(parsed.path))
    try:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return None
    return f"data:image/webp;base64,{encoded}"
