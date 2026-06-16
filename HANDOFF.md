# chatgpt-images — agent handoff

You are an agent with shell access. This tool generates/edits images via
gpt-image-2 using the operator's ChatGPT OAuth. In practice it consumes
negligible ChatGPT-plan usage (observed over a heavy day), so use it freely —
the real costs are LATENCY (single ~30-60s, multi-ref ~3-4min) and occasional
masked-path flakiness. Prefer a local generator only for speed, offline, or
deterministic needs — not to save quota.

CLI: `python3 scripts/codex_images.py <command>` (only dependency: httpx)

## Contract
1. Verify auth before first use: `check-auth`. If it fails, drive the device-code
   `login` for the operator: run it **in the background** (it blocks on a
   `Waiting for sign-in...` poll), read the emitted `Open: <url>` + `Enter code: <code>`
   from its output, and relay both to the operator to authorize. Only the operator can
   complete the sign-in (it's their ChatGPT account); the agent just relays the code.
   The device-code prompts are flushed, so they appear immediately even while backgrounded.
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
   (absolute path), `bytes`. On `success: false` read the error before retrying
   (retries cost wall-clock, not meaningful plan usage).
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
- `check-auth` fails → drive `login` in the background, relay the URL + device code to the operator to authorize.
- HTTP 429 / rate-limit → back off and surface; loop-retrying wastes wall-clock, not plan quota.
- Empty result ("no image_generation_call") → dry-run the payload, capture raw
  events, report findings.
- Masked edit returns `mask_degenerate:true` → retries exhausted on the flaky
  backend; fall back to crop→edit→composite rather than burning more attempts.

## Cost notes
Plan usage is effectively negligible in practice — don't ration calls. Do mind
LATENCY: multi-ref edits are ~3-4 min, so batch/parallelize and don't serialize
needlessly. Still ALWAYS view each output and judge it before reporting success.
