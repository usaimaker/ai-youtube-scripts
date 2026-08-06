#!/usr/bin/env python3
"""Retroactively set a custom high-CTR thumbnail on already-published videos.

Used once to backfill the 4 live videos that were uploaded before the
thumbnail pipeline existed. Safe to re-run (idempotent). Requires the same
YouTube OAuth secrets as upload_youtube.py.
"""
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
        make_thumbnail(title, "thumbnail.png")
        try:
            ok = set_thumbnail(vid, "thumbnail.png", at)
            print(f"[retro] {vid}  thumbnail {'SET' if ok else 'FAILED'}  ({title})")
        except Exception as e:
            print(f"[retro] {vid}  ERROR: {e!r}")


if __name__ == "__main__":
    main()
