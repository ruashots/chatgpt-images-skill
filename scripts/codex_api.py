"""Responses-API image payloads, SSE transport, result handling."""
# Decomposed from Yui's codex_oauth_image_handoff.py (references/handoff-original.py).
# Logic unchanged; split along module seams. PRIVATE — never publish.
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import time
from pathlib import Path
from typing import Any, Iterable

from codex_auth import (AppError, CODEX_BASE_URL, codex_headers, eprint, get_tokens,
                        refresh_tokens, require_httpx)

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

def output_dir() -> Path:
    explicit = os.environ.get("CODEX_IMAGE_OUTPUT_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return Path.cwd() / "codex_oauth_images"

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
