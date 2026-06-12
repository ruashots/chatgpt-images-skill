![chatgpt-images — generate & edit with gpt-image-2 from Claude](assets/banner.png)

# chatgpt-images

Generate and edit images with **gpt-image-2** from the command line — or from an
AI coding agent — using your **ChatGPT account's OAuth** (Plus/Pro/Codex
entitlement). No OpenAI Platform API key.

Handles the full path end to end: Codex device-code OAuth, token caching with
automatic refresh, the Responses-API `image_generation` tool shape for both
generation and reference-faithful editing, multi-reference composition, masked
region edits with local pre-flight validation, and stream-level debugging.

## Why
gpt-image-2's superpower is **reference fidelity**: give it an image and it
preserves identity (faces, characters, products) through aggressive edits —
pose changes, scene swaps, multi-image composition. Local models generate
cheaply; this covers the shots where faithfulness to a reference is the point.

## Install
Python 3.10+ and one dependency:
```bash
python3 -m pip install httpx
```

## Authenticate
```bash
python3 scripts/codex_images.py login        # device-code OAuth: open URL, enter code
python3 scripts/codex_images.py check-auth   # verify (add --refresh to force-renew)
python3 scripts/codex_images.py logout       # delete the local token cache
```
Tokens cache at `~/.config/codex-oauth-image-handoff/tokens.json` (mode 0600)
and refresh automatically on 401 — the refresh token rotates in the cache.

Alternative auth, no `login` needed:
```bash
export CODEX_ACCESS_TOKEN='...'          # direct token
export CODEX_REFRESH_TOKEN='...'         # optional, enables auto-refresh
export CODEX_OAUTH_CACHE=/path/to/tokens.json   # use a different cache file
export CODEX_ALWAYS_REFRESH=1            # refresh before every call
```
**The token cache is a password.** It is git-ignored here; keep it that way.

## Generate (text-to-image)
```bash
python3 scripts/codex_images.py generate \
  --prompt "a watercolor fox reading a newspaper" \
  --aspect-ratio portrait --quality medium --out fox.png --print-json
```

## Edit (reference images)
```bash
python3 scripts/codex_images.py edit \
  --image ref.png \
  --prompt "make it nighttime, keep everything else identical" \
  --out night.png
```
`--image` accepts a local path, `https://` URL, `data:` URL, or `file_…` ID,
and is **repeatable** for multi-reference composition:
```bash
python3 scripts/codex_images.py edit \
  --image identity.png --image pose.png \
  --prompt "Image 1 is identity. Image 2 is pose. Redraw the image-1 character in the image-2 pose." \
  --out combo.png
```
Multi-reference prompting pattern (tested): **number the images, give each a
job** (identity / pose / palette / product fidelity), state what must be
preserved exactly and what may be reinterpreted, and ask for one coherent
output rather than a collage.

## Masked edits

> ⚠️ **This backend's masked path is unreliable** (debugged 2026-06-11). The
> masked inpaint intermittently (~65-85% per attempt in testing) returns a black
> blob in the masked region — the model's intent and the mask geometry are always
> correct, but the backend's composite step fails. The skill **auto-detects** the
> black-blob failure (requires Pillow) and **retries** up to `--mask-retries`
> (default 3); the result JSON reports `mask_attempts` and, if still degenerate
> after retries, `mask_degenerate: true`.
>
> Because the per-attempt failure rate is high, retry is best-effort. **For a
> guaranteed surgical edit, use crop→edit→composite** — the unmasked edit path is
> fully reliable:
> ```bash
> # 1. crop the region you want to change, 2. edit the crop (no mask), 3. paste back
> python3 -c "from PIL import Image; Image.open('src.png').crop((x0,y0,x1,y1)).save('reg.png')"
> python3 scripts/codex_images.py edit --image reg.png --prompt "make it red" --out reg2.png
> python3 -c "from PIL import Image; b=Image.open('src.png'); b.paste(Image.open('reg2.png').resize((x1-x0,y1-y0)),(x0,y0)); b.save('out.png')"
> ```
```bash
python3 scripts/codex_images.py edit \
  --image source.png --mask mask.png \
  --prompt "Replace only the masked logo area with a sunflower" \
  --out masked.png
```
Local masks are validated **before any quota is spent**:
- mask must be PNG with real alpha (transparent = editable region, opaque = protected)
- mask dimensions must exactly match the first local source image
- non-PNG / no-alpha masks fail fast; override with `--allow-mask-warnings`

