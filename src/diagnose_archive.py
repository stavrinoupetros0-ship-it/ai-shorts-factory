
import json
import time
from urllib.parse import quote

import requests

SEARCH_URL = "https://archive.org/advancedsearch.php"
METADATA_URL = "https://archive.org/metadata/"

session = requests.Session()
session.headers.update({
    "User-Agent": "AIShortsFactory/1.0 (licence diagnostics)"
})


def clean(value):
    if isinstance(value, list):
        return ", ".join(str(x) for x in value)
    return str(value or "").strip()


def main():
    params = [
        ("q", "mediatype:movies"),
        ("fl[]", "identifier"),
        ("fl[]", "title"),
        ("fl[]", "creator"),
        ("fl[]", "licenseurl"),
        ("rows", "30"),
        ("page", "1"),
        ("output", "json"),
    ]

    response = session.get(
        SEARCH_URL, params=params, timeout=45
    )
    response.raise_for_status()

    docs = response.json().get("response", {}).get("docs", [])
    print(f"Found {len(docs)} movie candidates.")
    print("\n--- LICENCE DIAGNOSTICS ---\n")

    for index, doc in enumerate(docs, 1):
        identifier = clean(doc.get("identifier"))
        title = clean(doc.get("title"))
        indexed_license = clean(doc.get("licenseurl"))

        print(f"\nITEM {index}")
        print(f"Identifier: {identifier}")
        print(f"Title: {title[:150]}")
        print(f"Search licence: {indexed_license or '[EMPTY]'}")

        if not identifier:
            continue

        try:
            metadata_response = session.get(
                METADATA_URL + quote(identifier, safe=""),
                timeout=30,
            )
            metadata_response.raise_for_status()
            metadata = metadata_response.json()
            item_meta = metadata.get("metadata", {})

            actual_license = clean(item_meta.get("licenseurl"))
            print(
                "Metadata licence: "
                + (actual_license or "[EMPTY]")
            )

            print(
                "Metadata title: "
                + clean(item_meta.get("title"))[:150]
            )

        except Exception as error:
            print(f"Metadata error: {error}")

        time.sleep(0.3)

    print("\n--- DIAGNOSTIC COMPLETE ---")


if __name__ == "__main__":
    main()
