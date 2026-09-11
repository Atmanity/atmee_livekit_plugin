"""Generate the arc42 diagrams as inline SVG (currentColor strokes, CSS-token fills)
and splice them into the HTML pages; also emit standalone SVG files for Markdown.

Usage:
  python3 gen_svg.py page.html [more.html ...]       # replace the <svg> blocks in place
  python3 gen_svg.py --standalone docs/diagrams       # write context/session/creation/deployment.svg
"""
import re, sys, pathlib

FS = 12.5

def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def text(x, y, s, anchor="middle", cls="t", size=FS, weight=None):
    w = f' font-weight="{weight}"' if weight else ""
    return f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" class="{cls}" font-size="{size}"{w}>{esc(s)}</text>'

def multitext(x, y, lines, anchor="middle", cls="t", size=FS, lh=15, weight=None):
    y0 = y - (len(lines) - 1) * lh / 2
    return "\n".join(text(x, y0 + i * lh, l, anchor, cls, size, weight) for i, l in enumerate(lines))

def box(x, y, w, h, lines, cls="node", r=4, size=FS, weight=None):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" class="{cls}"/>\n'
            + multitext(x + w / 2, y + h / 2 + 4.5, lines, size=size, weight=weight))

def arrow(x1, y1, x2, y2, dashed=False, cls="edge"):
    d = ' stroke-dasharray="5 4"' if dashed else ""
    return f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" class="{cls}"{d} marker-end="url(#ah)"/>'

DEFS = ('<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 z" fill="currentColor"/></marker></defs>')

def svg(w, h, body, label):
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{esc(label)}" xmlns="http://www.w3.org/2000/svg" '
            f'font-family="IBM Plex Sans, system-ui, sans-serif" style="max-width:100%;height:auto;display:block">\n{DEFS}\n{body}\n</svg>')

# ---------------------------------------------------------------- sequence
def label_plate(cx, top, lines, size=12, lh=14, anchor="middle", x=None):
    """Text block with a surface-colored plate behind it so it never fights lines."""
    w = max(len(l) for l in lines) * size * 0.54 + 12
    h = lh * len(lines) + 6
    rx = (x - 4) if anchor == "start" else cx - w / 2
    out = [f'<rect x="{rx:.1f}" y="{top:.1f}" width="{w:.1f}" height="{h:.1f}" rx="3" class="plate"/>']
    for i, l in enumerate(lines):
        out.append(text(x if anchor == "start" else cx, top + 4 + size + i * lh - 1, l, anchor, "t", size))
    return "\n".join(out), h