Remote URLs / data URLs / file IDs skip local validation (the backend checks).
If your account behaves with the opposite alpha convention, state it in the prompt.

## Shared options (generate + edit)
| flag | default | notes |
|---|---|---|
| `--size` | `auto` | `1024x1024`, `1536x1024`, `1024x1536`, or other backend-supported WxH |
| `--aspect-ratio` | — | `landscape` / `square` / `portrait` → mapped sizes; `--size` wins if both |
| `--quality` | `medium` | `auto` / `low` / `medium` / `high` — use `low` for cheap tests |
| `--output-format` | `png` | `png` / `jpeg` / `webp` |
| `--output-compression` | — | 0–100, jpeg/webp only |
| `--background` | `opaque` | `opaque` / `auto` |
| `--out` | auto path | defaults to `./codex_oauth_images/codex_oauth_<mode>_<ts>.<ext>` |
| `--detail` | — | (edit) `low`/`high`/`auto` input-image detail hint |
| `--image-model` / `--chat-model` | `gpt-image-2` / `gpt-5.5` | overridable |
| `--instructions` | built-in | override the system instructions |
| `--timeout` | 300 | seconds |
| `--print-json` | off | machine-readable result line |

## Debugging
```bash
--dry-run            # print the exact request payload; no API call, no quota
--no-scrub-data-urls #   (dry-run embeds full data URLs instead of placeholders)
--raw-events-out events.jsonl   # record the raw SSE stream (may contain image
                                # base64 — keep private)
--verbose            # narrate event types to stderr
```

## Output
`--print-json` emits one line: `success`, `mode`, `image` (absolute path),
`bytes`, `quality`, `size`, `aspect_ratio`, `response_id`, and for edits
`source_images` + `masked`. Exit codes: 0 success, 1 error (JSON error line
with `--print-json`), 130 interrupted.

## Field notes (tested)
- **Flatten transparent PNGs before sending as references** — alpha channels
  render as black with speckle artifacts.
- Edits hold identity ~90%+ but tend to embellish small details (costume bits,
  gloss); chase style-exactness with a local img2img pass afterward.
- Quota is your ChatGPT account's — not unlimited, not free. Test at
  `--quality low`; don't loop retries on errors.

## Benchmarks

Wall-clock per call, measured 2026-06-11 (2 runs each, WSL client → ChatGPT/Codex
OAuth backend). **Latency is dominated by backend queue/load, not the quality
tier** — `high` was often faster than `low`. Treat these as rough ranges, not SLAs.

| Operation | Avg | Observed range |
|---|---|---|
| `generate` (low) | ~54s | 49–58s |
| `generate` (medium) | ~36s | 18–53s |
| `generate` (high) | ~27s | 25–29s |
| `edit` — 1 reference (medium) | ~63s | 61–65s |
| `edit` — 2 references (medium) | ~222s | 189–255s |

Takeaways: single-image gen/edit usually lands in **20–65s**. **Multi-reference
edits are markedly heavier (~3–4 min)** — each extra reference image is uploaded as
a base64 data URL and processed, and a large reference (e.g. an 8-view identity
sheet) dominates the cost. Quality tier mainly affects detail/cost, not speed.
Masked edits multiply by `mask_attempts` when the backend's black-blob flake triggers.

## Layout
| file | role |
|---|---|
| `scripts/codex_images.py` | CLI — login / check-auth / logout / generate / edit |
| `scripts/codex_auth.py` | OAuth: device login, token cache, refresh, headers |
| `scripts/codex_api.py` | payload builders, SSE transport, result saving |
| `scripts/codex_validation.py` | stdlib image/mask inspection (no Pillow needed) |
| `references/handoff-original.py` | the original standalone one-filer (canonical reference) |
| `SKILL.md` | Claude Code skill wiring |
| `HANDOFF.md` | agent-agnostic usage contract |

## Agents
Claude Code: drop this folder in `~/.claude/skills/`. Other agents: see
[`HANDOFF.md`](HANDOFF.md).

MIT licensed.
