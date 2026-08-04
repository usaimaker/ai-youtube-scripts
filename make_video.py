#!/usr/bin/env python3
"""
make_video.py — Faceless slideshow video generator for ai-youtube-scripts.

Reads a script markdown file (default: newest scripts/*.md), turns each section
into a slide (PIL image) with English voiceover (edge-tts), then concatenates
everything into a single 1280x720 mp4. Also emits out.json (title/description/
tags) consumed by upload_youtube.py.

Designed to run both locally (Windows, ffmpeg on G:) and in GitHub Actions
(ubuntu-latest, ffmpeg installed via apt). FFMPEG location is taken from the
FFMPEG_BIN env var, else "ffmpeg" on PATH.
"""
import os
import re
import sys
import json
import glob
import asyncio
import subprocess
import tempfile

SCRIPTS_DIR = "scripts"
OUT_VIDEO = os.environ.get("OUT_VIDEO", "output.mp4")
FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")

VOICE = os.environ.get("TTS_VOICE", "en-US-AriaNeural")


# --------------------------------------------------------------------------- #
# Font handling (cross-platform)
# --------------------------------------------------------------------------- #
def pick_font(size):
    candidates = []
    if sys.platform.startswith("win"):
        candidates = [
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
        ]
    else:  # linux / mac
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
    from PIL import ImageFont
    for c in candidates:
        if os.path.exists(c):
            return ImageFont.truetype(c, size)
    return ImageFont.load_default()


# --------------------------------------------------------------------------- #
# Script parsing
# --------------------------------------------------------------------------- #
def parse_script(path):
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    lines = raw.splitlines()
    title = lines[0].lstrip("#").strip() if lines else "Untitled"
    hook = ""
    slides = []  # list of {"heading": str, "body": str}
    cur = None
    for line in lines[1:]:
        s = line.rstrip()
        if s.startswith("## "):
            if cur is not None:
                slides.append(cur)
            cur = {"heading": s[3:].strip(), "body": []}
        elif s.startswith("# ") or s.startswith("### "):
            # top-level title already handled; treat ### as body continuation
            if cur is None:
                cur = {"heading": title, "body": []}
            cur["body"].append(s.lstrip("#").strip())
        elif s.strip() == "":
            continue
        else:
            if cur is None:
                # hook line or leading prose
                if s.lower().startswith("hook:"):
                    hook = s[5:].strip()
                else:
                    cur = {"heading": title, "body": []}
                    cur["body"].append(s)
            else:
                cur["body"].append(s)
    if cur is not None:
        slides.append(cur)

    # Title card slide
    title_slide = {"heading": title, "body": [hook] if hook else []}
    return title, hook, [title_slide] + slides


def spoken_text(slide):
    parts = [slide["heading"]] + slide["body"]
    return " ".join(p for p in parts if p).strip()


# --------------------------------------------------------------------------- #
# Image rendering
# --------------------------------------------------------------------------- #
def wrap_text(draw, text, font, max_width):
    words = text.split()
    lines = []
    cur = ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=font) <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def render_slide(slide, idx, total, out_png):
    from PIL import Image, ImageDraw

    W, H = 1280, 720
    img = Image.new("RGB", (W, H), (15, 23, 42))  # slate-900
    draw = ImageDraw.Draw(img)

    # Accent bar top
    draw.rectangle([0, 0, W, 10], fill=(56, 189, 248))  # sky-400

    heading_font = pick_font(54)
    body_font = pick_font(34)
    small_font = pick_font(26)

    # Heading (accent color)
    hx0, hy0 = 80, 80
    hlines = wrap_text(draw, slide["heading"], heading_font, W - 160)
    y = hy0
    for hl in hlines[:3]:
        draw.text((hx0, y), hl, font=heading_font, fill=(56, 189, 248))
        y += 64

    # Body (light)
    body = " ".join(slide["body"])
    blines = wrap_text(draw, body, body_font, W - 160)
    by = max(y + 30, 260)
    for bl in blines[:9]:
        draw.text((hx0, by), bl, font=body_font, fill=(226, 232, 240))
        by += 46

    # Footer
    draw.text((80, H - 60), f"AI Nexus Daily  ·  {idx}/{total}",
              font=small_font, fill=(100, 116, 139))

    img.save(out_png)


# --------------------------------------------------------------------------- #
# TTS
# --------------------------------------------------------------------------- #
async def tts(text, out_mp3):
    import edge_tts
    comm = edge_tts.Communicate(text, VOICE)
    await comm.save(out_mp3)


# --------------------------------------------------------------------------- #
# ffmpeg helpers
# --------------------------------------------------------------------------- #
def run_ffmpeg(args):
    cmd = [FFMPEG, "-y"] + args
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {' '.join(cmd[:6])}...\n{r.stderr[-800:]}")
    return True


def make_segment(png, mp3, seg_mp4):
    # static image + audio, length follows audio
    run_ffmpeg([
        "-loop", "1", "-i", png,
        "-i", mp3,
        "-c:v", "libx264", "-tune", "stillimage",
        "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", seg_mp4,
    ])


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def newest_script():
    files = sorted(glob.glob(os.path.join(SCRIPTS_DIR, "*.md")))
    if not files:
        raise SystemExit("No scripts/*.md found")
    return files[-1]


def main():
    script_path = sys.argv[1] if len(sys.argv) > 1 else newest_script()
    print(f"[make_video] using script: {script_path}")
    title, hook, slides = parse_script(script_path)
    print(f"[make_video] title: {title!r}  slides: {len(slides)}")

    tmp = tempfile.mkdtemp(prefix="ytvid_")
    segs = []
    for i, slide in enumerate(slides, 1):
        png = os.path.join(tmp, f"slide_{i:02d}.png")
        mp3 = os.path.join(tmp, f"audio_{i:02d}.mp3")
        seg = os.path.join(tmp, f"seg_{i:02d}.mp4")
        spk = spoken_text(slide)
        if not spk:
            spk = title
        print(f"[make_video] slide {i}/{len(slides)}: {slide['heading'][:40]!r} ({len(spk)} chars)")
        render_slide(slide, i, len(slides), png)
        asyncio.run(tts(spk, mp3))
        make_segment(png, mp3, seg)
        segs.append(seg)

    # concat
    concat_txt = os.path.join(tmp, "concat.txt")
    with open(concat_txt, "w", encoding="utf-8") as fh:
        for s in segs:
            fh.write(f"file '{s}'\n")
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", concat_txt,
               "-c", "copy", OUT_VIDEO])

    # metadata for uploader
    slug = os.path.splitext(os.path.basename(script_path))[0]
    desc = (
        f"{hook}\n\n"
        f"This is an AI Nexus Daily faceless explainer. {title}.\n\n"
        f"Subscribe for one practical free AI workflow every single day.\n\n"
        f"#Shorts #AI #Automation #Productivity #FreeTools"
    )
    meta = {
        "slug": slug,
        "title": title,
        "description": desc,
        "tags": ["AI", "automation", "free tools", "productivity",
                 "AI Nexus Daily", "faceless", "tutorial"],
        "categoryId": "28",
        "video_file": OUT_VIDEO,
    }
    with open("out.json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)

    # report duration
    try:
        dur = subprocess.run([FFMPEG, "-i", OUT_VIDEO], capture_output=True,
                             text=True, stderr=subprocess.STDOUT)
        m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", dur.stdout)
        if m:
            print(f"[make_video] OK -> {OUT_VIDEO}  duration {m.group(0)}")
    except Exception:
        pass
    print(f"[make_video] metadata -> out.json  slug={slug}")


if __name__ == "__main__":
    main()
