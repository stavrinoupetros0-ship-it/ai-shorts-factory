
"""Search Wikimedia Commons for reusable video footage.

Only permits material with a commercial-use-compatible licence:
CC0, public domain, CC BY, or CC BY-SA.
"""
import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

API_URL = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "AIShortsFactory/1.0 (footage research)"
ALLOWED_LICENSE_PATTERNS = (
    r"\bcc0\b",
    r"\bpublic domain\b",
    r"\bpd-",
    r"\bcc by(?:-sa)?(?:\s|$)",
    r"\bcreative commons attribution(?:-sharealike)?\b",
)
VIDEO_MIMES = {
    "video/mp4", "video/webm", "video/ogg", "video/quicktime"
}
MAX_DOWNLOAD_BYTES = 150 * 1024 * 1024


def _clean(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("value", "")).strip()
    return str(value or "").strip()


def _commercial_license_ok(metadata: dict) -> bool:
    short_name = _clean(metadata.get("LicenseShortName"))
    usage_terms = _clean(metadata.get("UsageTerms"))
    license_url = _clean(metadata.get("LicenseUrl"))
    text = f"{short_name} {usage_terms} {license_url}".lower()

    if any(term in text for term in (
        "noncommercial", "non-commercial",
        "no derivatives", "no-derivatives"
    )):
        return False

    return any(
        re.search(pattern, text, re.IGNORECASE)
        for pattern in ALLOWED_LICENSE_PATTERNS
    )


def search_commons_videos(
    query: str, limit: int = 12
) -> list[dict]:
    if not query.strip():
        raise ValueError("A footage search query is required.")

    response = requests.get(
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
        headers={"User-Agent": USER_AGENT},
        timeout=(15, 45),
    )
    response.raise_for_status()

    pages = response.json().get("query", {}).get("pages", [])
    matches = []

    for page in pages:
        info_list = page.get("imageinfo", [])
        if not info_list:
            continue

        info = info_list[0]
        mime = str(info.get("mime", "")).lower()
        metadata = info.get("extmetadata", {})

        if mime not in VIDEO_MIMES:
            continue

        if not _commercial_license_ok(metadata):
            continue

        title = str(page.get("title", ""))
        license_name = _clean(
            metadata.get("LicenseShortName")
        )

        matches.append({
            "title": title.removeprefix("File:"),
            "page_url": (
                "https://commons.wikimedia.org/wiki/"
                + quote(title.replace(" ", "_"))
            ),
            "download_url": info.get("url"),
            "mime": mime,
            "size_bytes": int(info.get("size", 0) or 0),
            "license": license_name,
            "license_url": _clean(
                metadata.get("LicenseUrl")
            ),
            "artist": _clean(metadata.get("Artist")),
            "credit": _clean(metadata.get("Credit")),
            "attribution_required": not any(
                term in license_name.lower()
                for term in ("cc0", "public domain", "pd-")
            ),
        })

    return matches


def download_commons_video(
    item: dict, destination: str | Path
) -> Path:
    url = str(item.get("download_url", ""))

    if not url.startswith("https://"):
        raise ValueError("Invalid HTTPS download URL.")

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    with requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        stream=True,
        timeout=(20, 90),
    ) as response:
        response.raise_for_status()

        content_type = response.headers.get(
            "Content-Type", ""
        ).split(";")[0].lower()

        if (
            content_type
            and content_type not in VIDEO_MIMES
            and content_type != "application/octet-stream"
        ):
            raise ValueError(
                f"Unexpected content type: {content_type}"
            )

        content_length = int(
            response.headers.get("Content-Length", "0") or 0
        )

        if content_length > MAX_DOWNLOAD_BYTES:
            raise ValueError("Video exceeds 150 MB.")

        total = 0

        with destination.open("wb") as output:
            for chunk in response.iter_content(
                chunk_size=1024 * 1024
            ):
                if not chunk:
                    continue

                total += len(chunk)

                if total > MAX_DOWNLOAD_BYTES:
                    output.close()
                    destination.unlink(missing_ok=True)
                    raise ValueError(
                        "Video exceeded 150 MB."
                    )

                output.write(chunk)

    if total < 1024:
        destination.unlink(missing_ok=True)
        raise ValueError("Downloaded file is too small.")

    return destination


def main():
    parser = argparse.ArgumentParser(
        description="Search reusable Wikimedia Commons videos."
    )
    parser.add_argument(
        "--query", required=True,
        help="Example: city timelapse"
    )
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args()

    results = search_commons_videos(
        args.query, args.limit
    )

    print(json.dumps(
        results, indent=2, ensure_ascii=False
    ))
    print(
        f"\nFound {len(results)} videos "
        "with accepted commercial-use licences."
    )


if __name__ == "__main__":
    main()
