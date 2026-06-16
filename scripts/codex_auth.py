"""Codex/ChatGPT OAuth: token cache, refresh, device login, headers."""
# Decomposed from Yui's codex_oauth_image_handoff.py (references/handoff-original.py).
# Logic unchanged; split along module seams. PRIVATE — never publish.
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Public constants used by the Codex/ChatGPT device-code OAuth flow.
CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_AUTH_ISSUER = "https://auth.openai.com"
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"

class AppError(RuntimeError):
    """Expected user-facing error."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def eprint(*parts: object) -> None:
    print(*parts, file=sys.stderr, flush=True)


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

    # flush=True is required: stdout is block-buffered when piped (e.g. an agent runs
    # `login` in the background to relay the code), and the "Waiting for sign-in..."
    # poll loop below never returns, so without an explicit flush the device code stays
    # stuck in the buffer and is invisible to whoever needs to enter it.
    print("To continue:", flush=True)
    print(f"  1. Open: {issuer}/codex/device", flush=True)
    print(f"  2. Enter code: {user_code}", flush=True)
    print("Waiting for sign-in... Ctrl+C to cancel.", flush=True)

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
