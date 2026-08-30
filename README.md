# HeyGen video by API

A single-file Python client for the [HeyGen](https://www.heygen.com) API:
list avatars and voices, check your credit balance, submit a video generation
job, poll it to completion, and download the MP4.

Stdlib only — no `pip install`, no virtualenv. Requires Python 3.8+.

## Credits

HeyGen's API is pay-as-you-go; the free API tier ended in February 2026.

**Only `generate` spends credits.** `avatars`, `voices`, `quota` and `status`
are all free to call. As a safety net, `generate` will not submit anything
unless you pass `--i-understand-this-spends-credits` — without it, it prints
the exact JSON payload it would send and exits. Use that to check your
parameters before spending anything.

## Authentication

The script reads the key from the environment. It is deliberately **not**
accepted as a command-line argument, which would leak it into your shell
history and the process list.

**Mode 1 — local machine.** Export the key:

```bash
export HEYGEN_API_KEY='sk_...'      # note the leading space to skip bash history
python3 heygen.py quota
```

The script sends `X-Api-Key: <your key>`.

**Mode 2 — Claude Code cloud session.** Leave `HEYGEN_API_KEY` unset. Store
the key on the cloud environment as an **API credential** instead
(claude.ai/code → environment editor → API credentials):

- Credential type: `Bearer`
- Allowed websites: `api.heygen.com`
- Custom headers: name `X-Api-Key`, **prefix cleared** (HeyGen wants the bare
  value, not `Bearer <key>`), value = your key

The agent proxy then attaches the header *after* the request leaves the VM.
The script sends no auth header of its own and the key never reaches the
session, its environment variables, or any file. This is the safer mode —
prefer it.

Never commit the key. `.gitignore` already covers `.env*`, `*.key` and `*.pem`.

## Network access

In a Claude Code cloud session, HeyGen hosts are blocked at the **Trusted**
network level. Set **Network access** to `Custom` and allowlist:

```
api.heygen.com
*.heygen.com
*.heygen.ai
```

`*.heygen.ai` is not optional: finished MP4s are served from a pre-signed CDN
link on `resource*.heygen.ai` / `files2.heygen.ai`, a different host from the
API. Without it the download fails even though generation succeeded.

Keep **"Also include default list of common package managers"** checked so
GitHub and pip keep working.

Environment settings are baked in when the session's VM is provisioned, so
**changes only take effect in a new session** — they will not appear in one
that is already running.

## Usage

```bash
python3 heygen.py quota                       # remaining API credits
python3 heygen.py avatars                     # avatar_id / name / gender
python3 heygen.py avatars --limit 0           # show all
python3 heygen.py voices --language hindi     # filter by language
python3 heygen.py voices --language english
python3 heygen.py status --video-id VIDEO_ID_PLACEHOLDER
```

Add `--json` to any command for the raw API response, for piping into `jq`.

### Generating a video

Dry run first — prints the payload, sends nothing:

```bash
python3 heygen.py generate \
    --script "PLACEHOLDER SCRIPT TEXT" \
    --avatar-id AVATAR_ID_PLACEHOLDER \
    --voice-id VOICE_ID_PLACEHOLDER
```

For real, once you've picked ids from `avatars` and `voices`:

```bash
python3 heygen.py generate \
    --script-file script.txt \
    --avatar-id AVATAR_ID_PLACEHOLDER \
    --voice-id VOICE_ID_PLACEHOLDER \
    --width 1280 --height 720 \
    --title "PLACEHOLDER TITLE" \
    --out out/video.mp4 \
    --i-understand-this-spends-credits
```

This submits the job, polls `video_status.get` every 10s until it completes,
then streams the MP4 to `--out`. Long scripts can take several minutes.

If it times out, the job is usually still running — the video id is printed,
so pick it back up with `heygen.py status --video-id ...`.

### `generate` options

| Flag | Default | Notes |
| --- | --- | --- |
| `--script` / `--script-file` | required | one or the other; file is read as UTF-8 |
| `--avatar-id` | required | from `heygen.py avatars` |
| `--voice-id` | required | from `heygen.py voices` |
| `--width` / `--height` | 1280×720 | 720p; raise for 1080p if your plan allows |
| `--title` | none | shown in the HeyGen dashboard |
| `--avatar-style` | `normal` | e.g. `normal`, `circle`, `closeUp` |
| `--speed` | `1.0` | voice speed, roughly 0.5–1.5 |
| `--background-color` | none | hex, e.g. `#ffffff` |
| `--out` | `out/video.mp4` | parent directories are created |
| `--poll-interval` | `10` | seconds between status checks |
| `--timeout` | `1800` | seconds before giving up on polling |

## Endpoints used

| Command | Method | Path | Credits |
| --- | --- | --- | --- |
| `avatars` | GET | `/v2/avatars` | free |
| `voices` | GET | `/v2/voices` | free |
| `quota` | GET | `/v2/user/remaining_quota` | free |
| `status` | GET | `/v1/video_status.get` | free |
| `generate` | POST | `/v2/video/generate` | **spends** |

Override the base URL with `HEYGEN_BASE_URL` if needed.

> **Heads up:** HeyGen has been migrating these v2 "Studio" endpoints toward a
> newer API and lists them as slated for deprecation. Re-check the current
> reference at <https://docs.heygen.com/> before relying on the request shape
> long-term.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `Could not reach ... 403 Forbidden` on the tunnel | Host blocked by the egress policy. See [Network access](#network-access). |
| `HTTP 401` | Key missing or invalid. Check `HEYGEN_API_KEY`, or that the environment credential is saved and you started a fresh session. |
| `HTTP 404` on `status` | Unknown `video_id`, or it belongs to another account. |
| `HTTP 424` | HeyGen rejected the parameters — check `avatar_id`, `voice_id`, and dimensions. |
| Generation succeeds, download 403s | `*.heygen.ai` is missing from the allowlist. |
| `remaining_quota` is 0 or negative | Top up the API balance. It is a separate pool from web-plan credits. |
