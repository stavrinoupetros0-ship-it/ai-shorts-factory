
import os
import json
import re
import time
import base64
import asyncio
import subprocess
from pathlib import Path

import requests
import edge_tts
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.getenv("OUTPUT_DIR", "output"))
OUT.mkdir(exist_ok=True)

COUNT = int(os.getenv("VIDEOS_PER_RUN", "5"))
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
CF_ID = os.environ["CF_ACCOUNT_ID"]
CF_TOKEN = os.environ["CF_API_TOKEN"]

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
IMAGE_MODEL = "@cf/black-forest-labs/flux-1-schnell"


def generate_scripts():
    topics = json.loads(
        (ROOT / "config/topics.json").read_text()
    )

    prompt = f"""
Create exactly {COUNT} original YouTube Shorts scripts.

Topics: {topics['categories']}

Requirements:
- English language.
- 90-115 spoken words per story.
- Strong opening hook.
- Engaging storytelling.
- Accurate, verifiable facts.
- No invented statistics or fake events.
- Include credible source URLs.
- Each story must be different.

Return ONLY a valid JSON array.
Each object must contain:
title, category, narration, image_prompt,
description, hashtags, source_urls.

Image prompts must describe cinematic vertical
photography without text, logos or watermarks.
"""

    url = (
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{GEMINI_MODEL}:generateContent"
    )

    response = requests.post(
        url,
        params={"key": GEMINI_KEY},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.8,
                "responseMimeType": "application/json"
            }
        },
        timeout=120
    )
    response.raise_for_status()

    raw = response.json()["candidates"][0]["content"]["parts"][0]["text"]
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.I | re.M)

    scripts = json.loads(raw)

    if not isinstance(scripts, list) or len(scripts) < COUNT:
        raise RuntimeError("Not enough scripts returned by Gemini.")

    return scripts[:COUNT]


def generate_image(prompt, destination):
    url = (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{CF_ID}/ai/run/{IMAGE_MODEL}"
    )

    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {CF_TOKEN}",
            "Content-Type": "application/json"
        },
        json={
            "prompt": (
                prompt
                + ", vertical 9:16 cinematic composition, "
                "photorealistic, dramatic lighting, no text, no watermark"
            )
        },
        timeout=180
    )
    response.raise_for_status()

    content_type = response.headers.get("content-type", "")

    if "image" in content_type:
        destination.write_bytes(response.content)
    else:
        result = response.json().get("result", {})
        image_data = result.get("image")

        if not image_data:
            raise RuntimeError("Cloudflare returned no image.")

        destination.write_bytes(base64.b64decode(image_data))

    with Image.open(destination) as image:
        image.verify()


async def generate_voice(text, destination):
    voice = edge_tts.Communicate(
        text,
        voice="en-US-AriaNeural",
        rate="+3%",
        pitch="-1Hz"
    )
    await voice.save(str(destination))


def timestamp(seconds):
    milliseconds = int((seconds % 1) * 1000)
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)

    return (
        f"{hours:02}:{minutes:02}:"
        f"{secs:02},{milliseconds:03}"
    )


def generate_subtitles(text, duration, destination):
    words = text.split()
    chunks = [
        " ".join(words[i:i + 6])
        for i in range(0, len(words), 6)
    ]

    lines = []

    for index, chunk in enumerate(chunks):
        start = duration * index / len(chunks)
        end = duration * (index + 1) / len(chunks)

        lines.extend([
            str(index + 1),
            f"{timestamp(start)} --> {timestamp(end)}",
            chunk,
            ""
        ])

    destination.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )


def render_video(image, audio, subtitles, destination):
    result = subprocess.check_output([
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio)
    ], text=True)

    duration = float(result.strip())

    subtitle_path = str(subtitles).replace(":", r"\:")

    video_filter = (
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        "zoompan=z='min(zoom+0.0007,1.12)':d=1:"
        "x='iw/2-(iw/zoom/2)':"
        "y='ih/2-(ih/zoom/2)':"
        "s=1080x1920:fps=30,"
        f"subtitles='{subtitle_path}':"
        "force_style='FontName=DejaVu Sans,"
        "FontSize=18,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,"
        "Outline=3,Shadow=1,Alignment=2,MarginV=150'"
    )

    command = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-framerate", "30",
        "-i", str(image),
        "-i", str(audio),
        "-t", str(duration),
        "-vf", video_filter,
        "-r", "30",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(destination)
    ]

    subprocess.run(
        command,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE
    )


def safe_filename(title):
    return (
        re.sub(r"[^a-zA-Z0-9_-]+", "_", title)
        .strip("_")[:45]
        or "short"
    )


def main():
    scripts = generate_scripts()
    manifest = []

    for index, story in enumerate(scripts, 1):
        title = str(story.get("title", f"Story {index}"))[:95]
        narration = str(story.get("narration", "")).strip()

        if len(narration.split()) < 55:
            print(f"Skipping short script: {title}")
            continue

        filename = f"{index:02d}_{safe_filename(title)}"

        image = OUT / f"{filename}.png"
        audio = OUT / f"{filename}.mp3"
        subtitles = OUT / f"{filename}.srt"
        video = OUT / f"{filename}.mp4"

        print(f"Generating story {index}: {title}")

        generate_image(
            str(story.get("image_prompt", title)),
            image
        )

        asyncio.run(generate_voice(narration, audio))

        probe = subprocess.check_output([
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio)
        ], text=True)

        duration = float(probe.strip())

        generate_subtitles(
            narration,
            duration,
            subtitles
        )

        render_video(
            image,
            audio,
            subtitles,
            video
        )

        description = (
            str(story.get("description", ""))
            + "\n\nSources:\n"
            + "\n".join(story.get("source_urls", []))
            + "\n\n#Shorts "
            + " ".join(story.get("hashtags", [])[:5])
        )

        manifest.append({
            "title": title,
            "description": description[:4900],
            "video": str(video),
            "category": story.get("category", "Education")
        })

        print(f"Finished: {title}")
        time.sleep(2)

    (OUT / "manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8"
    )

    print(f"Successfully rendered {len(manifest)} videos.")


if __name__ == "__main__":
    main()