def sequence(actors, steps, label, colw=290, left=120):
    """actors: [(key, [lines])]; steps: ('m', a, b, [lines], dashed) message,
    ('s', a, [lines]) self-message, ('L', label) loop start, ('E',) loop end, ('N', [lines]) note."""
    xs = {k: left + i * colw for i, (k, _) in enumerate(actors)}
    top, ah, aw = 8, 44, 190
    y = top + ah + 26
    parts, loop_stack = [], []
    for st in steps:
        kind = st[0]
        if kind == "m":
            _, a, b, lines, *rest = st
            dashed = bool(rest and rest[0])
            x1, x2 = xs[a], xs[b]
            plate, h = label_plate((x1 + x2) / 2, y, lines)
            ly = y + h + 9
            sign = 1 if x2 > x1 else -1
            parts.append(plate)
            parts.append(arrow(x1, ly, x2 - sign * 3, ly, dashed))
            y = ly + 24
        elif kind == "s":
            _, a, lines = st
            x = xs[a]
            plate, h = label_plate(None, y, lines, anchor="start", x=x + 22)
            ly = y + h / 2 + 3
            parts.append(f'<path d="M{x},{ly - 9} h 12 v 18 h -8" fill="none" class="edge" marker-end="url(#ah)"/>')
            parts.append(plate)
            y = y + h + 22
        elif kind == "L":
            loop_stack.append((y, st[1])); y += 30
        elif kind == "E":
            y0, lab = loop_stack.pop()
            x0, x1 = left - 80, left + (len(actors) - 1) * colw + 80
            parts.insert(0, f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y - y0}" rx="4" class="loop"/>')
            parts.insert(1, f'<rect x="{x0}" y="{y0}" width="{len(lab) * 7 + 18}" height="20" class="looptag"/>')
            parts.insert(2, text(x0 + 9, y0 + 14, lab, anchor="start", size=11.5, weight=600))
            y += 12
        elif kind == "N":
            lines = st[1]
            x0, x1 = left - 40, left + (len(actors) - 1) * colw + 40
            h = 14 * len(lines) + 16
            parts.append(f'<rect x="{x0}" y="{y}" width="{x1 - x0}" height="{h}" rx="3" class="note"/>')
            parts.append(multitext((x0 + x1) / 2, y + 8 + 12, lines, size=12, lh=14))
            y += h + 14
    height = y + 4
    head = [f'<line x1="{xs[k]}" y1="{top + ah}" x2="{xs[k]}" y2="{height - 4}" class="life"/>' for k, _ in actors]
    head += [box(xs[k] - aw / 2, top, aw, ah, lines, cls="actor", size=12.5, weight=600) for k, lines in actors]
    width = left + (len(actors) - 1) * colw + left
    return svg(width, height, "\n".join(head + parts), label)

# ---------------------------------------------------------------- context
def context():
    b = []
    b.append('<rect x="8" y="8" width="420" height="432" rx="6" class="group"/>')
    b.append(text(20, 28, "DEVELOPER", anchor="start", size=11, weight=600, cls="tm"))
    b.append('<rect x="470" y="8" width="430" height="432" rx="6" class="group"/>')
    b.append(text(482, 28, "ATMEE", anchor="start", size=11, weight=600, cls="tm"))
    b.append(box(40, 48, 150, 50, ["Agent worker", "(livekit-agents)"]))
    b.append(box(40, 150, 150, 50, ["livekit.plugins.atmee", "AvatarSession"], cls="node new"))
    b.append(box(40, 302, 150, 50, ["Browser"]))
    b.append('<ellipse cx="322" cy="328" rx="82" ry="42" class="node"/>' + multitext(322, 332, ["Developer's", "LiveKit room"]))
    b.append(box(500, 150, 170, 50, ["session_service", "api.atmanity.us"]))
    b.append(box(700, 150, 180, 50, ["Supabase", "avatars · sessions · billing"], cls="node db"))
    b.append(box(500, 300, 150, 44, ["Envoy"]))
    b.append(box(680, 300, 200, 60, ["avatar_service GPU pod", "POST /lk_avatar"]))
    b.append(arrow(115, 98, 115, 146))
    b.append(text(123, 127, "drives", anchor="start", size=11.5, cls="tm"))
    b.append(arrow(190, 175, 496, 175))
    b.append(text(343, 160, "create · avatar_sessions · end", size=11.5, cls="tm"))
    b.append(text(343, 194, "[X-Api-Key]", size=11.5, cls="tm"))
    b.append(arrow(670, 175, 696, 175))
    b.append(multitext(683, 224, ["reserve", "tick", "finalize"], size=11, cls="tm", lh=12))
    b.append(arrow(585, 200, 585, 296))
    b.append(multitext(593, 246, ["POST /lk_avatar (in-cluster, unchanged)", "SSE start handshake only"], anchor="start", size=11.5, cls="tm", lh=14))
    b.append(arrow(650, 322, 676, 322))
    b.append(arrow(690, 360, 406, 336))
    b.append(multitext(560, 386, ["joins as atmee-avatar-agent", "publishes video + audio"], size=11.5, cls="tm", lh=14))
    b.append(arrow(150, 200, 266, 296))
    b.append(multitext(218, 232, ["PCM over", "lk.audio_stream"], anchor="start", size=11.5, cls="tm", lh=14))
    b.append(arrow(190, 327, 238, 328))
    b.append(text(214, 318, "WebRTC", size=11.5, cls="tm"))
    return svg(908, 448, "\n".join(b), "Context: the plugin talks to Atmee's API and to the developer's LiveKit room; the GPU worker joins that room.")

