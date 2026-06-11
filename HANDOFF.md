# chatgpt-images — agent handoff

You are an agent with shell access. This tool generates/edits images via
gpt-image-2 using the operator's ChatGPT OAuth. It SPENDS THE OPERATOR'S QUOTA —
use it for reference-fidelity, masked-region, or instruction-heavy shots; route
bulk/style work to local generators first.

CLI: `python3 scripts/codex_images.py <command>` (only dependency: httpx)

## Contract
1. Verify auth before first use: `check-auth`. If it fails, STOP — the operator
   must run `login` (interactive device-code); not your call.
2. Pick the correct shape — under-using the tool wastes its capabilities:
   - `generate --prompt "..."` — text-to-image
   - `edit --image ref.png --prompt "..."` — faithful single-reference edit
   - `edit --image A.png --image B.png --prompt "Image 1 is identity. Image 2 is
     pose. ..."` — multi-reference composition: NUMBER the images, give each a
     JOB (identity/pose/palette/product), state preserved-exactly vs free.
   - `edit --image src.png --mask mask.png --prompt "Replace only the masked
     area..."` — region edit. Mask = PNG, alpha transparent = editable, must
     match source dimensions; validated locally before quota is spent.
3. Output shaping: `--aspect-ratio portrait|landscape|square` or explicit
   `--size WxH` (both work) · `--quality low` for tests, `medium` final ·
   `--output-format png|jpeg|webp` (+ `--output-compression 0-100` for the
   latter two) · `--out path.png`.
4. Always pass `--print-json` and parse the result line: `success`, `image`
   (absolute path), `bytes`. On `success: false` read the error — never
   blind-retry (quota).
5. ALWAYS view the output image and judge it against the request before
   reporting success.

## Input rules
- Flatten transparent PNGs before sending (alpha → black + speckle artifacts).
- `--image` accepts local path, https URL, data URL, or `file_…` ID; repeatable.
- Expect ~90%+ identity fidelity on edits with minor detail embellishment; for
  style-exact results suggest a local img2img pass afterward.

## Debugging ladder (cheap → expensive)
1. `--dry-run` — exact payload, zero quota (data URLs scrubbed by default).
2. `--verbose` — SSE event types to stderr on a real call.
3. `--raw-events-out events.jsonl` — full stream capture; may contain image
   base64, treat as private.

## Failure modes
- `check-auth` fails → operator runs `login`; stop.
- HTTP 429 / quota → stop and surface; never loop retries.
- Empty result ("no image_generation_call") → dry-run the payload, capture raw
  events, report findings.
- Mask validation error → fix the mask (PNG, alpha, matching dims) rather than
  forcing `--allow-mask-warnings`.

## Cost discipline
One verification image is fine; bulk experiments are not. When unsure whether a
shot needs gpt-image-2, it probably doesn't — local first.
