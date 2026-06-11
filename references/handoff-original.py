#!/usr/bin/env python3
"""
codex_oauth_image_handoff.py

A completely standalone, one-file handoff script for ChatGPT/Codex OAuth image
workflows. It does not import project-local modules and does not assume access
to the original author's machine.

What this is
------------
This script is a portable bridge to the ChatGPT/Codex OAuth image path. It uses
OpenAI's Responses API shape with the `image_generation` tool and `gpt-image-2`.
It is intended for people who have ChatGPT/Codex OAuth entitlement and want a
single file that can:

1. Sign in with device-code OAuth.
2. Cache and refresh tokens locally.
3. Generate images from text.
4. Edit, mix, or use reference images through `action: edit`.
5. Validate local masks before making an API call.
6. Save raw streamed events for debugging when the API behaves strangely.

Billing/auth note
-----------------
This uses ChatGPT/Codex OAuth account entitlements, not an OpenAI Platform API
key. It can still be quota/rate limited. Do not describe it as unlimited/free.
The token cache is sensitive; treat it like a password.

Install dependency
------------------
Python 3.10+ and httpx are required:

  python3 -m pip install httpx

If your OS blocks global pip installs, use a venv:

  python3 -m venv .venv
  . .venv/bin/activate
  python3 -m pip install httpx

Quick start
-----------
  # 1) Interactive device-code OAuth. Opens no browser automatically; copy URL.
  python3 codex_oauth_image_handoff.py login

  # 2) Verify cached token.
  python3 codex_oauth_image_handoff.py check-auth

  # 3) Generate from text. You can pass either --aspect-ratio or --size.
  #    aspect mappings: landscape=1536x1024, square=1024x1024, portrait=1024x1536.
  python3 codex_oauth_image_handoff.py generate \
    --prompt "A moody editorial photo of a tiny robot repairing a neon sign" \
    --size 1536x1024 \
    --quality medium \
    --out ./generated.png

  # 4) Edit/mix/reference images.
  python3 codex_oauth_image_handoff.py edit \
    --image ./person.png \
    --image ./product.png \
    --prompt "Image 1 is identity. Image 2 is product fidelity. Create a polished ad scene; preserve identity and product shape. No watermark." \
    --size 1536x1024 \
    --out ./edited.png

Generation options
------------------
Both `generate` and `edit` accept the shared image options below:

  --size auto|1024x1024|1536x1024|1024x1536
      Exact tool size. Other backend-supported WxH strings can be passed, but
      the three explicit sizes above are the safe defaults.

  --aspect-ratio landscape|square|portrait
      Convenience alias that maps to a known size:
      landscape -> 1536x1024, square -> 1024x1024, portrait -> 1024x1536.
      If both --size and --aspect-ratio are provided, --size wins.

  --quality auto|low|medium|high
      Image generation quality. Default: medium.

  --output-format png|jpeg|webp
      Output container. Default: png.

  --output-compression 0-100
      JPEG/WebP compression only. Not valid with PNG.

  --background opaque|auto
      Background handling passed through to the image tool.

  --partial-images N
      Requests partial image streaming support where the backend supports it.
      The script saves the final image result, not every partial.

Edit/reference image inputs
---------------------------
Use repeated `--image` flags for multiple references. Inputs can be:

  --image ./local.png
  --image https://example.com/reference.jpg
  --image 'data:image/png;base64,...'
  --image file_abc123

Prompting pattern for references:

  - Number images in order: "Image 1", "Image 2", etc.
  - Give each image a job: identity, product fidelity, pose, palette, style.
  - Say what must be preserved exactly and what can be reinterpreted.
  - Ask for a coherent new output, not a collage, unless collage is desired.

Mask workflow
-------------
Masked edits use:

  --mask ./mask.png

Local masks are validated before the API call:

  - source image exists and is PNG/JPEG/WebP
  - mask exists and is PNG/JPEG/WebP
  - if both source and mask are local, mask dimensions must match the first
    local source image exactly
  - local masks should be PNG with alpha/transparency; by default a non-PNG or
    non-alpha local mask fails fast because it is a common source of bad edits

Typical alpha-mask convention: the transparent/alpha area marks the editable
region and opaque area protects what should stay fixed. If your backend/account
uses the opposite convention, say it clearly in the prompt.

If you intentionally want to try a non-alpha or non-PNG mask anyway:

  python3 codex_oauth_image_handoff.py edit \
    --image ./source.png \
    --mask ./mask.jpg \
    --allow-mask-warnings \
    --prompt "Replace only the masked logo area" \
    --out ./masked-edit.png

Remote URLs, data URLs, and file IDs cannot be dimension-checked locally; the
script allows them and lets the backend validate.

Debugging and dry runs
----------------------
Use dry-run mode to inspect the request payload without calling the API:

  python3 codex_oauth_image_handoff.py generate \
    --prompt "test" \
    --size 1024x1024 \
    --dry-run \
    --print-json

Use raw event capture when the API returns no image, streams odd events, or you
need a bug report artifact:

  python3 codex_oauth_image_handoff.py generate \
    --prompt "debug me" \
    --raw-events-out ./events.jsonl \
    --out ./debug.png

`--raw-events-out` appends JSONL events exactly as parsed from the SSE stream.
The file may contain generated-image base64 and response metadata. Keep it
private; do not paste it publicly without redacting.

Alternative auth
----------------
Instead of `login`, you may provide tokens by environment:

  export CODEX_ACCESS_TOKEN='...'
  export CODEX_REFRESH_TOKEN='...'   # optional but recommended

or point to another token cache file:

  export CODEX_OAUTH_CACHE=/secure/path/codex_oauth_tokens.json

The cache format is intentionally simple:

  {"access_token": "...", "refresh_token": "...", "last_refresh": "ISO-8601"}

Security
--------
The token cache is written mode 0600 on POSIX systems. `logout` deletes the
local cache only; it does not remotely revoke the OAuth grant.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import stat
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# Public constants used by the Codex/ChatGPT device-code OAuth flow.
CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_AUTH_ISSUER = "https://auth.openai.com"
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"

IMAGE_MODEL = "gpt-image-2"
DEFAULT_CHAT_MODEL = "gpt-5.5"
ASPECT_TO_SIZE = {"landscape": "1536x1024", "square": "1024x1024", "portrait": "1024x1536"}
SIZE_TO_ASPECT = {v: k for k, v in ASPECT_TO_SIZE.items()}
QUALITY_CHOICES = ("auto", "low", "medium", "high")
OUTPUT_FORMAT_CHOICES = ("png", "jpeg", "webp")

GENERATE_INSTRUCTIONS = (
    "You are an assistant that must fulfill image generation requests by using "
    "the image_generation tool when provided."
)
EDIT_INSTRUCTIONS = (
    "Use the image_generation tool to edit or combine images according to the "
    "user's prompt. Preserve source identity, product shape, composition, and "
    "important details unless the user explicitly asks otherwise."
)


class AppError(RuntimeError):
    """Expected user-facing error."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def eprint(*parts: object) -> None:
    print(*parts, file=sys.stderr)


