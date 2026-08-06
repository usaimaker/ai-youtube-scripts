#!/usr/bin/env python3
"""
make_video.py — Faceless "perfect" video generator for ai-youtube-scripts.

Reads a script markdown (default: newest scripts/*.md), turns it into a
cinematic 1080p faceless video:

  * 1920x1080 branding slides (consistent palette, channel watermark, chapter index)
  * clean static branding slides (no busy zoom — matches the look the user
    preferred on the early videos)
  * crossfade transitions between chapters
  * burned-in subtitles synced to the real TTS word timeline (better retention)
  * low-volume royalty-free ambient background music
  * animated channel intro + CTA outro

Outputs output.mp4 + out.json (title/description/tags) for upload_youtube.py.

Runs locally (Windows, ffmpeg on G:) or in GitHub Actions (ubuntu-latest).
FFMPEG location comes from FFMPEG_BIN env, else "ffmpeg" on PATH.
"""
import os
import re
import sys
import json
import glob
import asyncio
import subprocess
import tempfile
import math
import wave
import struct

SCRIPTS_DIR = "scripts"
OUT_VIDEO = os.environ.get("OUT_VIDEO", "output.mp4")
FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN",
                         os.path.join(os.path.dirname(FFMPEG), "ffprobe") or "ffprobe")
VOICE = os.environ.get("TTS_VOICE", "en-US-AriaNeural")

W, H = 1920, 1080
TRANSITION = 0.5          # crossfade seconds
MAX_ZOOM = 1.0            # static slides (cleaner look per user preference)

# palette
BG_TOP = (15, 23, 42)      # slate-900
BG_BOT = (30, 41, 59)      # slate-800
ACCENT = (56, 189, 248)    # sky-400
ACCENT2 = (129, 140, 248)  # indigo-400
TEXT = (226, 232, 240)     # slate-200
MUTED = (100, 116, 139)    # slate-500
WHITE = (255, 255, 255)
BRAND = "Felix King"


