import os, glob, re, html

QRIFY_BLOCK = '<!-- QRIFY_TRAFFIC_START -->\n<section class="qrify-tool" style="margin:32px 0;padding:18px 20px;border:1px solid #d8dee9;border-radius:12px;background:#f8fafc;color:#0f172a;font-family:system-ui,Arial,sans-serif;line-height:1.6">\n  <div style="font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:#64748b;margin-bottom:8px">Free tool for readers</div>\n  <p style="margin:0 0 10px"><strong>Make a QR code in seconds &mdash; no signup, no watermark.</strong> QRify is a free browser-based QR generator with custom colours, a logo overlay and SVG export. Everything runs locally in your browser, so your links and data never leave your device.</p>\n  <p style="margin:0;font-size:14px">\n    <a href="https://qrify.dynv6.net/" rel="noopener" style="color:#0369a1">Free QR code generator</a> &nbsp;&middot;&nbsp;\n    <a href="https://qrify.dynv6.net/wifi-qr-code" rel="noopener" style="color:#0369a1">WiFi QR code</a> &nbsp;&middot;&nbsp;\n    <a href="https://qrify.dynv6.net/vcard-qr-code" rel="noopener" style="color:#0369a1">vCard QR code</a> &nbsp;&middot;&nbsp;\n    <a href="https://qrify.dynv6.net/qr-code-with-logo" rel="noopener" style="color:#0369a1">QR code with logo</a> &nbsp;&middot;&nbsp;\n    <a href="https://qrify.dynv6.net/restaurant-menu-qr-code" rel="noopener" style="color:#0369a1">Restaurant menu QR</a> &nbsp;&middot;&nbsp;\n    <a href="https://usaimaker.github.io/free-qr-code-generator.html" rel="noopener" style="color:#0369a1">Full how-to guide</a>\n  </p>\n</section>\n<!-- QRIFY_TRAFFIC_END -->'

SCRIPTS = "scripts"

def format_body(md):
    out = []
    for line in md.splitlines():
        line = line.rstrip()
        if line.startswith("## "):
            out.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("### "):
            out.append(f"<h3>{html.escape(line[4:])}</h3>")
        elif line.strip() == "":
            continue
        else:
            out.append(f"<p>{html.escape(line)}</p>")
    return "\n".join(out)

def build():
    items = ""
    for f in sorted(glob.glob(os.path.join(SCRIPTS, "*.md"))):
        slug = os.path.splitext(os.path.basename(f))[0]
        with open(f, encoding="utf-8") as fh:
            raw = fh.read()
        title = slug
        hook = ""
        m = re.match(r"#\s*(.+)\n+(.+)", raw)
        if m:
            title = m.group(1).strip()
            hook = m.group(2).strip()
        body = re.sub(r"^#.*\n", "", raw, count=1)
        page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title></head>
<body style="font-family:system-ui,Arial;max-width:760px;margin:40px auto;padding:0 20px;line-height:1.7">
<a href="../index.html">&larr; All scripts</a><h1>{html.escape(title)}</h1>
<p style="font-style:italic;color:#666">"{html.escape(hook)}"</p>
<div>{format_body(body)}</div></body></html>"""
        with open(os.path.join(SCRIPTS, slug + ".html"), "w", encoding="utf-8") as pf:
            pf.write(page)
        items += f'<li><a href="scripts/{slug}.html">{html.escape(title)}</a></li>\n'
    index = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Faceless YouTube Scripts</title>
<meta name="description" content="Ready-to-record faceless scripts for AI automation channels."></head>
<body style="font-family:system-ui,Arial;max-width:760px;margin:40px auto;padding:0 20px;line-height:1.7">
<h1>Faceless YouTube Scripts</h1><p>Ready-to-record scripts for AI automation channels. <a href="feed.xml">RSS</a></p>
<h2>Scripts</h2><ul>{items}</ul></body></html>"""
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(index)
    xml = '<?xml version="1.0"?>\n<rss version="2.0"><channel><title>Faceless YouTube Scripts</title>'
    for f in sorted(glob.glob(os.path.join(SCRIPTS, "*.md"))):
        s = os.path.splitext(os.path.basename(f))[0]
        xml += f'<item><title>{html.escape(s)}</title><link>scripts/{s}.html</link></item>'
    xml += "</channel></rss>"
    with open("feed.xml", "w", encoding="utf-8") as f:
        f.write(xml)
    print("built", len(glob.glob(os.path.join(SCRIPTS, "*.md"))), "scripts")

if __name__ == "__main__":
    build()

# QRIFY_TRAFFIC_INJECT_START (idempotent, survives the daily rebuild)
if __name__ == "__main__":
    try:
        with open("index.html", encoding="utf-8") as _qf:
            _qh = _qf.read()
        if "QRIFY_TRAFFIC_START" not in _qh:
            if "</body>" in _qh:
                _qh = _qh.replace("</body>", QRIFY_BLOCK + "\n</body>", 1)
            else:
                _qh = _qh + QRIFY_BLOCK
            with open("index.html", "w", encoding="utf-8") as _qf:
                _qf.write(_qh)
    except Exception as _qe:
        print("qrify inject skipped:", _qe)
# QRIFY_TRAFFIC_INJECT_END
