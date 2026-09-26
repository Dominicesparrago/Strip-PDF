"""Project lifecycle, atomic persistence, and recoverable session snapshots."""

from __future__ import annotations

import copy
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Mapping

from .persistence import atomic_write_json, read_json_object, unwrap_document, utc_timestamp, versioned_document


PROJECT_SCHEMA_VERSION = 1
RECOVERY_SCHEMA_VERSION = 1


class ProjectPathRequiredError(ValueError):
    """Raised when Save is requested before the project has a destination."""


def _default_project(name: str = "Untitled Project") -> dict[str, Any]:
    now = utc_timestamp()
    return {
        "kind": "project",
        "schemaVersion": PROJECT_SCHEMA_VERSION,
        "projectId": str(uuid.uuid4()),
        "name": name,
        "createdAt": now,
        "updatedAt": now,
        "images": [],
        "imageOrder": [],
        "sortMode": "smart",
        "grouping": {"slotsPerStrip": 4, "incomplete": "empty"},
        "strips": [],
        "selectedTemplateId": "dcapture-clean",
        "templateOverrides": {},
        "design": None,
        "printSettings": {
            "paper": "A4",
            "orientation": "landscape",
            "stripsPerPage": 5,
            "marginMm": 5.0,
            "gapMm": 2.0,
            "cutGuides": False,
        },
        "pdfSettings": {"resolutionDpi": 300},
        "themeMode": "system",
    }


