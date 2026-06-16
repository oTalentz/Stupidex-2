"""Validation utilities extracted from web.py."""

from __future__ import annotations

import base64
import binascii
import os
import re
from pathlib import Path
from typing import Any

MAX_CHAT_IMAGES = 4
MAX_CHAT_IMAGE_BYTES = 5 * 1024 * 1024
MAX_BROWSER_TOOL_TRACE = 20
MAX_BROWSER_TOOL_RESULT_CHARS = 64 * 1024

_CHAT_IMAGE_MIMES = {
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": b"RIFF",
}

_GIT_HOST_ALLOWLIST = {
    "github.com",
    "www.github.com",
    "gitlab.com",
    "www.gitlab.com",
}


def validate_browser_tool_trace(raw_trace, agent_bridge_tools: set) -> tuple[list[dict], str | None]:
    if raw_trace is None:
        return [], None
    if not isinstance(raw_trace, list) or len(raw_trace) > MAX_BROWSER_TOOL_TRACE:
        return [], "invalid tool trace"
    normalized = []
    for item in raw_trace:
        if not isinstance(item, dict):
            return [], "invalid tool trace"
        call_id = str(item.get("id") or "")[:128]
        name = str(item.get("name") or "")[:80]
        arguments = item.get("arguments") or {}
        result = str(item.get("result") or "")[:MAX_BROWSER_TOOL_RESULT_CHARS]
        if not call_id or name not in agent_bridge_tools or not isinstance(arguments, dict):
            return [], "invalid tool trace"
        normalized.append({
            "id": call_id,
            "name": name,
            "arguments": arguments,
            "result": result,
            "error": bool(item.get("error")),
        })
    return normalized, None


def validate_chat_images(raw_images) -> tuple[list[dict], str | None]:
    if raw_images is None:
        return [], None
    if not isinstance(raw_images, list):
        return [], "images must be a list"
    if len(raw_images) > MAX_CHAT_IMAGES:
        return [], f"too many images (max {MAX_CHAT_IMAGES})"

    normalized: list[dict] = []
    for index, raw in enumerate(raw_images):
        if not isinstance(raw, dict):
            return [], f"image {index + 1} is invalid"
        data_url = raw.get("data_url")
        if not isinstance(data_url, str) or not data_url.startswith("data:image/"):
            return [], f"image {index + 1} must use a data URL"
        try:
            header, encoded = data_url.split(",", 1)
            mime = header[5:].split(";", 1)[0].lower()
        except ValueError:
            return [], f"image {index + 1} has an invalid data URL"
        if ";base64" not in header.lower() or mime not in _CHAT_IMAGE_MIMES:
            return [], f"unsupported image type: {mime or 'unknown'}"
        if len(encoded) > ((MAX_CHAT_IMAGE_BYTES + 2) // 3) * 4 + 8:
            return [], f"image {index + 1} is too large"
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return [], f"image {index + 1} has invalid base64 data"
        if not content or len(content) > MAX_CHAT_IMAGE_BYTES:
            return [], f"image {index + 1} is too large"
        signature = _CHAT_IMAGE_MIMES[mime]
        signatures = signature if isinstance(signature, tuple) else (signature,)
        valid_signature = any(content.startswith(item) for item in signatures)
        if mime == "image/webp":
            valid_signature = valid_signature and len(content) >= 12 and content[8:12] == b"WEBP"
        if not valid_signature:
            return [], f"image {index + 1} content does not match {mime}"
        name = re.sub(r"[^A-Za-z0-9._ -]+", "_", str(raw.get("name") or f"image-{index + 1}"))[:120]
        normalized.append({"data_url": data_url, "mime": mime, "name": name, "size": len(content)})
    return normalized, None


def validate_git_url(url: str) -> str | None:
    """Returns an error message string, or None if the URL is valid."""
    if not url or not isinstance(url, str):
        return "invalid URL"
    max_len = 2000
    if len(url) > max_len:
        return "URL too long"
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("https",):
        return "only HTTPS URLs are allowed"
    if parsed.username or parsed.password:
        return "embedded credentials are not allowed"
    host = parsed.hostname.lower() if parsed.hostname else ""
    if not host:
        return "missing hostname"
    if host not in _GIT_HOST_ALLOWLIST:
        return "git host not allowed"
    if parsed.port:
        return "custom ports are not allowed"
    if parsed.query:
        return "query parameters are not allowed"
    return None


def path_within(path: Path, root: Path) -> bool:
    try:
        resolved = path.resolve()
        root_resolved = root.resolve()
        return root_resolved in resolved.parents or resolved == root_resolved
    except (OSError, ValueError):
        return False


# Lazy import to avoid circular at module level
import urllib.parse
