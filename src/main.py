
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
if not OUT.is_absolute():
    OUT = ROOT / OUT

OUT.mkdir(parents=True, exist_ok=True)

COUNT = int(os.getenv("VIDEOS_PER_RUN", "5"))

GEMINI_KEY = os.environ["GEMINI_API_KEY"]
CF_ID = os.environ["CF_ACCOUNT_ID"]
CF_TOKEN = os.environ["CF_API_TOKEN"]

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.8-flash"
)

IMAGE_MODEL = "@cf/black-forest-labs/flux-1-schnell"


# -----------------------------
# HTTP REQUEST RETRIES
# -----------------------------

def post_with_retries(
    url,
    headers=None,
    params=None,
    payload=None,
    timeout=(20, 120),
    attempts=3,
    label="API"
):
    retryable = (429, 500, 502, 503, 504)

    for attempt in range(1, attempts + 1):
        try:
            response = requests.post(
                url,
                headers=headers,
                params=params,
                json=payload,
                timeout=timeout
            )

            if response.status_code in retryable:
                if attempt == attempts:
                    response.raise_for_status()

                wait = min(10 * (2 ** (attempt - 1)), 40)

                print(
                    f"{label}: HTTP {response.status_code}. "
                    f"Retry {attempt}/{attempts} in {wait}s."
                )

                time.sleep(wait)
                continue

            response.raise_for_status()
            return response

        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError
        ) as error:

            if attempt == attempts:
                raise RuntimeError(
                    f"{label} failed after {attempts} attempts: {error}"
                ) from error

            wait = min(10 * (2 ** (attempt - 1)), 40)

            print(
                f"{label} connection failed: {error}"
            )
            print(f"Retrying in {wait} seconds...")

            time.sleep(wait)

    raise RuntimeError(f"{label} failed.")


# -----------------------------
# GEMINI STORY GENERATION
# -----------------------------

