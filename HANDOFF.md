# chatgpt-images — agent handoff

You are an agent with shell access. This tool generates/edits images via
gpt-image-2 using the operator's ChatGPT OAuth. It SPENDS THE OPERATOR'S QUOTA —
use it for reference-fidelity or instruction-heavy shots; route bulk/style work
to local generators first.

## Contract
1. Verify auth before first use: `python3 scripts/codex_images.py check-auth`.
   If it fails, STOP and tell the operator to run `login` (interactive).
2. Generate: `generate --prompt "..." [--aspect-ratio portrait|landscape|square] --out X.png --print-json`
3. Edit: `edit --image ref.png [--image ref2.png ...] --prompt "..." --out X.png --print-json`
   - Flatten transparent PNGs first (alpha → black artifacts).
   - Multi-reference: number images in the prompt, give each a job, state what
     must be preserved exactly.
4. Parse the `--print-json` line: `success`, `image` (path), `bytes`. On
   `success: false`, read the error; do not blind-retry (quota).
5. ALWAYS view the output image and judge it before reporting success.

## Failure modes
- `check-auth` fails → operator must `login`; not your call.
- HTTP 429 / quota errors → stop and surface; never loop retries.
- Empty result ("no image_generation_call") → try `--dry-run` to inspect the
  payload and `--raw-events-out` to capture the stream; report findings.

## Cost discipline
`--quality low` for tests, `medium` default. One verification image is fine;
bulk experiments are not. When unsure whether a shot needs gpt-image-2, it
probably doesn't.
