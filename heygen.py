#!/usr/bin/env python3
"""HeyGen API client: list avatars/voices, check quota, generate and download videos.

Stdlib only -- no pip install required.

Auth is dual-mode; see resolve_auth_header() for the details.
"""

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = os.environ.get("HEYGEN_BASE_URL", "https://api.heygen.com")

# Endpoints. Every one of these except GENERATE is free to call.
EP_AVATARS = "/v2/avatars"
EP_VOICES = "/v2/voices"
EP_QUOTA = "/v2/user/remaining_quota"
EP_STATUS = "/v1/video_status.get"
EP_GENERATE = "/v2/video/generate"

CONFIRM_FLAG = "--i-understand-this-spends-credits"

# Terminal states returned by video_status.get. HeyGen has used several
# spellings over time, so compare case-insensitively.
DONE_STATES = {"completed", "complete", "success"}
FAIL_STATES = {"failed", "error"}


class HeyGenError(Exception):
    """An API call failed in a way worth reporting without a traceback."""


def resolve_auth_header():
    """Return the auth headers to send, which may be none at all.

    Two supported modes:

    1. HEYGEN_API_KEY is set -- we send `X-Api-Key` ourselves. Use this when
       running locally.
    2. HEYGEN_API_KEY is unset -- we send no auth header. This is correct
       inside a Claude Code cloud session where the environment holds the key
       as an API credential: the agent proxy injects `X-Api-Key` after the
       request leaves the VM, so the key never touches this process.

    The key is deliberately never accepted as a CLI argument, which would
    leak it into shell history and the process list.
    """
    key = os.environ.get("HEYGEN_API_KEY", "").strip()
    return {"X-Api-Key": key} if key else {}


def _ssl_context():
    """Honour a corporate/proxy CA bundle when one is configured."""
    ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if ca and os.path.exists(ca):
        return ssl.create_default_context(cafile=ca)
    return ssl.create_default_context()


def request(method, path, params=None, body=None, timeout=60):
    """Make one JSON API call and return the decoded response."""
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    headers = {"Accept": "application/json", **resolve_auth_header()}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise HeyGenError(_explain_http_error(exc, url)) from exc
    except urllib.error.URLError as exc:
        raise HeyGenError(
            f"Could not reach {url}: {exc.reason}\n"
            "If this is a 403 CONNECT / tunnel failure, the host is blocked by the\n"
            "network egress policy. See the 'Network access' section of README.md."
        ) from exc

    # HeyGen reports application-level problems in an `error` field even on 200.
    err = payload.get("error")
    if err:
        raise HeyGenError(f"API returned an error for {path}: {json.dumps(err)}")
    return payload


def _explain_http_error(exc, url):
    """Turn an HTTPError into something actionable rather than a traceback."""
    try:
        detail = exc.read().decode("utf-8", "replace")[:800]
    except Exception:
        detail = ""

    hints = {
        401: "The API key is missing or invalid. Check HEYGEN_API_KEY, or the "
             "environment's API credential if you're relying on proxy injection.",
        403: "Authenticated but not permitted. This can also be the network egress "
             "proxy refusing the host -- see README.md.",
        404: "Not found. For `status`, this usually means the video_id is unknown "
             "or belongs to another account.",
        424: "HeyGen rejected the parameters. Check avatar_id, voice_id and dimension.",
        429: "Rate limited. Slow down and retry.",
    }
    hint = hints.get(exc.code, "")
    parts = [f"HTTP {exc.code} from {url}"]
    if hint:
        parts.append(hint)
    if detail:
        parts.append(f"Response: {detail}")
    return "\n".join(parts)


