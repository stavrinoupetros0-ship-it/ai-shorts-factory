
import os
import re
import json
import time
import base64
import asyncio
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import requests
import edge_tts
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.getenv("OUTPUT_DIR", "output"))
if not OUT.is_absolute():
    OUT = ROOT / OUT
OUT.mkdir(parents=True, exist_ok=True)

COUNT = int(os.getenv("VIDEOS_PER_RUN", "1"))
SCENE_COUNT = 5

OPENROUTER_KEY = os.environ["OPENROUTER_API_KEY"]
CF_ID = os.environ["CF_ACCOUNT_ID"]
CF_TOKEN = os.environ["CF_API_TOKEN"]

IMAGE_MODEL = "@cf/black-forest-labs/flux-1-schnell"


def post_retry(url, headers, payload, label, timeout=(20, 180)):
    for attempt in range(1, 5):
        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=timeout
            )

            if response.status_code in (408, 429, 500, 502, 503, 504):
                if attempt == 4:
                    response.raise_for_status()

                wait = min(10 * (2 ** (attempt - 1)), 60)
                print(f"{label}: HTTP {response.status_code}; retrying in {wait}s")
                time.sleep(wait)
                continue

            response.raise_for_status()
            return response

        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError
        ):
            if attempt == 4:
                raise

            wait = min(10 * (2 ** (attempt - 1)), 60)
            print(f"{label}: connection issue; retrying in {wait}s")
            time.sleep(wait)

    raise RuntimeError(f"{label} failed")


def openrouter_json(messages):
    response = post_retry(
        "https://openrouter.ai/api/v1/chat/completions",
        {
            "Authorization": f"Bearer {OPENROUTER_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/stavrinoupetros0-ship-it/ai-shorts-factory",
            "X-Title": "YouTube Shorts Factory"
        },
        {
            "model": "openrouter/free",
            "messages": messages,
            "temperature": 0.55,
            "max_tokens": 7000,
            "stream": False
        },
        "OpenRouter"
    )

    data = response.json()
    selected_model = str(data.get("model", "unknown"))

    print(f"Free model selected: {selected_model}")

    if not selected_model.endswith(":free"):
        raise RuntimeError(
            f"Paid or unverified model blocked: {selected_model}"
        )

    choices = data.get("choices", [])

    if not choices:
        raise RuntimeError("OpenRouter returned no choices.")

    raw = choices[0].get("message", {}).get("content", "")

    if not isinstance(raw, str) or not raw.strip():
        raise RuntimeError("OpenRouter returned empty content.")

    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        print(raw[:1500])
        raise RuntimeError("OpenRouter returned invalid JSON.") from error


def generate_scripts():
    topics = json.loads(
        (ROOT / "config/topics.json").read_text(encoding="utf-8")
    )

    prompt = f"""
Create exactly {COUNT} original YouTube Shorts scripts.

Choose from these topic categories:
{topics['categories']}

Narration must contain 95-115 spoken words, approximately 40-55 seconds.

Return ONLY a valid JSON array.

Each story must contain:
title
category
narration
description
hashtags
source_urls
scene_prompts

IMPORTANT REQUIREMENTS:

1. Start with a strong, specific hook.
2. Tell a clear story with a surprising development and satisfying ending.
3. Do not invent dates, statistics, quotes or discoveries.
4. Distinguish established facts from theories.
5. Include 2-4 direct HTTPS source URLs from credible institutions,
   museums, universities, journals or established reference works.
6. Do not invent URLs.
7. Do not put source URLs in the spoken narration.
8. Make exactly FIVE different scene_prompts.
9. Each scene must show a different shot, subject, detail or location.
10. Put the scenes in the same order as the narration.
11. Use cinematic documentary photography, vertical 9:16.
12. No text, logos or watermarks in the images.
13. Write natural spoken English.
"""

    print("Connecting to OpenRouter...")
    print("Using the FREE model router.")

    result = openrouter_json([
        {
            "role": "system",
            "content": (
                "You are a careful documentary researcher and "
                "YouTube storyteller. Return valid JSON only. "
                "Never fabricate facts or references."
            )
        },
        {"role": "user", "content": prompt}
    ])

    if not isinstance(result, list):
        raise RuntimeError("Expected a JSON array.")

    stories = [item for item in result if isinstance(item, dict)]

    if not stories:
        raise RuntimeError("No usable stories returned.")

    print(f"OpenRouter generated {len(stories)} story.")
    return stories[:COUNT]


def generate_image(prompt, destination):
    url = (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{CF_ID}/ai/run/{IMAGE_MODEL}"
    )

    response = post_retry(
        url,
        {
            "Authorization": f"Bearer {CF_TOKEN}",
            "Content-Type": "application/json"
        },
        {
            "prompt": (
                str(prompt)
                + ", vertical 9:16 cinematic documentary photography, "
                "highly detailed, natural lighting, no text, "
                "no logos, no watermark"
            )
        },
        "Cloudflare image generation"
    )

    if "image" in response.headers.get("content-type", ""):
        destination.write_bytes(response.content)
    else:
        image_data = response.json().get("result", {}).get("image")

        if not image_data:
            raise RuntimeError("Cloudflare returned no image.")

        destination.write_bytes(base64.b64decode(image_data))

    with Image.open(destination) as image:
        image.verify()

    print(f"Image ready: {destination.name}")


async def generate_voice(text, destination):
    for attempt in range(3):
        try:
            voice = edge_tts.Communicate(
                text,
                voice="en-US-AriaNeural",
                rate="+2%",
                pitch="-1Hz"
            )
            await voice.save(str(destination))
            return

        except Exception:
            if attempt == 2:
                raise

            await asyncio.sleep(5 * (attempt + 1))


