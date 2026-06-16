---
name: chatgpt-images
description: Generate or edit images with ChatGPT's gpt-image-2 directly from Claude, via the operator's ChatGPT OAuth (no API key). Use when asked to generate/edit an image with ChatGPT/gpt-image quality, when an edit must stay faithful to a reference image (identity, faces, characters, products), for multi-reference composition ("X from image 1 in the pose/style of image 2"), for masked region-edits (replace only one area, protect the rest), or for instruction-following edits local models can't do. Standalone general-purpose tool; image gen via the ChatGPT plan consumes negligible plan usage in practice, so use it freely — pick local ideogram4 only when you want speed, offline, or deterministic output.
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
| change ONE region, protect the rest | `--mask` (auto-retries the flaky backend; needs Pillow) — or crop→edit→composite on the reliable unmasked path for guaranteed results |
| portrait/landscape output | `--aspect-ratio portrait\|landscape\|square` (or explicit `--size 1024x1536` etc. — explicit sizes WORK) |
| pick quality | `--quality low/medium/high` — tier sets detail, NOT speed (latency is backend-load roulette); default high freely |
| jpeg/webp + size control | `--output-format jpeg --output-compression 80` |
| inspect request without spending quota | `--dry-run` |
| API acting weird | `--raw-events-out events.jsonl --verbose` (jsonl may embed image base64 — keep private) |

## Multi-reference prompting (tested pattern)
**Tell the model HOW to interpret each reference, not just which pixels.** A flat
graphic stays flat unless you say "render image 2 as real fabric, fitted to the 3D
head" — tested: same inputs, "wearing the mask from image 2" gave a sticker overlay;
adding "render as real lycra fabric with seams/sheen, conforming to his skull, not a
flat overlay" gave a photorealistic worn mask. Each image gets: a JOB (identity /
pose / object / material) AND, when it's a 2D source becoming 3D, an interpretation
instruction (material, lighting, how it wraps/sits).
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
- **Masks FLAKE on this backend (debugged 2026-06-11):** the masked-inpaint path
  intermittently (~65-85% per attempt, observed) returns a black blob in the hole.
  Model intent + mask geometry are always correct; the backend composite fails.
  The skill AUTO-DETECTS this (needs Pillow) and retries up to `--mask-retries`
  (default 3); result JSON carries `mask_attempts`, and `mask_degenerate:true` if
  still black after all retries. Because the base failure rate is high, retry is
  best-effort — for a GUARANTEED surgical edit use crop→edit→composite on the
  reliable UNMASKED path (unmasked edits never flaked in testing). `--mask` is not
  "broken," just unreliable; auto-retry makes casual use mostly work.
- **Flatten transparent PNGs before sending as references** — alpha renders as
  black + speckle.
- Edits hold identity ~90%+ but embellish small details (costume bits, gloss);
  chase style-exactness with a local i2i pass after (ideogram4).
- Auth: 401 auto-refreshes; if `check-auth` itself fails, **drive the `login` yourself** —
  don't make the operator run it. Run `login` in the **background** (it blocks on a
  `Waiting for sign-in...` poll), read the flushed `Open: <url>` + `Enter code: <code>` from
  its output, and hand the operator just the URL and code to authorize. They complete the
  sign-in (their ChatGPT account); the background `login` then saves the token and you proceed.
- Cost is LATENCY + flakiness, not plan usage. Observed (one heavy day, ~30+
  calls): negligible dent in the ChatGPT plan's usage meter — use it freely; no
  need to ration. The real costs: single gen/edit ~30-60s, multi-ref edits ~3-4
  min, and the masked-path black-blob flake. Reach for local ideogram4 when you
  want SPEED, OFFLINE, or DETERMINISTIC output — not to save quota.
  (Caveat: based on account observation, not billing internals; a monthly
  ceiling we haven't hit can't be ruled out.)