def _rows(payload):
    """Pull the list out of a v2 response, which nests it a couple of ways."""
    data = payload.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("avatars", "voices", "list", "data"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def print_table(rows, columns, limit=None):
    """Render rows as a plain aligned table."""
    if not rows:
        print("(no results)")
        return

    shown = rows[:limit] if limit else rows
    headers = [label for label, _ in columns]
    table = [[str(row.get(field, "") or "") for _, field in columns] for row in shown]

    widths = [len(h) for h in headers]
    for line in table:
        widths = [max(w, len(cell)) for w, cell in zip(widths, line)]
    # Keep any single column from swamping the terminal.
    widths = [min(w, 44) for w in widths]

    def fmt(cells):
        return "  ".join(c[:w].ljust(w) for c, w in zip(cells, widths))

    print(fmt(headers))
    print("  ".join("-" * w for w in widths))
    for line in table:
        print(fmt(line))

    if limit and len(rows) > limit:
        print(f"\n... {len(rows) - limit} more (use --limit 0 to show all)")


def cmd_avatars(args):
    payload = request("GET", EP_AVATARS)
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    rows = _rows(payload)
    if args.filter:
        needle = args.filter.lower()
        rows = [r for r in rows if needle in json.dumps(r).lower()]

    print(f"Avatars: {len(rows)}\n")
    print_table(
        rows,
        [
            ("AVATAR_ID", "avatar_id"),
            ("NAME", "avatar_name"),
            ("GENDER", "gender"),
            ("PREMIUM", "premium"),
        ],
        limit=args.limit or None,
    )
    return 0


def cmd_voices(args):
    payload = request("GET", EP_VOICES)
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    rows = _rows(payload)
    if args.language:
        needle = args.language.lower()
        rows = [r for r in rows if needle in str(r.get("language", "")).lower()]
    if args.filter:
        needle = args.filter.lower()
        rows = [r for r in rows if needle in json.dumps(r).lower()]

    print(f"Voices: {len(rows)}\n")
    print_table(
        rows,
        [
            ("VOICE_ID", "voice_id"),
            ("NAME", "name"),
            ("LANGUAGE", "language"),
            ("GENDER", "gender"),
        ],
        limit=args.limit or None,
    )
    return 0


def cmd_quota(args):
    payload = request("GET", EP_QUOTA)
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    data = payload.get("data") or {}
    remaining = data.get("remaining_quota")
    print(f"Remaining API quota: {remaining}")
    details = data.get("details")
    if isinstance(details, dict):
        for key, value in details.items():
            print(f"  {key}: {value}")
    if isinstance(remaining, (int, float)) and remaining <= 0:
        print("\nWARNING: balance is zero or negative -- generate calls will fail.")
    print(
        "\nNote: this is the API dashboard balance. HeyGen bills web-plan credits "
        "from a separate pool."
    )
    return 0


def cmd_status(args):
    payload = request("GET", EP_STATUS, params={"video_id": args.video_id})
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    data = payload.get("data") or {}
    print(f"video_id: {args.video_id}")
    print(f"status:   {data.get('status')}")
    for field in ("video_url", "thumbnail_url", "duration", "error"):
        if data.get(field):
            print(f"{field}: {data[field]}")
    return 0


def build_payload(args, script_text):
    """Assemble the /v2/video/generate request body."""
    character = {
        "type": "avatar",
        "avatar_id": args.avatar_id,
        "avatar_style": args.avatar_style,
    }
    voice = {
        "type": "text",
        "voice_id": args.voice_id,
        "input_text": script_text,
        "speed": args.speed,
    }

    scene = {"character": character, "voice": voice}
    if args.background_color:
        scene["background"] = {"type": "color", "value": args.background_color}

    payload = {
        "video_inputs": [scene],
        "dimension": {"width": args.width, "height": args.height},
    }
    if args.title:
        payload["title"] = args.title
    if args.callback_url:
        payload["callback_id"] = args.callback_url
    return payload


def read_script(args):
    if args.script_file:
        with open(args.script_file, "r", encoding="utf-8") as handle:
            text = handle.read().strip()
        if not text:
            raise HeyGenError(f"{args.script_file} is empty.")
        return text
    return args.script


def download(url, out_path, timeout=600):
    """Stream the finished MP4 to disk without buffering it in memory."""
    directory = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(directory, exist_ok=True)

    # The download URL is a pre-signed CDN link on *.heygen.ai and carries its
    # own auth, so no API key header here.
    req = urllib.request.Request(url, headers={"Accept": "*/*"})
    tmp_path = out_path + ".part"
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp, \
                open(tmp_path, "wb") as handle:
            total = int(resp.headers.get("Content-Length") or 0)
            written = 0
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                written += len(chunk)
                if total:
                    pct = written * 100 // total
                    print(f"\r  downloading... {pct}% ({written//1024} KiB)", end="", flush=True)
        print()
    except urllib.error.URLError as exc:
        raise HeyGenError(
            f"Download failed from {url}: {exc}\n"
            "If this is a proxy 403, allowlist *.heygen.ai -- the CDN host differs "
            "from the API host."
        ) from exc

    os.replace(tmp_path, out_path)
    return written


def poll_until_done(video_id, interval, timeout):
    """Poll video_status.get until the video is ready, or give up."""
    deadline = time.time() + timeout
    last = None

    while time.time() < deadline:
        data = request("GET", EP_STATUS, params={"video_id": video_id}).get("data") or {}
        status = str(data.get("status", "")).lower()

        if status != last:
            print(f"  status: {status or '(unknown)'}")
            last = status

        if status in DONE_STATES:
            url = data.get("video_url")
            if not url:
                raise HeyGenError(f"Status is '{status}' but no video_url was returned.")
            return url
        if status in FAIL_STATES:
            raise HeyGenError(f"Generation failed: {data.get('error') or data}")

        time.sleep(interval)

    raise HeyGenError(
        f"Timed out after {timeout}s waiting on {video_id}. The job may still finish -- "
        f"check with: python3 heygen.py status --video-id {video_id}"
    )


def cmd_generate(args):
    script_text = read_script(args)
    payload = build_payload(args, script_text)

    if not args.confirm:
        print("DRY RUN -- nothing was sent, no credits spent.\n")
        print(f"Would POST to {BASE_URL}{EP_GENERATE}:\n")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"\nTo actually submit this and spend credits, re-run with {CONFIRM_FLAG}")
        return 0

    print(f"Submitting to {EP_GENERATE} (this spends credits)...")
    data = request("POST", EP_GENERATE, body=payload).get("data") or {}
    video_id = data.get("video_id")
    if not video_id:
        raise HeyGenError(f"No video_id in response: {json.dumps(data)}")

    print(f"  video_id: {video_id}")
    print(f"Polling every {args.poll_interval}s (timeout {args.timeout}s)...")
    video_url = poll_until_done(video_id, args.poll_interval, args.timeout)

    print(f"Ready. Downloading to {args.out}")
    size = download(video_url, args.out)
    print(f"\nSaved {args.out} ({size // 1024} KiB)")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="heygen.py",
        description="HeyGen API client. Only `generate` spends credits.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
