
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
MAX_BYTES = 150 * 1024 * 1024

VIDEO_EXTENSIONS = {".mp4", ".m4v", ".webm", ".mov", ".ogv"}


def make_session():
    retry = Retry(
        total=4,
        connect=3,
        read=3,
        status=4,
        backoff_factor=3,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({
        "User-Agent": "AIShortsFactory/1.0 (video research)"
    })
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


def search_items(query, limit=5):
    session = make_session()

    # Search broadly by media type and licence metadata.
    # Individual item licences are checked again below.
    search_query = (
        'mediatype:movies AND '
        '(licenseurl:http*by* OR licenseurl:http*zero*)'
    )

    params = [
        ("q", search_query),
        ("fl[]", "identifier"),
        ("fl[]", "title"),
        ("fl[]", "creator"),
        ("fl[]", "licenseurl"),
        ("rows", str(max(30, min(limit * 20, 100)))),
        ("page", "1"),
        ("output", "json"),
    ]

    response = session.get(
        SEARCH_URL, params=params, timeout=(15, 45)
    )
    response.raise_for_status()

    docs = response.json().get("response", {}).get("docs", [])
    print(f"Archive search returned {len(docs)} candidate items.")

    results = []

    for doc in docs:
        identifier = clean(doc.get("identifier"))
        if not identifier:
            continue

        # Check title/creator relevance after broad licence search.
        text = (
            clean(doc.get("title")) + " " +
            clean(doc.get("creator"))
        ).lower()
        terms = [x.lower() for x in query.split() if len(x) > 2]

        if terms and not any(term in text for term in terms):
            continue

        license_url = clean(doc.get("licenseurl"))

        if not allowed_license(license_url):
            continue

        meta_response = session.get(
            METADATA_URL + quote(identifier, safe=""),
            timeout=(15, 45),
        )
        meta_response.raise_for_status()
        metadata = meta_response.json()
        item_meta = metadata.get("metadata", {})

        # Recheck the actual item metadata, not only search index.
        actual_license = clean(item_meta.get("licenseurl"))

        if not allowed_license(actual_license):
            continue

        video_files = []

        for file_info in metadata.get("files", []):
            name = clean(file_info.get("name"))
            if Path(name).suffix.lower() not in VIDEO_EXTENSIONS:
                continue

            try:
                size = int(file_info.get("size", 0))
            except (TypeError, ValueError):
                continue

            if 1024 <= size <= MAX_BYTES:
                video_files.append({
                    "name": name,
                    "size_bytes": size,
                })

        if not video_files:
            continue

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
            "license": actual_license,
            "source_url": f"https://archive.org/details/{identifier}",
            "file_name": selected["name"],
            "size_bytes": selected["size_bytes"],
            "download_url": (
                DOWNLOAD_URL + quote(identifier, safe="") +
                "/" + quote(selected["name"], safe="/")
            ),
        })

        print(f"Accepted: {identifier}")

        if len(results) >= limit:
            break

        time.sleep(0.3)

    return results


def download_item(item, output_dir):
    session = make_session()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    safe_name = re.sub(
        r"[^A-Za-z0-9._-]+", "_", item["identifier"]
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
        "Changes: May be cropped or edited for vertical video.\n"
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
    parser.add_argument(
        "--output-dir", default="output/archive-footage"
    )
    args = parser.parse_args()

    results = search_items(args.query, args.limit)

    print(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\nFound {len(results)} videos with accepted licence metadata.")

    if args.download_first and results:
        path = download_item(results[0], args.output_dir)
        print(f"Downloaded successfully: {path}")
    elif args.download_first:
        raise SystemExit("No suitable licensed videos were found.")


if __name__ == "__main__":
    main()
