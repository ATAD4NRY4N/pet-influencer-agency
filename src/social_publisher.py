import os
import json
import time
import requests

def upload_to_youtube_shorts(video_url: str, title: str, script: str, yt_creds: dict) -> str:
    token_resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": yt_creds["client_id"],
            "client_secret": yt_creds["client_secret"],
            "refresh_token": yt_creds["refresh_token"],
            "grant_type": "refresh_token"
        },
        timeout=20
    ).json()
    access_token = token_resp["access_token"]

    video_bytes = requests.get(video_url, timeout=60).content

    metadata = {
        "snippet": {
            "title": f"{title[:80]} #Shorts",
            "description": f"{script}\n\n#Shorts #Pets #PetLovers",
            "categoryId": "15"
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True  # Official compliance flag
        }
    }

    init_resp = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(len(video_bytes)),
            "X-Upload-Content-Type": "video/mp4"
        },
        data=json.dumps(metadata),
        timeout=30
    )
    init_resp.raise_for_status()
    upload_url = init_resp.headers["Location"]

    upload_resp = requests.put(
        upload_url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "video/mp4",
            "Content-Length": str(len(video_bytes))
        },
        data=video_bytes,
        timeout=120
    ).json()

    return f"https://youtube.com/shorts/{upload_resp['id']}"

def upload_to_instagram_reels(video_url: str, caption: str, ig_creds: dict) -> str:
    ig_user_id = ig_creds["ig_user_id"]
    access_token = ig_creds["access_token"]
    base_url = f"https://graph.facebook.com/v21.0/{ig_user_id}"

    # Container creation
    create_resp = requests.post(
        f"{base_url}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": f"{caption}\n\n#petreels #petlife #cuteanimals",
            "share_to_feed": "true",
            "access_token": access_token
        },
        timeout=30
    ).json()
    container_id = create_resp["id"]

    # Poll status
    for _ in range(18):
        time.sleep(5)
        st = requests.get(
            f"https://graph.facebook.com/v21.0/{container_id}",
            params={"fields": "status_code", "access_token": access_token},
            timeout=15
        ).json()
        if st.get("status_code") == "FINISHED":
            break

    # Publish
    pub_resp = requests.post(
        f"{base_url}/media_publish",
        data={"creation_id": container_id, "access_token": access_token},
        timeout=30
    ).json()
    return f"https://www.instagram.com/reels/{pub_resp['id']}/"