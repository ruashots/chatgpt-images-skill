# chatgpt-images

Generate and edit images with **gpt-image-2** from the command line — or from an
AI coding agent — using your **ChatGPT account's OAuth** (Plus/Pro/Codex
entitlement). No OpenAI Platform API key.

Born as a handoff script built with Yui (a Hermes agent) that solved the hard
parts: the Codex device-code OAuth flow, token caching with auto-refresh, and
the Responses-API `image_generation` tool shape for both generation and
reference-faithful editing. Decomposed here into small modules with a thin CLI.

## Why this exists
gpt-image-2's superpower is **reference fidelity**: give it an image and it
preserves identity (faces, characters, products) through aggressive edits —
pose changes, scene swaps, multi-image composition. Local models generate
cheaply; this covers the shots where faithfulness to a reference is the point.

## Setup
```bash
python3 -m pip install httpx           # the only dependency (Python 3.10+)
python3 scripts/codex_images.py login  # one-time device-code OAuth
python3 scripts/codex_images.py check-auth
```
Tokens cache at `~/.config/codex-oauth-image-handoff/tokens.json` (0600) and
auto-refresh on 401. `CODEX_ACCESS_TOKEN` env or `CODEX_OAUTH_CACHE` override
supported. **The token cache is a password — never commit it.**

## Use
```bash
# text-to-image
python3 scripts/codex_images.py generate \
  --prompt "a watercolor fox reading a newspaper" \
  --aspect-ratio portrait --out fox.png

# reference-faithful edit
python3 scripts/codex_images.py edit \
  --image ref.png --prompt "make it nighttime, keep everything else identical" \
  --out night.png

# multi-reference composition
python3 scripts/codex_images.py edit \
  --image identity.png --image pose.png \
  --prompt "Image 1 is identity. Image 2 is pose. Redraw the image-1 character in the image-2 pose." \
  --out combo.png
```
Aspect ratios: `landscape|square|portrait` (or explicit `--size`). Masked edits:
`--mask mask.png` (validated locally: PNG + alpha + matching dimensions, before
any quota is spent). Debugging: `--dry-run` (payload preview, data URLs
scrubbed), `--raw-events-out events.jsonl`, `--verbose`.

## Field notes (tested)
- Flatten transparent PNGs before sending — alpha renders as black + speckle.
- Edits hold identity ~90%+ but embellish small details; chase style-exactness
  with a local i2i pass afterward.
- Multi-reference prompting: number the images and give each a job (identity /
  pose / palette), and state what must be preserved exactly.

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
Claude Code: drop this folder in `~/.claude/skills/`. Other agents: `HANDOFF.md`.

## Notes
Uses ChatGPT/Codex OAuth entitlements — subject to your account's quota and
rate limits; not unlimited, not free. MIT licensed.
