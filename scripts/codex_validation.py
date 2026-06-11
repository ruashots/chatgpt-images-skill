"""Local input validation: stdlib image inspection, mask checks."""
# Decomposed from Yui's codex_oauth_image_handoff.py (references/handoff-original.py).
# Logic unchanged; split along module seams. PRIVATE — never publish.
from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

from codex_auth import AppError

def is_remote_or_file_id(ref: str) -> bool:
    return ref.startswith(("http://", "https://", "data:", "file_"))


def local_path_or_none(ref: str) -> Path | None:
    if is_remote_or_file_id(ref):
        return None
    return Path(ref).expanduser()


def inspect_image_file(pathish: str) -> dict[str, Any]:
    """Inspect PNG/JPEG/WebP dimensions using only the Python stdlib.

    Returns: {path, format, width, height, has_alpha}. `has_alpha` is best-effort;
    for JPEG it is always False, for PNG it is based on color type/tRNS, and for
    WebP it is unknown because VP8X alpha flags vary by container chunk.
    """

    path = Path(pathish).expanduser()
    if not path.exists():
        raise AppError(f"Image file not found: {path}")
    if not path.is_file():
        raise AppError(f"Image path is not a file: {path}")
    data = path.read_bytes()
    if len(data) < 12:
        raise AppError(f"Image file is too small or corrupt: {path}")

    # PNG: signature + IHDR width/height/color type; tRNS means alpha-like transparency.
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        if data[12:16] != b"IHDR" or len(data) < 33:
            raise AppError(f"PNG missing IHDR chunk: {path}")
        width, height = struct.unpack(">II", data[16:24])
        color_type = data[25]
        has_alpha = color_type in {4, 6} or b"tRNS" in data
        return {"path": str(path), "format": "png", "width": width, "height": height, "has_alpha": has_alpha}

    # JPEG: scan segments for SOF marker.
    if data.startswith(b"\xff\xd8"):
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            i += 2
            while marker == 0xFF and i < len(data):
                marker = data[i]
                i += 1
            if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
                continue
            if i + 2 > len(data):
                break
            seg_len = int.from_bytes(data[i:i + 2], "big")
            if seg_len < 2 or i + seg_len > len(data):
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                height = int.from_bytes(data[i + 3:i + 5], "big")
                width = int.from_bytes(data[i + 5:i + 7], "big")
                return {"path": str(path), "format": "jpeg", "width": width, "height": height, "has_alpha": False}
            i += seg_len
        raise AppError(f"Could not find JPEG dimensions: {path}")

    # WebP: RIFF header; dimensions depend on VP8/VP8L/VP8X chunk.
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8X" and len(data) >= 30:
            width = 1 + int.from_bytes(data[24:27], "little")
            height = 1 + int.from_bytes(data[27:30], "little")
            alpha = bool(data[20] & 0x10)
            return {"path": str(path), "format": "webp", "width": width, "height": height, "has_alpha": alpha}
        if chunk == b"VP8L" and len(data) >= 25:
            bits = int.from_bytes(data[21:25], "little")
            width = (bits & 0x3FFF) + 1
            height = ((bits >> 14) & 0x3FFF) + 1
            return {"path": str(path), "format": "webp", "width": width, "height": height, "has_alpha": None}
        raise AppError(f"WebP dimensions require VP8X/VP8L header support: {path}")

    raise AppError(f"Unsupported local image format for validation: {path}. Use PNG, JPEG, WebP, a URL, data URL, or file_id.")


def validate_edit_inputs(images: list[str], mask: str | None, *, strict: bool = True) -> list[str]:
    """Validate local edit inputs before paying the API round trip.

    Remote URLs, data URLs, and file IDs are allowed but cannot be dimension-
    checked locally. For a local mask and at least one local source image, require
    dimensions to match the first local source image. Warn/raise when a local mask
    clearly lacks alpha because masked edits generally work best with a PNG alpha
    channel where transparent areas mark the editable region.
    """

    warnings: list[str] = []
    local_images = [p for p in (local_path_or_none(ref) for ref in images) if p is not None]
    local_mask = local_path_or_none(mask) if mask else None
    image_infos = [inspect_image_file(str(path)) for path in local_images]
    mask_info = inspect_image_file(str(local_mask)) if local_mask is not None else None

    if mask_info:
        if image_infos:
            src = image_infos[0]
            if (src["width"], src["height"]) != (mask_info["width"], mask_info["height"]):
                msg = (
                    f"Mask dimensions {mask_info['width']}x{mask_info['height']} do not match first local source image "
                    f"dimensions {src['width']}x{src['height']}. Masks should be the same pixel size as the edited image."
                )
                if strict:
                    raise AppError(msg)
                warnings.append(msg)
        if mask_info.get("format") != "png":
            msg = "Mask is not PNG; alpha-mask workflows are most reliable with PNG masks."
            if strict:
                raise AppError(msg)
            warnings.append(msg)
        elif mask_info.get("has_alpha") is False:
            msg = "Mask PNG does not appear to contain alpha/transparency; transparent regions usually mark the editable area."
            if strict:
                raise AppError(msg)
            warnings.append(msg)

    return warnings


def masked_fill_is_degenerate(image_b64: str, mask_path: str, *, black_threshold: float = 25.0):
    """Detect the backend's intermittent black-blob failure on masked edits.

    Maps the mask's transparent (editable) region onto the output by relative
    position (output resolution differs from mask), samples mean luminance there,
    and reports True if the fill came back near-black.

    Returns True (degenerate), False (looks fine), or None (cannot tell —
    Pillow not installed, or mask has no transparent region).
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    import base64, io
    if Image.open(mask_path).convert("RGBA").getchannel("A").point(
            lambda a: 255 if a < 128 else 0).getbbox() is None:
        return None  # mask has no transparent (editable) region
    out = Image.open(io.BytesIO(base64.b64decode(image_b64))).convert("RGB")
    ow, oh = out.size
    # Resize the editable map to the output and sample EXACTLY the edited pixels
    # (the transparent shape), not its bounding box — a black ellipse fills only
    # ~78% of its bbox, so bbox-averaging dilutes the failure above threshold.
    editable = Image.open(mask_path).convert("RGBA").getchannel("A") \
        .point(lambda a: 255 if a < 128 else 0).resize((ow, oh), Image.NEAREST)
    sel = [(r, g, b) for (r, g, b), m in zip(out.getdata(), editable.getdata()) if m]
    if not sel:
        return None
    lum = sum(0.299 * r + 0.587 * g + 0.114 * b for r, g, b in sel) / len(sel)
    return lum < black_threshold
