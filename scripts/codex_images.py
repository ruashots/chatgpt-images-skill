"""CLI: login / check-auth / logout / generate / edit."""
# Decomposed from Yui's codex_oauth_image_handoff.py (references/handoff-original.py).
# Logic unchanged; split along module seams. PRIVATE — never publish.
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from codex_auth import (AppError, eprint, run_device_login, get_tokens, refresh_tokens,
                        token_cache_path)
from codex_api import (ASPECT_TO_SIZE, IMAGE_MODEL, DEFAULT_CHAT_MODEL, OUTPUT_FORMAT_CHOICES,
                       QUALITY_CHOICES, build_edit_payload, build_generate_payload,
                       decode_and_save, default_out_path, normalize_size, post_responses,
                       scrub_payload_for_print)
from codex_validation import validate_edit_inputs

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
