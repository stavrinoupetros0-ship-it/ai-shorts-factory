
import os
import json
import time
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

required = [
    "YT_CLIENT_ID",
    "YT_REFRESH_TOKEN"
]

for key in required:
    if not os.getenv(key):
        raise RuntimeError(f"Missing secret: {key}")

credentials = Credentials(
    token=None,
    refresh_token=os.environ["YT_REFRESH_TOKEN"],
    token_uri="https://oauth2.googleapis.com/token",
    client_id=os.environ["YT_CLIENT_ID"],
    client_secret=os.getenv("YT_CLIENT_SECRET", ""),
    scopes=[
        "https://www.googleapis.com/auth/youtube.upload"
    ]
)

youtube = build(
    "youtube",
    "v3",
    credentials=credentials
)

manifest_path = Path("output/manifest.json")

if not manifest_path.exists():
    raise RuntimeError("No generated videos found.")

manifest = json.loads(manifest_path.read_text())

for item in manifest:

    video_path = Path(item["video"])

    if not video_path.exists():
        print(f"Missing video: {video_path}")
        continue

    body = {
        "snippet": {
            "title": item["title"][:100],
            "description": item["description"],
            "tags": [
                "shorts",
                "facts",
                "stories",
                "education"
            ],
            "categoryId": "27"
        },
        "status": {
            "privacyStatus": "private",
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        resumable=True
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )

    response = None

    while response is None:
        status, response = request.next_chunk()

        if status:
            print(
                f"Upload progress: "
                f"{int(status.progress() * 100)}%"
            )

    print(
        "Uploaded privately: "
        f"https://youtube.com/watch?v={response['id']}"
    )

    time.sleep(3)

print("All uploads completed.")
