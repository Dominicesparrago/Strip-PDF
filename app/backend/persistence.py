"""Small, defensive JSON persistence primitives used by backend services."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


def utc_timestamp() -> str:
    """Return a JSON friendly, timezone-aware UTC timestamp."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_write_json(path: str | os.PathLike[str], value: Mapping[str, Any]) -> None:
    """Write a JSON object using a temporary sibling and atomic replacement.

    Creating the temporary beside the destination keeps replacement on the same
    volume, which is required for atomic ``os.replace`` semantics on Windows.
    The previous file remains intact if serialization or the write fails.
    """

    destination = Path(path)
    if not isinstance(value, Mapping):
        raise TypeError("JSON document must be an object")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            json.dump(value, temporary, ensure_ascii=False, indent=2, allow_nan=False)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, destination)
        temporary_name = None
        # Directory fsync is available on POSIX and not needed/available on Windows.
        if os.name != "nt":
            try:
                descriptor = os.open(destination.parent, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            except OSError:
                pass
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass


def read_json_object(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read a JSON object, rejecting arrays/scalars with a useful error."""

    source = Path(path)
    try:
        with source.open("r", encoding="utf-8-sig") as stream:
            value = json.load(stream)
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON in {source.name}: {error.msg}") from error
    except OSError as error:
        raise OSError(f"Could not read {source}: {error.strerror or error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {source.name}")
    return value


def unwrap_document(document: Mapping[str, Any], expected_kind: str | None = None) -> dict[str, Any]:
    """Unwrap our optional ``data`` envelope while accepting direct documents.

    Services write direct versioned dictionaries. Accepting an envelope makes
    migrations and externally-authored built-in templates straightforward.
    """

    kind = document.get("kind")
    if expected_kind and kind not in (None, expected_kind):
        raise ValueError(f"Expected a {expected_kind} document, got {kind!r}")
    data = document.get("data")
    if isinstance(data, dict):
        merged = dict(data)
        if "schemaVersion" not in merged and "schemaVersion" in document:
            merged["schemaVersion"] = document["schemaVersion"]
        if "kind" not in merged and kind is not None:
            merged["kind"] = kind
        return merged
    return dict(document)


def versioned_document(kind: str, data: Mapping[str, Any], version: int = 1) -> dict[str, Any]:
    """Return a copied direct JSON document with standard version metadata."""

    result = dict(data)
    result["kind"] = kind
    result["schemaVersion"] = version
    result.setdefault("updatedAt", utc_timestamp())
    return result

