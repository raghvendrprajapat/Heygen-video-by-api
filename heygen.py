#!/usr/bin/env python3
"""HeyGen v3 API client: browse avatar looks and voices, check the wallet,
generate a video, poll it, and download the MP4.

Stdlib only -- no pip install required.

Targets the v3 API. The older v2 endpoints (/v2/avatars, /v2/video/generate,
/v1/video_status.get) are legacy and HeyGen removes them on 2026-10-31.

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

# v3 endpoints. Everything except CREATE_VIDEO is free to call.
EP_LOOKS = "/v3/avatars/looks"
EP_AVATAR_GROUPS = "/v3/avatars"
EP_VOICES = "/v3/voices"
EP_ME = "/v3/users/me"
EP_VIDEOS = "/v3/videos"

CONFIRM_FLAG = "--i-understand-this-spends-credits"

DONE_STATES = {"completed"}
FAIL_STATES = {"failed"}

# Page size ceilings differ per endpoint (looks 50, voices 100).
MAX_PAGE = {EP_LOOKS: 50, EP_VOICES: 100}


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
        clean = {k: v for k, v in params.items() if v is not None}
        if clean:
            url += "?" + urllib.parse.urlencode(clean)

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

    # Surface a sunset notice if we ever hit a legacy path.
    warning = payload.get("warning")
    if isinstance(warning, dict) and warning.get("message"):
        print(f"  [API warning] {warning['message']}", file=sys.stderr)

    err = payload.get("error")
    if err:
        raise HeyGenError(f"API returned an error for {path}: {json.dumps(err)}")
    return payload


def paginate(path, params=None, max_items=0, enforce=None):
    """Walk a v3 cursor-paginated collection and return the rows.

    v3 list endpoints return {"data": [...], "has_more": bool, "next_token": str}
    and take the previous `next_token` back as the `token` query parameter.

    `enforce` maps response field -> expected value, and is re-checked on the
    client. This is not belt-and-braces: as of 2026-08 the API applies query
    filters to the first page only, and silently drops them once you follow a
    `next_token`. Filtering `language=Hindi` without this returns 54 Hindi
    voices followed by ~2000 unrelated ones. Verify before removing.
    """
    params = dict(params or {})
    params.setdefault("limit", MAX_PAGE.get(path, 50))
    enforce = {k: v for k, v in (enforce or {}).items() if v is not None}
    collected = []

    while True:
        payload = request("GET", path, params=params)
        rows = payload.get("data") or []
        if not isinstance(rows, list):
            raise HeyGenError(f"Unexpected response shape from {path}")

        for row in rows:
            if all(
                str(row.get(field, "")).lower() == str(want).lower()
                for field, want in enforce.items()
            ):
                collected.append(row)

        if max_items and len(collected) >= max_items:
            return collected[:max_items]
        if not payload.get("has_more") or not payload.get("next_token"):
            return collected
        params["token"] = payload["next_token"]


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
        404: "Not found. For `status`, this usually means the video id is unknown "
             "or belongs to another account.",
        422: "HeyGen rejected the parameters. Check avatar_id (must be a LOOK id), "
             "voice_id, resolution and aspect_ratio.",
        429: "Rate limited. Slow down and retry.",
    }
    hint = hints.get(exc.code, "")
    parts = [f"HTTP {exc.code} from {url}"]
    if hint:
        parts.append(hint)
    if detail:
        parts.append(f"Response: {detail}")
    return "\n".join(parts)


def print_table(rows, columns):
    """Render rows as a plain aligned table."""
    if not rows:
        print("(no results)")
        return

    headers = [label for label, _ in columns]
    table = []
    for row in rows:
        cells = []
        for _, field in columns:
            value = row.get(field, "")
            if isinstance(value, list):
                value = ",".join(str(v) for v in value)
            # Some catalogue entries carry stray newlines/padding in `name`,
            # which would otherwise break the column alignment.
            cells.append(" ".join(str(value if value is not None else "").split()))
        table.append(cells)

    widths = [len(h) for h in headers]
    for line in table:
        widths = [max(w, len(cell)) for w, cell in zip(widths, line)]
    widths = [min(w, 40) for w in widths]

    def fmt(cells):
        return "  ".join(c[:w].ljust(w) for c, w in zip(cells, widths))

    print(fmt(headers))
    print("  ".join("-" * w for w in widths))
    for line in table:
        print(fmt(line))


def cmd_avatars(args):
    """List avatar LOOKS -- the id here is what `generate --avatar-id` wants."""
    params = {
        "avatar_type": args.avatar_type,
        "ownership": args.ownership,
        "group_id": args.group_id,
    }
    rows = paginate(
        EP_LOOKS,
        params,
        max_items=args.limit,
        enforce={"avatar_type": args.avatar_type, "group_id": args.group_id},
    )

    if args.filter:
        needle = args.filter.lower()
        rows = [r for r in rows if needle in json.dumps(r).lower()]

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    print(f"Avatar looks: {len(rows)}\n")
    print_table(
        rows,
        [
            ("AVATAR_ID (look id)", "id"),
            ("NAME", "name"),
            ("GENDER", "gender"),
            ("TYPE", "avatar_type"),
            ("DEFAULT_VOICE_ID", "default_voice_id"),
        ],
    )
    print("\nPass the AVATAR_ID column to `generate --avatar-id`.")
    return 0


def cmd_voices(args):
    params = {
        "language": args.language,
        "gender": args.gender,
        "type": args.type,
        "engine": args.engine,
    }
    rows = paginate(
        EP_VOICES,
        params,
        max_items=args.limit,
        enforce={
            "language": args.language,
            "gender": args.gender,
            "type": args.type,
        },
    )

    if args.filter:
        needle = args.filter.lower()
        rows = [r for r in rows if needle in json.dumps(r).lower()]

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    print(f"Voices: {len(rows)}\n")
    print_table(
        rows,
        [
            ("VOICE_ID", "voice_id"),
            ("NAME", "name"),
            ("LANGUAGE", "language"),
            ("GENDER", "gender"),
            ("PAUSE", "support_pause"),
        ],
    )
    return 0


def cmd_credits(args):
    payload = request("GET", EP_ME)
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    data = payload.get("data") or {}
    wallet = data.get("wallet") or {}
    balance = wallet.get("remaining_balance")
    currency = str(wallet.get("currency", "")).upper()

    print(f"Account:      {data.get('email', '(unknown)')}")
    print(f"Billing type: {data.get('billing_type', '(unknown)')}")
    print(f"Balance:      {balance} {currency}")

    auto = wallet.get("auto_reload") or {}
    print(f"Auto-reload:  {'on' if auto.get('enabled') else 'off'}")

    if isinstance(balance, (int, float)) and balance <= 0:
        print("\nWARNING: balance is empty -- generate calls will fail.")
    return 0


def cmd_status(args):
    payload = request("GET", f"{EP_VIDEOS}/{args.video_id}")
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    data = payload.get("data") or {}
    print(f"video id: {args.video_id}")
    print(f"status:   {data.get('status')}")
    for field in ("video_url", "captioned_video_url", "subtitle_url",
                  "thumbnail_url", "duration", "failure_message"):
        if data.get(field):
            print(f"{field}: {data[field]}")
    return 0


def build_payload(args, script_text):
    """Assemble the POST /v3/videos request body."""
    payload = {
        "type": "avatar",
        "avatar_id": args.avatar_id,
        "script": script_text,
        "voice_id": args.voice_id,
        "resolution": args.resolution,
        "aspect_ratio": args.aspect_ratio,
    }
    if args.title:
        payload["title"] = args.title
    if args.engine:
        payload["engine"] = {"type": args.engine}
    if args.output_format != "mp4":
        payload["output_format"] = args.output_format
    if args.motion_prompt:
        payload["motion_prompt"] = args.motion_prompt
    if args.remove_background:
        payload["remove_background"] = True
    if args.background_color:
        payload["background"] = {"type": "color", "value": args.background_color}

    # voice_settings is only sent when something actually differs from default,
    # since an empty object is rejected.
    voice_settings = {}
    if args.speed != 1.0:
        voice_settings["speed"] = args.speed
    if args.pitch:
        voice_settings["pitch"] = args.pitch
    if args.locale:
        voice_settings["locale"] = args.locale
    if voice_settings:
        payload["voice_settings"] = voice_settings

    # expressiveness is Avatar IV only; sending it with avatar_v is a hard error.
    if args.expressiveness:
        if args.engine == "avatar_v":
            raise HeyGenError(
                "--expressiveness is not supported by the avatar_v engine and will "
                "fail validation. Drop it, or use --engine avatar_iv."
            )
        payload["expressiveness"] = args.expressiveness

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
    """Stream the finished video to disk without buffering it in memory."""
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
    """Poll GET /v3/videos/{id} until the video is ready, or give up."""
    deadline = time.time() + timeout
    last = None

    while time.time() < deadline:
        data = request("GET", f"{EP_VIDEOS}/{video_id}").get("data") or {}
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
            raise HeyGenError(
                f"Generation failed: {data.get('failure_message') or data}"
            )

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
        print(f"Would POST to {BASE_URL}{EP_VIDEOS}:\n")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"\nTo actually submit this and spend credits, re-run with {CONFIRM_FLAG}")
        return 0

    print(f"Submitting to {EP_VIDEOS} (this spends credits)...")
    data = request("POST", EP_VIDEOS, body=payload).get("data") or {}
    video_id = data.get("video_id") or data.get("id")
    if not video_id:
        raise HeyGenError(f"No video id in response: {json.dumps(data)}")

    print(f"  video id: {video_id}")
    print(f"Polling every {args.poll_interval}s (timeout {args.timeout}s)...")
    video_url = poll_until_done(video_id, args.poll_interval, args.timeout)

    print(f"Ready. Downloading to {args.out}")
    size = download(video_url, args.out)
    print(f"\nSaved {args.out} ({size // 1024} KiB)")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="heygen.py",
        description="HeyGen v3 API client. Only `generate` spends credits.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
examples:
  python3 heygen.py credits
  python3 heygen.py avatars --limit 20
  python3 heygen.py voices --language Hindi
  python3 heygen.py voices --language English --gender female
  python3 heygen.py status --video-id VIDEO_ID_PLACEHOLDER

  # dry run -- prints the payload, spends nothing:
  python3 heygen.py generate \\
      --script "PLACEHOLDER SCRIPT TEXT" \\
      --avatar-id AVATAR_LOOK_ID_PLACEHOLDER \\
      --voice-id VOICE_ID_PLACEHOLDER

  # for real, once you've picked ids:
  python3 heygen.py generate \\
      --script-file script.txt \\
      --avatar-id AVATAR_LOOK_ID_PLACEHOLDER \\
      --voice-id VOICE_ID_PLACEHOLDER \\
      --resolution 1080p --aspect-ratio auto \\
      --out out/video.mp4 \\
      {CONFIRM_FLAG}
""",
    )
    parser.add_argument("--json", action="store_true", help="print raw JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --json is accepted both before and after the subcommand; argparse keeps
    # them as separate destinations, so the subcommand copy is merged in main().
    def add_json(sub):
        sub.add_argument(
            "--json", action="store_true", dest="json_sub", help="print raw JSON"
        )
        return sub

    avatars = add_json(subparsers.add_parser(
        "avatars", help="list avatar looks; the id is what generate wants (free)"
    ))
    avatars.add_argument("--limit", type=int, default=40, help="0 for all")
    avatars.add_argument(
        "--avatar-type", choices=["studio_avatar", "digital_twin", "photo_avatar"]
    )
    avatars.add_argument("--ownership", choices=["public", "private"])
    avatars.add_argument("--group-id", help="restrict to one character")
    avatars.add_argument("--filter", help="case-insensitive substring match")
    avatars.set_defaults(func=cmd_avatars)

    voices = add_json(subparsers.add_parser("voices", help="browse voices (free)"))
    voices.add_argument("--limit", type=int, default=40, help="0 for all")
    voices.add_argument("--language", help='e.g. Hindi, English')
    voices.add_argument("--gender", choices=["male", "female"])
    voices.add_argument("--type", choices=["public", "private"])
    voices.add_argument("--engine", help='e.g. starfish for TTS-compatible')
    voices.add_argument("--filter", help="case-insensitive substring match")
    voices.set_defaults(func=cmd_voices)

    credits = add_json(subparsers.add_parser("credits", help="show wallet balance (free)"))
    credits.set_defaults(func=cmd_credits)

    status = add_json(subparsers.add_parser("status", help="check one video's status (free)"))
    status.add_argument("--video-id", required=True)
    status.set_defaults(func=cmd_status)

    generate = subparsers.add_parser(
        "generate",
        help="generate a video, poll, and download it (SPENDS CREDITS)",
    )
    script_group = generate.add_mutually_exclusive_group(required=True)
    script_group.add_argument("--script", help="script text the avatar speaks")
    script_group.add_argument("--script-file", help="read the script from a UTF-8 file")

    generate.add_argument("--avatar-id", required=True, help="a LOOK id from `avatars`")
    generate.add_argument("--voice-id", required=True, help="from `voices`")
    generate.add_argument(
        "--resolution", default="1080p", choices=["720p", "1080p", "4k"]
    )
    generate.add_argument(
        "--aspect-ratio", default="auto",
        choices=["auto", "16:9", "9:16", "4:5", "5:4", "1:1"],
    )
    generate.add_argument("--title", default=None, help="display name in HeyGen")
    generate.add_argument(
        "--engine", default=None, choices=["avatar_iii", "avatar_iv", "avatar_v"],
        help="omit for the Avatar IV default; check the look's supported engines",
    )
    generate.add_argument("--speed", type=float, default=1.0, help="0.5-1.5")
    generate.add_argument("--pitch", type=int, default=None, help="-50 to +50")
    generate.add_argument("--locale", default=None, help="e.g. en-US, hi-IN")
    generate.add_argument("--motion-prompt", default=None, help="body/hand motion")
    generate.add_argument(
        "--expressiveness", choices=["low", "medium", "high"],
        help="Avatar IV only; invalid with --engine avatar_v",
    )
    generate.add_argument("--background-color", default=None, help='hex, e.g. "#ffffff"')
    generate.add_argument("--remove-background", action="store_true")
    generate.add_argument("--output-format", default="mp4", choices=["mp4", "webm"])
    generate.add_argument("--out", default="out/video.mp4", help="where to save it")
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
    args.json = args.json or getattr(args, "json_sub", False)
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
