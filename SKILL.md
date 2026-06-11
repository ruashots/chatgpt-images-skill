---
name: chatgpt-images
description: Generate or edit images with ChatGPT's gpt-image-2 directly from Claude, via the operator's ChatGPT OAuth (no API key). Use when asked to generate/edit an image with ChatGPT/gpt-image quality, when an edit must stay faithful to a reference image (identity, faces, characters, products), for multi-reference composition ("X from image 1 in the pose/style of image 2"), or for instruction-following edits local models can't do. Standalone general-purpose tool; costs the operator's ChatGPT quota, so prefer local generation (ideogram4) when reference fidelity isn't needed.
---

# chatgpt-images — gpt-image-2 gen + edit from Claude

Engine: the codex-oauth bridge **built with Yui**, delivered as a handoff (preserved verbatim at `references/handoff-original.py`)
and decomposed here into modules. Auth is self-contained: cached OAuth tokens with
built-in refresh at `~/.config/codex-oauth-image-handoff/tokens.json` — no homelab
dependency at call time.

All commands: `python3 ~/.claude/skills/chatgpt-images/scripts/codex_images.py <cmd>`

## Generate
```bash
... generate --prompt "a watercolor fox reading a newspaper" \
    --aspect-ratio portrait --quality medium --out fox.png --print-json
```
`--aspect-ratio landscape|square|portrait` (→1536x1024 / 1024x1024 / 1024x1536) or
explicit `--size`. Explicit sizes WORK in this implementation (gen and edit).

## Edit / multi-reference
```bash
... edit --image ref.png --prompt "make it nighttime, keep everything else identical" --out night.png
... edit --image identity.png --image pose.png \
    --prompt "Image 1 is identity. Image 2 is pose. Redraw image-1 character in image-2 pose." --out combo.png
```
Reference prompting pattern (Yui's, tested): number the images, give each a job
(identity / pose / palette / product fidelity), say what must be preserved exactly.
`--mask mask.png` for region edits — local masks validated (PNG+alpha, dims must
match source) before spending quota; `--allow-mask-warnings` to override.

## Auth
- `check-auth` — verify cached token (add `--refresh` to force-renew)
- `login` — device-code OAuth re-seed (only if the refresh chain ever dies)
- `logout` — delete the token cache
- Renewal is AUTOMATIC: 401 → refresh → retry, refresh token rotates in cache.

## Debugging
`--dry-run` prints the exact payload (data URLs scrubbed) without spending quota.
`--raw-events-out events.jsonl` records the SSE stream when the API acts weird.
`--verbose` narrates event types.

## Gotchas (tested 2026-06-11)
- **Flatten transparent PNGs before sending** — alpha renders as black + speckle.
- Edits hold identity ~90%+ but embellish details (costume bits, gloss); chase
  style-exactness with a local i2i pass after.
- Quality `low` is fine for tests; default medium. It's the operator's ChatGPT quota —
  identity-critical or instruction-heavy shots only; bulk/style work → ideogram4.

## Layout
`scripts/codex_auth.py` (tokens, refresh, device login) · `scripts/codex_validation.py`
(stdlib image/mask inspection) · `scripts/codex_api.py` (payloads, SSE, save) ·
`scripts/codex_images.py` (CLI). `references/handoff-original.py` = Yui's onefiler
+ the one fix (httpx client lifecycle in post_responses) — canonical reference.