def require_httpx():
    try:
        import httpx  # type: ignore
        return httpx
    except ImportError as exc:
        raise AppError("Missing dependency: httpx. Install with `python3 -m pip install httpx`.") from exc


def token_cache_path() -> Path:
    """Return token cache path. Defaults to a local user config cache."""

    explicit = os.environ.get("CODEX_OAUTH_CACHE", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        return Path(xdg).expanduser() / "codex-oauth-image-handoff" / "tokens.json"
    return Path.home() / ".config" / "codex-oauth-image-handoff" / "tokens.json"


def output_dir() -> Path:
    explicit = os.environ.get("CODEX_IMAGE_OUTPUT_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return Path.cwd() / "codex_oauth_images"


def write_token_cache(tokens: dict[str, Any]) -> Path:
    path = token_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "access_token": str(tokens.get("access_token", "") or ""),
        "refresh_token": str(tokens.get("refresh_token", "") or ""),
        "last_refresh": tokens.get("last_refresh") or utc_now_iso(),
        "token_type": tokens.get("token_type") or "Bearer",
    }
    if not payload["access_token"]:
        raise AppError("Refusing to write token cache without access_token.")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        tmp.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600 on POSIX
    except Exception:
        pass
    tmp.replace(path)
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass
    return path


def read_token_cache() -> dict[str, Any] | None:
    path = token_cache_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AppError(f"Could not read token cache {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise AppError(f"Token cache {path} is not a JSON object.")
    return data


def tokens_from_env() -> dict[str, Any] | None:
    access = os.environ.get("CODEX_ACCESS_TOKEN") or os.environ.get("OPENAI_CODEX_ACCESS_TOKEN")
    refresh = os.environ.get("CODEX_REFRESH_TOKEN") or os.environ.get("OPENAI_CODEX_REFRESH_TOKEN")
    if access and access.strip():
        return {"access_token": access.strip(), "refresh_token": (refresh or "").strip(), "source": "env"}
    return None


def refresh_tokens(refresh_token: str, *, timeout_seconds: float = 20.0, save: bool = True) -> dict[str, Any]:
    """Refresh Codex OAuth tokens using refresh_token."""

    if not refresh_token:
        raise AppError("No refresh_token available. Run `login` again or export CODEX_ACCESS_TOKEN.")
    httpx = require_httpx()
    with httpx.Client(timeout=httpx.Timeout(max(5.0, timeout_seconds)), headers={"Accept": "application/json"}) as client:
        resp = client.post(
            CODEX_OAUTH_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CODEX_OAUTH_CLIENT_ID,
            },
        )
    if resp.status_code != 200:
        body = resp.text[:1000]
        raise AppError(f"Token refresh failed with HTTP {resp.status_code}: {body}")
    payload = resp.json()
    access = str(payload.get("access_token", "") or "").strip()
    if not access:
        raise AppError("Token refresh response did not contain access_token.")
    updated = {
        "access_token": access,
        "refresh_token": str(payload.get("refresh_token") or refresh_token).strip(),
        "token_type": str(payload.get("token_type") or "Bearer"),
        "last_refresh": utc_now_iso(),
    }
    if save:
        write_token_cache(updated)
    return updated


def get_tokens(*, allow_refresh: bool = True) -> dict[str, Any]:
    """Resolve tokens from env or local cache, refreshing cache tokens when possible."""

    env_tokens = tokens_from_env()
    if env_tokens:
        return env_tokens
    cached = read_token_cache()
    if not cached:
        raise AppError("No OAuth tokens found. Run `python3 codex_oauth_image_handoff.py login` first, or export CODEX_ACCESS_TOKEN.")
    access = str(cached.get("access_token", "") or "").strip()
    refresh = str(cached.get("refresh_token", "") or "").strip()
    if not access:
        raise AppError("Token cache is missing access_token. Run `login` again.")
    # Access-token JWT expiry parsing is intentionally omitted to stay minimal and
    # robust. We attempt the API call first; callers can refresh/retry on 401.
    if allow_refresh and refresh and os.environ.get("CODEX_ALWAYS_REFRESH", "").lower() in {"1", "true", "yes"}:
        return refresh_tokens(refresh, save=True)
    return {**cached, "access_token": access, "refresh_token": refresh, "source": "cache"}


def codex_headers(access_token: str) -> dict[str, str]:
    """Headers for ChatGPT/Codex backend requests."""

    return {
        "Accept": "text/event-stream",
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/",
        "User-Agent": "Codex-OAuth-Image-Handoff/1.0",
    }


def run_device_login(args: argparse.Namespace) -> int:
    """Run standalone Codex device-code OAuth and save tokens locally."""

    httpx = require_httpx()
    issuer = CODEX_AUTH_ISSUER
    client_id = CODEX_OAUTH_CLIENT_ID

    with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
        resp = client.post(
            f"{issuer}/api/accounts/deviceauth/usercode",
            json={"client_id": client_id},
            headers={"Content-Type": "application/json"},
        )
    if resp.status_code != 200:
        raise AppError(f"Device-code request failed with HTTP {resp.status_code}: {resp.text[:1000]}")
    device_data = resp.json()
    user_code = str(device_data.get("user_code", "") or "")
    device_auth_id = str(device_data.get("device_auth_id", "") or "")
    poll_interval = max(3, int(device_data.get("interval", 5) or 5))
    if not user_code or not device_auth_id:
        raise AppError(f"Device-code response missing user_code/device_auth_id: {device_data}")

    print("To continue:")
    print(f"  1. Open: {issuer}/codex/device")
    print(f"  2. Enter code: {user_code}")
    print("Waiting for sign-in... Ctrl+C to cancel.")

    max_wait = int(args.timeout)
    start = time.monotonic()
    code_resp: dict[str, Any] | None = None
    with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
        while time.monotonic() - start < max_wait:
            time.sleep(poll_interval)
            poll = client.post(
                f"{issuer}/api/accounts/deviceauth/token",
                json={"device_auth_id": device_auth_id, "user_code": user_code},
                headers={"Content-Type": "application/json"},
            )
            if poll.status_code == 200:
                code_resp = poll.json()
                break
            if poll.status_code in {403, 404}:
                continue
            raise AppError(f"Device-code poll failed with HTTP {poll.status_code}: {poll.text[:1000]}")

    if code_resp is None:
        raise AppError(f"Login timed out after {max_wait} seconds.")
    authorization_code = str(code_resp.get("authorization_code", "") or "")
    code_verifier = str(code_resp.get("code_verifier", "") or "")
    if not authorization_code or not code_verifier:
        raise AppError(f"Device auth response missing authorization_code/code_verifier: {code_resp}")

    with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
        token_resp = client.post(
            CODEX_OAUTH_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": authorization_code,
                "redirect_uri": f"{issuer}/deviceauth/callback",
                "client_id": client_id,
                "code_verifier": code_verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if token_resp.status_code != 200:
        raise AppError(f"Token exchange failed with HTTP {token_resp.status_code}: {token_resp.text[:1000]}")
    tokens = token_resp.json()
    access = str(tokens.get("access_token", "") or "").strip()
    if not access:
        raise AppError("Token exchange did not return access_token.")
    saved = write_token_cache({
        "access_token": access,
        "refresh_token": str(tokens.get("refresh_token", "") or "").strip(),
        "token_type": str(tokens.get("token_type") or "Bearer"),
        "last_refresh": utc_now_iso(),
    })
    result = {"success": True, "token_cache": str(saved), "auth": "ChatGPT/Codex OAuth"}
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"Login complete. Tokens saved to: {saved}")
    return 0


def file_to_data_url(pathish: str) -> str:
    path = Path(pathish).expanduser()
    if not path.exists():
        raise AppError(f"Image file not found: {path}")
    if not path.is_file():
        raise AppError(f"Image path is not a file: {path}")
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def image_ref_to_content(ref: str, *, detail: str | None = None) -> dict[str, Any]:
    if ref.startswith(("http://", "https://", "data:")):
        content: dict[str, Any] = {"type": "input_image", "image_url": ref}
    elif ref.startswith("file_"):
        content = {"type": "input_image", "file_id": ref}
    else:
        content = {"type": "input_image", "image_url": file_to_data_url(ref)}
    if detail:
        content["detail"] = detail
    return content


def mask_ref_to_tool_param(ref: str) -> dict[str, str]:
    if ref.startswith(("http://", "https://", "data:")):
        return {"image_url": ref}
    if ref.startswith("file_"):
        return {"file_id": ref}
    return {"image_url": file_to_data_url(ref)}


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


def normalize_size(args: argparse.Namespace) -> tuple[str, str | None]:
    if getattr(args, "size", None) and args.size != "auto":
        return args.size, SIZE_TO_ASPECT.get(args.size)
    if getattr(args, "aspect_ratio", None):
        return ASPECT_TO_SIZE[args.aspect_ratio], args.aspect_ratio
    return "auto", None


def build_generate_payload(args: argparse.Namespace) -> dict[str, Any]:
    size, _aspect = normalize_size(args)
    tool: dict[str, Any] = {
        "type": "image_generation",
        "model": args.image_model,
        "size": size,
        "quality": args.quality,
        "output_format": args.output_format,
        "background": args.background,
        "partial_images": args.partial_images,
    }
    if args.output_compression is not None and args.output_format in {"jpeg", "webp"}:
        tool["output_compression"] = args.output_compression
    return {
        "model": args.chat_model,
        "store": False,
        "instructions": args.instructions or GENERATE_INSTRUCTIONS,
        "input": [{
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": args.prompt.strip()}],
        }],
        "tools": [tool],
        "tool_choice": {"type": "allowed_tools", "mode": "required", "tools": [{"type": "image_generation"}]},
        "stream": True,
    }


def build_edit_payload(args: argparse.Namespace) -> dict[str, Any]:
    size, _aspect = normalize_size(args)
    content: list[dict[str, Any]] = [{"type": "input_text", "text": args.prompt.strip()}]
    content.extend(image_ref_to_content(image, detail=args.detail) for image in args.image)
    tool: dict[str, Any] = {
        "type": "image_generation",
        "model": args.image_model,
        "action": "edit",
        "size": size,
        "quality": args.quality,
        "output_format": args.output_format,
        "background": args.background,
        "partial_images": args.partial_images,
    }
    if args.mask:
        tool["input_image_mask"] = mask_ref_to_tool_param(args.mask)
    if args.output_compression is not None and args.output_format in {"jpeg", "webp"}:
        tool["output_compression"] = args.output_compression
    return {
        "model": args.chat_model,
        "store": False,
        "instructions": args.instructions or EDIT_INSTRUCTIONS,
        "input": [{"role": "user", "content": content}],
        "tools": [tool],
        "tool_choice": {"type": "image_generation"},
        "stream": True,
    }


def iter_sse_json(response: Any) -> Iterable[dict[str, Any]]:
    event_name: str | None = None
    data_lines: list[str] = []

    def flush() -> dict[str, Any] | None:
        nonlocal event_name, data_lines
        if not data_lines:
            event_name = None
            return None
        raw = "\n".join(data_lines).strip()
        event = event_name
        event_name = None
        data_lines = []
        if not raw or raw == "[DONE]":
            return None
        payload = json.loads(raw)
        if isinstance(payload, dict) and event and "type" not in payload:
            payload["type"] = event
        return payload if isinstance(payload, dict) else {"type": event or "data", "data": payload}

    for line in response.iter_lines():
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        line = str(line)
        if line == "":
            payload = flush()
            if payload is not None:
                yield payload
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip())
    payload = flush()
    if payload is not None:
        yield payload


