
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
        print(f"\nDownload attempt {index}/{len(results)}: {item['title']}")

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
