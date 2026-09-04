#!/usr/bin/env python
"""Build the submission video: a PDF deck + a narration track -> one MP4.

Slide boundaries are placed at an even split of the narration, then snapped to
the nearest natural pause so cuts land on a breath instead of mid-word.

    python scripts/build_pitch_video.py DECK.pdf NARRATION.mp3 OUT.mp4

Requires poppler's pdftoppm on PATH and ffmpeg (pip install imageio-ffmpeg
supplies a static build; this script finds it automatically).
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

# Cuts land on a pause only if one sits within this fraction of a slide's length
# of the ideal even-split target; otherwise the target itself is used.
SNAP_WINDOW_FRACTION = 0.40
# A pause this much longer than another is preferred even if it sits further out.
PAUSE_BIAS = 0.15
MIN_SLIDE_SECONDS = 2.5
# Held on the last slide after the voice stops, so the video does not cut on the
# final word.
TAIL_SECONDS = 1.2

SILENCE_NOISE_DB = -35
SILENCE_MIN_SECONDS = 0.25


def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        sys.exit("ffmpeg not found. Install it, or: pip install imageio-ffmpeg")


def run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    return proc.stdout + proc.stderr


def audio_duration(ffmpeg: str, path: str) -> float:
    out = run([ffmpeg, "-hide_banner", "-i", path])
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.?\d*)", out)
    if not m:
        sys.exit(f"could not read duration of {path}")
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


def detect_pauses(ffmpeg: str, path: str) -> list[tuple[float, float]]:
    """Return (midpoint, duration) for every detected silence."""
    out = run(
        [
            ffmpeg, "-hide_banner", "-i", path,
            "-af", f"silencedetect=noise={SILENCE_NOISE_DB}dB:d={SILENCE_MIN_SECONDS}",
            "-f", "null", "-",
        ]
    )
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", out)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", out)]
    return [((s + e) / 2, e - s) for s, e in zip(starts, ends)]


def slide_boundaries(duration: float, n_slides: int, pauses: list[tuple[float, float]]) -> list[float]:
    """Even split, each cut snapped to the best nearby pause."""
    slot = duration / n_slides
    window = slot * SNAP_WINDOW_FRACTION
    cuts: list[float] = []
    previous = 0.0
    for k in range(1, n_slides):
        target = k * slot
        nearby = [(mid, dur) for mid, dur in pauses if abs(mid - target) <= window]
        if nearby:
            mid, _ = max(nearby, key=lambda p: p[1] - PAUSE_BIAS * abs(p[0] - target))
            cut = mid
        else:
            cut = target
        # Never let a snap collapse a slide or run backwards.
        cut = max(cut, previous + MIN_SLIDE_SECONDS)
        remaining_slides = n_slides - k
        cut = min(cut, duration - MIN_SLIDE_SECONDS * remaining_slides)
        cuts.append(cut)
        previous = cut
    return cuts


def render_slides(pdf: str, outdir: str) -> list[str]:
    if not shutil.which("pdftoppm"):
        sys.exit("pdftoppm not found (install poppler-utils)")
    subprocess.run(
        [
            "pdftoppm", "-png", "-r", "150",
            "-scale-to-x", "1920", "-scale-to-y", "1080",
            pdf, os.path.join(outdir, "slide"),
        ],
        check=True,
    )
    slides = sorted(glob.glob(os.path.join(outdir, "slide*.png")))
    if not slides:
        sys.exit("pdftoppm produced no pages")
    return slides


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("audio")
    ap.add_argument("out")
    ap.add_argument("--crf", default="20", help="x264 quality, lower is better")
    args = ap.parse_args()

    ffmpeg = find_ffmpeg()
    workdir = tempfile.mkdtemp(prefix="pitchvideo-")
    try:
        slides = render_slides(args.pdf, workdir)
        duration = audio_duration(ffmpeg, args.audio)
        pauses = detect_pauses(ffmpeg, args.audio)
        cuts = slide_boundaries(duration, len(slides), pauses)

        edges = [0.0] + cuts + [duration + TAIL_SECONDS]
        durations = [edges[i + 1] - edges[i] for i in range(len(slides))]

        print(f"{len(slides)} slides over {duration:.2f}s of narration "
              f"({len(pauses)} pauses detected)")
        for i, (start, dur) in enumerate(zip(edges, durations), start=1):
            print(f"  slide {i:2d}  {start:6.2f}s  ->  {start + dur:6.2f}s   ({dur:5.2f}s)")

        listfile = os.path.join(workdir, "concat.txt")
        with open(listfile, "w", encoding="utf-8") as fh:
            for slide, dur in zip(slides, durations):
                fh.write(f"file '{slide.replace(os.sep, '/')}'\n")
                fh.write(f"duration {dur:.3f}\n")
            # The concat demuxer drops the final entry's duration unless the
            # last file is repeated without one.
            fh.write(f"file '{slides[-1].replace(os.sep, '/')}'\n")

        cmd = [
            ffmpeg, "-y", "-hide_banner",
            "-f", "concat", "-safe", "0", "-i", listfile,
            "-i", args.audio,
            "-filter_complex", f"[1:a]apad=pad_dur={TAIL_SECONDS}[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "libx264", "-preset", "medium", "-crf", args.crf,
            "-pix_fmt", "yuv420p", "-r", "30",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            "-shortest",
            args.out,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
        if proc.returncode != 0:
            sys.exit(proc.stdout + proc.stderr)

        size_mb = os.path.getsize(args.out) / 1_000_000
        print(f"\nwrote {args.out}  ({size_mb:.1f} MB, {duration + TAIL_SECONDS:.1f}s)")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