def extract_image_b64(value: Any) -> str | None:
    found: str | None = None
    if isinstance(value, dict):
        if value.get("type") == "image_generation_call" and isinstance(value.get("result"), str):
            found = value["result"]
        if isinstance(value.get("partial_image_b64"), str):
            found = value["partial_image_b64"]
        for child in value.values():
            nested = extract_image_b64(child)
            if nested:
                found = nested
    elif isinstance(value, list):
        for child in value:
            nested = extract_image_b64(child)
            if nested:
                found = nested
    return found


def extract_response_id(value: Any) -> str | None:
    if isinstance(value, dict):
        if isinstance(value.get("id"), str) and value["id"].startswith("resp_"):
            return value["id"]
        if isinstance(value.get("response"), dict) and isinstance(value["response"].get("id"), str):
            return value["response"]["id"]
        for child in value.values():
            found = extract_response_id(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = extract_response_id(child)
            if found:
                return found
    return None


def post_responses(
    payload: dict[str, Any],
    *,
    timeout_seconds: float,
    verbose: bool = False,
    raw_events_out: str | None = None,
) -> tuple[str, str | None]:
    httpx = require_httpx()
    tokens = get_tokens(allow_refresh=False)
    access = str(tokens.get("access_token", "") or "")
    refresh = str(tokens.get("refresh_token", "") or "")

    def once(token: str):
        timeout = httpx.Timeout(timeout_seconds, connect=30.0, read=timeout_seconds, write=30.0, pool=30.0)
        client = httpx.Client(timeout=timeout, headers=codex_headers(token))
        return client, client.stream("POST", f"{CODEX_BASE_URL}/responses", json=payload)

    raw_events_path = Path(raw_events_out).expanduser() if raw_events_out else None

    def consume(client_and_stream) -> tuple[str, str | None]:
        client, stream_cm = client_and_stream
        image_b64: str | None = None
        response_id: str | None = None
        if raw_events_path:
            raw_events_path.parent.mkdir(parents=True, exist_ok=True)
        with client, stream_cm as response:
            if response.status_code in {401, 403} and refresh:
                raise PermissionError(str(response.status_code))
            try:
                response.raise_for_status()
            except Exception as exc:
                response.read()
                body = response.text[:1500]
                raise AppError(f"Codex Responses API HTTP {response.status_code}: {body}") from exc
            raw_handle = raw_events_path.open("a", encoding="utf-8") if raw_events_path else None
            try:
                for event in iter_sse_json(response):
                    if raw_handle:
                        raw_handle.write(json.dumps(event, ensure_ascii=False) + "\n")
                        raw_handle.flush()
                    if verbose:
                        eprint(f"event: {event.get('type', '<unknown>')}")
                    response_id = extract_response_id(event) or response_id
                    found = extract_image_b64(event)
                    if found:
                        image_b64 = found
            finally:
                if raw_handle:
                    raw_handle.close()
        if not image_b64:
            raise AppError("Codex response contained no image_generation_call result.")
        return image_b64, response_id

    try:
        return consume(once(access))
    except PermissionError:
        # Access token likely expired. Refresh once and retry.
        if not refresh:
            raise AppError("Access token was rejected and no refresh_token is available. Run `login` again.")
        updated = refresh_tokens(refresh, timeout_seconds=20.0, save=True)
        return consume(once(str(updated["access_token"])))


def default_out_path(mode: str, output_format: str) -> Path:
    suffix = "jpg" if output_format == "jpeg" else output_format
    return output_dir() / f"codex_oauth_{mode}_{int(time.time())}.{suffix}"


def decode_and_save(image_b64: str, path: Path) -> int:
    try:
        data = base64.b64decode(image_b64, validate=True)
    except Exception as exc:
        raise AppError(f"Response image was not valid base64: {exc}") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return len(data)


def scrub_payload_for_print(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, child in value.items():
            if key == "image_url" and isinstance(child, str) and child.startswith("data:"):
                out[key] = child[:64] + f"...<data-url {len(child)} chars>"
            else:
                out[key] = scrub_payload_for_print(child)
        return out
    if isinstance(value, list):
        return [scrub_payload_for_print(v) for v in value]
    return value


def validate_common(args: argparse.Namespace) -> None:
    if not args.prompt.strip():
        raise AppError("--prompt is required and must be non-empty.")
    if args.output_compression is not None and not (0 <= args.output_compression <= 100):
        raise AppError("--output-compression must be between 0 and 100.")
    if args.output_compression is not None and args.output_format == "png":
        raise AppError("--output-compression only applies to jpeg/webp, not png.")


def execute_image_mode(args: argparse.Namespace, mode: str) -> int:
    validate_common(args)
    if mode == "edit" and not args.image:
        raise AppError("edit requires at least one --image.")
    if mode == "edit":
        warnings = validate_edit_inputs(args.image, args.mask, strict=not args.allow_mask_warnings)
        for warning in warnings:
            eprint(f"warning: {warning}")
    payload = build_generate_payload(args) if mode == "generate" else build_edit_payload(args)
    if args.dry_run:
        printable = scrub_payload_for_print(payload) if args.scrub_data_urls else payload
        print(json.dumps({"success": True, "dry_run": True, "mode": mode, "payload": printable}, ensure_ascii=False, indent=2))
        return 0
    image_b64, response_id = post_responses(
        payload,
        timeout_seconds=args.timeout,
        verbose=args.verbose,
        raw_events_out=args.raw_events_out,
    )
    out_path = Path(args.out).expanduser() if args.out else default_out_path(mode, args.output_format)
    bytes_written = decode_and_save(image_b64, out_path)
    size, aspect = normalize_size(args)
    result = {
        "success": True,
        "mode": mode,
        "image": str(out_path.resolve()),
        "bytes": bytes_written,
        "provider": "openai-codex",
        "auth": "ChatGPT/Codex OAuth",
        "image_model": args.image_model,
        "chat_model": args.chat_model,
        "quality": args.quality,
        "size": size,
        "aspect_ratio": aspect,
        "output_format": args.output_format,
        "response_id": response_id,
    }
    if mode == "edit":
        result.update({"source_images": len(args.image), "masked": bool(args.mask)})
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"saved: {result['image']} ({bytes_written} bytes)")
        print(f"provider: openai-codex via ChatGPT/Codex OAuth | image_model={args.image_model} | quality={args.quality} | size={size}")
    return 0


def command_check_auth(args: argparse.Namespace) -> int:
    tokens = get_tokens(allow_refresh=not args.no_refresh)
    if args.refresh:
        refresh = str(tokens.get("refresh_token", "") or "")
        tokens = refresh_tokens(refresh, save=True)
    token = str(tokens.get("access_token", "") or "")
    result = {
        "success": True,
        "auth": "ChatGPT/Codex OAuth",
        "access_token_present": bool(token),
        "refresh_token_present": bool(tokens.get("refresh_token")),
        "token_preview": token[:6] + "..." + token[-4:] if len(token) > 12 else "<present>",
        "token_cache": str(token_cache_path()),
        "source": tokens.get("source") or "cache/env",
    }
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("Codex OAuth token available.")
        print(f"cache: {result['token_cache']}")
        print(f"refresh_token_present: {result['refresh_token_present']}")
    return 0


def command_logout(args: argparse.Namespace) -> int:
    path = token_cache_path()
    if path.exists():
        path.unlink()
        msg = f"Removed token cache: {path}"
    else:
        msg = f"No token cache found at: {path}"
    if args.print_json:
        print(json.dumps({"success": True, "message": msg, "token_cache": str(path)}, ensure_ascii=False))
    else:
        print(msg)
    return 0


def add_common_image_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--prompt", required=True, help="Text prompt/instruction.")
    parser.add_argument("--out", help="Output path. Defaults to ./codex_oauth_images/codex_oauth_<mode>_<timestamp>.<ext>.")
    parser.add_argument("--size", default="auto", help="auto, 1024x1024, 1536x1024, 1024x1536, or another backend-supported WxH string.")
    parser.add_argument("--aspect-ratio", choices=sorted(ASPECT_TO_SIZE), help="Convenience mapping to a supported size.")
    parser.add_argument("--quality", default="medium", choices=QUALITY_CHOICES)
    parser.add_argument("--background", default="opaque", choices=("auto", "opaque"))
    parser.add_argument("--output-format", default="png", choices=OUTPUT_FORMAT_CHOICES)
    parser.add_argument("--output-compression", type=int, help="JPEG/WEBP compression 0-100. Not valid for PNG.")
    parser.add_argument("--partial-images", type=int, default=1)
    parser.add_argument("--image-model", default=IMAGE_MODEL)
    parser.add_argument("--chat-model", default=DEFAULT_CHAT_MODEL)
    parser.add_argument("--instructions", help="Override system instructions sent with the Responses request.")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--raw-events-out", help="Append raw Responses SSE events as JSONL for debugging. May contain generated-image base64; keep private.")
    parser.add_argument("--dry-run", action="store_true", help="Print request payload without calling the API.")
    parser.add_argument("--scrub-data-urls", action="store_true", default=True, help="Replace embedded data URLs with placeholders in --dry-run output. Default on.")
    parser.add_argument("--no-scrub-data-urls", action="store_false", dest="scrub_data_urls")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--print-json", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone Codex OAuth image generation + edit/mix script.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="Run standalone ChatGPT/Codex OAuth device-code login and cache tokens locally.")
    p_login.add_argument("--timeout", type=int, default=15 * 60, help="Seconds to wait for browser sign-in.")
    p_login.add_argument("--print-json", action="store_true")
    p_login.set_defaults(func=run_device_login)

    p_auth = sub.add_parser("check-auth", help="Verify token availability; optionally refresh cached token.")
    p_auth.add_argument("--refresh", action="store_true", help="Force refresh using refresh_token.")
    p_auth.add_argument("--no-refresh", action="store_true", help="Do not auto-refresh even if CODEX_ALWAYS_REFRESH is set.")
    p_auth.add_argument("--print-json", action="store_true")
    p_auth.set_defaults(func=command_check_auth)

    p_logout = sub.add_parser("logout", help="Delete the local token cache file.")
    p_logout.add_argument("--print-json", action="store_true")
    p_logout.set_defaults(func=command_logout)

    p_gen = sub.add_parser("generate", help="Generate an image from text via Codex OAuth Responses image_generation.")
    add_common_image_args(p_gen)
    p_gen.set_defaults(func=lambda args: execute_image_mode(args, "generate"))

    p_edit = sub.add_parser("edit", help="Edit/mix/reference one or more images via image_generation action=edit.")
    p_edit.add_argument("--image", action="append", required=True, help="Source image path, URL, data URL, or file_id. Repeatable.")
    p_edit.add_argument("--mask", help="Optional mask path, URL, data URL, or file_id. Local masks are validated for dimensions/PNG alpha by default.")
    p_edit.add_argument("--allow-mask-warnings", action="store_true", help="Warn instead of failing when a local mask is non-PNG or lacks alpha.")
    p_edit.add_argument("--detail", choices=("low", "high", "auto"), help="Input image detail hint.")
    add_common_image_args(p_edit)
    p_edit.set_defaults(func=lambda args: execute_image_mode(args, "edit"))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        eprint("interrupted")
        return 130
    except AppError as exc:
        if getattr(args, "print_json", False):
            print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
        else:
            eprint(f"error: {exc}")
        return 1
    except Exception as exc:
        if getattr(args, "print_json", False):
            print(json.dumps({"success": False, "error": f"unexpected error: {exc}"}, ensure_ascii=False))
        else:
            eprint(f"unexpected error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