# ---------------------------------------------------------------- deployment
def deployment():
    b = []
    b.append('<rect x="8" y="8" width="300" height="290" rx="6" class="group"/>')
    b.append(text(20, 28, "DEVELOPER INFRASTRUCTURE", anchor="start", size=11, weight=600, cls="tm"))
    b.append(box(40, 48, 236, 54, ["Agent worker + plugin", "(anywhere Python runs)"]))
    b.append(box(40, 206, 236, 54, ["LiveKit Cloud or self-hosted", "(the developer's project)"]))
    b.append('<rect x="340" y="8" width="380" height="290" rx="6" class="group"/>')
    b.append(text(352, 28, "ATMEE · GKE us-central1 · us-east4 · us-west1", anchor="start", size=11, weight=600, cls="tm"))
    b.append(box(372, 48, 316, 54, ["session_service", "Argo CD · image tag from main"]))
    b.append(box(372, 130, 140, 54, ["Envoy", "/lk_avatar · 503 retry"]))
    b.append(box(548, 130, 140, 54, ["GPU pods", "1 GPU each · KEDA 2–10"]))
    b.append(box(372, 236, 316, 44, ["atmanity-agent CPU pool (Atmee's own product)"], cls="node dim"))
    b.append('<rect x="752" y="8" width="180" height="290" rx="6" class="group"/>')
    b.append(text(764, 28, "SUPABASE", anchor="start", size=11, weight=600, cls="tm"))
    b.append(box(776, 48, 132, 54, ["production", "← main (releases)"], cls="node db"))
    b.append(box(776, 130, 132, 54, ["staging", "← dev"], cls="node db"))
    b.append(arrow(276, 75, 368, 75))
    b.append(text(322, 66, "HTTPS", size=11, cls="tm"))
    b.append(arrow(442, 102, 442, 126))
    b.append(arrow(512, 157, 544, 157))
    b.append(arrow(618, 184, 280, 226))
    b.append(text(470, 226, "joins room (WebRTC)", size=11, cls="tm"))
    b.append(arrow(158, 102, 158, 202))
    b.append(text(166, 156, "audio stream", anchor="start", size=11, cls="tm"))
    b.append(arrow(688, 75, 772, 75))
    return svg(940, 306, "\n".join(b), "Deployment: developer infrastructure, Atmee's GKE clusters, and the two Supabase projects with their release branches.")

# ---------------------------------------------------------------- content
SESSION = sequence(
    [("P", ["Agent job", "+ plugin"]), ("S", ["session_service"]), ("G", ["GPU pod", "/lk_avatar"]), ("R", ["Developer's", "LiveKit room"])],
    [
        ("s", "P", ["mint LiveKit access token (JWT):", "kind=agent, identity=atmee-avatar-agent,", "lk.publish_on_behalf=agent"]),
        ("m", "P", "S", ["POST /v1/avatars/{avatarId}/avatar_sessions"]),
        ("s", "S", ["parse token → room, identity", "reserve session (billing_mode)", "load + presign avatar config"]),
        ("m", "S", "G", ["POST /lk_avatar {url, token, config}", "(unchanged)"]),
        ("m", "G", "S", ["SSE initializing"], True),
        ("m", "S", "P", ["202 {sessionId, avatarParticipantIdentity}"], True),
        ("s", "P", ["output.replace_audio_tail(DataStreamAudioOutput)"]),
        ("m", "G", "R", ["connect · publish", "avatar_video + avatar_audio"]),
        ("m", "G", "S", ["SSE avatar_joined · user_joined (agent seen)", "then the stream closes, as today"], True),
        ("m", "P", "R", ["wait for the avatar's video track"]),
        ("L", "conversation"),
        ("m", "P", "R", ["PCM over lk.audio_stream"]),
        ("m", "R", "G", ["data stream"]),
        ("m", "G", "R", ["synced video + audio"]),
        ("s", "S", ["metered tick (60 s) until /end or ceiling"]),
        ("E",),
        ("m", "P", "R", ["aclose(): remove_participant(avatar)", "(LiveKit base class, developer's credentials)"]),
        ("m", "P", "S", ["POST /v1/avatar_sessions/{id}/end → finalize billing"]),
        ("N", ["Ungraceful agent shutdown: the agent participant drops, the worker leaves on its own (publish-on-behalf rule);",
               "no /end arrives, so the session bills to its ceiling. GPU backstop: 3 h."]),
    ],
    "One avatar session from token minting to finalize; the plugin ends it, LiveKit participant semantics cover the rest.",
)

