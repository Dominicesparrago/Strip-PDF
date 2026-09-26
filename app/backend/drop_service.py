"""Normalize filesystem paths from PyWebView DOM drop events."""

from __future__ import annotations


def extract_dropped_paths(event: object) -> list[str]:
    """Read deduplicated full paths from PyWebView's enhanced drop payload."""
    if not isinstance(event, dict):
        return []
    transfer = event.get("dataTransfer") or event.get("domTransfer")
    if not isinstance(transfer, dict):
        return []
    files = transfer.get("files")
    if not isinstance(files, (list, tuple)):
        return []
    paths: list[str] = []
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            continue
        path = item.get("pywebviewFullPath")
        if isinstance(path, str) and path and path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


__all__ = ["extract_dropped_paths"]
