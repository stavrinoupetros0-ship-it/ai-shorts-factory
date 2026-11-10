
"""Search and download commercially reusable video from Internet Archive.

Only accepts explicit CC0, Public Domain, CC BY and CC BY-SA licence
metadata. Every candidate must still be checked on its original item page.
"""

import argparse
import html
import json
import re
import time
from pathlib import Path
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

SEARCH_URL = "https://archive.org/advancedsearch.php"
METADATA_URL = "https://archive.org/metadata/"
DOWNLOAD_URL = "https://archive.org/download/"
USER_AGENT = "AIShortsFactory/1.0 (video research)"
MAX_BYTES = 150 * 1024 * 1024

VIDEO_EXTENSIONS = {
    ".mp4", ".m4v", ".webm", ".mov", ".ogv"
}


def make_session():
    retry = Retry(
        total=4,
        connect=3,
        read=3,
        status=4,
        backoff_factor=4,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def clean(value):
    if isinstance(value, list):
        return ", ".join(str(x) for x in value)
    return html.unescape(str(value or "")).strip()


def allowed_license(value):
    url = clean(value).lower().rstrip("/")
    accepted = (
        "creativecommons.org/publicdomain/zero/1.0",
        "creativecommons.org/licenses/by/3.0",
        "creativecommons.org/licenses/by/4.0",
        "creativecommons.org/licenses/by-sa/3.0",
        "creativecommons.org/licenses/by-sa/4.0",
    )
    return any(url.startswith(x) for x in accepted)


def search_items(query, limit=10):
    session = make_session()

    # Search only movie items. The exact licence is checked again
    # against each item's metadata before it can be downloaded.
    search_terms = re.findall(r"[A-Za-z0-9]+", query)
    if not search_terms:
        raise ValueError("Please provide a search query.")

    term_query = " OR ".join(
        f"title:{term}" for term in search_terms
    )
    params = {
        "q": f"mediatype:movies AND ({term_query})",
        "fl[]": [
            "identifier", "title", "creator", "licenseurl"
        ],
        "rows": max(10, min(limit * 5, 50)),
        "page": 1,
        "output": "json",
    }

    response = session.get(
        SEARCH_URL, params=params, timeout=(15, 45)
    )
    response.raise_for_status()

    docs = response.json().get("response", {}).get("docs", [])
    results = []

    for doc in docs:
        identifier = clean(doc.get("identifier"))
        if not identifier:
            continue

        meta_response = session.get(
            METADATA_URL + quote(identifier, safe=""),
            timeout=(15, 45),
        )
        meta_response.raise_for_status()
        metadata = meta_response.json()

        item_meta = metadata.get("metadata", {})
        license_url = clean(
            item_meta.get("licenseurl") or doc.get("licenseurl")
        )

        if not allowed_license(license_url):
            continue

        files = metadata.get("files", [])
        video_files = []

        for file_info in files:
            name = clean(file_info.get("name"))
            extension = Path(name).suffix.lower()

            if extension not in VIDEO_EXTENSIONS:
                continue

            try:
                size = int(file_info.get("size", 0))
            except (TypeError, ValueError):
                continue

            if size <= 0 or size > MAX_BYTES:
                continue

            video_files.append({
                "name": name,
                "size_bytes": size,
            })

        if not video_files:
            continue

        # Prefer MP4 for easier editing, then smaller files.
        video_files.sort(
            key=lambda x: (
                Path(x["name"]).suffix.lower() != ".mp4",
                x["size_bytes"],
            )
        )

        selected = video_files[0]
        results.append({
            "identifier": identifier,
            "title": clean(item_meta.get("title") or doc.get("title")),
            "creator": clean(
                item_meta.get("creator") or doc.get("creator")
            ),
            "license": license_url,
            "source_url": f"https://archive.org/details/{identifier}",
            "file_name": selected["name"],
            "size_bytes": selected["size_bytes"],
            "download_url": (
                DOWNLOAD_URL
                + quote(identifier, safe="")
                + "/"
                + quote(selected["name"], safe="/")
            ),
        })

        if len(results) >= limit:
            break

        time.sleep(0.5)

    return results


def download_item(item, output_dir):
    session = make_session()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    safe_name = re.sub(
        r"[^A-Za-z0-9._-]+", "_",
        item["identifier"]
    ).strip("._")

    extension = Path(item["file_name"]).suffix.lower()
    destination = output_dir / (safe_name + extension)
    total = 0

    with session.get(
        item["download_url"],
        stream=True,
        timeout=(20, 120),
    ) as response:
        response.raise_for_status()

        with destination.open("wb") as output:
            for chunk in response.iter_content(1024 * 1024):
                if not chunk:
                    continue

                total += len(chunk)
                if total > MAX_BYTES:
                    output.close()
                    destination.unlink(missing_ok=True)
                    raise ValueError("Video exceeded 150 MB.")

                output.write(chunk)

    if total < 1024:
        destination.unlink(missing_ok=True)
        raise ValueError("Downloaded file is too small.")

    item["local_file"] = str(destination)
    item["downloaded_bytes"] = total

    (output_dir / (safe_name + "_credits.json")).write_text(
        json.dumps(item, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    attribution = (
        f"Title: {item['title']}\n"
        f"Creator: {item['creator'] or 'Not listed'}\n"
        f"Source: {item['source_url']}\n"
        f"Licence: {item['license']}\n"
        f"Changes: May be cropped or edited for vertical video.\n"
    )

    (output_dir / (safe_name + "_ATTRIBUTION.txt")).write_text(
        attribution, encoding="utf-8"
    )

    return destination


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=True)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--download-first", action="store_true")
    parser.add_argument("--output-dir", default="output/archive-footage")
    args = parser.parse_args()

    results = search_items(args.query, args.limit)

    print(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\nFound {len(results)} videos with accepted licence metadata.")

    if args.download_first and results:
        print("\nDownloading first result...")
        path = download_item(results[0], args.output_dir)
        print(f"Downloaded: {path}")
        print("Attribution records saved.")


if __name__ == "__main__":
    main()
