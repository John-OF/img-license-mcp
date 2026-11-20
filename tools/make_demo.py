"""Generate assets/demo.svg: an animated, CSS-only demo for the README.

GitHub renders README images through <img>, which never runs JavaScript, so
every animation here is a CSS @keyframes rule. Search, WebP conversion and the
credit come from a real run on 2026-11-20 (Commons + Openverse, no API keys);
the upload step comes from the verified run against a live WordPress site.

Usage:  python tools/make_demo.py
"""

from __future__ import annotations

from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "assets" / "demo.svg"

W, H = 800, 510
D = 18.0  # loop length, seconds
CHAR = 7.8  # monospace advance at 13px (0.6em)

PROMPT = "Find a photo for my Kyoto post that I can legally use, and upload it"

CALLS = [
    ("search_images", 'query="Arashiyama bamboo forest"', "5 candidates · licence read at the source"),
    ("prepare_image", 'max_width=1600, format="webp"', "1920 px JPEG → 1600 px WebP · −46.5 %"),
    ("build_attribution", 'author="Basile Morin", "CC BY-SA 4.0"', "credit paragraph with 3 links"),
    ("upload_to_wordpress", "alt_text, caption, provenance", "media #608 · metadata written"),
]
CANDIDATES = [
    ("Bamboo Forest, Arashiyama", "CC BY-SA 4.0", "6558"),
    ("Bamboo Grove, Arashiyama", "CC BY-SA 4.0", "4051"),
    ("Sagano Bamboo forest", "CC BY-SA 4.0", "3837"),
]
BYTES_BEFORE, BYTES_AFTER = 1_010_870, 541_320
PROVENANCE = [
    "Source: commons.wikimedia.org/wiki/File:Bamboo_Fo…",
    "Author: Basile Morin",
    "Licence: CC BY-SA 4.0",
    "Original resolution: 6558x4372",
]

# Timeline (seconds).
TYPE_START, TYPE_END = 0.5, 2.8
CALL_START = [3.1, 5.3, 7.7, 9.9]
CALL_DONE = [4.3, 6.9, 8.9, 11.3]
SUMMARY_AT = 12.6
FADE_OUT = 17.2

css_rules: list[str] = []
_counter = 0


def pct(t: float) -> str:
    return f"{max(0.0, min(100.0, t / D * 100)):.3f}%"


def animate(base: str, frames: list[tuple[float, str]], timing: str = "ease-out") -> str:
    """Register a keyframes animation and return its class name.

    `base` is the style applied when animations are off (reduced motion): it
    must be the final, fully revealed state.
    """
    global _counter
    _counter += 1
    name = f"a{_counter}"
    steps = " ".join(f"{pct(t)}{{{style}}}" for t, style in frames)
    css_rules.append(f"@keyframes k{name}{{{steps}}}")
    css_rules.append(f".{name}{{{base};animation:k{name} {D}s {timing} infinite}}")
    return name


def fade_in(at: float, dur: float = 0.35, dx: float = 0) -> str:
    start = f"opacity:0;transform:translateX({dx}px)" if dx else "opacity:0"
    end = "opacity:1;transform:translateX(0)" if dx else "opacity:1"
    return animate(end, [(0, start), (at, start), (at + dur, end), (D, end)])


def visible_between(start: float, end: float) -> str:
    off, on = "opacity:0", "opacity:1"
    return animate(off, [(0, off), (start, off), (start + 0.1, on), (end, on), (end + 0.1, off), (D, off)])


def grow(at: float, dur: float = 0.6) -> str:
    off, on = "transform:scaleX(0)", "transform:scaleX(1)"
    return animate(on, [(0, off), (at, off), (at + dur, on), (D, on)])


def draw(at: float, length: float, dur: float = 0.3) -> str:
    off, on = f"stroke-dashoffset:{length}", "stroke-dashoffset:0"
    return animate(on, [(0, off), (at, off), (at + dur, on), (D, on)])


def typing(start: float, end: float, chars: int) -> str:
    dist = chars * CHAR
    hidden, shown = "transform:translateX(0)", f"transform:translateX({dist:.1f}px)"
    frames = [(0, hidden), (start, f"{hidden};animation-timing-function:steps({chars},end)"), (end, shown), (D, shown)]
    return animate(shown, frames, timing="linear")