CREATION = sequence(
    [("D", ["Developer"]), ("S", ["session_service"]), ("DB", ["Supabase"])],
    [
        ("m", "D", "S", ["POST /v1/avatars", "(multipart image + name, no voice)"]),
        ("m", "S", "DB", ["avatar_import_create"]),
        ("m", "S", "DB", ["storage put ACCOUNT/AVATAR/source_image_*"]),
        ("m", "S", "DB", ["avatar_import_set_config (kind = render_only)"]),
        ("m", "S", "DB", ["avatar_import_set_default_config", "(render config + portrait)"]),
        ("m", "S", "D", ["201 {avatarId, status: ready, kind: render_only}"], True),
    ],
    "Avatar creation from a portrait: four writes, then a ready avatar.",
    colw=260,
)

ORDER = [("context", context), ("session", lambda: SESSION), ("creation", lambda: CREATION), ("deployment", deployment)]

STANDALONE_CSS = """<style>
  svg { color: #1b2a2e; }
  .t { fill: #1b2a2e; } .tm { fill: #5b6b70; }
  .node { fill: #ffffff; stroke: #1b2a2e; stroke-width: 1.2; }
  .node.new { fill: #fbeee2; stroke: #b4560e; }
  .node.db { fill: #e6f2f0; stroke: #3d8b84; }
  .node.dim { fill: #f5f8f7; stroke: #d5dedc; }
  .actor { fill: #e6f2f0; stroke: #3d8b84; stroke-width: 1.2; }
  .group { fill: #f5f8f7; stroke: #d5dedc; stroke-width: 1; }
  .edge { stroke: #1b2a2e; stroke-width: 1.3; fill: none; }
  .life { stroke: #d5dedc; stroke-width: 1; stroke-dasharray: 3 4; }
  .loop { fill: none; stroke: #2f5bea; stroke-width: 1; stroke-dasharray: 6 4; }
  .looptag { fill: #2f5bea; } .looptag + text { fill: #ffffff; }
  .plate { fill: #ffffff; } .note { fill: #fbeee2; stroke: #b4560e; stroke-width: 1; }
</style>"""

def splice(path):
    p = pathlib.Path(path); s = p.read_text()
    blocks = re.findall(r"<svg.*?</svg>", s, flags=re.S)
    assert len(blocks) == 4, f"{path}: expected 4 <svg> blocks, found {len(blocks)}"
    for blk, (_, fn) in zip(blocks, ORDER):
        s = s.replace(blk, fn(), 1)
    p.write_text(s); print("spliced", path)

def write_standalone(outdir):
    for name, fn in ORDER:
        body = fn().replace("<defs>", '<rect width="100%" height="100%" fill="#ffffff"/>\n' + STANDALONE_CSS + "\n<defs>", 1)
        pathlib.Path(outdir, name + ".svg").write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n")
    print("standalone svgs ->", outdir)

if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--standalone":
        write_standalone(args[1])
    else:
        for path in args:
            splice(path)
