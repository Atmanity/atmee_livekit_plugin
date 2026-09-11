"""Convert the arc42 page (the HTML fragment or the standalone page) into
GitHub-flavoured Markdown. Structure-aware for exactly the elements the page uses.

Usage: python3 html2md.py docs/architecture.html docs/ARCHITECTURE.md
"""
import re, sys, html

DIAGRAMS = ["context", "session", "creation", "deployment"]

def inline(s):
    s = re.sub(r"<code>(.*?)</code>", lambda m: "`" + html.unescape(m.group(1)) + "`", s, flags=re.S)
    s = re.sub(r"<strong>(.*?)</strong>", r"**\1**", s, flags=re.S)
    s = re.sub(r"<em>(.*?)</em>", r"*\1*", s, flags=re.S)
    s = re.sub(r"<br\s*/?>", " ", s)
    s = re.sub(r'<span class="tag [^"]*">(.*?)</span>', r"`\1`", s, flags=re.S)
    s = re.sub(r"<span[^>]*>(.*?)</span>", r"\1", s, flags=re.S)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()

def cell(s):
    return inline(s).replace("|", "\\|")

def convert(src):
    out = []
    head = re.search(r"<h1>(.*?)</h1>", src, re.S).group(1)
    lede = re.search(r'<p class="lede">(.*?)</p>', src, re.S).group(1)
    meta = re.findall(r'<div class="meta">(.*?)</div>', src, re.S)[0]
    out.append(f"# {inline(head)}\n")
    out.append("arc42 architecture documentation\n")
    out.append(inline(lede) + "\n")
    out.append(" · ".join(inline(m) for m in re.findall(r"<span>(.*?)</span>", meta, re.S)) + "\n")
    out.append("The same document is published as a Claude artifact (HTML, both themes) and as `docs/architecture.html` in this repo.\n")
    fig_i = 0
    for num, body in re.findall(r'<section id="s(\d+)">(.*?)</section>', src, re.S):
        title = inline(re.search(r"<h2>(.*?)</h2>", body, re.S).group(1))
        out.append(f"\n## {num}. {title}\n")
        tokens = re.finditer(
            r'(?P<h3><h3>.*?</h3>)|(?P<p><p>.*?</p>)|(?P<pre><pre><code>.*?</code></pre>)|(?P<table><div class="tablewrap"><table>.*?</table></div>)'
            r'|(?P<ul><ul>.*?</ul>)|(?P<fig><figure>.*?</figure>)|(?P<callout><div class="callout">.*?</div>)|(?P<dl><dl class="gloss">.*?</dl>)',
            body, re.S)
        for m in tokens:
            kind, frag = m.lastgroup, m.group(0)
            if kind == "h3":
                out.append(f"\n### {inline(frag)}\n")
            elif kind == "p":
                out.append(inline(frag) + "\n")
            elif kind == "pre":
                code = html.unescape(re.sub(r"</?(pre|code)>", "", frag))
                out.append("```python\n" + code.strip("\n") + "\n```\n")
            elif kind == "table":
                rows = re.findall(r"<tr>(.*?)</tr>", frag, re.S)
                hdr = re.findall(r"<th>(.*?)</th>", rows[0], re.S)
                out.append("| " + " | ".join(cell(h) for h in hdr) + " |")
                out.append("|" + "---|" * len(hdr))
                for r in rows[1:]:
                    out.append("| " + " | ".join(cell(t) for t in re.findall(r"<td>(.*?)</td>", r, re.S)) + " |")
                out.append("")
            elif kind == "ul":
                out += ["- " + inline(li) for li in re.findall(r"<li>(.*?)</li>", frag, re.S)] + [""]
            elif kind == "fig":
                cap = inline(re.search(r"<figcaption>(.*?)</figcaption>", frag, re.S).group(1))
                name = DIAGRAMS[fig_i]; fig_i += 1
                out += [f"![{cap}](diagrams/{name}.svg)\n", f"*{cap}*\n"]
            elif kind == "callout":
                out.append("> " + inline(frag) + "\n")
            elif kind == "dl":
                out += [f"- **{inline(dt)}** — {inline(dd)}" for dt, dd in re.findall(r"<dt>(.*?)</dt><dd>(.*?)</dd>", frag, re.S)] + [""]
    return "\n".join(out).rstrip() + "\n"

if __name__ == "__main__":
    open(sys.argv[2], "w").write(convert(open(sys.argv[1]).read()))
    print("wrote", sys.argv[2])