examples:
  python3 heygen.py quota
  python3 heygen.py avatars --limit 20
  python3 heygen.py voices --language hindi
  python3 heygen.py status --video-id VIDEO_ID_PLACEHOLDER

  # dry run -- prints the payload, spends nothing:
  python3 heygen.py generate \\
      --script "PLACEHOLDER SCRIPT TEXT" \\
      --avatar-id AVATAR_ID_PLACEHOLDER \\
      --voice-id VOICE_ID_PLACEHOLDER

  # for real, once you've picked ids:
  python3 heygen.py generate \\
      --script-file script.txt \\
      --avatar-id AVATAR_ID_PLACEHOLDER \\
      --voice-id VOICE_ID_PLACEHOLDER \\
      --width 1280 --height 720 \\
      --out out/video.mp4 \\
      {CONFIRM_FLAG}
""",
    )
    parser.add_argument("--json", action="store_true", help="print the raw API response")
    subparsers = parser.add_subparsers(dest="command", required=True)

    avatars = subparsers.add_parser("avatars", help="list available avatars (free)")
    avatars.add_argument("--limit", type=int, default=40, help="rows to show; 0 for all")
    avatars.add_argument("--filter", help="case-insensitive substring match")
    avatars.set_defaults(func=cmd_avatars)

    voices = subparsers.add_parser("voices", help="list available voices (free)")
    voices.add_argument("--limit", type=int, default=40, help="rows to show; 0 for all")
    voices.add_argument("--language", help="filter by language, e.g. hindi, english")
    voices.add_argument("--filter", help="case-insensitive substring match")
    voices.set_defaults(func=cmd_voices)

    quota = subparsers.add_parser("quota", help="show remaining API credits (free)")
    quota.set_defaults(func=cmd_quota)

    status = subparsers.add_parser("status", help="check one video's status (free)")
    status.add_argument("--video-id", required=True)
    status.set_defaults(func=cmd_status)

    generate = subparsers.add_parser(
        "generate",
        help="generate a video, poll, and download the MP4 (SPENDS CREDITS)",
    )
    script_group = generate.add_mutually_exclusive_group(required=True)
    script_group.add_argument("--script", help="script text the avatar speaks")
    script_group.add_argument("--script-file", help="read the script from a UTF-8 file")

    generate.add_argument("--avatar-id", required=True, help="from `heygen.py avatars`")
    generate.add_argument("--voice-id", required=True, help="from `heygen.py voices`")
    generate.add_argument("--width", type=int, default=1280)
    generate.add_argument("--height", type=int, default=720)
    generate.add_argument("--title", default=None, help="video title in HeyGen")
    generate.add_argument("--avatar-style", default="normal")
    generate.add_argument("--speed", type=float, default=1.0, help="voice speed, 0.5-1.5")
    generate.add_argument("--background-color", default=None, help='hex, e.g. "#ffffff"')
    generate.add_argument("--callback-url", default=None)
    generate.add_argument("--out", default="out/video.mp4", help="where to save the MP4")
    generate.add_argument("--poll-interval", type=int, default=10)
    generate.add_argument("--timeout", type=int, default=1800)
    generate.add_argument(
        CONFIRM_FLAG,
        dest="confirm",
        action="store_true",
        help="required to actually submit; without it this is a dry run",
    )
    generate.set_defaults(func=cmd_generate)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except HeyGenError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
