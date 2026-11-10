
"""Search, download and record reusable Wikimedia Commons video footage."""

import argparse
import html
import json
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API_URL = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "AIShortsFactory/1.0 (footage research)"
MAX_DOWNLOAD_BYTES = 150 * 1024 * 1024

VIDEO_MIMES = {
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/ogg": ".ogv",
    "video/quicktime": ".mov",
}


def make_session():
    retry = Retry(
        total=5,
        connect=3,
        read=3,
        status=5,
        backoff_factor=5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        respect_retry_after_header=True,
        raise_on_status=True,
    )

    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": USER_AGENT})
    return session


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def text(self):
        return " ".join(" ".join(self.parts).split())


def clean(value):
    if isinstance(value, dict):
        value = value.get("value", "")
    return str(value or "").strip()


def plain_text(value):
    parser = TextExtractor()
    parser.feed(html.unescape(clean(value)))
    return parser.text()


def commercial_license_ok(metadata):
    short = clean(metadata.get("LicenseShortName"))
    terms = clean(metadata.get("UsageTerms"))
    url = clean(metadata.get("LicenseUrl"))
    text = f"{short} {terms} {url}".lower()

    if any(x in text for x in (
        "noncommercial",
        "non-commercial",
        "no derivatives",
        "no-derivatives",
    )):
        return False

    return bool(re.search(
        r"\bcc0\b|\bpublic domain\b|\bpd-|"
        r"\bcc by(?:-sa)?(?:\s|$)|"
        r"\bcreative commons attribution",
        text,
        re.IGNORECASE,
    ))


def search_commons_videos(query, limit=10):
    session = make_session()

    response = session.get(
        API_URL,
        params={
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "generator": "search",
            "gsrsearch": f"filetype:video {query}",
            "gsrnamespace": "6",
            "gsrlimit": str(max(1, min(limit, 30))),
            "prop": "imageinfo",
            "iiprop": "url|mime|size|extmetadata",
        },
        timeout=(15, 45),
    )
    response.raise_for_status()

    pages = response.json().get("query", {}).get("pages", [])
    results = []

    for page in pages:
        info_list = page.get("imageinfo", [])
        if not info_list:
            continue

        info = info_list[0]
        mime = str(info.get("mime", "")).lower()
        metadata = info.get("extmetadata", {})

        if mime not in VIDEO_MIMES:
            continue

        if not commercial_license_ok(metadata):
            continue

        title = str(page.get("title", ""))
        license_name = clean(metadata.get("LicenseShortName"))
        license_url = clean(metadata.get("LicenseUrl"))
        artist = plain_text(metadata.get("Artist"))
        credit = plain_text(metadata.get("Credit"))

        results.append({
            "title": title.removeprefix("File:"),
            "page_url": (
                "https://commons.wikimedia.org/wiki/"
                + quote(title.replace(" ", "_"))
            ),
            "download_url": info.get("url"),
            "mime": mime,
            "size_bytes": int(info.get("size", 0) or 0),
            "license": license_name,
            "license_url": license_url,
            "artist": artist,
            "credit": credit,
            "attribution_required": not any(
                x in license_name.lower()
                for x in ("cc0", "public domain", "pd-")
            ),
        })

    return results


def download_video(item, output_dir):
    url = str(item.get("download_url", ""))

    if not url.startswith("https://"):
        raise ValueError("Invalid video download URL.")

    if item.get("size_bytes", 0) > MAX_DOWNLOAD_BYTES:
        raise ValueError("Video exceeds the 150 MB download limit.")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    safe_name = re.sub(
        r"[^A-Za-z0-9._-]+", "_", item["title"]
    ).strip("._")

    extension = VIDEO_MIMES[item["mime"]]
    destination = output_dir / (safe_name[:100] + extension)

    session = make_session()
    total = 0

    try:
        with session.get(
            url,
            stream=True,
            timeout=(20, 90),
        ) as response:
            response.raise_for_status()

            content_type = response.headers.get(
                "Content-Type", ""
            ).split(";")[0].lower()

            if content_type and content_type not in (
                *VIDEO_MIMES.keys(),
                "application/octet-stream",
            ):
                raise ValueError(
                    f"Unexpected download type: {content_type}"
                )

            with destination.open("wb") as output:
                for chunk in response.iter_content(
                    chunk_size=1024 * 1024
                ):
                    if not chunk:
                        continue

                    total += len(chunk)

                    if total > MAX_DOWNLOAD_BYTES:
                        raise ValueError(
                            "Download exceeded 150 MB."
                        )

                    output.write(chunk)

    except Exception:
        destination.unlink(missing_ok=True)
        raise

    if total < 1024:
        destination.unlink(missing_ok=True)
        raise ValueError("Downloaded file is too small.")

    item["local_file"] = str(destination)
    item["downloaded_bytes"] = total

    manifest = output_dir / (
        safe_name[:100] + "_credits.json"
    )
    manifest.write_text(
        json.dumps(item, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    attribution = (
        f"Title: {item['title']}\n"
        f"Creator: {item['artist'] or item['credit'] or 'Not listed'}\n"
        f"Source: {item['page_url']}\n"
        f"Licence: {item['license']}\n"
        f"Licence URL: {item['license_url']}\n"
        "Changes: Video may be cropped or edited for vertical format.\n"
    )

    (output_dir / (
        safe_name[:100] + "_ATTRIBUTION.txt"
    )).write_text(attribution, encoding="utf-8")

    return destination


def main():
    parser = argparse.ArgumentParser(
        description="Search and download reusable Wikimedia videos."
    )
    parser.add_argument("--query", required=True)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--download-first", action="store_true")
    parser.add_argument("--output-dir", default="output/footage")
    args = parser.parse_args()

    results = search_commons_videos(args.query, args.limit)
    print(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\nFound {len(results)} matching videos.")

    if not args.download_first:
        return

    if not results:
        raise SystemExit("No matching licensed videos were found.")

    for index, item in enumerate(results, start=1):
        print(
            f"\nDownload attempt {index}/{len(results)}: "
            f"{item['title']}"
        )

        try:
            path = download_video(item, args.output_dir)
            print(f"\nDownloaded successfully: {path}")
            print("Attribution and licence records saved.")
            return
        except requests.RequestException as exc:
            print(f"Skipping source due to network error: {exc}")
        except (ValueError, OSError) as exc:
            print(f"Skipping source: {exc}")

        if index < len(results):
            time.sleep(3)

    raise SystemExit("All download attempts failed.")


if __name__ == "__main__":
    main()