def text(x: float, y: float, value: str, cls: str, extra: str = "") -> str:
    return f'<text x="{x}" y="{y}" class="{cls}"{extra}>{escape(value)}</text>'


def build() -> str:
    parts: list[str] = []
    add = parts.append

    # Frame and prompt.
    add(f'<rect x="1" y="1" width="{W - 2}" height="{H - 2}" rx="10" class="frame"/>')
    add('<rect x="24" y="20" width="752" height="34" rx="17" class="pill"/>')
    add(text(44, 42, "›", "accent b"))
    add(text(58, 42, PROMPT, "ink t13"))
    cover = typing(TYPE_START, TYPE_END, len(PROMPT))
    caret = visible_between(0.2, TYPE_END + 0.6)
    add(f'<g class="{cover}"><rect x="58" y="27" width="{len(PROMPT) * CHAR + 4:.0f}" height="21" class="bgfill"/>'
        f'<rect x="58" y="29" width="7" height="16" class="accentfill {caret}"/></g>')

    # Left column: tool calls.
    for i, (name, args, result) in enumerate(CALLS):
        y = 96 + i * 88
        start, done = CALL_START[i], CALL_DONE[i]
        add(f'<g class="{fade_in(start, dx=-8)}">')
        add(f'<g class="{visible_between(start, done)}"><circle cx="36" cy="{y - 4}" r="5" class="spin"/></g>')
        add(f'<path d="M30 {y - 4} l4 5 l8 -9" class="tick {draw(done, 19)}"/>')
        add(text(52, y, name, "accent b t13"))
        add(text(52, y + 19, args, "mut t11"))
        add("</g>")
        add(text(52, y + 40, "→ " + result, f"ink t11 {fade_in(done)}"))

    add(f'<g class="{fade_in(SUMMARY_AT, dx=-8)}">')
    add(text(30, 462, "✓", "ok b t13"))
    add(text(52, 462, "Published with a verified licence", "ink b t13"))
    add(text(52, 482, "no licence, author or alt text was guessed", "mut t11"))
    add("</g>")

    # Right column: the results, visualised.
    add('<rect x="410" y="76" width="366" height="414" rx="6" class="card"/>')

    def label(y: float, value: str, at: float) -> None:
        add(text(428, y, value, f"mut cap {fade_in(at)}"))

    # 1) Candidates.
    label(100, "CANDIDATES · LICENCE READ AT SOURCE", CALL_DONE[0])
    for j, (title, licence, width) in enumerate(CANDIDATES):
        y, at = 124 + j * 21, CALL_DONE[0] + 0.15 * j
        best = j == 0
        add(f'<g class="{fade_in(at)}">')
        add(text(428, y, title, "accent b t11" if best else "ink t11"))
        add(text(626, y, licence, "mut t11"))
        add(text(758, y, width + " px", ("accent b" if best else "mut") + " t11", ' text-anchor="end"'))
        add("</g>")
    add(text(428, 190, "30 examined · 4 too small · 0 unreadable licence", f"mut t10 {fade_in(CALL_DONE[0] + 0.6)}"))

    # 2) WebP conversion.
    add('<line x1="428" y1="204" x2="758" y2="204" class="rule"/>')
    label(226, "WEBP · 1600 PX", CALL_DONE[1])
    add(text(758, 226, "−46.5 %", f"ok b t11 {fade_in(CALL_DONE[1] + 0.9)}", ' text-anchor="end"'))
    bars = [("JPEG 1920px", BYTES_BEFORE, "1.01 MB", "mutfill"), ("WebP 1600px", BYTES_AFTER, "541 KB", "accentfill")]
    for j, (name, size, size_label, fill) in enumerate(bars):
        y, at = 250 + j * 22, CALL_DONE[1] + 0.25 * j
        add(f'<g class="{fade_in(at)}">')
        add(text(428, y, name, "ink t11"))
        add(text(758, y, size_label, "mut t11", ' text-anchor="end"'))
        add("</g>")
        add(f'<rect x="522" y="{y - 8}" width="{size / BYTES_BEFORE * 170:.1f}" height="8" rx="1.5" '
            f'class="{fill} bar {grow(at + 0.1)}"/>')

    # 3) Credit paragraph, links in the accent colour.
    add('<line x1="428" y1="290" x2="758" y2="290" class="rule"/>')
    label(312, "CREDIT", CALL_DONE[2])
    add(f'<g class="{fade_in(CALL_DONE[2] + 0.1, dx=-6)}">')
    add('<text x="428" y="336" class="ink t11">Photo: <tspan class="accent link">Basile Morin</tspan>, '
        '<tspan class="accent link">CC BY-SA 4.0</tspan>,</text>')
    add('<text x="428" y="354" class="ink t11">via <tspan class="accent link">Wikimedia Commons</tspan></text>')
    add("</g>")

    # 4) WordPress media with its provenance block.
    add('<line x1="428" y1="370" x2="758" y2="370" class="rule"/>')
    label(392, "WORDPRESS · MEDIA #608 · DESCRIPTION", CALL_DONE[3])
    for j, line in enumerate(PROVENANCE):
        add(text(428, 414 + j * 18, line, f"ink t10 {fade_in(CALL_DONE[3] + 0.15 * j, dx=-6)}"))

    body = "\n".join(parts)
    root = animate("opacity:1", [(0, "opacity:0"), (0.3, "opacity:1"), (FADE_OUT, "opacity:1"),
                                 (FADE_OUT + 0.5, "opacity:0"), (D, "opacity:0")], timing="linear")

    style = "\n".join([
        "svg{--bg:#f2efe6;--soft:#e6e9f0;--ink:#1d2733;--mut:#47545f;--line:rgba(29,39,51,.16);"
        "--accent:#6247aa;--ok:#2e9457}",
        "@media (prefers-color-scheme:dark){svg{--bg:#0a1c2e;--soft:#15304a;--ink:#d9e8f4;--mut:#a3c2d8;"
        "--line:rgba(169,212,234,.18);--accent:#a48ef0;--ok:#5cc98a}}",
        "text{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace}",
        ".t13{font-size:13px}.t11{font-size:11px}.t10{font-size:10px}.b{font-weight:700}",
        ".cap{font-size:10px;letter-spacing:1.5px}.link{text-decoration:underline}",
        ".ink{fill:var(--ink)}.mut{fill:var(--mut)}.accent{fill:var(--accent)}.ok{fill:var(--ok)}",
        ".frame{fill:var(--bg);stroke:var(--line)}.bgfill{fill:var(--bg)}",
        ".pill{fill:var(--bg);stroke:var(--ink);stroke-width:1.4}",
        ".card{fill:var(--soft);fill-opacity:.45;stroke:var(--line)}",
        ".rule{stroke:var(--line)}",
        ".accentfill{fill:var(--accent)}.mutfill{fill:var(--mut);fill-opacity:.55}",
        ".bar{transform-box:fill-box;transform-origin:left center}",
        ".tick{fill:none;stroke:var(--ok);stroke-width:2;stroke-linecap:round;stroke-linejoin:round;"
        "stroke-dasharray:19}",
        ".spin{fill:none;stroke:var(--accent);stroke-width:1.6;stroke-dasharray:20 12;"
        "transform-box:fill-box;transform-origin:center;animation:spin .8s linear infinite}",
        "@keyframes spin{to{transform:rotate(360deg)}}",
        *css_rules,
        "@media (prefers-reduced-motion:reduce){*{animation:none!important}.spin{opacity:0}}",
    ])

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        'role="img" aria-labelledby="title desc">\n'
        "<title id=\"title\">Img-License MCP demo</title>\n"
        "<desc id=\"desc\">An agent calls search_images, prepare_image, build_attribution and "
        "upload_to_wordpress: it finds a CC BY-SA photo whose licence was read at the source, converts it "
        "to a 1600 px WebP 46.5 % lighter, builds the credit line and uploads it with its provenance.</desc>\n"
        f"<style>\n{style}\n</style>\n"
        f'<g class="{root}">\n{body}\n</g>\n</svg>\n'
    )


if __name__ == "__main__":
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(build(), encoding="utf-8")
    print(f"wrote {OUT.relative_to(OUT.parent.parent)} ({OUT.stat().st_size / 1024:.1f} KB)")