class ProjectService:
    """Own the active project and store recovery under the supplied data root.

    ``data_dir`` should be the app's per-user data directory (normally
    ``%LOCALAPPDATA%/StripPrintStudio``). Project image paths are references;
    project operations never copy or delete original photographs.
    """

    def __init__(self, data_dir: str | os.PathLike[str], autosave_delay: float = 1.0):
        self.data_dir = Path(data_dir)
        self.recovery_path = self.data_dir / "recovery" / "session-autosave.json"
        self.current_project: dict[str, Any] = _default_project()
        self.current_path: Path | None = None
        self.autosave_delay = max(0.0, float(autosave_delay))
        self._timer: threading.Timer | None = None
        self._dirty = False
        self._revision = 0
        self._lock = threading.RLock()

    @property
    def project(self) -> dict[str, Any]:
        """Return a detached snapshot suitable for API serialization."""

        with self._lock:
            return copy.deepcopy(self.current_project)

    @property
    def path(self) -> str | None:
        with self._lock:
            return str(self.current_path) if self.current_path else None

    @property
    def is_dirty(self) -> bool:
        """Whether the active project has edits since its last explicit save."""

        with self._lock:
            return self._dirty

    def new_project(self) -> dict[str, Any]:
        with self._lock:
            self._cancel_timer_locked()
            self._revision += 1
            self._dirty = False
            self.current_project = _default_project()
            self.current_path = None
            project = copy.deepcopy(self.current_project)
        self.recovery_path.unlink(missing_ok=True)
        return project

    def open_project(self, path: str | os.PathLike[str]) -> dict[str, Any]:
        source = Path(path).expanduser()
        document = unwrap_document(read_json_object(source), "project")
        normalized = self._normalize_project(document)
        normalized["name"] = str(normalized.get("name") or source.stem)
        normalized["missingImagePaths"] = self._missing_image_paths(normalized)
        with self._lock:
            self._cancel_timer_locked()
            self._revision += 1
            self._dirty = False
            self.current_project = normalized
            self.current_path = source.resolve()
            project = copy.deepcopy(self.current_project)
        self.recovery_path.unlink(missing_ok=True)
        return project

    def save_project(self, project: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            path = self.current_path
            current = copy.deepcopy(project if project is not None else self.current_project)
        if path is None:
            raise ProjectPathRequiredError("Choose a project file with Save As before saving")
        return self._save_to_path(path, current, make_active=True)

    def save_as_project(
        self,
        path: str | os.PathLike[str],
        project: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        destination = Path(path).expanduser()
        if not destination.suffix:
            destination = destination.with_suffix(".stripproj")
        with self._lock:
            current = copy.deepcopy(project if project is not None else self.current_project)
        return self._save_to_path(destination, current, make_active=True)

    def _save_to_path(
        self, destination: Path, project: Mapping[str, Any], make_active: bool
    ) -> dict[str, Any]:
        normalized = self._normalize_project(dict(project))
        normalized.pop("missingImagePaths", None)
        normalized["updatedAt"] = utc_timestamp()
        name = str(normalized.get("name") or "").strip()
        normalized["name"] = destination.stem if not name or name == "Untitled Project" else name
        normalized = versioned_document("project", normalized, PROJECT_SCHEMA_VERSION)
        atomic_write_json(destination, normalized)
        with self._lock:
            self._cancel_timer_locked()
            self._revision += 1
            self._dirty = False
            if make_active:
                self.current_project = copy.deepcopy(normalized)
                self.current_project["missingImagePaths"] = self._missing_image_paths(self.current_project)
                self.current_path = destination.resolve()
        self.recovery_path.unlink(missing_ok=True)
        return {"path": str(destination.resolve()), "project": copy.deepcopy(normalized)}

    def schedule_autosave(self, project: Mapping[str, Any] | None = None) -> None:
        """Debounce a recovery snapshot; call after state mutations."""

        with self._lock:
            if project is not None:
                self.current_project = self._normalize_project(dict(project))
            self._dirty = True
            self._revision += 1
            revision = self._revision
            snapshot = copy.deepcopy(self.current_project)
            active_path = str(self.current_path) if self.current_path else None
            self._cancel_timer_locked()
            timer = threading.Timer(
                self.autosave_delay, self._write_recovery_snapshot, (snapshot, active_path, revision)
            )
            timer.daemon = True
            self._timer = timer
            timer.start()

    def autosave(self, project: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Write a recovery snapshot immediately (useful at orderly shutdown)."""

        with self._lock:
            self._cancel_timer_locked()
            if project is not None:
                self.current_project = self._normalize_project(dict(project))
            self._dirty = True
            self._revision += 1
            revision = self._revision
            snapshot = copy.deepcopy(self.current_project)
            active_path = str(self.current_path) if self.current_path else None
        return self._write_recovery_snapshot(snapshot, active_path, revision)

    def _write_recovery_snapshot(
        self, project: Mapping[str, Any], active_path: str | None, revision: int | None = None
    ) -> dict[str, Any]:
        with self._lock:
            # A debounced write can already be running when Save/Open happens.
            # Do not let that older snapshot recreate stale recovery afterward.
            if revision is not None and revision != self._revision:
                return {"path": str(self.recovery_path), "skipped": True}
            if not self._dirty:
                return {"path": str(self.recovery_path), "skipped": True}
            snapshot = self._normalize_project(dict(project))
            snapshot.pop("missingImagePaths", None)
            document = versioned_document(
                "session-recovery",
                {
                    "project": snapshot,
                    "activeProjectPath": active_path,
                    "savedAt": utc_timestamp(),
                },
                RECOVERY_SCHEMA_VERSION,
            )
            atomic_write_json(self.recovery_path, document)
            return {"path": str(self.recovery_path), "savedAt": document["savedAt"]}

    def get_recovery(self) -> dict[str, Any]:
        """Describe a valid recovery snapshot without changing active state."""

        if not self.recovery_path.is_file():
            return {"available": False}
        try:
            document = unwrap_document(read_json_object(self.recovery_path), "session-recovery")
            project = self._normalize_project(document.get("project", {}))
        except (OSError, ValueError, TypeError) as error:
            return {"available": False, "error": str(error), "corrupt": True}
        return {
            "available": True,
            "projectName": str(project.get("name") or "Untitled Project"),
            "imageCount": len(project.get("images", [])),
            "updatedAt": document.get("savedAt") or project.get("updatedAt"),
            "projectPath": document.get("activeProjectPath"),
        }

    def restore_recovery(self) -> dict[str, Any]:
        document = unwrap_document(read_json_object(self.recovery_path), "session-recovery")
        project = self._normalize_project(document.get("project", {}))
        project["missingImagePaths"] = self._missing_image_paths(project)
        active_path = document.get("activeProjectPath")
        with self._lock:
            self._cancel_timer_locked()
            self._revision += 1
            self._dirty = True
            self.current_project = project
            self.current_path = Path(active_path) if isinstance(active_path, str) and active_path else None
        return copy.deepcopy(project)

    def start_new_session(self) -> dict[str, Any]:
        """Discard only the recovery snapshot, then initialize a blank project."""

        with self._lock:
            self._cancel_timer_locked()
        try:
            self.recovery_path.unlink(missing_ok=True)
        except OSError as error:
            raise OSError(f"Could not discard session recovery: {error}") from error
        return self.new_project()

    def clear_recovery(self) -> None:
        with self._lock:
            self._cancel_timer_locked()
        self.recovery_path.unlink(missing_ok=True)

    def flush_autosave(self, project: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if not self.is_dirty:
            self.clear_recovery()
            return {"path": str(self.recovery_path), "cleared": True}
        return self.autosave(project)

    def close(self) -> None:
        """Cancel pending delayed autosave; call after ``flush_autosave`` if desired."""

        with self._lock:
            self._cancel_timer_locked()

    def _cancel_timer_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    @staticmethod
    def _normalize_project(source: Mapping[str, Any]) -> dict[str, Any]:
        project = copy.deepcopy(dict(source))
        raw_version = project.get("schemaVersion", PROJECT_SCHEMA_VERSION)
        try:
            version = int(raw_version)
        except (TypeError, ValueError) as error:
            raise ValueError("Project has an invalid schemaVersion") from error
        if version > PROJECT_SCHEMA_VERSION:
            raise ValueError(f"Project schema version {version} is newer than supported")
        if version < 1:
            raise ValueError(f"Project schema version {version} is invalid")
        project["schemaVersion"] = PROJECT_SCHEMA_VERSION
        project["kind"] = "project"
        project.setdefault("projectId", str(uuid.uuid4()))
        project.setdefault("name", "Untitled Project")
        project.setdefault("createdAt", utc_timestamp())
        project.setdefault("updatedAt", utc_timestamp())
        for key, default in (("images", []), ("imageOrder", []), ("strips", [])):
            if not isinstance(project.get(key), list):
                project[key] = copy.deepcopy(default)
        project.setdefault("sortMode", "smart")
        grouping = project.get("grouping")
        if not isinstance(grouping, dict):
            grouping = {}
        project["grouping"] = {"slotsPerStrip": 4, "incomplete": "empty", **grouping}
        project.setdefault("selectedTemplateId", "dcapture-clean")
        if not isinstance(project.get("templateOverrides"), dict):
            project["templateOverrides"] = {}
        project.setdefault("design", None)
        if not isinstance(project.get("printSettings"), dict):
            project["printSettings"] = {}
        if not isinstance(project.get("pdfSettings"), dict):
            project["pdfSettings"] = {}
        project.setdefault("themeMode", "system")
        return project

    @staticmethod
    def _missing_image_paths(project: Mapping[str, Any]) -> list[str]:
        missing: list[str] = []
        for image in project.get("images", []):
            if not isinstance(image, dict):
                continue
            image_path = image.get("path")
            if not isinstance(image_path, str) or not image_path:
                continue
            if not Path(image_path).expanduser().is_file():
                missing.append(image_path)
                image["status"] = "missing"
            else:
                image["status"] = "available"
        return missing
