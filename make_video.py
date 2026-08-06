#!/usr/bin/env python3
"""
make_video.py — Faceless "lively" video generator for ai-youtube-scripts.

Reads a script markdown (default: newest scripts/*.md), turns it into a
cinematic 1080p faceless video that is VISUAL and ALIVE:

  * 1920x1080 "dynamic infographic" slides:
      - gentle Ken Burns motion (slow zoom 1.0->1.12, alternating in/out)
      - left: title + colour-dotted bullet points with keyword highlights
      - right: a drawn icon badge (ring + rays + centre glyph) so the frame
        is actually a PICTURE, not flat text
      - soft glow blobs on the background for depth + life
  * slide (left/right) transitions between chapters — livelier than fade
  * burned-in subtitles with outline + shadow + bottom lock (readable)
  * low-volume royalty-free ambient background music
  * animated channel intro (idea badge) + CTA outro (bell badge)

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
TRANSITION = 0.6          # slide transition seconds
MAX_ZOOM = 1.12           # gentle Ken Burns (alive, not static)

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
# Image rendering helpers
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


def _draw_glow(img, cx, cy, r, color, alpha=0.12):
    """Soft radial glow blob (depth + life)."""
    from PIL import Image, ImageDraw
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    steps = 34
    for i in range(steps):
        rr = r * (i + 1) / steps
        a = int(alpha * 255 * (1 - i / steps))
        if a <= 0:
            continue
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=color + (a,))
    base = img.convert("RGBA")
    base.alpha_composite(layer)
    img.paste(base.convert("RGB"))


def _split_sentences(body):
    sents = re.split(r'(?<=[.!?])\s+|\n+', body or "")
    out = []
    for s in sents:
        s = s.strip().strip('"').strip()
        if s:
            out.append(s)
    return out


def _draw_highlighted(draw, text, pos, font, hi_color):
    """Render a sentence word-by-word; quote-wrapped or Capitalised tool
    names get the accent colour so keywords pop (visual, not flat)."""
    x, y = pos
    for tok in re.split(r'(\s+)', text):
        if not tok:
            continue
        if tok.isspace():
            x += draw.textlength(tok, font=font)
            continue
        core = tok.strip('".,():')
        hi = (tok.startswith('"') and tok.endswith('"')) or \
             (len(core) >= 4 and core[0:1].isupper() and core.isalpha())
        col = hi_color if hi else TEXT
        # strip only outer quote markers; keep other punctuation readable
        display = tok.strip('"')
        draw.text((x, y), display, font=font, fill=col)
        x += draw.textlength(tok, font=font)


def _draw_icon_badge(draw, cx, cy, R, glyph, color):
    """A drawn 'picture' on the right side: ring + rays + centre glyph.
    glyph: 'num' (centre number, idx passed via color? no) | 'idea' | 'bell'."""
    from PIL import ImageDraw
    # rays
    for k in range(12):
        ang = 2 * math.pi * k / 12
        x1 = cx + (R + 16) * math.cos(ang)
        y1 = cy + (R + 16) * math.sin(ang)
        x2 = cx + (R + 52) * math.cos(ang)
        y2 = cy + (R + 52) * math.sin(ang)
        draw.line([x1, y1, x2, y2], fill=color, width=4)
    # rings
    draw.ellipse([cx - R, cy - R, cx + R, cy + R], outline=color, width=10)
    draw.ellipse([cx - R + 24, cy - R + 24, cx + R - 24, cy + R - 24],
                 outline=color, width=3)
    if glyph == "idea":
        # light bulb: dome + base lines
        br = R * 0.5
        draw.ellipse([cx - br, cy - br - 14, cx + br, cy + br - 14],
                     outline=WHITE, width=8)
        draw.line([cx - 26, cy + br - 6, cx + 26, cy + br - 6],
                  fill=WHITE, width=8)
        draw.line([cx - 16, cy + br + 14, cx + 16, cy + br + 14],
                  fill=WHITE, width=8)
    elif glyph == "bell":
        # bell: arc + clapper + top knob
        draw.arc([cx - R * 0.55, cy - R * 0.7, cx + R * 0.55, cy + R * 0.55],
                 20, 160, fill=WHITE, width=9)
        draw.line([cx, cy - R * 0.7, cx, cy - R * 0.92],
                  fill=WHITE, width=9)
        draw.ellipse([cx - 12, cy + R * 0.5, cx + 12, cy + R * 0.74],
                     fill=WHITE)
    elif glyph.isdigit():
        num_font = pick_font(140, bold=True)
        draw.text((cx, cy), glyph, font=num_font, fill=WHITE, anchor="mm")
    else:
        # default: play triangle
        draw.polygon([(cx - 40, cy - 56), (cx - 40, cy + 56),
                      (cx + 64, cy)], fill=WHITE)


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
    # soft glow blobs (depth + life)
    _draw_glow(img, 320, 260, 560, ACCENT, 0.12)
    _draw_glow(img, 1640, 860, 640, ACCENT2, 0.12)
    draw = ImageDraw.Draw(img)

    # top accent bar
    draw.rectangle([0, 0, W, 12], fill=ACCENT)
    draw.rectangle([0, 12, 460, 26], fill=ACCENT2)

    heading_font = pick_font(68, bold=True)
    body_font = pick_font(38)
    small_font = pick_font(30)

    if kind == "intro":
        brand_font = pick_font(104, bold=True)
        draw.text((120, 220), BRAND, font=brand_font, fill=WHITE)
        draw.rectangle([120, 360, 120 + 560, 372], fill=ACCENT)
        hlines = wrap_text(draw, heading, heading_font, 900)
        y = 450
        for hl in hlines[:2]:
            draw.text((120, y), hl, font=heading_font, fill=ACCENT)
            y += 84
        blines = wrap_text(draw, " ".join(body or []), body_font, 900)
        y += 16
        for bl in blines[:3]:
            draw.text((120, y), bl, font=body_font, fill=TEXT)
            y += 54
        draw.text((120, H - 96),
                  "Daily AI tools you can build for $0",
                  font=small_font, fill=MUTED)
        _draw_icon_badge(draw, 1540, 540, 210, "idea", ACCENT)
    elif kind == "outro":
        big_font = pick_font(120, bold=True)
        draw.text((W // 2, 250), "Subscribe", font=big_font, fill=WHITE,
                  anchor="mm")
        draw.rectangle([W // 2 - 170, 348, W // 2 + 170, 360], fill=ACCENT)
        blines = wrap_text(draw, " ".join(body or []), heading_font, W - 460)
        y = 450
        for bl in blines[:3]:
            draw.text((W // 2, y), bl, font=heading_font, fill=ACCENT,
                      anchor="mm")
            y += 88
        draw.text((W // 2, H - 120),
                  "Turn on notifications for one free AI workflow every day",
                  font=small_font, fill=MUTED, anchor="mm")
        _draw_icon_badge(draw, W // 2, 760, 96, "bell", ACCENT2)
    else:
        # chapter tag
        tag_font = pick_font(30, bold=True)
        draw.text((120, 92), f"STEP {idx:02d} / {total:02d}",
                  font=tag_font, fill=ACCENT2)
        # title
        hlines = wrap_text(draw, heading, heading_font, 860)
        y = 150
        for hl in hlines[:2]:
            draw.text((120, y), hl, font=heading_font, fill=WHITE)
            y += 82
        # bullets with highlights
        sentences = _split_sentences(" ".join(body or []))
        by = max(y + 34, 372)
        for i, sent in enumerate(sentences[:4]):
            cy = by + 18
            dot = ACCENT if i % 2 == 0 else ACCENT2
            draw.ellipse([132, cy - 13, 158, cy + 13], fill=dot)
            _draw_highlighted(draw, sent, (190, by), body_font, dot)
            by += 82
        # right: icon badge (the "picture")
        _draw_icon_badge(draw, 1480, 540, 220, str(idx), ACCENT)

    # footer brand
    draw.text((120, H - 64), BRAND, font=small_font, fill=(148, 163, 184))
    if kind == "slide":
        draw.text((W - 120, H - 64), f"{idx:02d} / {total:02d}",
                  font=small_font, fill=MUTED, anchor="ra")

    img.save(out_png)


def make_thumbnail(title, out_png, tw=1280, th=720):
    """Render a bold, high-CTR 1280x720 thumbnail (no auto-frame guessing)."""
    from PIL import Image, ImageDraw
    img = _vgradient(tw, th, BG_TOP, BG_BOT)
    _draw_glow(img, 220, 180, 360, ACCENT, 0.16)
    _draw_glow(img, 1120, 620, 380, ACCENT2, 0.16)
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
            f"ffmpeg failed: {' '.join(cmd[:8])}...\n{r.stderr[-1500:]}")
    return True


def make_clip(png, mp3, dur, out_mp4, motion):
    """Gentle Ken Burns: motion>0 zoom in (1.0->MAX_ZOOM), else zoom out."""
    n = max(2, int(round(dur * 30)))
    inc = (MAX_ZOOM - 1.0) / n
    if motion > 0:
        z_expr = f"min(zoom+{inc:.6f},{MAX_ZOOM:.3f})"
        z_start = 1.0
    else:
        z_expr = f"max(zoom-{inc:.6f},1.0)"
        z_start = MAX_ZOOM
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
        motion = 1 if ci % 2 == 0 else -1
        make_clip(png, mp3, dur, seg, motion)
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
    # video: slide (left/right) transition chain — livelier than fade
    vlabel = "0:v"
    for k in range(1, m):
        off = starts[k]
        trans = "slideleft" if k % 2 == 0 else "slideright"
        filt.append(
            f"[{vlabel}][{k}:v]xfade=transition={trans}:duration={TRANSITION}:"
            f"offset={off:.4f}[xv{k}]")
        vlabel = f"xv{k}"
    sub_style = ("FontSize=34,PrimaryColour=&HFFFFFF&,"
                "OutlineColour=&H10171F&,Outline=3,ShadowColour=&H000000&,"
                "Shadow=1,BackColour=&H40000000&,Bold=0,Alignment=2,MarginV=70")
    filt.append(f"[{vlabel}]subtitles={srt_path}:force_style='{sub_style}'[vsub]")
    filt.append(f"[vsub]scale={W}:{H},setsar=1[v]")
    # audio: delay each clip to its timeline position, then mix (aligned
    # with the video slide overlaps). Background music mixed underneath.
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
