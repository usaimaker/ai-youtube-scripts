import os, glob, re, html

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