# --------------------------------------------------------------------------- #
# Fonts
# --------------------------------------------------------------------------- #
def pick_font(size, bold=False):
    if sys.platform.startswith("win"):
        cands = [
            "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ]
    else:
        cands = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
    from PIL import ImageFont
    for c in cands:
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
    sections = []
    cur = None
    for line in lines[1:]:
        s = line.rstrip()
        if s.startswith("## "):
            if cur is not None:
                sections.append(cur)
            cur = {"heading": s[3:].strip(), "body": []}
        elif s.startswith("### "):
            if cur is None:
                cur = {"heading": title, "body": []}
            cur["body"].append(s.lstrip("#").strip())
        elif not s.strip():
            continue
        else:
            if cur is None:
                if s.lower().startswith("hook:"):
                    hook = s[5:].strip()
                else:
                    cur = {"heading": title, "body": []}
                    cur["body"].append(s)
            else:
                cur["body"].append(s)
    if cur is not None:
        sections.append(cur)
    cta = ""
    for sec in sections:
        if sec["heading"].lower() == "cta":
            cta = " ".join(sec["body"]).strip()
    sections = [s for s in sections if s["heading"].lower() != "cta"]
    return title, hook, sections, cta


def spoken_text(heading, body):
    parts = [heading] + (body or [])
    return " ".join(p for p in parts if p).strip()


# --------------------------------------------------------------------------- #
# Image rendering
# --------------------------------------------------------------------------- #
def _vgradient(w, h, top, bot):
    from PIL import Image
    base = Image.new("RGB", (w, h), top)
    px = base.load()
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(top[0] + (bot[0] - top[0]) * t)
        g = int(top[1] + (bot[1] - top[1]) * t)
        b = int(top[2] + (bot[2] - top[2]) * t)
        for x in range(w):
            px[x, y] = (r, g, b)
    return base


def wrap_text(draw, text, font, max_width):
    out = []
    for para in text.split("\n"):
        words = para.split()
        cur = ""
        for w in words:
            test = (cur + " " + w).strip()
            if draw.textlength(test, font=font) <= max_width:
                cur = test
            else:
                if cur:
                    out.append(cur)
                cur = w
        if cur:
            out.append(cur)
    return out


def render_slide(heading, body, idx, total, kind, out_png):
    from PIL import Image, ImageDraw
    img = _vgradient(W, H, BG_TOP, BG_BOT)
    draw = ImageDraw.Draw(img)

    # background faint giant index number (depth)
    if kind == "slide":
        big = pick_font(620, bold=True)
        draw.text((W - 70, 40), f"{idx:02d}", font=big,
                  fill=(255, 255, 255), anchor="ra", opacity=18)
    # accent bar top
    draw.rectangle([0, 0, W, 12], fill=ACCENT)
    draw.rectangle([0, 12, 320, 22], fill=ACCENT2)

    heading_font = pick_font(72, bold=True)
    body_font = pick_font(42)
    small_font = pick_font(30)

    if kind == "intro":
        # channel wordmark
        brand_font = pick_font(96, bold=True)
        draw.text((110, 250), BRAND, font=brand_font, fill=WHITE)
        draw.rectangle([110, 372, 110 + 540, 382], fill=ACCENT)
        hlines = wrap_text(draw, heading, heading_font, W - 220)
        y = 470
        for hl in hlines[:2]:
            draw.text((110, y), hl, font=heading_font, fill=ACCENT)
            y += 86
        blines = wrap_text(draw, " ".join(body or []), body_font, W - 220)
        y += 20
        for bl in blines[:3]:
            draw.text((110, y), bl, font=body_font, fill=TEXT)
            y += 56
        draw.text((110, H - 90),
                  "Daily AI tools you can build for $0",
                  font=small_font, fill=MUTED)
    elif kind == "outro":
        big_font = pick_font(120, bold=True)
        draw.text((W // 2, 300), "Subscribe", font=big_font, fill=WHITE,
                  anchor="mm")
        draw.rectangle([W // 2 - 160, 392, W // 2 + 160, 404], fill=ACCENT)
        blines = wrap_text(draw, " ".join(body or []), heading_font, W - 400)
        y = 470
        for bl in blines[:3]:
            draw.text((W // 2, y), bl, font=heading_font, fill=ACCENT,
                      anchor="mm")
            y += 90
        draw.text((W // 2, H - 110),
                  "🔔 Turn on notifications for one free AI workflow every day",
                  font=small_font, fill=MUTED, anchor="mm")
    else:
        hx, hy = 110, 120
        hlines = wrap_text(draw, heading, heading_font, W - 220)
        y = hy
        for hl in hlines[:2]:
            draw.text((hx, y), hl, font=heading_font, fill=ACCENT)
            y += 86
        body_txt = " ".join(body or [])
        blines = wrap_text(draw, body_txt, body_font, W - 220)
        by = max(y + 30, 420)
        for bl in blines[:9]:
            draw.text((hx, by), bl, font=body_font, fill=TEXT)
            by += 58

    # footer brand + chapter index
    if kind == "slide":
        draw.text((110, H - 70), BRAND, font=small_font, fill=(148, 163, 184))
        draw.text((W - 110, H - 70), f"{idx:02d} / {total:02d}",
                  font=small_font, fill=MUTED, anchor="ra")
    else:
        draw.text((110, H - 70), BRAND, font=small_font, fill=(148, 163, 184))

    img.save(out_png)


def make_thumbnail(title, out_png, tw=1280, th=720):
    """Render a bold, high-CTR 1280x720 thumbnail (no auto-frame guessing)."""
    from PIL import Image, ImageDraw
    img = _vgradient(tw, th, BG_TOP, BG_BOT)
    draw = ImageDraw.Draw(img)

    # left accent rail
    draw.rectangle([0, 0, 12, th], fill=ACCENT)
    # "100% FREE" badge (CTR hook)
    badge_font = pick_font(30, bold=True)
    draw.rounded_rectangle([44, 44, 250, 98], radius=14, fill=ACCENT)
    draw.text((147, 71), "100% FREE", font=badge_font,
              fill=(15, 23, 42), anchor="mm")
    # title (wrapped, big bold, max 3 lines)
    title_font = pick_font(72, bold=True)
    lines = wrap_text(draw, title, title_font, tw - 110)
    y = 175
    for ln in lines[:3]:
        draw.text((56, y), ln, font=title_font, fill=WHITE)
        y += 80
    # accent underline under first title line
    if lines:
        w = int(draw.textlength(lines[0], font=title_font))
        draw.rectangle([56, 247, 56 + w, 255], fill=ACCENT)
    # bottom brand
    small_font = pick_font(28)
    draw.text((56, th - 64), BRAND, font=small_font, fill=MUTED)
    # play-hint glyph (top-right)
    draw.ellipse([tw - 150, th - 150, tw - 74, th - 74], fill=ACCENT)
    draw.polygon([(tw - 122, th - 124), (tw - 122, th - 100),
                  (tw - 98, th - 112)], fill=(15, 23, 42))
    img.save(out_png)


# --------------------------------------------------------------------------- #
# TTS with word-level boundaries
# --------------------------------------------------------------------------- #
async def _tts_once(text, mp3_path):
    """Single edge-tts attempt. Raises on empty/error so callers can retry."""
    import edge_tts
    comm = edge_tts.Communicate(text, VOICE)
    bounds = []          # (offset_ticks, duration_ticks, text)
    audio = bytearray()
    async for ev in comm.stream():
        if ev["type"] in ("WordBoundary", "SentenceBoundary"):
            bounds.append((ev["offset"], ev["duration"], ev["text"]))
        elif ev["type"] == "audio":
            audio.extend(ev["data"])
    if not audio:
        raise RuntimeError("edge-tts returned empty audio")
    with open(mp3_path, "wb") as fh:
        fh.write(audio)
    return bounds


def _write_silent_mp3(mp3_path, duration_s=6.0):
    """Last-resort fallback so a TTS outage never hangs/kills the pipeline."""
    subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-i",
                    f"anullsrc=r=24000:cl=mono", "-t", str(duration_s),
                    "-c:a", "libmp3lame", "-q:a", "5", mp3_path],
                   capture_output=True)


async def tts_with_boundaries(text, mp3_path, retries=3, timeout=60):
    """TTS with hard timeout + retry. Returns boundary list, or [] on total
    failure (after writing a silent MP3 so downstream steps still run)."""
    last = None
    for attempt in range(1, retries + 1):
        try:
            return await asyncio.wait_for(_tts_once(text, mp3_path),
                                          timeout=timeout)
        except Exception as e:          # timeout or network error
            last = e
            print(f"[tts] attempt {attempt}/{retries} failed: {e!r}")
            if attempt < retries:
                await asyncio.sleep(2 * attempt)
    print(f"[tts] ALL {retries} attempts failed ({last!r}); "
          f"using silent fallback for this clip")
    _write_silent_mp3(mp3_path, duration_s=6.0)
    return []


def audio_duration(mp3):
    try:
        r = subprocess.run([FFPROBE, "-v", "error",
                            "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", mp3],
                           capture_output=True, text=True)
        return float(r.stdout.strip())
    except Exception:
        return 6.0


# --------------------------------------------------------------------------- #
# Background music (royalty-free, synthesized) — soft ambient pad
# --------------------------------------------------------------------------- #
def synth_bgmusic(path, duration, sr=22050):
    freqs = [110.0, 164.81, 220.0, 277.18]   # A2 E3 A3 C#4 (A major pad)
    frames = int(duration * sr)
    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        buf = bytearray()
        for i in range(frames):
            t = i / sr
            lfo = 0.7 + 0.3 * math.sin(2 * math.pi * 0.05 * t)
            s = 0.0
            for f in freqs:
                s += math.sin(2 * math.pi * f * t) * 0.25
            s *= lfo
            if t < 2.0:
                s *= t / 2.0
            if t > duration - 3.0:
                s *= max(0.0, (duration - t) / 3.0)
            s *= 0.16
            v = int(max(-1.0, min(1.0, s)) * 32767)
            buf += struct.pack("<h", v)
            if len(buf) >= 65536:
                w.writeframes(buf)
                buf = bytearray()
        if buf:
            w.writeframes(buf)


# --------------------------------------------------------------------------- #
# Subtitles (SRT) from word boundaries
# --------------------------------------------------------------------------- #
def srt_time(sec):
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def chunk_text(text, width=34):
    out, cur = [], ""
    for w in text.split():
        test = (cur + " " + w).strip()
        if len(test) <= width:
            cur = test
        else:
            if cur:
                out.append(cur)
            cur = w
    if cur:
        out.append(cur)
    return out


def build_srt(caps, srt_path):
    """caps: list of (gstart, gend, text) on the global timeline."""
    with open(srt_path, "w", encoding="utf-8") as fh:
        for i, (s, e, txt) in enumerate(caps, 1):
            if not txt or not txt.strip():
                continue
            fh.write(f"{i}\n{srt_time(s)} --> {srt_time(e)}\n{txt.strip()}\n\n")


# --------------------------------------------------------------------------- #
# ffmpeg
# --------------------------------------------------------------------------- #
def run_ffmpeg(args):
    cmd = [FFMPEG, "-y"] + args
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed: {' '.join(cmd[:8])}...\n{r.stderr[-1200:]}")
    return True


def make_clip(png, mp3, dur, out_mp4, zoom_dir):
    n = max(2, int(round(dur * 30)))
    inc = (MAX_ZOOM - 1.0) / n
    if zoom_dir > 0:
        z_expr = f"min(zoom+{inc:.6f},{MAX_ZOOM:.3f})"
    else:
        z_expr = f"max(zoom-{inc:.6f},1.0)"
    z_start = MAX_ZOOM if zoom_dir < 0 else 1.0
    vf = (f"zoompan=z='if(eq(on,1),{z_start},{z_expr})'"
          f":d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
          f":s={W}x{H}:fps=30,scale={W}:{H},setsar=1")
    run_ffmpeg([
        "-loop", "1", "-i", png,
        "-i", mp3,
        "-vf", vf,
        "-t", f"{dur:.3f}",
        "-r", "30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", out_mp4,
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
    print(f"[make_video] script: {script_path}")
    title, hook, sections, cta = parse_script(script_path)
    print(f"[make_video] title={title!r} sections={len(sections)} cta?{'Y' if cta else 'N'}")

    tmp = tempfile.mkdtemp(prefix="ytvid_", dir=".")
    music_path = "bgmusic.wav"
    srt_path = "subs.srt"

    # Build clip definitions: intro, sections, outro
    clips = []
    clips.append(("intro", title, hook, "intro"))
    for i, sec in enumerate(sections, 1):
        clips.append(("slide", sec["heading"], " ".join(sec["body"]), "slide", i, len(sections)))
    if cta:
        clips.append(("outro", "Subscribe", cta, "outro"))

    clip_files = []
    clip_durs = []
    caps = []          # global-timeline subtitle cues: (start, end, text)
    timeline = 0.0

    for ci, c in enumerate(clips):
        kind = c[3]
        if kind == "slide":
            _, heading, body, _, idx, total = c
        else:
            _, heading, body, _ = c
            idx = total = 0
        png = os.path.join(tmp, f"slide_{ci:02d}.png")
        mp3 = os.path.join(tmp, f"audio_{ci:02d}.mp3")
        seg = os.path.join(tmp, f"clip_{ci:02d}.mp4")
        spk = spoken_text(heading, [body])
        if not spk:
            spk = title
        print(f"[make_video] clip {ci}/{len(clips)-1} {kind}: {heading[:42]!r}")
        render_slide(heading, [body] if kind != "slide" else [body],
                     idx, total, kind, png)
        bounds = asyncio.run(tts_with_boundaries(spk, mp3))
        dur = audio_duration(mp3)
        if dur < 2.0:
            dur = 2.0
        zoom_dir = 1 if ci % 2 == 0 else -1
        make_clip(png, mp3, dur, seg, zoom_dir)
        clip_files.append(seg)
        clip_durs.append(dur)

        # subtitles for this clip
        if bounds:
            for (o, d, w) in bounds:
                gs = timeline + o / 1e7
                ge = gs + d / 1e7
                pieces = chunk_text(w, 38) or [w]
                n = len(pieces)
                if n == 1:
                    caps.append((gs, ge, w))
                else:
                    step = (ge - gs) / n
                    for i, ln in enumerate(pieces):
                        caps.append((gs + i * step, gs + (i + 1) * step, ln))
        else:
            lines = chunk_text(spk, 34) or [spk]
            n = len(lines)
            for i, ln in enumerate(lines):
                cs = timeline + i * dur / n
                ce = timeline + (i + 1) * dur / n
                caps.append((cs, ce, ln))

        timeline += dur - TRANSITION

    total_dur = max(0.1, sum(clip_durs) - TRANSITION * (len(clips) - 1))
    print(f"[make_video] total duration ~ {total_dur:.1f}s")

    build_srt(caps, srt_path)
    synth_bgmusic(music_path, total_dur + 1.0)

    # ---- build filter_complex ----
    m = len(clip_files)
    starts = [0.0]
    for k in range(1, m):
        starts.append(sum(clip_durs[:k]) - k * TRANSITION)

    filt = []
    # video: crossfade chain
    vlabel = "0:v"
    for k in range(1, m):
        off = starts[k]
        filt.append(
            f"[{vlabel}][{k}:v]xfade=transition=fade:duration={TRANSITION}:"
            f"offset={off:.4f}[xv{k}]")
        vlabel = f"xv{k}"
    filt.append(f"[{vlabel}]subtitles={srt_path}[vsub]")
    filt.append(f"[vsub]scale={W}:{H},setsar=1[v]")
    # audio: delay each clip to its timeline position, then mix (aligned
    # with the video crossfade overlaps). Background music mixed underneath.
    for k in range(m):
        ms = int(round(starts[k] * 1000))
        filt.append(f"[{k}:a]adelay=delays={ms}:all=1[a{k}]")
    audio_ins = "".join(f"[a{k}]" for k in range(m))
    filt.append(f"{audio_ins}amix=inputs={m}:duration=longest:"
                f"dropout_transition=0[speech]")
    filt.append(f"[{m}:a]volume=0.10[mus]")
    filt.append(f"[speech][mus]amix=inputs=2:duration=longest:"
                f"dropout_transition=0[a]")
    filter_complex = ";".join(filt)

    inputs = []
    for cf in clip_files:
        inputs += ["-i", cf]
    inputs += ["-i", music_path]

    run_ffmpeg(inputs + [
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        OUT_VIDEO,
    ])

    # custom CTR thumbnail (highest-leverage traffic fix)
    thumb_path = "thumbnail.png"
    make_thumbnail(title, thumb_path)
    print(f"[make_video] thumbnail -> {thumb_path}")

    # metadata
    def _fmt_ts(s):
        s = int(round(s))
        h, m = divmod(s, 3600)
        m, sec = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"

    chapters = "\n".join(
        f"{_fmt_ts(starts[k])} "
        f"{'Intro' if clips[k][3]=='intro' else ('Subscribe for a free AI workflow daily' if clips[k][3]=='outro' else clips[k][1])}"
        for k in range(len(clips))
    )
    slug = os.path.splitext(os.path.basename(script_path))[0]
    desc = (
        f"{hook}\n\n"
        f"{title} — an AI Nexus Daily faceless explainer.\n\n"
        f"Chapters:\n{chapters}\n\n"
        f"Subscribe for one practical, free AI workflow every single day.\n\n"
        f"#AI #Automation #FreeTools #Productivity #AItools #tutorial"
    )
    meta = {
        "slug": slug,
        "title": title,
        "description": desc,
        "tags": ["AI", "automation", "free tools", "productivity",
                 "AI Nexus Daily", "faceless", "tutorial", "AI tools",
                 "how to", "step by step"],
        "categoryId": "28",
        "video_file": OUT_VIDEO,
        "thumbnail": "thumbnail.png",
    }
    with open("out.json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)

    # cleanup intermediates (keep nothing large in repo)
    try:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        for f in (music_path, srt_path):
            if os.path.exists(f):
                os.remove(f)
    except Exception:
        pass

    print(f"[make_video] DONE -> {OUT_VIDEO}  slug={slug}")


if __name__ == "__main__":
    main()
