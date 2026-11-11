
#!/usr/bin/env python3
"""
AI Shorts Factory - Gaming Clipper
Creates vertical gaming clips for authorized Content Rewards campaigns.
"""

import argparse
import json
import re
import subprocess
from pathlib import Path

from PIL import Image


def safe_name(value):
    return re.sub(
        r"[^a-zA-Z0-9_-]+", "_", value
    ).strip("_")[:60] or "gaming_clip"


def run_ffmpeg(command):
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(
            "FFmpeg error:\n" + result.stderr[-3000:]
        )


def get_duration(video):
    result = subprocess.check_output([
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video)
    ], text=True)

    return float(result.strip())


def make_srt(hook, duration, destination):
    if not hook.strip():
        destination.write_text("", encoding="utf-8")
        return

    def timestamp(seconds):
        total_ms = int(seconds * 1000)
        hours, remainder = divmod(total_ms, 3600000)
        minutes, remainder = divmod(remainder, 60000)
        seconds, milliseconds = divmod(remainder, 1000)

        return (
            f"{hours:02}:{minutes:02}:"
            f"{seconds:02},{milliseconds:03}"
        )

    safe_hook = hook.replace("-->", "→")

    destination.write_text(
        "1\n"
        f"{timestamp(0)} --> {timestamp(min(3, duration))}\n"
        f"{safe_hook}\n",
        encoding="utf-8"
    )


def create_clip(
    source,
    destination,
    start,
    duration,
    hook
):
    subtitle_file = destination.with_suffix(".srt")

    make_srt(hook, duration, subtitle_file)

    filters = (
        "scale=1080:1920:"
        "force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        "setsar=1,"
        "fps=30"
    )

    if hook.strip():
        subtitle_path = (
            str(subtitle_file.resolve())
            .replace("\\", "/")
            .replace(":", r"\:")
            .replace("'", r"\'")
        )

        filters += (
            ",subtitles='"
            + subtitle_path
            + "':force_style='"
            "FontName=DejaVu Sans,"
            "FontSize=24,"
            "Bold=1,"
            "PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00000000,"
            "Outline=3,"
            "Alignment=8,"
            "MarginV=180'"
        )

    command = [
        "ffmpeg",
        "-y",
        "-ss", str(start),
        "-i", str(source),
        "-t", str(duration),
        "-vf", filters,
        "-map", "0:v:0",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "21",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-movflags", "+faststart",
        "-avoid_negative_ts", "make_zero",
        str(destination)
    ]

    run_ffmpeg(command)

    if not destination.exists() or destination.stat().st_size < 1024:
        raise RuntimeError("Output video is missing or too small.")

    with Image.open(destination) if False else open(destination, "rb") as f:
        if not f.read(12).startswith(b"\x00\x00\x00"):
            raise RuntimeError("Unexpected MP4 output.")

    print(f"Created: {destination}")


def main():
    parser = argparse.ArgumentParser(
        description="Create vertical gaming clips."
    )

    parser.add_argument("--input", required=True)
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--hook", default="")
    parser.add_argument("--clip-seconds", type=int, default=30)
    parser.add_argument("--clips", type=int, default=3)
    parser.add_argument("--start-seconds", type=float, default=0)
    parser.add_argument("--output-dir", default="output/gaming")

    args = parser.parse_args()

    source = Path(args.input)
    output = Path(args.output_dir)

    if not source.exists():
        raise SystemExit(f"Input video not found: {source}")

    if source.suffix.lower() not in (
        ".mp4", ".mov", ".mkv", ".webm"
    ):
        raise SystemExit("Unsupported video format.")

    if not 10 <= args.clip_seconds <= 60:
        raise SystemExit("Clip duration must be between 10 and 60 seconds.")

    if not 1 <= args.clips <= 10:
        raise SystemExit("Choose between 1 and 10 clips.")

    if args.start_seconds < 0:
        raise SystemExit("Start time cannot be negative.")

    output.mkdir(parents=True, exist_ok=True)

    duration = get_duration(source)

    if args.start_seconds >= duration:
        raise SystemExit("Starting position exceeds video duration.")

    campaign = safe_name(args.campaign)
    results = []

    print("AI SHORTS FACTORY - GAMING CLIPPER")
    print(f"Campaign: {args.campaign}")
    print(f"Source: {source}")
    print(f"Source duration: {duration:.2f} seconds")

    for index in range(args.clips):
        start = args.start_seconds + index * args.clip_seconds

        if start >= duration:
            print(f"Skipping clip {index + 1}: no footage remaining.")
            continue

        clip_duration = min(
            args.clip_seconds,
            duration - start
        )

        filename = (
            f"{campaign}_{index + 1:02d}.mp4"
        )

        destination = output / filename

        try:
            create_clip(
                source,
                destination,
                start,
                clip_duration,
                args.hook
            )

            results.append({
                "campaign": args.campaign,
                "source_file": str(source),
                "clip_file": str(destination),
                "start_seconds": start,
                "duration_seconds": clip_duration,
                "hook": args.hook,
                "platforms": ["TikTok", "Instagram Reels"],
                "status": "ready_for_review",
                "published_url": None,
                "approved_views": 0,
                "earnings": 0
            })

        except Exception as error:
            print(f"Clip {index + 1} failed: {error}")

    manifest = output / f"{campaign}_manifest.json"

    manifest.write_text(
        json.dumps(results, indent=2),
        encoding="utf-8"
    )

    print("\nPROCESSING COMPLETE")
    print(f"Successful clips: {len(results)}")
    print(f"Manifest: {manifest}")

    if not results:
        raise SystemExit("No clips were successfully generated.")


if __name__ == "__main__":
    main()
