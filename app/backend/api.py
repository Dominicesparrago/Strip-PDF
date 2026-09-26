"""Allowlisted PyWebView bridge and application service integration."""

from __future__ import annotations

import copy
import json
import math
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Mapping

from app.backend.image_service import ImageService, thumbnail_data_uri_from_uri
from app.backend.models import DEFAULT_PRINT_SETTINGS, default_design, new_project
from app.backend.persistence import atomic_write_json, read_json_object
from app.backend.pdf_service import render_preview_png, write_pdf
from app.backend.print_layout import calculate_layout, normalize_settings
from app.backend.project_service import ProjectPathRequiredError, ProjectService
from app.backend.sorting_service import reorder_images, sort_images
from app.backend.strip_renderer import render_strips
from app.backend.strip_service import generate_strips, set_slot_crop
from app.backend.template_service import DesignHistory, TemplateService
from app.backend.validation_service import validate_print


def _data_directory() -> Path:
    override = os.environ.get("STRIP_PRINT_STUDIO_DATA_DIR")
    if override:
        return Path(override).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "StripPrintStudio"
    return Path.home() / ".local" / "share" / "StripPrintStudio"


class StudioAPI:
    """Single structured entry point exposed to JavaScript as ``invoke``."""

    def __init__(self, root: str | os.PathLike[str], data_dir: str | os.PathLike[str] | None = None):
        self.root = Path(root).resolve()
        self.data_dir = Path(data_dir).expanduser() if data_dir else _data_directory()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.project_service = ProjectService(self.data_dir)
        self.preferences_path = self.data_dir / "preferences.json"
        self._theme_mode = self._load_theme_preference()
        self.template_service = TemplateService(
            self.root / "templates" / "builtin",
            self.data_dir / "templates" / "custom",
        )
        self.image_service = ImageService(self.data_dir / "thumbnails")
        self.project_lock = threading.RLock()
        self._project = self._normalize_project(self.project_service.project)
        self._project["theme"] = {"mode": self._theme_mode}
        self._project["themeMode"] = self._theme_mode
        self._history = DesignHistory({"design": self._project["design"], "strips": self._project["strips"]})
        self._window = None
        self._jobs_lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="strip-studio")
        self._closed = False

    def attach_window(self, window: Any) -> None:
        self._window = window
        try:
            window.events.closing += self._on_closing
        except Exception:
            pass

    def invoke(self, method: str, params: dict | None = None) -> dict:
        """Dispatch an allowlisted method; never expose Python tracebacks to the UI."""
        if not isinstance(method, str) or not method or len(method) > 100:
            return self._failure("A valid method name is required.")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return self._failure("Method parameters must be an object.")
        try:
            with self.project_lock:
                data = self._dispatch(method, params)
            return {"ok": True, "data": data}
        except (ValueError, KeyError, TypeError, OSError, RuntimeError, PermissionError) as exc:
            return self._failure(str(exc) or "The request could not be completed.", {"type": type(exc).__name__})
        except Exception as exc:
            # Keep the UI response predictable. The concise message remains
            # useful for diagnostics without leaking a Python traceback.
            return self._failure("The request could not be completed.", {"type": type(exc).__name__, "reason": str(exc)[:300]})

    @staticmethod
    def _failure(message: str, detail: Any = None) -> dict:
        result = {"ok": False, "error": message or "The request could not be completed."}
        if detail is not None:
            result["detail"] = detail
        return result

    def _dispatch(self, method: str, p: dict) -> Any:
        routes: dict[str, Callable[[dict], Any]] = {
            "app.get_state": lambda _: self._app_state(),
            "app.get_info": lambda _: self._app_info(),
            "session.get_recovery": lambda _: self.project_service.get_recovery(),
            "session.restore": lambda _: self._restore_session(),
            "session.start_new": lambda _: self._start_new_session(),
            "images.pick": lambda _: self._pick_images(),
            "images.import_paths": self._import_paths,
            "images.remove": self._remove_images,
            "images.sort": self._sort,
            "images.reorder": self._reorder,
            "images.get_thumbnail": self._get_thumbnail,
            "strips.generate": self._generate_groups,
            "strips.set_crop": self._set_crop,
            "templates.list": lambda _: {"templates": self.template_service.list_templates()},
            "templates.load": self._load_template,
            "templates.save_as": self._save_template,
            "templates.duplicate": self._duplicate_template,
            "templates.rename": self._rename_template,
            "templates.delete": self._delete_template,
            "templates.reset": self._reset_template,
            "design.get": lambda _: self._design_state(),
            "design.update": self._update_design,
            "design.add_element": self._add_design_element,
            "design.undo": lambda _: self._history_move("undo"),
            "design.redo": lambda _: self._history_move("redo"),
            "design.align": self._align_design,
            "design.set_snap": self._set_snap,
            "print.get_settings": lambda _: copy.deepcopy(self._project["printSettings"]),
            "print.update_settings": self._update_print_settings,
            "print.get_layout": lambda _: self._layout(),
            "print.validate": lambda _: self._validate(),
            "print.preview": self._preview,
            "print.export": self._export,
            "print.presets.list": lambda _: {"presets": self._list_presets()},
            "print.presets.save": self._save_preset,
            "project.new": lambda _: self._new_project(),
            "project.open": self._open_project,
            "project.save": lambda _: self._save_project(),
            "project.save_as": self._save_project_as,
            "theme.set": self._set_theme,
            "jobs.get": self._get_job,
        }
        route = routes.get(method)
        if route is None:
            raise ValueError(f"Unsupported method: {method}")
        return route(p)

    def _app_state(self) -> dict:
        with self.project_lock:
            project = copy.deepcopy(self._project)
            path = self.project_service.path
        project["path"] = path
        project["theme"] = self._theme_state(self._theme_mode)
        project["themeMode"] = self._theme_mode
        return {"project": project, "path": path, "theme": project["theme"]}

    def _load_theme_preference(self) -> str:
        try:
            mode = str(read_json_object(self.preferences_path).get("themeMode", "system"))
        except (OSError, ValueError, TypeError):
            mode = "system"
        return mode if mode in {"light", "dark", "system"} else "system"

    def _app_info(self) -> dict:
        return {"name": "Strip Print Studio", "version": "0.1.0", "dataDirectory": str(self.data_dir),
                "capabilities": {"pdf": True, "supportedImages": ["jpg", "jpeg", "png", "webp"], "paper": ["A4 landscape"]}}

    def _normalize_project(self, source: Mapping[str, Any]) -> dict:
        project = copy.deepcopy(dict(source))
        if not project.get("id") and project.get("projectId"):
            project["id"] = project["projectId"]
        project.setdefault("schemaVersion", 1)
        project.setdefault("name", "Untitled Project")
        project.setdefault("images", [])
        project.setdefault("strips", [])
        project.setdefault("selectedTemplateId", "dcapture-clean")
        project.setdefault("sortMode", "smart")
        grouping = project.get("grouping") if isinstance(project.get("grouping"), dict) else {}
        project["slotsPerStrip"] = int(project.get("slotsPerStrip", grouping.get("slotsPerStrip", 4)) or 4)
        project["incompleteHandling"] = str(project.get("incompleteHandling", grouping.get("incomplete", "empty")) or "empty")
        project["grouping"] = {"slotsPerStrip": project["slotsPerStrip"], "incomplete": project["incompleteHandling"]}
        project["printSettings"] = normalize_settings({**DEFAULT_PRINT_SETTINGS, **(project.get("printSettings") or {})})
        project.setdefault("pdfSettings", {"resolutionDpi": project["printSettings"]["resolutionDpi"]})
        mode = project.get("theme", {}).get("mode") if isinstance(project.get("theme"), dict) else None
        project["theme"] = {"mode": mode or project.get("themeMode", "system")}
        project["themeMode"] = project["theme"]["mode"]
        if not isinstance(project.get("imageOrder"), list) or not project["imageOrder"]:
            project["imageOrder"] = [image.get("id") for image in project["images"] if isinstance(image, dict) and image.get("id")]
        if not isinstance(project.get("design"), dict):
            try:
                template = self.template_service.load_template(project["selectedTemplateId"])
                project["design"] = copy.deepcopy(template.get("design") or default_design())
            except (ValueError, KeyError, OSError):
                project["design"] = default_design()
        design = project["design"]
        if not isinstance(design.get("artboard"), dict):
            design["artboard"] = default_design()["artboard"]
        design["artboard"].setdefault("widthMm", project["printSettings"]["stripWidthMm"])
        design["artboard"].setdefault("heightMm", project["printSettings"]["stripHeightMm"])
        return project

    def _commit_project(self, project: Mapping[str, Any], *, schedule: bool = True) -> dict:
        normalized = self._normalize_project(project)
        normalized["theme"] = {"mode": self._theme_mode}
        normalized["themeMode"] = self._theme_mode
        with self.project_lock:
            self._project = normalized
        if schedule:
            self.project_service.schedule_autosave(normalized)
        return copy.deepcopy(normalized)

    def _replace_project(self, project: Mapping[str, Any], *, reset_history: bool = True) -> dict:
        normalized = self._normalize_project(project)
        # Theme is an application preference, so opening a project or starting
        # a fresh one does not silently override the user's selected mode.
        normalized["theme"] = {"mode": self._theme_mode}
        normalized["themeMode"] = self._theme_mode
        with self.project_lock:
            self._project = normalized
        if reset_history:
            self._history.reset({"design": normalized["design"], "strips": normalized["strips"]})
        return copy.deepcopy(normalized)

    def _refresh_project(self) -> dict:
        self._project = self._normalize_project(self._project)
        return self._project

    def _restore_session(self) -> dict:
        project = self.project_service.restore_recovery()
        return {"project": self._replace_project(project)}

    def _start_new_session(self) -> dict:
        project = self.project_service.start_new_session()
        project = self._normalize_project(project)
        return {"project": self._replace_project(project)}

    def _new_project(self) -> dict:
        project = self.project_service.new_project()
        project = self._normalize_project(project)
        return {"project": self._replace_project(project)}

    def _pick_images(self) -> dict:
        paths = self._dialog_open_images()
        if not paths:
            return {"cancelled": True}
        return self._queue_job("image_import", lambda: self._do_import(paths))

    def _import_paths(self, p: dict) -> dict:
        paths = p.get("paths")
        if not isinstance(paths, list):
            raise ValueError("Image paths must be provided as a list.")
        return self._queue_job("image_import", lambda: self._do_import(paths))

    def _do_import(self, paths: list[str]) -> dict:
        result = self.image_service.import_paths(paths)
        if result.get("serviceError"):
            info = result["serviceError"]
            raise RuntimeError(info.get("message", "Image processing is unavailable."))
        # Keep the read/merge/write phase atomic with respect to other bridge
        # calls and concurrently finishing imports. Decoding happened above,
        # outside the lock, so the UI remains responsive.
        with self.project_lock:
            project = copy.deepcopy(self._project)
            existing = {str(image.get("id")): image for image in project.get("images", []) if isinstance(image, dict)}
            added = []
            for image in result.get("images", []):
                image_id = str(image.get("id"))
                if image_id not in existing:
                    existing[image_id] = image
                    added.append(image)
            combined = list(existing.values())
            mode = project.get("sortMode", "smart")
            if mode == "manual":
                order = list(project.get("imageOrder", [])) + [image["id"] for image in added]
                ordered = reorder_images(combined, order)
            else:
                ordered = sort_images(combined, mode)
            project["images"] = ordered
            project["imageOrder"] = [image["id"] for image in ordered]
            if project.get("strips"):
                self._regroup(project)
            self._reset_history(project)
            project = self._commit_project(project)
        return {"images": added, "errors": result.get("errors", []), "project": project}

    def _remove_images(self, p: dict) -> dict:
        ids = {str(value) for value in p.get("imageIds", [])}
        project = copy.deepcopy(self._project)
        project["images"] = [image for image in project["images"] if str(image.get("id")) not in ids]
        project["imageOrder"] = [value for value in project["imageOrder"] if str(value) not in ids]
        if project.get("strips"):
            self._regroup(project)
        self._reset_history(project)
        return {"project": self._commit_project(project)}

    def _sort(self, p: dict) -> dict:
        mode = str(p.get("mode", "smart"))
        project = copy.deepcopy(self._project)
        ordered = sort_images(project["images"], mode, project.get("imageOrder"))
        project["sortMode"] = mode
        project["images"] = ordered
        project["imageOrder"] = [image["id"] for image in ordered]
        if project.get("strips"):
            self._regroup(project)
        self._reset_history(project)
        return {"project": self._commit_project(project)}

    def _reorder(self, p: dict) -> dict:
        order = p.get("imageIds")
        if not isinstance(order, list):
            raise ValueError("imageIds must be a list.")
        project = copy.deepcopy(self._project)
        project["images"] = reorder_images(project["images"], order)
        project["imageOrder"] = [image["id"] for image in project["images"]]
        project["sortMode"] = "manual"
        if project.get("strips"):
            self._regroup(project)
        self._reset_history(project)
        return {"project": self._commit_project(project)}

    def _get_thumbnail(self, p: dict) -> dict:
        image_id = str(p.get("imageId", ""))
        image = next((item for item in self._project["images"] if str(item.get("id")) == image_id), None)
        if image is None:
            raise ValueError("Image was not found in the active project.")
        data_uri = self.image_service.get_thumbnail(image_id)
        if data_uri is None and image.get("thumbnail"):
            data_uri = thumbnail_data_uri_from_uri(str(image["thumbnail"]))
        if data_uri is None and image.get("path"):
            refreshed = self.image_service.import_paths([image["path"]])
            if refreshed.get("images"):
                data_uri = self.image_service.get_thumbnail(image_id)
        return {"imageId": image_id, "dataUrl": data_uri}

    def _generate_groups(self, p: dict) -> dict:
        project = copy.deepcopy(self._project)
        slots_per_strip = int(p.get("slotsPerStrip", project.get("slotsPerStrip", 4)))
        incomplete = str(p.get("incomplete", project.get("incompleteHandling", "empty")))
        project["slotsPerStrip"] = slots_per_strip
        project["incompleteHandling"] = incomplete
        project["grouping"] = {"slotsPerStrip": slots_per_strip, "incomplete": incomplete}
        self._regroup(project)
        self._reset_history(project)
        return {"project": self._commit_project(project), "strips": copy.deepcopy(project["strips"])}

    def _regroup(self, project: dict) -> None:
        result = generate_strips(
            project.get("images", []),
            int(project.get("slotsPerStrip", 4)),
            str(project.get("incompleteHandling", "empty")),
            str(project.get("selectedTemplateId", "dcapture-clean")),
            project.get("strips", []),
        )
        project["strips"] = result["strips"]
        project["incompleteImageIds"] = result["incompleteImageIds"]
        project["excludedImageIds"] = result["excludedImageIds"]

    def _reset_history(self, project: Mapping[str, Any]) -> None:
        self._history.reset({"design": project["design"], "strips": project.get("strips", [])})

    def _set_crop(self, p: dict) -> dict:
        project = copy.deepcopy(self._project)
        crop = dict(p.get("crop") or {})
        strip = set_slot_crop(project["strips"], str(p.get("stripId", "")), int(p.get("slotIndex", 0)), crop)
        self._history.commit({"design": project["design"], "strips": project["strips"]}, "Adjust photo crop")
        project = self._commit_project(project)
        return {"project": project, "strip": strip}

    def _load_template(self, p: dict) -> dict:
        template_id = str(p.get("templateId", ""))
        template = self.template_service.load_template(template_id)
        project = copy.deepcopy(self._project)
        project["selectedTemplateId"] = template_id
        project["design"] = copy.deepcopy(template.get("design") or default_design())
        physical = template.get("physical") if isinstance(template.get("physical"), dict) else {}
        project["printSettings"]["stripWidthMm"] = float(physical.get("widthMm", project["printSettings"]["stripWidthMm"]))
        project["printSettings"]["stripHeightMm"] = float(physical.get("heightMm", project["printSettings"]["stripHeightMm"]))
        slots = int(template.get("slotsPerStrip", project.get("slotsPerStrip", 4)))
        project["slotsPerStrip"] = slots
        project["grouping"]["slotsPerStrip"] = slots
        if project.get("images"):
            self._regroup(project)
        project = self._commit_project(project)
        self._history.reset({"design": project["design"], "strips": project["strips"]})
        return {"template": template, "project": project}

    def _save_template(self, p: dict) -> dict:
        name = str(p.get("name", ""))
        settings = self._project["printSettings"]
        template = self.template_service.save_as(
            name,
            self._project["design"],
            self._project.get("selectedTemplateId"),
            physical={"widthMm": settings["stripWidthMm"], "heightMm": settings["stripHeightMm"]},
            slots_per_strip=self._project.get("slotsPerStrip", 4),
        )
        return {"template": template}

    def _duplicate_template(self, p: dict) -> dict:
        template = self.template_service.duplicate(str(p.get("templateId", "")), p.get("name"))
        return {"template": template}

    def _rename_template(self, p: dict) -> dict:
        return {"template": self.template_service.rename(str(p.get("templateId", "")), str(p.get("name", "")))}

    def _delete_template(self, p: dict) -> dict:
        template_id = str(p.get("templateId", ""))
        result = self.template_service.delete(template_id)
        if template_id != str(self._project.get("selectedTemplateId", "")):
            return result
        # Keep the active design in place, but point it at a durable template
        # so validation, reset, and future project reopen remain well-defined.
        project = copy.deepcopy(self._project)
        project["selectedTemplateId"] = "dcapture-clean"
        project = self._commit_project(project)
        self._history.reset({"design": project["design"], "strips": project["strips"]})
        return {**result, "project": project}

    def _reset_template(self, p: dict) -> dict:
        template_id = str(p.get("templateId") or self._project.get("selectedTemplateId", "dcapture-clean"))
        template = self.template_service.reset(template_id)
        project = copy.deepcopy(self._project)
        project["design"] = copy.deepcopy(template.get("design") or default_design())
        project["selectedTemplateId"] = template_id
        physical = template.get("physical") if isinstance(template.get("physical"), dict) else {}
        if physical:
            project["printSettings"]["stripWidthMm"] = float(physical.get("widthMm", project["printSettings"]["stripWidthMm"]))
            project["printSettings"]["stripHeightMm"] = float(physical.get("heightMm", project["printSettings"]["stripHeightMm"]))
        project = self._commit_project(project)
        self._history.reset({"design": project["design"], "strips": project["strips"]})
        return {"template": template, "project": project}

    def _design_state(self) -> dict:
        return {"design": copy.deepcopy(self._project["design"]), "history": self._history.status()}

    def _update_design(self, p: dict) -> dict:
        changes = p.get("changes")
        if not isinstance(changes, dict):
            raise ValueError("Design changes must be an object.")
        design = copy.deepcopy(self._project["design"])
        if isinstance(changes.get("elements"), list):
            elements = {str(item.get("id")): item for item in design.get("elements", []) if isinstance(item, dict)}
            for patch in changes["elements"]:
                if not isinstance(patch, dict) or not patch.get("id"):
                    raise ValueError("Each element edit needs an element ID.")
                element = elements.get(str(patch["id"]))
                if element is None:
                    raise ValueError(f"Design element {patch['id']!r} was not found.")
                allowed = {"x", "y", "width", "height", "fill", "color", "borderColor", "borderWidth", "borderRadius", "opacity", "padding", "visible", "locked", "aspectRatio", "cropMode", "slotIndex", "patternType", "scale", "spacing", "offsetX", "offsetY"}
                for key in allowed.intersection(patch):
                    value = patch[key]
                    if key in {"x", "y", "width", "height", "borderWidth", "borderRadius", "opacity", "padding", "slotIndex", "scale", "spacing", "offsetX", "offsetY"}:
                        value = float(value)
                        if not (-100000 <= value <= 100000):
                            raise ValueError(f"{key} is outside the supported range.")
                        if key in {"width", "height"} and value <= 0:
                            raise ValueError(f"{key} must be positive.")
                        if key in {"scale", "spacing"} and value <= 0:
                            raise ValueError(f"{key} must be positive.")
                        if key == "opacity": value = min(1.0, max(0.0, value))
                        if key == "padding" and value < 0: raise ValueError("Padding cannot be negative.")
                        if key == "slotIndex": value = int(value)
                    elif key in {"visible", "locked"}:
                        value = bool(value)
                    elif key == "cropMode" and value not in {"cover", "contain"}:
                        raise ValueError("Image fit must be cover or contain.")
                    elif key == "aspectRatio" and value not in {"original", "1:1", "4:3", "16:9"}:
                        raise ValueError("Unsupported image frame ratio.")
                    elif key == "patternType" and value not in {"none", "dots", "lines", "grid"}:
                        raise ValueError("Unsupported pattern type.")
                    elif key in {"fill", "color", "borderColor"}:
                        value = self._valid_color(value, key)
                    element[key] = value
                    if element.get("type") == "pattern":
                        if key == "fill": element["color"] = value
                        elif key == "color": element["fill"] = value
        if changes.get("duplicateElementId"):
            source = next((item for item in design.get("elements", []) if item.get("id") == changes["duplicateElementId"]), None)
            if source is None: raise ValueError("The element to duplicate was not found.")
            duplicate = copy.deepcopy(source); duplicate["id"] = f"element-{uuid.uuid4().hex[:10]}"
            duplicate["x"] = float(duplicate.get("x", 0)) + 18; duplicate["y"] = float(duplicate.get("y", 0)) + 18
            if duplicate.get("type") == "image_frame" and duplicate.get("slotIndex") is None:
                suffix = "".join(character for character in str(source.get("id", "")) if character.isdigit())
                duplicate["slotIndex"] = max(0, int(suffix) - 1) if suffix else 0
            design["elements"].append(duplicate)
        if isinstance(changes.get("pattern"), dict):
            pattern = dict(design.get("pattern") or {})
            allowed_pattern = {"type", "scale", "spacing", "opacity", "color", "offsetX", "offsetY"}
            for key, value in changes["pattern"].items():
                if key not in allowed_pattern: continue
                if key == "type" and value not in {"none", "dots", "lines", "grid"}: raise ValueError("Unsupported pattern type.")
                if key == "color": value = self._valid_color(value, key)
                if key in {"scale", "spacing", "opacity", "offsetX", "offsetY"}:
                    value = float(value)
                    if not (-100000 <= value <= 100000): raise ValueError(f"Pattern {key} is outside the supported range.")
                    if key == "opacity": value = min(1.0, max(0.0, value))
                    if key in {"scale", "spacing"} and value <= 0: raise ValueError(f"Pattern {key} must be positive.")
                pattern[key] = value
            design["pattern"] = pattern
        if "snap" in changes:
            design["snap"] = bool(changes["snap"])
        label = str(p.get("label", "Edit design"))[:80]
        history_state = self._history.commit({"design": design, "strips": self._project["strips"]}, label)
        design = history_state["design"]
        project = copy.deepcopy(self._project); project["design"] = design
        project = self._commit_project(project)
        return {"design": copy.deepcopy(design), "project": project, "history": self._history.status()}

    def _add_design_element(self, p: dict) -> dict:
        kind = str(p.get("type", "shape"))
        if kind not in {"shape", "decoration", "accent", "border", "pattern"}:
            raise ValueError("New elements must be a shape, decoration, accent, border, or pattern.")
        design = copy.deepcopy(self._project["design"])
        artboard = design.get("artboard") if isinstance(design.get("artboard"), dict) else {}
        board_width = float(artboard.get("width", 1200))
        board_height = float(artboard.get("height", 1600))
        try:
            width = float(p.get("width", min(240, board_width)))
            height = float(p.get("height", min(160, board_height)))
            x = float(p.get("x", (board_width - width) / 2))
            y = float(p.get("y", (board_height - height) / 2))
            opacity = min(1.0, max(0.0, float(p.get("opacity", 1.0))))
            border_width = max(0.0, float(p.get("borderWidth", 0.0)))
            border_radius = max(0.0, float(p.get("borderRadius", 0.0)))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Element geometry and appearance values must be numeric.") from exc
        numbers = (x, y, width, height, opacity, border_width, border_radius)
        if not all(math.isfinite(value) and abs(value) <= 100000 for value in numbers):
            raise ValueError("Element values must be finite numbers.")
        if width <= 0 or height <= 0 or width > board_width * 2 or height > board_height * 2:
            raise ValueError("Element width and height must be positive and within the artboard range.")
        if abs(x) > board_width * 2 or abs(y) > board_height * 2:
            raise ValueError("Element position is outside the supported artboard range.")
        element = {
            "id": f"element-{uuid.uuid4().hex[:10]}",
            "type": kind,
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "fill": self._valid_color(p.get("fill", "#ffffff"), "fill"),
            "borderColor": self._valid_color(p.get("borderColor", "#000000"), "borderColor"),
            "borderWidth": border_width,
            "borderRadius": border_radius,
            "opacity": opacity,
            "visible": True,
            "locked": False,
        }
        if kind == "pattern":
            pattern_type = str(p.get("patternType", "dots"))
            if pattern_type not in {"none", "dots", "lines", "grid"}:
                raise ValueError("Unsupported pattern type.")
            try:
                spacing = float(p.get("spacing", 24))
                scale = float(p.get("scale", 1))
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("Pattern scale and spacing must be numeric.") from exc
            if not all(math.isfinite(value) and 0 < value <= 100000 for value in (spacing, scale)):
                raise ValueError("Pattern scale and spacing must be positive and within the supported range.")
            pattern_color = self._valid_color(p.get("color", p.get("fill", "#000000")), "color")
            element.update({"fill": pattern_color, "patternType": pattern_type,
                            "spacing": spacing, "scale": scale, "color": pattern_color})
        elements = design.get("elements")
        if not isinstance(elements, list):
            raise ValueError("The active design does not have a valid element list.")
        elements.append(element)
        label = str(p.get("label", "Add design element"))[:80]
        state = self._history.commit({"design": design, "strips": self._project["strips"]}, label)
        design = state["design"]
        project = copy.deepcopy(self._project)
        project["design"] = design
        project = self._commit_project(project)
        return {"element": copy.deepcopy(element), "design": copy.deepcopy(design),
                "project": project, "history": self._history.status()}

    @staticmethod
    def _valid_color(value: Any, label: str) -> str:
        text = str(value)
        if not (len(text) in {7, 9} and text.startswith("#") and all(c in "0123456789abcdefABCDEF" for c in text[1:])):
            raise ValueError(f"{label} must be a hexadecimal color.")
        return text.lower()

    def _history_move(self, direction: str) -> dict:
        state = getattr(self._history, direction)()
        if state is None:
            return {"design": copy.deepcopy(self._project["design"]), "history": self._history.status()}
        project = copy.deepcopy(self._project)
        design = state.get("design", self._project["design"])
        project["design"] = design
        if isinstance(state.get("strips"), list): project["strips"] = state["strips"]
        project = self._commit_project(project)
        return {"design": design, "project": project, "history": self._history.status()}

    def _align_design(self, p: dict) -> dict:
        design = copy.deepcopy(self._project["design"])
        element_ids = {str(value) for value in p.get("elementIds", [])}
        selected = [element for element in design.get("elements", []) if str(element.get("id")) in element_ids and not element.get("locked")]
        if not selected: raise ValueError("Select at least one unlocked design element.")
        board = design.get("artboard", {}); width, height = float(board.get("width", 1200)), float(board.get("height", 1600))
        alignment = str(p.get("alignment", "center"))
        for element in selected:
            if alignment in {"center", "horizontal-center"}: element["x"] = (width - float(element["width"])) / 2
            if alignment in {"center", "vertical-center"}: element["y"] = (height - float(element["height"])) / 2
            if alignment == "left": element["x"] = 0
            elif alignment == "right": element["x"] = width - float(element["width"])
            elif alignment == "top": element["y"] = 0
            elif alignment == "bottom": element["y"] = height - float(element["height"])
        history_state = self._history.commit({"design": design, "strips": self._project["strips"]}, "Align elements")
        project = copy.deepcopy(self._project); project["design"] = history_state["design"]
        project = self._commit_project(project)
        return {"design": project["design"], "project": project}

    def _set_snap(self, p: dict) -> dict:
        return self._update_design({"changes": {"snap": bool(p.get("enabled"))}, "label": "Set snapping"})

    def _update_print_settings(self, p: dict) -> dict:
        settings = p.get("settings")
        if not isinstance(settings, dict): raise ValueError("Print settings must be an object.")
        project = copy.deepcopy(self._project)
        project["printSettings"] = normalize_settings({**project["printSettings"], **settings})
        project["pdfSettings"] = {**(project.get("pdfSettings") or {}), "resolutionDpi": project["printSettings"]["resolutionDpi"], "cutGuides": project["printSettings"]["cutGuides"]}
        project = self._commit_project(project)
        layout = calculate_layout(project["printSettings"], project.get("strips", []))
        return {"settings": project["printSettings"], "fit": layout["fit"], "project": project}

    def _layout(self, project: Mapping[str, Any] | None = None) -> dict:
        project = project or self._project
        return calculate_layout(project["printSettings"], project.get("strips", []))

    def _template_for_validation(self, project: Mapping[str, Any] | None = None) -> dict | None:
        project = project or self._project
        try:
            template = self.template_service.load_template(project.get("selectedTemplateId", "dcapture-clean"))
            design = project.get("design")
            return {
                **template,
                "design": copy.deepcopy(design) if isinstance(design, dict) else template.get("design"),
                "valid": self._design_is_valid(template.get("design")) and self._design_is_valid(design),
            }
        except (OSError, ValueError, KeyError):
            return {"id": project.get("selectedTemplateId"), "valid": False}

    @staticmethod
    def _design_is_valid(design: Any) -> bool:
        if not isinstance(design, dict) or not isinstance(design.get("artboard"), dict):
            return False
        try:
            width = float(design["artboard"].get("width"))
            height = float(design["artboard"].get("height"))
        except (TypeError, ValueError, OverflowError):
            return False
        elements = design.get("elements")
        return (
            math.isfinite(width) and width > 0
            and math.isfinite(height) and height > 0
            and isinstance(elements, list)
            and all(isinstance(item, dict) and item.get("id") and item.get("type") for item in elements)
        )

    def _validate(self, project: Mapping[str, Any] | None = None) -> dict:
        project = copy.deepcopy(project or self._project)
        layout = self._layout(project)
        validation = validate_print(layout, project["printSettings"], project.get("strips", []),
                                    project.get("images", []), self._template_for_validation(project),
                                    incomplete_image_ids=project.get("incompleteImageIds", []),
                                    excluded_image_ids=project.get("excludedImageIds", []))
        if not project.get("strips"):
            validation["checks"].append({"id": "strip_count", "status": "ERROR", "message": "Generate at least one strip before preview or export."})
        statuses = {check.get("status") for check in validation.get("checks", [])}
        validation["status"] = "ERROR" if "ERROR" in statuses else "WARNING" if "WARNING" in statuses else "PASS"
        return {"validation": validation, "layout": layout}

    def _preview(self, p: dict) -> dict:
        page = int(p.get("page", 0)); dpi = float(p.get("dpi", 96))
        return self._queue_job("pdf_preview", lambda: self._do_preview(page, dpi))

    def _do_preview(self, page: int, dpi: float) -> dict:
        with self.project_lock:
            project = copy.deepcopy(self._project)
        layout = self._layout(project)
        if not layout["pages"]:
            raise ValueError("Generate strips before creating a print preview.")
        if not 0 <= page < len(layout["pages"]):
            raise ValueError("Preview page index is out of range.")
        assets = render_strips(project["strips"], project["images"], project["design"],
                               project["printSettings"], self.data_dir / "render-cache")
        result = render_preview_png(layout, assets, page_index=page, dpi=dpi,
                                    output_path=self.data_dir / "preview-cache" / f"page-{page + 1}.png")
        if not result.get("ok"):
            raise RuntimeError(result.get("error", "Preview rendering failed."))
        preview = dict(result["data"])
        preview["page"] = page
        return {"preview": preview, "layout": layout}

    def _export(self, p: dict) -> dict:
        output_path = p.get("path") or self._dialog_save_pdf()
        if not output_path:
            return {"cancelled": True}
        return self._queue_job("pdf_export", lambda: self._do_export(str(output_path)))

    def _do_export(self, output_path: str) -> dict:
        with self.project_lock:
            project = copy.deepcopy(self._project)
        validation = self._validate(project)["validation"]
        errors = [check for check in validation.get("checks", []) if check.get("status") == "ERROR"]
        if errors:
            raise ValueError("Print validation has errors. Resolve the blocking checks before exporting.")
        layout = self._layout(project)
        assets = render_strips(project["strips"], project["images"], project["design"],
                               project["printSettings"], self.data_dir / "render-cache")
        result = write_pdf(layout, assets, output_path, title=str(project.get("name", "Strip Print Studio")))
        if not result.get("ok"):
            raise RuntimeError(result.get("error", "PDF export failed."))
        return {**result["data"], "validation": validation}

    def _list_presets(self) -> list[dict]:
        path = self.data_dir / "print-presets.json"
        try:
            data = read_json_object(path)
            return data.get("presets", []) if isinstance(data.get("presets"), list) else []
        except (OSError, ValueError):
            return []

    def _save_preset(self, p: dict) -> dict:
        name = str(p.get("name", "")).strip()
        if not name: raise ValueError("Preset name cannot be empty.")
        settings = normalize_settings(p.get("settings") if isinstance(p.get("settings"), dict) else self._project["printSettings"])
        presets = [item for item in self._list_presets() if str(item.get("name", "")).casefold() != name.casefold()]
        preset = {"id": f"preset-{uuid.uuid4().hex[:12]}", "name": name[:100], "settings": settings}
        presets.append(preset)
        atomic_write_json(self.data_dir / "print-presets.json", {"schemaVersion": 1, "presets": presets})
        return {"preset": preset, "presets": presets}

    def _set_theme(self, p: dict) -> dict:
        mode = str(p.get("mode", ""))
        if mode not in {"light", "dark", "system"}: raise ValueError("Theme mode must be light, dark, or system.")
        atomic_write_json(self.preferences_path, {"schemaVersion": 1, "themeMode": mode})
        self._theme_mode = mode
        with self.project_lock:
            self._project["theme"] = {"mode": mode}
            self._project["themeMode"] = mode
        return self._theme_state(mode)

    @staticmethod
    def _theme_state(mode: str) -> dict:
        effective = mode if mode in {"light", "dark"} else "light"
        return {"mode": mode, "effective": effective}

    def _open_project(self, p: dict) -> dict:
        path = p.get("path") or self._dialog_open_project()
        if not path: return {"cancelled": True}
        project = self.project_service.open_project(str(path))
        project = self._replace_project(project)
        return {"project": project, "path": self.project_service.path}

    def _save_project(self) -> dict:
        try:
            result = self.project_service.save_project(self._project)
        except ProjectPathRequiredError:
            path = self._dialog_save_project()
            if not path: return {"cancelled": True}
            result = self.project_service.save_as_project(path, self._project)
        project = self._replace_project(result["project"], reset_history=False)
        return {"path": result["path"], "project": project}

    def _save_project_as(self, p: dict) -> dict:
        path = p.get("path") or self._dialog_save_project()
        if not path: return {"cancelled": True}
        result = self.project_service.save_as_project(str(path), self._project)
        project = self._replace_project(result["project"], reset_history=False)
        return {"path": result["path"], "project": project}

    def _queue_job(self, kind: str, operation: Callable[[], Any]) -> dict:
        job_id = uuid.uuid4().hex
        job = {"jobId": job_id, "kind": kind, "status": "queued", "progress": 0, "result": None, "error": None}
        with self._jobs_lock:
            self._jobs[job_id] = job

        def run() -> None:
            with self._jobs_lock:
                job["status"] = "running"
            try:
                result = operation()
                with self._jobs_lock:
                    job["status"] = "completed"; job["progress"] = 1; job["result"] = result
            except Exception as exc:
                with self._jobs_lock:
                    job["status"] = "failed"; job["error"] = str(exc)[:500]

        self._executor.submit(run)
        return {"jobId": job_id}

    def _get_job(self, p: dict) -> dict:
        job_id = str(p.get("jobId", ""))
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if job is None: raise ValueError("Background job was not found.")
            return copy.deepcopy(job)

    def _dialog(self, kind: str, *, allow_multiple: bool = False, filename: str = "", filters: tuple[str, ...] = ()) -> Any:
        if self._window is None:
            raise RuntimeError("The desktop file dialog is unavailable.")
        try:
            import webview
        except ImportError as exc:
            raise RuntimeError("PyWebView is required for native file dialogs.") from exc
        dialog_type = webview.OPEN_DIALOG if kind == "open" else webview.SAVE_DIALOG
        return self._window.create_file_dialog(dialog_type, allow_multiple=allow_multiple, save_filename=filename, file_types=filters)

    def _dialog_open_images(self) -> list[str] | None:
        result = self._dialog("open", allow_multiple=True,
                              filters=("Image files (*.jpg;*.jpeg;*.png;*.webp)",))
        if isinstance(result, (tuple, list)): return [str(path) for path in result]
        return [str(result)] if result else None

    def _dialog_open_project(self) -> str | None:
        result = self._dialog("open", filters=(
            "Strip Print Studio project (*.stripproj)", "JSON project (*.json)",
        ))
        if isinstance(result, (tuple, list)): return str(result[0]) if result else None
        return str(result) if result else None

    def _dialog_save_project(self) -> str | None:
        result = self._dialog("save", filename="Untitled Project.stripproj",
                              filters=("Strip Print Studio project (*.stripproj)",))
        if isinstance(result, (tuple, list)): return str(result[0]) if result else None
        return str(result) if result else None

    def _dialog_save_pdf(self) -> str | None:
        result = self._dialog("save", filename="Strip Print.pdf",
                              filters=("PDF document (*.pdf)",))
        if isinstance(result, (tuple, list)): return str(result[0]) if result else None
        return str(result) if result else None

    def _on_closing(self, *_: Any) -> None:
        try:
            # Let active imports finish their state merge before taking the
            # final recovery snapshot. Pending queued work is cancelled.
            self._executor.shutdown(wait=True, cancel_futures=True)
        except Exception:
            pass
        try:
            if self.project_service.is_dirty:
                self.project_service.flush_autosave(self._project)
            else:
                self.project_service.clear_recovery()
        except Exception:
            pass
        self.close()

    def _has_recoverable_work(self) -> bool:
        return self.project_service.is_dirty

    def close(self) -> None:
        if self._closed: return
        self._closed = True
        self.project_service.close()
        self._executor.shutdown(wait=True, cancel_futures=True)


__all__ = ["StudioAPI"]