def generate_scripts():

    topics = json.loads(
        (ROOT / "config/topics.json").read_text(
            encoding="utf-8"
        )
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
- Avoid repeating previous stories.

Return ONLY a valid JSON array.

Each object must contain:

title
category
narration
image_prompt
description
hashtags
source_urls

Image prompts must describe cinematic vertical
photography without text, logos or watermarks.
"""

    url = (
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{GEMINI_MODEL}:generateContent"
    )

    print(f"Connecting to Gemini: {GEMINI_MODEL}")
    print(f"Requesting {COUNT} stories...")

    response = post_with_retries(
        url=url,
        headers={
            "x-goog-api-key": GEMINI_KEY,
            "Content-Type": "application/json"
        },
        payload={
            "contents": [
                {
                    "parts": [
                        {"text": prompt}
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 8192,
                "responseMimeType": "application/json",
                "thinkingConfig": {
                    "thinkingLevel": "low"
                }
            }
        },
        timeout=(20, 120),
        attempts=3,
        label="Gemini"
    )

    data = response.json()

    candidates = data.get("candidates", [])

    if not candidates:
        raise RuntimeError(
            f"Gemini returned no candidates: {data}"
        )

    parts = candidates[0].get(
        "content", {}
    ).get("parts", [])

    raw = "".join(
        part.get("text", "")
        for part in parts
    ).strip()

    raw = re.sub(
        r"^```(?:json)?\s*",
        "",
        raw
    )

    raw = re.sub(
        r"\s*```$",
        "",
        raw
    )

    try:
        scripts = json.loads(raw)

    except json.JSONDecodeError as error:
        print("Gemini returned invalid JSON:")
        print(raw[:2000])

        raise RuntimeError(
            "Could not decode Gemini response."
        ) from error

    if not isinstance(scripts, list):
        raise RuntimeError(
            "Gemini response was not a JSON list."
        )

    scripts = [
        item for item in scripts
        if isinstance(item, dict)
    ]

    if not scripts:
        raise RuntimeError(
            "Gemini returned no usable stories."
        )

    if len(scripts) < COUNT:
        print(
            f"Warning: requested {COUNT} stories, "
            f"received {len(scripts)}."
        )

    print(
        f"Gemini successfully generated "
        f"{len(scripts)} stories."
    )

    return scripts[:COUNT]


# -----------------------------
# CLOUDFLARE IMAGE GENERATION
# -----------------------------

def generate_image(prompt, destination):

    url = (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{CF_ID}/ai/run/{IMAGE_MODEL}"
    )

    response = post_with_retries(
        url=url,
        headers={
            "Authorization": f"Bearer {CF_TOKEN}",
            "Content-Type": "application/json"
        },
        payload={
            "prompt": (
                prompt
                + ", vertical 9:16 cinematic composition, "
                "photorealistic, dramatic lighting, "
                "no text, no watermark"
            )
        },
        timeout=(20, 180),
        attempts=3,
        label="Cloudflare image generation"
    )

    content_type = response.headers.get(
        "content-type", ""
    )

    if "image" in content_type:
        destination.write_bytes(response.content)

    else:
        result = response.json().get("result", {})
        image_data = result.get("image")

        if not image_data:
            raise RuntimeError(
                "Cloudflare returned no image."
            )

        destination.write_bytes(
            base64.b64decode(image_data)
        )

    with Image.open(destination) as image:
        image.verify()

    print(f"Image saved: {destination.name}")


# -----------------------------
# VOICE GENERATION
# -----------------------------

async def generate_voice(text, destination):

    for attempt in range(3):

        try:
            voice = edge_tts.Communicate(
                text,
                voice="en-US-AriaNeural",
                rate="+3%",
                pitch="-1Hz"
            )

            await voice.save(str(destination))
            return

        except Exception as error:

            if attempt == 2:
                raise

            wait = 5 * (attempt + 1)

            print(
                f"Voice generation failed: {error}"
            )

            await asyncio.sleep(wait)


# -----------------------------
# VIDEO UTILITIES
# -----------------------------

def timestamp(seconds):

    milliseconds = int(
        (seconds % 1) * 1000
    )

    total = int(seconds)

    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)

    return (
        f"{hours:02}:"
        f"{minutes:02}:"
        f"{secs:02},"
        f"{milliseconds:03}"
    )


def generate_subtitles(
    text,
    duration,
    destination
):

    words = text.split()

    chunks = [
        " ".join(words[i:i + 6])
        for i in range(0, len(words), 6)
    ]

    if not chunks:
        raise RuntimeError(
            "No subtitle text available."
        )

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


def render_video(
    image,
    audio,
    subtitles,
    destination
):

    result = subprocess.check_output([
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio)
    ], text=True)

    duration = float(result.strip())

    subtitle_path = str(
        subtitles.resolve()
    ).replace("\\", "/").replace(":", r"\:")

    video_filter = (
        "scale=1080:1920:"
        "force_original_aspect_ratio=increase,"
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

    result = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg failed:\n{result.stderr[-3000:]}"
        )


# -----------------------------
# FILE UTILITIES
# -----------------------------

def safe_filename(title):

    return (
        re.sub(
            r"[^a-zA-Z0-9_-]+",
            "_",
            title
        ).strip("_")[:45]
        or "short"
    )


# -----------------------------
# MAIN GENERATION PROCESS
# -----------------------------

def main():

    scripts = generate_scripts()

    manifest = []
    failed = []

    for index, story in enumerate(scripts, 1):

        title = str(
            story.get("title", f"Story {index}")
        )[:95]

        narration = str(
            story.get("narration", "")
        ).strip()

        if len(narration.split()) < 55:

            print(
                f"Skipping invalid short script: {title}"
            )

            failed.append(title)
            continue

        filename = (
            f"{index:02d}_{safe_filename(title)}"
        )

        image = OUT / f"{filename}.png"
        audio = OUT / f"{filename}.mp3"
        subtitles = OUT / f"{filename}.srt"
        video = OUT / f"{filename}.mp4"

        print("\n" + "=" * 50)
        print(f"Generating story {index}: {title}")
        print("=" * 50)

        try:

            generate_image(
                str(
                    story.get("image_prompt", title)
                ),
                image
            )

            asyncio.run(
                generate_voice(
                    narration,
                    audio
                )
            )

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
                + "\n".join(
                    story.get("source_urls", [])
                )
                + "\n\n#Shorts "
                + " ".join(
                    story.get("hashtags", [])[:5]
                )
            )

            manifest.append({
                "title": title,
                "description": description[:4900],
                "video": str(video),
                "category": story.get(
                    "category", "Education"
                )
            })

            print(
                f"SUCCESS: Finished {title}"
            )

        except Exception as error:

            print(
                f"FAILED: {title}"
            )

            print(
                f"Reason: {error}"
            )

            failed.append(title)

            continue

        time.sleep(2)

    manifest_path = OUT / "manifest.json"

    manifest_path.write_text(
        json.dumps(
            manifest,
            indent=2
        ),
        encoding="utf-8"
    )

    print("\n" + "=" * 50)

    print(
        f"SUCCESSFUL VIDEOS: {len(manifest)}"
    )

    print(
        f"FAILED VIDEOS: {len(failed)}"
    )

    print("=" * 50)

    if failed:
        print("Failed stories:")
        for title in failed:
            print(f"- {title}")

    if not manifest:
        raise RuntimeError(
            "No videos were successfully generated."
        )


if __name__ == "__main__":
    main()

