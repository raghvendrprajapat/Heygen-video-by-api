#!/usr/bin/env python3
"""Composite a long-form product walkthrough from three cheap parts.

The finished video has four sections:

  1. intro       avatar full frame, setting up the problem
  2. walkthrough screen recording with the avatar in a corner inset
  3. deep dive   screen recording full frame, narrated by a TTS voiceover
  4. close       avatar full frame, delivering the takeaway

Section 3 is the reason this exists. Avatar video bills at $0.0167/sec but
Starfish TTS bills at $0.000667/sec -- twenty-five times cheaper -- so the long
screen-only stretch is narrated by TTS in the same voice as the avatar, and the
avatar is spent only where a face on camera actually earns it.

Timing for sections 1, 2 and 4 comes from the avatar narration's SRT sidecar,
so cuts land on the sentence that describes them. Section 3 has no SRT, so its
beats are placed proportionally through the text -- close enough at a steady
narration pace, and easy to nudge with --deepdive-beats.

Needs ffmpeg; `pip install imageio-ffmpeg` supplies one if it is not on PATH.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

W, H, FPS = 1920, 1080, 25
GROUND = "0x0d0d14"

# Avatar-narration markers that split the three avatar sections.
DEMO_STARTS_AT = "let me show you what i have been using instead"
CLOSE_STARTS_AT = "here is what i would actually take away"

# From this point on, the recording carries a red "AI placement failed" error
# banner in the toolbar. Spans starting at or after this have that strip cut
# out; earlier spans keep it, because the same strip carries the genuine
# "Reordered screenshots ..." status message we do want on screen.
BANNER_FROM = 185.0
BANNER_TOP, BANNER_BOTTOM = 94, 130  # source y range of the strip

# phrase in the avatar narration -> timestamp in the screen recording
WALKTHROUGH_BEATS = [
    ("this is convertscreen", 18),
    ("drop in your raw app screens", 47),
    ("pick a style and a device frame", 66),
    ("reads your screens and writes the headlines", 95),
    # 110s is where the app actually prints the reorder message: "Reordered
    # screenshots to follow a standard conversion story: Home (Hook) -> Shorts
    # (Engagement) -> Subscriptions (Social) -> Library (Utility) -> CTA."
    ("reorders your screenshots into a conversion story", 110),
    ("every slide gets a job", 120),
    ("from here you can adjust anything", 160),
    ("need another language", 175),
    ("then you export", 330),
    # "five slides" also occurs in the intro, so anchor on a phrase that only
    # appears at the export beat.
    ("four device sizes", 352),
]

# phrase in the deep-dive voiceover -> timestamp in the screen recording
DEEPDIVE_BEATS = [
    ("start with the editor itself", 96),
    ("underneath that is the layout picker", 104),
    ("slide role selector", 112),
    ("each role changes what the ai writes", 120),
    ("the screenshots panel", 132),
    ("status bar control", 140),
    ("highlight cards", 236),
    ("callouts", 262),
    ("device frames", 292),
    ("typography", 300),
    ("backgrounds", 312),
    ("then languages", 150),
    ("finally the export", 326),
    ("review grid", 350),
    ("then you confirm", 372),
]


def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        sys.exit("ffmpeg not found. Install it, or: pip install imageio-ffmpeg")


FFMPEG = None


def run(args):
    proc = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y"] + args,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f"ffmpeg failed:\n{proc.stderr[-2500:]}")


def duration_of(path):
    proc = subprocess.run([FFMPEG, "-hide_banner", "-i", path], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.?\d*)", proc.stderr)
    if not m:
        sys.exit(f"Could not read duration of {path}")
    h, mm, ss = m.groups()
    return int(h) * 3600 + int(mm) * 60 + float(ss)


def normalise(text):
    return re.sub(r"[^a-z0-9 ]", " ", text.lower())


def squash(text):
    return " ".join(normalise(text).split())


def parse_srt(path):
    with open(path, "r", encoding="utf-8-sig") as handle:
        raw = handle.read()
    pattern = re.compile(
        r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)(.*?)(?=\n\s*\n|\Z)", re.S
    )
    cues = []
    for m in pattern.finditer(raw):
        start = (int(m.group(1)) * 3600 + int(m.group(2)) * 60
                 + int(m.group(3)) + int(m.group(4)) / 1000.0)
        cues.append((start, " ".join(m.group(9).split())))
    if not cues:
        sys.exit(f"No cues parsed from {path}")
    return cues


def locate_in_srt(cues, phrase):
    target = squash(phrase)
    for i, (start, _) in enumerate(cues):
        window = squash(" ".join(t for _, t in cues[i : i + 5]))
        if target in window:
            return start
    return None


def locate_in_text(text, phrase, total_seconds):
    """Place a phrase in time by where it sits in the script.

    No SRT exists for TTS output, so assume a steady reading pace and map
    character offset onto the measured duration.
    """
    idx = squash(text).find(squash(phrase))
    if idx < 0:
        return None
    return idx / max(1, len(squash(text))) * total_seconds


def recording_span(recording, start, length, out, zoom=1.06, hide_banner=False):
    """One chunk of the screen recording, fitted to the output frame.

    When hide_banner is set, the toolbar strip carrying the error message is
    removed by stacking the rows above it onto the rows below it, so the banner
    never reaches the cut.
    """
    if hide_banner:
        pre = (
            f"[0:v]crop=iw:{BANNER_TOP}:0:0[top];"
            f"[0:v]crop=iw:ih-{BANNER_BOTTOM}:0:{BANNER_BOTTOM}[bot];"
            f"[top][bot]vstack=inputs=2,"
        )
    else:
        pre = "[0:v]"

    run([
        "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", recording, "-an",
        "-filter_complex",
        pre
        + f"scale={int(W*zoom)}:-2:flags=lanczos,crop='min(iw,{W})':'min(ih,{H})',"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={GROUND},setsar=1,fps={FPS}[v]",
        "-map", "[v]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", out,
    ])


def build_bed(recording, beats, section_start, section_end, work, tag, rec_len):
    """Lay screen-recording chunks end to end to cover one narrated section."""
    inside = sorted((t, at, p) for t, at, p in beats if section_start <= t < section_end)
    if not inside:
        sys.exit(f"No beats landed inside the {tag} section.")

    spans = []
    lead = inside[0][0] - section_start
    if lead > 0.05:
        spans.append((inside[0][1], lead, "(lead-in)"))
    for i, (when, at, phrase) in enumerate(inside):
        end = inside[i + 1][0] if i + 1 < len(inside) else section_end
        spans.append((at, end - when, phrase))

    print(f"\n  {tag} spans:")
    parts = []
    for i, (at, length, phrase) in enumerate(spans):
        if length <= 0.08:
            continue
        start = max(0.0, min(at, rec_len - length - 0.1))
        hide = start >= BANNER_FROM
        print(f"    {length:6.1f}s  from {start:6.1f}s{'  [banner cut]' if hide else '':14} <- {phrase}")
        out = os.path.join(work, f"{tag}{i:02d}.mp4")
        recording_span(recording, start, length, out, hide_banner=hide)
        parts.append(out)

    listing = os.path.join(work, f"{tag}.txt")
    with open(listing, "w") as handle:
        for part in parts:
            handle.write(f"file '{part}'\n")
    bed = os.path.join(work, f"{tag}-bed.mp4")
    run(["-f", "concat", "-safe", "0", "-i", listing, "-c", "copy", bed])
    return bed


def full_frame(src, start, end, out):
    """A slice of the avatar video, fitted to the frame, audio intact.

    Fitted, never cropped. HeyGen renders these avatars near-square (1088x1080),
    so cropping to 16:9 slices the top of the head off. Pad instead and let the
    portrait sit centred on the ground colour.
    """
    args = ["-i", src, "-ss", f"{start:.2f}"]
    if end is not None:
        args += ["-to", f"{end:.2f}"]
    run(args + [
        "-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
               f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={GROUND},setsar=1,fps={FPS}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", out,
    ])


def main():
    global FFMPEG
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--intro-video", required=True, help="avatar clip: problem intro")
    parser.add_argument("--walk-video", required=True, help="avatar clip: guided walkthrough")
    parser.add_argument("--walk-srt", required=True, help="SRT for the walkthrough clip")
    parser.add_argument("--close-video", required=True, help="avatar clip: takeaway")
    parser.add_argument("--recording", required=True, help="product screen recording")
    parser.add_argument("--deepdive-audio", action="append", default=[],
                        help="TTS mp3 for the screen-only section. Repeatable, in order.")
    parser.add_argument("--deepdive-text", action="append", default=[],
                        help="text matching each --deepdive-audio, same order")
    parser.add_argument("--deepdive-beats", help="JSON [[phrase, recording_seconds], ...]")
    parser.add_argument("--out", default="out/demo.mp4")
    parser.add_argument("--inset-scale", type=float, default=0.23)
    parser.add_argument("--margin", type=int, default=44)
    args = parser.parse_args()

    FFMPEG = find_ffmpeg()
    work = tempfile.mkdtemp(prefix="demo-")
    sections = []

    rec_len = duration_of(args.recording)
    intro_len = duration_of(args.intro_video)
    walk_len = duration_of(args.walk_video)
    close_len = duration_of(args.close_video)
    cues = parse_srt(args.walk_srt)

    print(f"screen recording : {rec_len:.1f}s")
    print(f"  intro       {intro_len:.1f}s")
    print(f"  walkthrough {walk_len:.1f}s")
    print(f"  close       {close_len:.1f}s")

    # 1. intro, avatar full frame
    intro = os.path.join(work, "s1.mp4")
    full_frame(args.intro_video, 0, None, intro)
    sections.append(intro)

    # 2. walkthrough, recording with the avatar inset. The walkthrough clip
    # starts at zero, so beats resolve against its own SRT directly.
    demo_start, close_start = 0.0, walk_len
    beats = [(locate_in_srt(cues, p), at, p) for p, at in WALKTHROUGH_BEATS]
    beats = [b for b in beats if b[0] is not None]
    bed = build_bed(args.recording, beats, demo_start, close_start, work, "walk", rec_len)

    inset_w = int(W * args.inset_scale)
    walk = os.path.join(work, "s2.mp4")
    run([
        "-i", args.walk_video, "-i", bed,
        "-filter_complex",
        # Square avatar -> a rounded-feeling portrait card in the corner.
        f"[0:v]scale={inset_w}:-2,setsar=1[pip];"
        f"[1:v]setsar=1,fps={FPS}[bg];"
        f"[bg][pip]overlay=W-w-{args.margin}:H-h-{args.margin}:shortest=1[v];"
        f"[0:a]anull[a]",
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", walk,
    ])
    sections.append(walk)

    # 3. deep dive, recording full frame under the TTS voiceover
    if args.deepdive_audio:
        if len(args.deepdive_text) != len(args.deepdive_audio):
            sys.exit("--deepdive-text must be given once per --deepdive-audio, in order.")

        dd_beats_src = DEEPDIVE_BEATS
        if args.deepdive_beats:
            with open(args.deepdive_beats) as handle:
                dd_beats_src = [tuple(r) for r in json.load(handle)]

        # Concatenate the voiceover parts, tracking where each one begins so
        # phrase offsets resolve against the right part.
        offset, dd_beats, alist = 0.0, [], os.path.join(work, "dd.txt")
        with open(alist, "w") as handle:
            for audio, textfile in zip(args.deepdive_audio, args.deepdive_text):
                seconds = duration_of(audio)
                text = open(textfile, encoding="utf-8").read()
                for phrase, at in dd_beats_src:
                    when = locate_in_text(text, phrase, seconds)
                    if when is not None:
                        dd_beats.append((offset + when, at, phrase))
                handle.write(f"file '{os.path.abspath(audio)}'\n")
                offset += seconds

        dd_audio = os.path.join(work, "dd.m4a")
        run(["-f", "concat", "-safe", "0", "-i", alist,
             "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", dd_audio])
        dd_len = offset
        print(f"\ndeep-dive voiceover : {dd_len:.1f}s")

        dd_bed = build_bed(args.recording, dd_beats, 0, dd_len, work, "deep", rec_len)
        deep = os.path.join(work, "s3.mp4")
        run([
            "-i", dd_bed, "-i", dd_audio,
            "-map", "0:v", "-map", "1:a", "-shortest",
            "-vf", f"setsar=1,fps={FPS}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", deep,
        ])
        sections.append(deep)

    # 4. close, avatar full frame
    close = os.path.join(work, "s4.mp4")
    full_frame(args.close_video, 0, None, close)
    sections.append(close)

    listing = os.path.join(work, "final.txt")
    with open(listing, "w") as handle:
        for section in sections:
            handle.write(f"file '{section}'\n")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    print("\nstitching sections...")
    run(["-f", "concat", "-safe", "0", "-i", listing,
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", args.out])

    total = duration_of(args.out)
    shutil.rmtree(work, ignore_errors=True)
    print(f"\nWrote {args.out}  {total:.1f}s ({total/60:.2f} min), "
          f"{os.path.getsize(args.out)/1e6:.1f} MB")


if __name__ == "__main__":
    main()
