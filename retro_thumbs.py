#!/usr/bin/env python3
"""Retroactively set a custom high-CTR thumbnail on already-published videos.

Idempotent + rate-limit aware: YouTube throttles thumbnail uploads
(uploadRateLimitExceeded / HTTP 429), so we back off and retry, and space
the videos out to avoid a burst. Requires the same YouTube OAuth secrets
as upload_youtube.py.
"""
import time
from upload_youtube import refresh_access_token, set_thumbnail
from make_video import make_thumbnail

# (videoId, title-used-for-thumbnail-text)
VIDEOS = [
    ("FSC9vs4QdL0", "Turn Google Sheets Into A Free AI Analyst"),
    ("vzxYPLFAPKA", "Free Local AI: Summarize Long Articles in Seconds"),
    ("tFUQudLUguo", "Free AI Coding Agents: Build Software Without A Subscription"),
    ("zSaR5qoE0YA", "How to Remove Image Backgrounds Free with AI in 10 Seconds"),
]


def main():
    tok = refresh_access_token()
    at = tok.get("access_token")
    if not at:
        raise RuntimeError(f"token refresh failed: {tok}")
    print(f"[retro] token OK (expires_in={tok.get('expires_in')})")
    for vid, title in VIDEOS:
        ok = False
        for attempt in range(1, 6):
            make_thumbnail(title, "thumbnail.png")
            try:
                ok = set_thumbnail(vid, "thumbnail.png", at)
                break
            except Exception as e:
                if "429" in str(e):
                    wait = 60 * attempt
                    print(f"[retro] {vid} rate-limited, wait {wait}s (try {attempt})")
                    time.sleep(wait)
                else:
                    print(f"[retro] {vid} ERROR: {e!r}")
                    break
        print(f"[retro] {vid}  thumbnail {'SET' if ok else 'FAILED'}  ({title})")
        time.sleep(15)  # space out to dodge burst limit


if __name__ == "__main__":
    main()
