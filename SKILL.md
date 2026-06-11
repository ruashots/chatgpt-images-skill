---
name: chatgpt-images
description: Generate or edit images with ChatGPT's gpt-image-2 directly from Claude, via the operator's ChatGPT OAuth (no API key). Use when asked to generate/edit an image with ChatGPT/gpt-image quality, when an edit must stay faithful to a reference image (identity, faces, characters, products), for multi-reference composition ("X from image 1 in the pose/style of image 2"), for masked region-edits (replace only one area, protect the rest), or for instruction-following edits local models can't do. Standalone general-purpose tool; costs the operator's ChatGPT quota, so prefer local generation (ideogram4) when reference fidelity isn't needed.
---

# chatgpt-images — gpt-image-2 gen + edit from Claude

Decomposed from the original standalone implementation (preserved verbatim at
`references/handoff-original.py`). Auth is self-contained: cached OAuth tokens
with built-in 401→refresh→retry at `~/.config/codex-oauth-image-handoff/tokens.json`.

CLI: `python3 ~/.claude/skills/chatgpt-images/scripts/codex_images.py <cmd>`
Commands: `login` · `check-auth [--refresh]` · `logout` · `generate` · `edit`

## Capability map — pick the right shape
| Need | Shape |
|---|---|
| text → image | `generate --prompt "..."` |
| faithful edit of one image | `edit --image ref.png --prompt "..."` |
| identity from A + pose/style/product from B | `edit --image A.png --image B.png` + numbered-jobs prompt |
| change ONE region, protect the rest | `edit --image src.png --mask mask.png` |
| portrait/landscape output | `--aspect-ratio portrait\|landscape\|square` (or explicit `--size 1024x1536` etc. — explicit sizes WORK) |
| cheap test | `--quality low`; final: `medium` (default) or `high` |
| jpeg/webp + size control | `--output-format jpeg --output-compression 80` |
| inspect request without spending quota | `--dry-run` |
| API acting weird | `--raw-events-out events.jsonl --verbose` (jsonl may embed image base64 — keep private) |

## Multi-reference prompting (tested pattern)
Number the images and give each a JOB: "Image 1 is identity. Image 2 is pose.
Redraw the image-1 character in the image-2 pose. Preserve the mask design and
colors exactly; the background may change." State preserved-exactly vs
reinterpretable; ask for one coherent output, not a collage.

## Mask workflow
Mask = PNG with alpha; **transparent marks the EDITABLE region**, opaque is
protected. Local masks are pre-flight validated free of charge: PNG + real
alpha + dimensions exactly matching the first local source — failures stop
before quota. Build masks with PIL (alpha 0 where edits go). Override
validation only deliberately: `--allow-mask-warnings`.

## Edit extras
`--detail low|high|auto` input-image hint · `--instructions "..."` overrides the
system prompt · `--image` accepts path / https URL / data URL / file_id ·
`--chat-model` / `--image-model` overridable (defaults gpt-5.5 / gpt-image-2).

## Result contract
`--print-json` → one line: success, mode, image (abs path), bytes, quality,
size, aspect_ratio, response_id (+ source_images, masked for edits). Exit 0/1/130.
ALWAYS view the output image before reporting success.

## Gotchas (tested 2026-06-11)
- **Flatten transparent PNGs before sending as references** — alpha renders as
  black + speckle.
- Edits hold identity ~90%+ but embellish small details (costume bits, gloss);
  chase style-exactness with a local i2i pass after (ideogram4).
- Auth: 401 auto-refreshes; if `check-auth` itself fails, the operator must run
  `login` (interactive device code) — not yours to fix.
- Quota discipline: operator's ChatGPT account. Test at `--quality low`, never
  blind-retry errors, route bulk/style work to ideogram4 (local, free).
