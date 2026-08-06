#!/usr/bin/env python3
"""
upload_youtube.py — Upload a generated faceless video to YouTube.

Reads metadata from out.json (produced by make_video.py), refreshes a Google
OAuth access token from the stored refresh token, and performs a resumable
upload to the YouTube Data API v3. Writes uploaded.json (video id + url) and
appends the script slug to last_uploaded.txt so re-runs never double-post.

Credentials come from environment (set in GitHub Actions as secrets, or
exported locally for testing):
  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN
Optional:
  YT_PRIVACY  (default "unlisted"; set "public" to publish)
  OUT_VIDEO   (override video file path; else out.json video_file)
"""
import os
import sys
import json
import time
import urllib.request
import urllib.parse

META_FILE = "out.json"
UPLOADED_FILE = "uploaded.json"
STATE_FILE = "last_uploaded.txt"

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = ("https://www.googleapis.com/upload/youtube/v3/videos"
              "?uploadType=resumable&part=snippet,status,contentDetails,statistics")


def refresh_access_token():
    cid = os.environ["GOOGLE_CLIENT_ID"]
    csecret = os.environ["GOOGLE_CLIENT_SECRET"]
    rt = os.environ["YOUTUBE_REFRESH_TOKEN"]
    data = urllib.parse.urlencode({
        "client_id": cid,
        "client_secret": csecret,
        "refresh_token": rt,
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def already_uploaded(slug):
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            # format: slug<TAB>videoId
            parts = line.split("\t")
            if parts[0] == slug:
                return parts[1] if len(parts) > 1 else "unknown"
    return None


def mark_uploaded(slug, video_id):
    with open(STATE_FILE, "a", encoding="utf-8") as fh:
        fh.write(f"{slug}\t{video_id}\n")


def http_json(url, method="GET", body=None, headers=None):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode(), r.headers


def set_thumbnail(video_id, png_path, access_token):
    """Upload a custom thumbnail via thumbnails.set (media upload)."""
    url = ("https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
           f"?videoId={video_id}&uploadType=media")
    with open(png_path, "rb") as fh:
        img = fh.read()
    req = urllib.request.Request(
        url, data=img, method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "image/png",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"thumbnails.set HTTP {e.code}: {body[:500]}")
    items = resp.get("items") or []
    if not items:
        print(f"[thumb] DEBUG empty items. resp={json.dumps(resp)[:800]}")
    return bool(items and items[0].get("thumbnail", {}).get("default"))


def upload_video(meta, access_token, privacy):
    video_file = meta.get("video_file") or os.environ.get("OUT_VIDEO", "output.mp4")
    if not os.path.exists(video_file):
        raise SystemExit(f"Video file not found: {video_file}")
    size = os.path.getsize(video_file)

    snippet = {
        "snippet": {
            "title": meta["title"],
            "description": meta["description"],
            "tags": meta.get("tags", []),
            "categoryId": meta.get("categoryId", "28"),
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }
    body = json.dumps(snippet).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "video/mp4",
        "X-Upload-Content-Length": str(size),
    }
    _, hdrs = http_json(UPLOAD_URL, method="POST", body=body, headers=headers)
    location = hdrs.get("Location") or hdrs.get("location")
    if not location:
        raise RuntimeError(f"No upload URL returned. headers={dict(hdrs)}")

    # PUT the binary
    with open(video_file, "rb") as fh:
        binary = fh.read()
    up_req = urllib.request.Request(location, data=binary, method="PUT",
                                    headers={"Content-Type": "video/mp4"})
    with urllib.request.urlopen(up_req, timeout=300) as r:
        resp = json.loads(r.read().decode())

    vid = resp.get("id")
    return vid, f"https://youtu.be/{vid}"


def main():
    if not os.path.exists(META_FILE):
        raise SystemExit(f"{META_FILE} not found — run make_video.py first")
    with open(META_FILE, encoding="utf-8") as fh:
        meta = json.load(fh)
    slug = meta.get("slug", "unknown")

    prior = already_uploaded(slug)
    if prior:
        print(f"[upload] SKIP — slug '{slug}' already uploaded (videoId={prior})")
        return

    for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"):
        if not os.environ.get(k):
            raise SystemExit(f"Missing env var: {k}")

    privacy = os.environ.get("YT_PRIVACY", "unlisted")
    print(f"[upload] refreshing access token for slug '{slug}' ...")
    tok = refresh_access_token()
    at = tok.get("access_token")
    if not at:
        raise RuntimeError(f"Token refresh failed: {tok}")
    print(f"[upload] token OK (expires_in={tok.get('expires_in')})")

    vid, url = upload_video(meta, at, privacy)

    # custom thumbnail (highest-leverage CTR fix)
    thumb = meta.get("thumbnail")
    if thumb and os.path.exists(thumb):
        try:
            ok = set_thumbnail(vid, thumb, at)
            print(f"[upload] thumbnail {'SET' if ok else 'FAILED'} ({thumb})")
        except Exception as e:
            print(f"[upload] thumbnail error (non-fatal): {e!r}")

    mark_uploaded(slug, vid)
    with open(UPLOADED_FILE, "w", encoding="utf-8") as fh:
        json.dump({"slug": slug, "videoId": vid, "url": url, "privacy": privacy},
                  fh, ensure_ascii=False, indent=2)
    print(f"[upload] SUCCESS  videoId={vid}  url={url}  privacy={privacy}")


if __name__ == "__main__":
    main()
