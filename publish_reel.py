import os
import sys
import time
import requests

CF_ACCOUNT_ID = os.environ["CF_ACCOUNT_ID"]
CF_API_TOKEN = os.environ["CF_API_TOKEN"]
FB_PAGE_ID = os.environ["FB_PAGE_ID"]
FB_ACCESS_TOKEN = os.environ["FB_ACCESS_TOKEN"]

HISTORY_FILE = "published.txt"
GRAPH_VERSION = "v27.0"


def get_published_ids():
    if not os.path.exists(HISTORY_FILE):
        return set()
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def save_published_id(video_uid):
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(f"{video_uid}\n")


def get_next_cloudflare_video(published_ids):
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/stream"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    videos = resp.json().get("result", [])

    for video in reversed(videos):
        uid = video.get("uid")
        state = video.get("status", {}).get("state")
        if state == "ready" and uid not in published_ids:
            raw_name = video.get("meta", {}).get("name", "")
            caption = os.path.splitext(raw_name)[0].strip()
            return uid, caption
    return None, None


def get_mp4_download_url(video_uid):
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/stream/{video_uid}/downloads"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}"}

    for _ in range(15):
        resp = requests.post(url, headers=headers, timeout=30)
        resp.raise_for_status()
        default_info = resp.json().get("result", {}).get("default", {})
        if default_info.get("status") == "ready" and default_info.get("url"):
            return default_info["url"]
        time.sleep(5)

    raise RuntimeError("انتهت مهلة تجهيز رابط MP4 من Cloudflare Stream.")


def publish_reel_to_facebook(mp4_url, caption):
    base_url = f"https://graph.facebook.com/{GRAPH_VERSION}/{FB_PAGE_ID}/video_reels"

    start_resp = requests.post(
        base_url,
        data={"upload_phase": "start", "access_token": FB_ACCESS_TOKEN},
        timeout=30,
    )
    start_resp.raise_for_status()
    start_data = start_resp.json()
    video_id = start_data["video_id"]
    upload_url = start_data["upload_url"]

    upload_resp = requests.post(
        upload_url,
        headers={
            "Authorization": f"OAuth {FB_ACCESS_TOKEN}",
            "file_url": mp4_url,
        },
        timeout=120,
    )
    upload_resp.raise_for_status()

    finish_resp = requests.post(
        base_url,
        data={
            "access_token": FB_ACCESS_TOKEN,
            "video_id": video_id,
            "upload_phase": "finish",
            "video_state": "PUBLISHED",
            "description": caption,
        },
        timeout=60,
    )
    finish_resp.raise_for_status()
    print(f"تم نشر الريلز بنجاح! رقم الفيديو على فيسبوك: {video_id}")


def main():
    published_ids = get_published_ids()
    uid, caption = get_next_cloudflare_video(published_ids)

    if not uid:
        print("لا توجد فيديوهات جديدة غير منشورة في Cloudflare Stream حالياً.")
        sys.exit(0)

    print(f"جاري تجهيز الفيديو: {uid} | الوصف: {caption}")
    mp4_url = get_mp4_download_url(uid)
    publish_reel_to_facebook(mp4_url, caption)
    save_published_id(uid)


if __name__ == "__main__":
    main()
