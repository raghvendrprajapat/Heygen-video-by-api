# HeyGen video by API

A single-file Python client for the [HeyGen](https://www.heygen.com) **v3** API:
browse avatar looks and voices, check your wallet balance, submit a video
generation job, poll it to completion, and download the MP4.

Stdlib only — no `pip install`, no virtualenv. Requires Python 3.8+.

> **v2 is being removed.** The legacy `/v2/avatars`, `/v2/video/generate` and
> `/v1/video_status.get` endpoints sunset on **2026-10-31**. This client targets
> v3 throughout.

## Credits

HeyGen's API is pay-as-you-go; the free API tier ended in February 2026.

**Only `generate` spends credits.** `avatars`, `voices`, `credits` and `status`
are all free. As a safety net, `generate` will not submit anything unless you
pass `--i-understand-this-spends-credits` — without it, it prints the exact
JSON payload it would send and exits. Use that to check your parameters before
spending anything.

## Authentication

The script reads the key from the environment. It is deliberately **not**
accepted as a command-line argument, which would leak it into your shell
history and the process list.

**Mode 1 — local machine.** Export the key:

```bash
 export HEYGEN_API_KEY='sk_...'     # leading space keeps it out of bash history
python3 heygen.py credits
```

The script sends `X-Api-Key: <your key>`.

**Mode 2 — Claude Code cloud session.** Leave `HEYGEN_API_KEY` unset and store
the key on the cloud environment as an **API credential** instead
(claude.ai/code → environment editor → API credentials):

- Credential type: `Bearer`
- Allowed websites: `api.heygen.com`
- Custom headers: name `X-Api-Key`, **prefix cleared** (HeyGen wants the bare
  value, not `Bearer <key>`), value = your key

The agent proxy attaches the header *after* the request leaves the VM, so the
key never reaches the session, its environment variables, or any file. Prefer
this mode.

Verify either mode with `python3 heygen.py credits`.

Never commit the key. `.gitignore` covers `.env*`, `*.key` and `*.pem`.

## Network access

In a Claude Code cloud session, HeyGen hosts are blocked at the **Trusted**
network level. Set **Network access** to `Custom` and allowlist:

```
api.heygen.com
*.heygen.com
*.heygen.ai
```

`*.heygen.ai` is not optional: finished videos are served from a pre-signed CDN
link on `resource*.heygen.ai` / `files*.heygen.ai`, a different host from the
API. Without it the download fails even though generation succeeded.

Keep **"Also include default list of common package managers"** checked so
GitHub and pip keep working.

## Usage

```bash
python3 heygen.py credits                        # wallet balance
python3 heygen.py avatars --limit 20             # avatar LOOK ids
python3 heygen.py avatars --avatar-type digital_twin
python3 heygen.py voices --language Hindi --limit 0
python3 heygen.py voices --language English --gender female
python3 heygen.py status --video-id VIDEO_ID_PLACEHOLDER
```

Add `--json` (before or after the subcommand) for raw JSON to pipe into `jq`.

### Avatars: groups vs looks

A **group** is a character (e.g. "Saoirse"); a **look** is one outfit/pose for
that character. `avatars` lists *looks*, because **the look id is what
`--avatar-id` takes**. Each character has 18–32 looks, so results cluster by
character — use `--limit` generously, or `--group-id` to focus on one.

### Generating a video

Dry run first — prints the payload, sends nothing:

```bash
python3 heygen.py generate \
    --script "PLACEHOLDER SCRIPT TEXT" \
    --avatar-id AVATAR_LOOK_ID_PLACEHOLDER \
    --voice-id VOICE_ID_PLACEHOLDER
```

For real, once you've picked ids:

```bash
python3 heygen.py generate \
    --script-file script.txt \
    --avatar-id AVATAR_LOOK_ID_PLACEHOLDER \
    --voice-id VOICE_ID_PLACEHOLDER \
    --resolution 1080p --aspect-ratio auto \
    --title "PLACEHOLDER TITLE" \
    --out out/video.mp4 \
    --i-understand-this-spends-credits
```

This submits the job, polls `GET /v3/videos/{id}` every 10s until it completes,
then streams the MP4 to `--out`. If it times out the job is usually still
running — the video id is printed, so pick it back up with
`heygen.py status --video-id ...`.

### `generate` options

| Flag | Default | Notes |
| --- | --- | --- |
| `--script` / `--script-file` | required | one or the other; file read as UTF-8 |
| `--avatar-id` | required | a **look** id from `heygen.py avatars` |
| `--voice-id` | required | from `heygen.py voices` |
| `--resolution` | `1080p` | `720p`, `1080p`, or `4k` |
| `--aspect-ratio` | `auto` | `auto`, `16:9`, `9:16`, `4:5`, `5:4`, `1:1` |
| `--engine` | Avatar IV | `avatar_iii`, `avatar_iv`, `avatar_v` — check the look's `supported_api_engines` first |
| `--title` | none | display name in the HeyGen dashboard |
| `--speed` | `1.0` | 0.5–1.5 |
| `--pitch` | none | −50 to +50 |
| `--locale` | none | e.g. `en-US`, `hi-IN` |
| `--motion-prompt` | none | natural-language body/hand motion |
| `--expressiveness` | none | `low`/`medium`/`high`; **Avatar IV only** |
| `--background-color` | none | hex, e.g. `#ffffff` |
| `--remove-background` | off | transparent background |
| `--output-format` | `mp4` | `mp4` or `webm` (alpha channel) |
| `--out` | `out/video.mp4` | parent directories are created |
| `--poll-interval` | `10` | seconds between status checks |
| `--timeout` | `1800` | seconds before giving up on polling |

`--expressiveness` with `--engine avatar_v` is rejected locally, before the
request is sent, because HeyGen returns a validation error for that pair.

## Endpoints used

| Command | Method | Path | Credits |
| --- | --- | --- | --- |
| `avatars` | GET | `/v3/avatars/looks` | free |
| `voices` | GET | `/v3/voices` | free |
| `credits` | GET | `/v3/users/me` | free |
| `status` | GET | `/v3/videos/{video_id}` | free |
| `generate` | POST | `/v3/videos` | **spends** |

Override the base URL with `HEYGEN_BASE_URL`.

## Known API quirk: filters are dropped when paginating

As of 2026-08, v3 list endpoints apply query filters to the **first page only**.
Follow a `next_token` and the filter is silently dropped, so a naive
paginate-with-filter loop returns mostly wrong rows — `?language=Hindi` walked
to completion yields 2096 rows of which only 54 are actually Hindi.

`paginate()` therefore re-applies the filter client-side on every page. Don't
remove that without re-testing:

```bash
python3 heygen.py voices --language Hindi --limit 0 --json \
  | python3 -c "import json,sys,collections;print(collections.Counter(v['language'] for v in json.load(sys.stdin)))"
# expect: Counter({'Hindi': 54})
```

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `Could not reach ... 403 Forbidden` on the tunnel | Host blocked by the egress policy. See [Network access](#network-access). |
| `HTTP 401` | Key missing or invalid. Check `HEYGEN_API_KEY`, or that the environment credential is saved. |
| `HTTP 404` on `status` | Unknown video id, or it belongs to another account. |
| `HTTP 422` | Bad parameters — most often `avatar_id` set to a *group* id instead of a *look* id. |
| Generation succeeds, download 403s | `*.heygen.ai` missing from the allowlist. |
| Balance is 0 | Top up at [app.heygen.com](https://app.heygen.com/home?nav=API). |
