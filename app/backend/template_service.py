"""Built-in/custom template storage and bounded structured design history."""

from __future__ import annotations

import copy
import os
import re
import uuid
from pathlib import Path
from typing import Any, Mapping

from .persistence import atomic_write_json, read_json_object, unwrap_document, utc_timestamp, versioned_document
from .strip_geometry import legacy_strip_spec, normalize_strip_spec


TEMPLATE_SCHEMA_VERSION = 1
CUSTOM_ID_RE = re.compile(r"^custom-[0-9a-f]{32}$")


class TemplateNotFoundError(KeyError):
    pass


class BuiltInTemplateError(PermissionError):
    pass


class TemplateService:
    """Load immutable built-ins and save user templates in a separate folder.

    ``builtin_dir`` may contain template JSON files and referenced assets.
    Custom templates are written only to ``custom_dir`` and are never saved
    into or over a built-in file.
    """

    def __init__(
        self,
        builtin_dir: str | os.PathLike[str],
        custom_dir: str | os.PathLike[str],
    ):
        self.builtin_dir = Path(builtin_dir)
        self.custom_dir = Path(custom_dir)

    def list_templates(self) -> list[dict[str, Any]]:
        templates = self._read_builtin_templates() + self._read_custom_templates()
        templates.sort(key=lambda item: (not item["builtIn"], item["name"].casefold(), item["templateId"]))
        return templates

    def load_template(self, template_id: str) -> dict[str, Any]:
        if not isinstance(template_id, str) or not template_id:
            raise TemplateNotFoundError("A template ID is required")
        for template in self._read_custom_templates():
            if template["templateId"] == template_id:
                return template
        for template in self._read_builtin_templates():
            if template["templateId"] == template_id:
                return template
        raise TemplateNotFoundError(f"Template {template_id!r} was not found")

    def save_as(
        self,
        name: str,
        design: Mapping[str, Any],
        source_template_id: str | None = None,
        *,
        physical: Mapping[str, Any] | None = None,
        slots_per_strip: int | None = None,
        strip_spec: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Save a design state as a new custom template, leaving its source intact."""

        if not isinstance(design, Mapping):
            raise TypeError("Design must be a structured object")
        template_id = self._new_id()
        record: dict[str, Any] = {
            "templateId": template_id,
            "id": template_id,
            "name": self._validate_name(name),
            "builtIn": False,
            "createdAt": utc_timestamp(),
            "design": copy.deepcopy(dict(design)),
        }
        if source_template_id:
            record["sourceTemplateId"] = str(source_template_id)
        if physical is not None:
            record["physical"] = copy.deepcopy(dict(physical))
        if slots_per_strip is not None:
            record["slotsPerStrip"] = int(slots_per_strip)
        if strip_spec is not None:
            record["stripSpec"] = normalize_strip_spec(strip_spec)
        else:
            record["stripSpec"] = legacy_strip_spec(design, physical=physical,
                                                   frame_count=slots_per_strip)
        return self._write_custom(record)

    def duplicate(self, template_id: str, name: str | None = None) -> dict[str, Any]:
        """Create an independent custom copy of a built-in or custom template."""

        source = self.load_template(template_id)
        duplicate = {
            key: copy.deepcopy(value)
            for key, value in source.items()
            if key not in {"templateId", "id", "builtIn", "readOnly", "sourcePath", "assetRoot"}
        }
        duplicate["templateId"] = self._new_id()
        duplicate["id"] = duplicate["templateId"]
        duplicate["name"] = self._validate_name(name or f"{source['name']} Copy")
        duplicate["builtIn"] = False
        duplicate["sourceTemplateId"] = template_id
        duplicate["createdAt"] = utc_timestamp()
        return self._write_custom(duplicate)

    def rename(self, template_id: str, name: str) -> dict[str, Any]:
        path, template = self._find_custom(template_id)
        if path is None:
            raise BuiltInTemplateError("Built-in templates cannot be renamed; duplicate it first")
        template["name"] = self._validate_name(name)
        template["updatedAt"] = utc_timestamp()
        return self._write_custom(template, path=path)

    def delete(self, template_id: str) -> dict[str, Any]:
        path, _ = self._find_custom(template_id)
        if path is None:
            raise BuiltInTemplateError("Built-in templates cannot be deleted")
        path.unlink()
        return {"templateId": template_id, "deleted": True}

    def reset(self, template_id: str) -> dict[str, Any]:
        """Return the saved defaults for a template without mutating its file."""

        return self.load_template(template_id)

    def _read_builtin_templates(self) -> list[dict[str, Any]]:
        if not self.builtin_dir.is_dir():
            return []
        records: list[dict[str, Any]] = []
        for path in sorted(self.builtin_dir.rglob("*.json")):
            try:
                raw = read_json_object(path)
                record = unwrap_document(raw, "template")
                if isinstance(record.get("template"), dict):
                    record = {**record, **record["template"]}
                version = self._version(record)
                if version > TEMPLATE_SCHEMA_VERSION:
                    continue
                template_id = str(record.get("templateId") or record.get("id") or f"builtin-{path.stem}")
                record["templateId"] = template_id
                record["id"] = str(record.get("id") or template_id)
                record["name"] = str(record.get("name") or path.stem.replace("-", " ").title())
                record["kind"] = "template"
                record["schemaVersion"] = TEMPLATE_SCHEMA_VERSION
                record["builtIn"] = True
                record["readOnly"] = True
                # Keep the origin useful for referenced SVG/PNG assets without
                # rewriting any asset path or changing the source files.
                record["sourcePath"] = str(path.resolve())
                record["assetRoot"] = str(path.parent.resolve())
                record["stripSpec"] = self._strip_spec_for_record(record)
                records.append(record)
            except (OSError, ValueError, TypeError):
                # A malformed optional built-in is ignored so one bad asset
                # definition does not prevent the rest of the studio opening.
                continue
        return records

    def _read_custom_templates(self) -> list[dict[str, Any]]:
        if not self.custom_dir.is_dir():
            return []
        records: list[dict[str, Any]] = []
        for path in sorted(self.custom_dir.glob("custom-*.json")):
            try:
                record = unwrap_document(read_json_object(path), "template")
                template_id = str(record.get("templateId") or record.get("id") or path.stem)
                if not CUSTOM_ID_RE.fullmatch(template_id):
                    continue
                if self._version(record) > TEMPLATE_SCHEMA_VERSION:
                    continue
                record["templateId"] = template_id
                record["id"] = template_id
                record["name"] = str(record.get("name") or "Untitled Template")
                record["kind"] = "template"
                record["schemaVersion"] = TEMPLATE_SCHEMA_VERSION
                record["builtIn"] = False
                record["readOnly"] = False
                record.pop("sourcePath", None)
                record["stripSpec"] = self._strip_spec_for_record(record)
                records.append(record)
            except (OSError, ValueError, TypeError):
                continue
        return records

    def _find_custom(self, template_id: str) -> tuple[Path | None, dict[str, Any]]:
        for path in self.custom_dir.glob("custom-*.json") if self.custom_dir.is_dir() else ():
            try:
                record = unwrap_document(read_json_object(path), "template")
            except (OSError, ValueError, TypeError):
                continue
            found_id = str(record.get("templateId") or record.get("id") or path.stem)
            if found_id == template_id:
                return path, record
        return None, {}

    def _write_custom(self, record: Mapping[str, Any], path: Path | None = None) -> dict[str, Any]:
        output = copy.deepcopy(dict(record))
        template_id = str(output.get("templateId") or output.get("id") or self._new_id())
        if not CUSTOM_ID_RE.fullmatch(template_id):
            raise ValueError("Custom template ID is invalid")
        destination = path or (self.custom_dir / f"{template_id}.json")
        # A caller cannot redirect writes to an arbitrary path or a built-in.
        if destination.parent.resolve() != self.custom_dir.resolve():
            raise ValueError("Custom templates must remain inside the custom template directory")
        output["kind"] = "template"
        output["schemaVersion"] = TEMPLATE_SCHEMA_VERSION
        output["templateId"] = template_id
        output["id"] = template_id
        output["name"] = self._validate_name(str(output.get("name") or "Untitled Template"))
        output["builtIn"] = False
        output["readOnly"] = False
        output["updatedAt"] = utc_timestamp()
        output.pop("sourcePath", None)
        output.pop("assetRoot", None)
        output["stripSpec"] = self._strip_spec_for_record(output)
        atomic_write_json(destination, output)
        return copy.deepcopy(output)

    @staticmethod
    def _strip_spec_for_record(record: Mapping[str, Any]) -> dict[str, Any]:
        if isinstance(record.get("stripSpec"), Mapping):
            return normalize_strip_spec(record["stripSpec"])
        return legacy_strip_spec(record.get("design"), physical=record.get("physical"),
                                 frame_count=record.get("slotsPerStrip"))

    @staticmethod
    def _version(record: Mapping[str, Any]) -> int:
        try:
            version = int(record.get("schemaVersion", TEMPLATE_SCHEMA_VERSION))
        except (TypeError, ValueError) as error:
            raise ValueError("Template has an invalid schemaVersion") from error
        if version < 1:
            raise ValueError("Template schemaVersion is invalid")
        return version

    @staticmethod
    def _new_id() -> str:
        return f"custom-{uuid.uuid4().hex}"

    @staticmethod
    def _validate_name(name: str) -> str:
        cleaned = str(name).strip()
        if not cleaned:
            raise ValueError("Template name cannot be empty")
        if len(cleaned) > 100:
            raise ValueError("Template name must be 100 characters or fewer")
        return cleaned


class DesignHistory:
    """Bounded undo/redo over structured design state, never raster snapshots."""

    def __init__(self, initial_state: Mapping[str, Any] | None = None, max_entries: int = 50):
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self.max_entries = int(max_entries)
        self._current: dict[str, Any] = copy.deepcopy(dict(initial_state or {}))
        self._current_label = "Initial state"
        self._past: list[tuple[dict[str, Any], str]] = []
        self._future: list[tuple[dict[str, Any], str]] = []

    @property
    def current(self) -> dict[str, Any]:
        return copy.deepcopy(self._current)

    @property
    def can_undo(self) -> bool:
        return bool(self._past)

    @property
    def can_redo(self) -> bool:
        return bool(self._future)

    def commit(self, state: Mapping[str, Any], label: str = "Edit") -> dict[str, Any]:
        next_state = copy.deepcopy(dict(state))
        if next_state == self._current:
            return self.current
        self._past.append((self._current, self._current_label))
        if len(self._past) > self.max_entries:
            del self._past[: len(self._past) - self.max_entries]
        self._current = next_state
        self._current_label = str(label or "Edit")
        self._future.clear()
        return self.current

    def undo(self) -> dict[str, Any] | None:
        if not self._past:
            return None
        self._future.append((self._current, self._current_label))
        self._current, self._current_label = self._past.pop()
        return self.current

    def redo(self) -> dict[str, Any] | None:
        if not self._future:
            return None
        self._past.append((self._current, self._current_label))
        if len(self._past) > self.max_entries:
            del self._past[: len(self._past) - self.max_entries]
        self._current, self._current_label = self._future.pop()
        return self.current

    def reset(self, state: Mapping[str, Any]) -> dict[str, Any]:
        self._current = copy.deepcopy(dict(state))
        self._current_label = "Reset"
        self._past.clear()
        self._future.clear()
        return self.current

    def status(self) -> dict[str, Any]:
        return {
            "canUndo": self.can_undo,
            "canRedo": self.can_redo,
            "undoLabel": self._past[-1][1] if self._past else None,
            "redoLabel": self._future[-1][1] if self._future else None,
            "undoDepth": len(self._past),
            "redoDepth": len(self._future),
        }