def get_duration(audio):
    result = subprocess.check_output(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio)
        ],
        text=True
    )

    return float(result.strip())


def make_timestamp(seconds):
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
        " ".join(words[i:i + 4])
        for i in range(0, len(words), 4)
    ]

    if not chunks:
        raise RuntimeError("No subtitle text available.")

    lines = []

    for index, chunk in enumerate(chunks):
        start = duration * index / len(chunks)
        end = duration * (index + 1) / len(chunks)

        lines.extend([
            str(index + 1),
            f"{make_timestamp(start)} --> {make_timestamp(end)}",
            chunk,
            ""
        ])

    destination.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )


def render_video(images, audio, subtitles, destination, duration):
    scene_duration = duration / len(images)

    command = ["ffmpeg", "-y"]

    for image in images:
        command += [
            "-loop", "1",
            "-framerate", "30",
            "-t", f"{scene_duration:.4f}",
            "-i", str(image)
        ]

    filters = []
    labels = []

    for index in range(len(images)):
        label = f"scene{index}"
        labels.append(f"[{label}]")

        filters.append(
            f"[{index}:v]"
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,"
            "zoompan=z='min(zoom+0.0008,1.13)':d=1:"
            "x='iw/2-(iw/zoom/2)':"
            "y='ih/2-(ih/zoom/2)':"
            "s=1080x1920:fps=30,"
            f"trim=duration={scene_duration:.4f},"
            "setpts=PTS-STARTPTS,setsar=1"
            f"[{label}]"
        )

    filters.append(
        "".join(labels)
        + f"concat=n={len(images)}:v=1:a=0[base]"
    )

    subtitle_path = str(subtitles.resolve()).replace("\\", "/")
    subtitle_path = subtitle_path.replace(":", r"\:")

    filters.append(
        "[base]subtitles='"
        + subtitle_path
        + "':force_style='"
        "FontName=DejaVu Sans,"
        "FontSize=19,"
        "PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,"
        "BorderStyle=1,"
        "Outline=3,"
        "Shadow=1,"
        "Alignment=2,"
        "MarginV=150'"
        "[vout]"
    )

    command += [
        "-filter_complex", ";".join(filters),
        "-i", str(audio),
        "-map", "[vout]",
        "-map", f"{len(images)}:a",
        "-t", f"{duration:.3f}",
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
            "FFmpeg failed:\n" + result.stderr[-3500:]
        )


def safe_filename(title):
    return (
        re.sub(r"[^a-zA-Z0-9_-]+", "_", title)
        .strip("_")[:45]
        or "short"
    )


def main():
    stories = generate_scripts()

    manifest = []
    failed = []

    for index, story in enumerate(stories, 1):
        title = str(story.get("title", f"Story {index}"))[:95]
        narration = str(story.get("narration", "")).strip()

        if not 75 <= len(narration.split()) <= 140:
            print(f"Skipping unsuitable narration length: {title}")
            failed.append(title)
            continue

        filename = f"{index:02d}_{safe_filename(title)}"

        audio = OUT / f"{filename}.mp3"
        subtitles = OUT / f"{filename}.srt"
        video = OUT / f"{filename}.mp4"

        prompts = story.get("scene_prompts", [])

        if not isinstance(prompts, list):
            prompts = []

        prompts = [
            str(prompt).strip()
            for prompt in prompts
            if str(prompt).strip()
        ]

        if len(prompts) < SCENE_COUNT:
            raise RuntimeError(
                "The script must provide five distinct scene prompts. "
                "Refusing to repeat one image."
            )

        prompts = prompts[:SCENE_COUNT]

        images = [
            OUT / f"{filename}_scene_{number:02d}.png"
            for number in range(1, SCENE_COUNT + 1)
        ]

        print("\n" + "=" * 50)
        print(f"Generating: {title}")
        print("=" * 50)

        try:
            for number, (prompt, image) in enumerate(
                zip(prompts, images), 1
            ):
                print(f"Creating visual {number}/{SCENE_COUNT}")
                generate_image(prompt, image)

            asyncio.run(generate_voice(narration, audio))

            duration = get_duration(audio)

            generate_subtitles(
                narration,
                duration,
                subtitles
            )

            render_video(
                images,
                audio,
                subtitles,
                video,
                duration
            )

            sources = []

            source_urls = story.get("source_urls", [])

            if isinstance(source_urls, list):
                for source in source_urls:
                    source = str(source).strip()
                    parsed = urlparse(source)

                    if (
                        parsed.scheme == "https"
                        and parsed.netloc
                        and source not in sources
                    ):
                        sources.append(source)

            description = (
                str(story.get("description", ""))
                + "\n\nSources (review before publishing):\n"
                + "\n".join(sources)
                + "\n\n#Shorts "
                + " ".join(story.get("hashtags", [])[:5])
            )

            manifest.append({
                "title": title,
                "description": description[:4900],
                "video": str(video),
                "duration_seconds": round(duration, 2),
                "visual_scenes": len(images),
                "scene_images": [str(image) for image in images],
                "source_urls": sources,
                "category": story.get("category", "Education")
            })

            print(f"SUCCESS: Finished {title}")

        except Exception as error:
            print(f"FAILED: {title} - {error}")
            failed.append(title)

    (OUT / "manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8"
    )

    print(f"SUCCESSFUL VIDEOS: {len(manifest)}")
    print(f"FAILED VIDEOS: {len(failed)}")

    if not manifest:
        raise RuntimeError("No videos were successfully generated.")


if __name__ == "__main__":
    main()
