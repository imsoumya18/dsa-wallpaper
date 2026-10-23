#!/usr/bin/env python3
"""
dsa-wallpaper — turn a markdown DSA problem sheet into a heatmap
that covers your desktop wallpaper. Solve a problem and its tile disappears,
revealing the photo underneath.

Tile rules
  size   : by difficulty (Hard > Medium > Easy)
  color  : red = Hard, yellow = Medium, green = Easy
  open   : solid tile
  solved : tile removed (photo shows through)
  solved + 📌 revision : tile at 50% opacity
  ❌      : ignored — treated as cleared
  labels : problem name only, cut off with … when it doesn't fit

Modes
  overlay   : tiles drawn in a transparent window above the wallpaper (works
              over video wallpapers like WallSpace) — needs overlay/Overlay.swift
  wallpaper : tiles composited over a photo and set as the desktop picture

Usage
  python3 dsa_wallpaper.py                 # render + apply
  python3 dsa_wallpaper.py --preview a.png # render only
  python3 dsa_wallpaper.py --reshuffle     # new random layout, then render
  python3 dsa_wallpaper.py --simulate unsolved   # one-off: as if nothing were solved
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
OUT_DIR = HERE / "out"

DIFFICULTY = {"🟢": "easy", "🟡": "medium", "🔴": "hard"}
ROW_RE = re.compile(r"^\|\s*`(\d+(?:\.\d+)+)`\s*\|")

DEFAULTS = {
    "sheet_path": "",
    "photo_path": "photo.jpg",
    "seed": 42,
    "resolution": "auto",          # "auto" or e.g. "3024x1964"
    "weights": {"easy": 1, "medium": 2, "hard": 4},   # tile AREA ratio
    "colors": {"easy": "#3FA35B", "medium": "#E3B23C", "hard": "#D9473F"},
    "gap_color": "#16181D",
    "gap_px": 2,
    "revision_opacity": 0.5,
    "labels": True,
    "legend": True,
    "legend_position": "notch",   # "notch" (top center) or "bottom-right"
    "mode": "overlay",            # "overlay" (floats above video wallpapers) or "wallpaper"
    "overlay_level_offset": -1,   # window level relative to desktop icons
    "poll_minutes": 3,            # how often a GitHub sheet is checked
    "progress_path": "",          # JSON source only: progress file next to the catalog
}


# ───────────────────────────── sheet parsing ─────────────────────────────

@dataclass
class Problem:
    pid: str
    name: str
    diff: str
    solved: bool = False
    skipped: bool = False
    revision: bool = False

    @property
    def state(self) -> str:
        if self.skipped:
            return "clear"
        if self.solved:
            return "revision" if self.revision else "clear"
        return "open"


def parse_sheet(text: str) -> list[Problem]:
    """Collect every table row that starts with a `x.y.z` id.

    A problem can appear in several rows (new + revision weeks); flags are
    OR-ed across all of its rows.
    """
    problems: dict[str, Problem] = {}
    for line in text.splitlines():
        if not ROW_RE.match(line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        pid = cells[0].strip("`")
        name, diff_icon = cells[1], cells[2]
        solved_cell, rev_cell = cells[-2], cells[-1]
        p = problems.setdefault(
            pid, Problem(pid, name, DIFFICULTY.get(diff_icon, "medium"))
        )
        p.solved |= "✅" in solved_cell
        p.skipped |= "❌" in solved_cell
        p.revision |= "✅" in rev_cell or "📌" in rev_cell
    return list(problems.values())


def parse_catalog(catalog_text: str, progress_text: str | None) -> list[Problem]:
    """JSON source: a catalog `[[id, name, "easy"|"medium"|"hard"], ...]` plus a
    progress map `{id: {"s": <solved date>, "flag": true}, ...}` — `s` means
    solved, `flag` means marked for revision."""
    prog = json.loads(progress_text) if progress_text else {}
    if isinstance(prog.get("progress"), dict):
        prog = prog["progress"]
    out = []
    for pid, name, diff in json.loads(catalog_text):
        x = prog.get(pid) or {}
        out.append(Problem(pid, name, diff if diff in ("easy", "medium", "hard") else "medium",
                           solved=bool(x.get("s")), revision=bool(x.get("flag"))))
    return out


def load_problems(cfg: dict) -> list[Problem]:
    if cfg["sheet_path"].endswith(".json"):
        return parse_catalog(cfg["_sheet_text"], cfg.get("_progress_text"))
    return parse_sheet(cfg["_sheet_text"])


# ───────────────────────────── sheet source ─────────────────────────────

SHEET_CACHE = OUT_DIR / "sheet.md"   # last copy fetched from GitHub (offline fallback)
GITHUB_BLOB = re.compile(r"https://github\.com/([^/]+)/([^/]+)/(?:blob|raw)/([^/]+)/(.+)")


def is_url(src: str) -> bool:
    return src.startswith(("http://", "https://"))


def curl(url: str, *headers: str) -> str | None:
    cmd = ["curl", "-fsSL", "--max-time", "20"]
    for h in headers:
        cmd += ["-H", h]
    try:
        r = subprocess.run(cmd + [url], capture_output=True, timeout=30)
        if r.returncode == 0 and r.stdout:
            return r.stdout.decode("utf-8")
        print(f"fetch failed ({url}): {r.stderr.decode().strip()}")
    except Exception as e:
        print(f"fetch failed ({url}): {e}")
    return None


def fetch_sheet(url: str) -> str:
    """Latest sheet from GitHub. A github.com/<user>/<repo>/blob/<branch>/<path>
    link goes through the GitHub API first (always fresh), then the raw file
    (can lag a few minutes behind a push). Falls back to the cached copy."""
    text = None
    m = GITHUB_BLOB.match(url)
    if m:
        owner, repo, ref, path = m.groups()
        text = curl(f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={ref}",
                    "Accept: application/vnd.github.raw")
        if text is None:
            text = curl(f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}?t={int(time.time())}")
    else:
        text = curl(url)
    if text is not None:
        OUT_DIR.mkdir(exist_ok=True)
        SHEET_CACHE.write_text(text, encoding="utf-8")
        return text
    if SHEET_CACHE.exists():
        print("offline — using the last fetched copy")
        return SHEET_CACHE.read_text(encoding="utf-8")
    sys.exit("Could not fetch the sheet and there is no cached copy yet.")


def load_sheet(src: str) -> str:
    return fetch_sheet(src) if is_url(src) else resolve(src).read_text(encoding="utf-8")


# ───────────────────────────── layout ─────────────────────────────

def scatter_layout(items: list[Problem], weights: dict, seed: int, w: int, h: int):
    """Shuffle (seeded, so stable), then recursively split the screen in
    proportion to tile weight. Every pixel is covered, sizes stay mixed."""
    order = sorted(items, key=lambda p: [int(x) for x in p.pid.split(".")])
    random.Random(seed).shuffle(order)
    wt = [weights[p.diff] for p in order]
    rects: list[tuple[Problem, tuple[int, int, int, int]]] = []

    def split(lo: int, hi: int, x: float, y: float, rw: float, rh: float):
        if hi - lo == 1:
            rects.append((order[lo], (round(x), round(y), round(x + rw), round(y + rh))))
            return
        total = sum(wt[lo:hi])
        acc, best, best_d = 0.0, lo + 1, float("inf")
        for i in range(lo, hi - 1):
            acc += wt[i]
            d = abs(acc - total / 2)
            if d < best_d:
                best, best_d = i + 1, d
        frac = sum(wt[lo:best]) / total
        if rw >= rh:
            split(lo, best, x, y, rw * frac, rh)
            split(best, hi, x + rw * frac, y, rw * (1 - frac), rh)
        else:
            split(lo, best, x, y, rw, rh * frac)
            split(best, hi, x, y + rh * frac, rw, rh * (1 - frac))

    split(0, len(order), 0, 0, w, h)
    return rects


# ───────────────────────────── rendering ─────────────────────────────

SF_PRO = "/System/Library/Fonts/SFNS.ttf"   # SF Pro, the macOS system font
FALLBACK_FONTS = [
    ("/System/Library/Fonts/HelveticaNeue.ttc", 1),
    ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
]
EMOJI_FONT = "/System/Library/Fonts/Apple Color Emoji.ttc"


@lru_cache(maxsize=None)
def font(size: int, weight: str = "Semibold") -> ImageFont.FreeTypeFont:
    """SF Pro at the given weight (it's a variable font); falls back to
    other bold sans fonts when SF Pro isn't available."""
    if Path(SF_PRO).exists():
        try:
            f = ImageFont.truetype(SF_PRO, size)
            names = [n.decode() if isinstance(n, bytes) else n for n in f.get_variation_names()]
            for w in (weight, "Semibold", "Bold"):
                if w in names:
                    f.set_variation_by_name(w)
                    break
            return f
        except Exception:
            pass
    for path, idx in FALLBACK_FONTS:
        if Path(path).exists():
            return ImageFont.truetype(path, size, index=idx)
    return ImageFont.load_default(size)


@lru_cache(maxsize=None)
def emoji(char: str, px: int) -> Image.Image | None:
    """A color emoji as an RGBA image px tall. Apple Color Emoji only has fixed
    bitmap sizes, so draw at one of those and scale down."""
    if not Path(EMOJI_FONT).exists():
        return None
    for size in (160, 96, 64, 48):
        try:
            f = ImageFont.truetype(EMOJI_FONT, size)
            im = Image.new("RGBA", (size * 2, size * 2), (0, 0, 0, 0))
            ImageDraw.Draw(im).text((size // 2, size // 2), char, font=f, embedded_color=True)
            box = im.getbbox()
            if not box:
                continue
            im = im.crop(box)
            return im.resize((round(im.width * px / im.height), px), Image.LANCZOS)
        except Exception:
            continue
    return None


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def ellipsize(text: str, f, max_w: int) -> str:
    if f.getlength(text) <= max_w:
        return text
    while text and f.getlength(text + "…") > max_w:
        text = text[:-1]
    return text.rstrip() + "…"


def wrap(text: str, f, max_w: int, max_lines: int) -> tuple[list[str], bool]:
    """Greedy word wrap. Returns (lines, truncated)."""
    lines, cur = [], ""
    for wd in text.split():
        trial = f"{cur} {wd}".strip()
        if f.getlength(trial) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    truncated = len(lines) > max_lines
    if truncated:
        lines = lines[:max_lines]
        lines[-1] = lines[-1] + " …"
    out = [ellipsize(ln, f, max_w) for ln in lines]
    truncated |= out != lines
    return out, truncated


def draw_label(d: ImageDraw.ImageDraw, p: Problem, box, ink, alpha: int, scale: float):
    """Problem name only, centered. Shrinks to fit; if it still doesn't fit at
    the minimum size, it is cut off with an ellipsis."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    pad = max(2, int(min(w, h) * 0.06))
    max_w, max_h = w - 2 * pad, h - 2 * pad
    min_px = int(9 * scale)
    start = max(min_px, min(int(22 * scale), int(min(w, h) * 0.22)))

    chosen = None
    for size in range(start, min_px - 1, -1):
        f = font(size)
        lh = int(size * 1.15)
        max_lines = max_h // lh
        if max_lines < 1:
            continue
        lines, truncated = wrap(p.name, f, max_w, max_lines)
        chosen = (f, lh, lines)
        if not truncated:
            break
    if not chosen:
        return
    f, lh, lines = chosen
    y = y0 + (h - lh * len(lines)) / 2 + (lh - f.size) / 2
    for ln in lines:
        d.text((x0 + w / 2, y), ln, font=f, fill=(*ink, alpha), anchor="ma")
        y += lh


def cover(photo: Image.Image, w: int, h: int) -> Image.Image:
    sw, sh = photo.size
    s = max(w / sw, h / sh)
    photo = photo.resize((max(w, round(sw * s)), max(h, round(sh * s))), Image.LANCZOS)
    l, t = (photo.width - w) // 2, (photo.height - h) // 2
    return photo.crop((l, t, l + w, t + h))


def placeholder_photo(w: int, h: int, seed: int = 7) -> Image.Image:
    """Procedural mountain dusk, used until you set photo_path."""
    rnd = random.Random(seed)
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    top, mid, low = (24, 30, 72), (196, 98, 120), (250, 186, 120)
    for y in range(h):
        t = y / h
        a, b, k = (top, mid, t / 0.55) if t < 0.55 else (mid, low, (t - 0.55) / 0.45)
        d.line([(0, y), (w, y)], fill=tuple(int(a[i] + (b[i] - a[i]) * min(k, 1)) for i in range(3)))
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    cx, cy, r = int(w * 0.62), int(h * 0.58), int(h * 0.09)
    gd.ellipse((cx - r * 3, cy - r * 3, cx + r * 3, cy + r * 3), fill=(255, 200, 150, 70))
    gd.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 236, 200, 255))
    img = Image.alpha_composite(img.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(r * 0.6)))
    d = ImageDraw.Draw(img)
    layers = [((92, 62, 104), 0.50, 0.20), ((62, 42, 84), 0.62, 0.16),
              ((38, 28, 58), 0.74, 0.12), ((20, 16, 34), 0.86, 0.08)]
    for color, base, amp in layers:
        n = 2 ** 7
        pts = [0.0] * (n + 1)
        step, rough = n, amp
        while step > 1:
            half = step // 2
            for i in range(half, n, step):
                pts[i] = (pts[i - half] + pts[i + half]) / 2 + rnd.uniform(-rough, rough)
            step, rough = half, rough * 0.55
        poly = [(i * w / n, h * (base - pts[i])) for i in range(n + 1)]
        d.polygon(poly + [(w, h), (0, h)], fill=color)
    return img.convert("RGB")


def resolve(p: str) -> Path:
    """Absolute, ~-expanded, or relative to this repo folder."""
    path = Path(p).expanduser()
    return path if path.is_absolute() else HERE / path


@dataclass
class Screen:
    """Main display, in pixels. Insets are the menu bar / Dock strips."""
    w: int
    h: int
    scale: float
    top: int = 0
    bottom: int = 0
    left: int = 0
    right: int = 0


SCREEN_JSON = OUT_DIR / "screen.json"


def detect_screen() -> Screen:
    """Prefer out/screen.json (written by the overlay app straight from macOS),
    then system_profiler, then a 14" MacBook Pro default."""
    try:
        s = json.loads(SCREEN_JSON.read_text())
        return Screen(round(s["width"]), round(s["height"]), float(s["scale"]),
                      round(s["inset_top"]), round(s["inset_bottom"]),
                      round(s["inset_left"]), round(s["inset_right"]))
    except Exception:
        pass
    try:
        out = subprocess.run(["system_profiler", "SPDisplaysDataType", "-json"],
                             capture_output=True, text=True, timeout=20).stdout
        best = None
        for gpu in json.loads(out).get("SPDisplaysDataType", []):
            for disp in gpu.get("spdisplays_ndrvs", []):
                raw = disp.get("_spdisplays_pixels") or disp.get("_spdisplays_resolution", "")
                m = re.search(r"(\d+)\s*x\s*(\d+)", raw)
                if m:
                    wh = (int(m.group(1)), int(m.group(2)))
                    if disp.get("spdisplays_main") == "spdisplays_yes":
                        best = wh
                        break
                    best = best or wh
        if best:
            return Screen(*best, scale=2.0, top=round(37 * 2))
    except Exception:
        pass
    return Screen(3024, 1964, 2.0, top=74)


def render(cfg: dict, scr: Screen, transparent: bool = False) -> tuple[Image.Image, dict]:
    """transparent=True → tiles only on a clear background (overlay mode)."""
    w, h, scale = scr.w, scr.h, scr.scale
    problems = load_problems(cfg)
    if not problems:
        sys.exit(f"No problems found in {cfg['sheet_path']}")
    if cfg.get("simulate") == "unsolved":
        for p in problems:
            p.solved = p.skipped = p.revision = False

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    gap = max(1, round(cfg["gap_px"] * scale / 2))  # half-gap per side
    gap_rgb = hex_rgb(cfg["gap_color"])
    rev_a = int(255 * cfg["revision_opacity"])

    # tiles fill the usable area: below the menu bar, above a pinned Dock
    area_w, area_h = w - scr.left - scr.right, h - scr.top - scr.bottom
    stats = {"open": 0, "revision": 0, "clear": 0,
             "by_diff": {d: [0, 0] for d in ("easy", "medium", "hard")}}  # [solved, total]
    for p, (x0, y0, x1, y1) in scatter_layout(problems, cfg["weights"], cfg["seed"], area_w, area_h):
        x0, x1, y0, y1 = x0 + scr.left, x1 + scr.left, y0 + scr.top, y1 + scr.top
        st = p.state
        stats[st] += 1
        stats["by_diff"][p.diff][1] += 1
        if st != "open":
            stats["by_diff"][p.diff][0] += 1
        if st == "clear":
            continue
        a = 255 if st == "open" else rev_a
        rgb = hex_rgb(cfg["colors"][p.diff])
        d.rectangle((x0, y0, x1 - 1, y1 - 1), fill=(*gap_rgb, a))
        inner = (x0 + gap, y0 + gap, x1 - gap, y1 - gap)
        d.rectangle((inner[0], inner[1], inner[2] - 1, inner[3] - 1), fill=(*rgb, a))
        if cfg["labels"]:
            ink = (40, 32, 10) if p.diff == "medium" else (255, 255, 255)
            draw_label(d, p, inner, ink, a, scale)

    if transparent:
        img = overlay
    else:
        photo_path = resolve(cfg["photo_path"]) if cfg.get("photo_path") else None
        if photo_path and photo_path.exists():
            base = cover(Image.open(photo_path).convert("RGB"), w, h)
        else:
            base = placeholder_photo(w, h)
        img = Image.alpha_composite(base.convert("RGBA"), overlay)
    if cfg["legend"]:
        draw_legend(img, stats, scr, cfg)
    return img, stats


DIFF_EMOJI = {"easy": "🟢", "medium": "🟡", "hard": "🔴"}


def draw_legend(img: Image.Image, stats: dict, scr: Screen, cfg: dict):
    """Total 349/455   🟢 127/131   🟡 149/188   🔴 73/136"""
    scale = scr.scale
    solved = stats["clear"] + stats["revision"]
    total = stats["clear"] + stats["revision"] + stats["open"]
    f_lbl = font(int(13 * scale), "Medium")
    f_num = font(int(14 * scale), "Bold")
    pad, gap, dot_gap = int(14 * scale), int(16 * scale), int(5 * scale)
    dot = int(12 * scale)
    height = int(30 * scale)

    # segments: (kind, payload, font)
    segs = [("text", "Total", f_lbl), ("space", int(6 * scale), None), ("text", f"{solved}/{total}", f_num)]
    for d in ("easy", "medium", "hard"):
        s_, t_ = stats["by_diff"][d]
        segs += [("space", gap, None), ("dot", d, None), ("space", dot_gap, None),
                 ("text", f"{s_}/{t_}", f_num)]

    def seg_w(kind, val, fn):
        if kind == "text":
            return fn.getlength(val)
        if kind == "space":
            return val
        return dot

    width = sum(seg_w(*sg) for sg in segs) + 2 * pad
    notch = cfg.get("legend_position") != "bottom-right"
    if notch:  # centered, hanging from the bottom of the menu bar / notch
        x0, y0 = (scr.w - width) / 2, scr.top
    else:
        x0 = scr.w - scr.right - width - int(16 * scale)
        y0 = scr.h - scr.bottom - height - int(16 * scale)

    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((x0, y0, x0 + width, y0 + height), radius=int(10 * scale),
                        fill=(0, 0, 0, 235) if notch else (14, 16, 20, 225),
                        corners=(not notch, not notch, True, True))
    x, cy = x0 + pad, y0 + height / 2
    for kind, val, fn in segs:
        if kind == "text":
            col = (170, 170, 175, 255) if fn is f_lbl else (245, 245, 247, 255)
            d.text((x, cy), val, font=fn, fill=col, anchor="lm")
        elif kind == "dot":
            em = emoji(DIFF_EMOJI[val], dot)
            if em is not None:
                layer.alpha_composite(em, (round(x), round(cy - em.height / 2)))
            else:
                d.ellipse((x, cy - dot / 2, x + dot, cy + dot / 2), fill=(*hex_rgb(cfg["colors"][val]), 255))
        x += seg_w(kind, val, fn)
    img.alpha_composite(layer)


# ───────────────────────────── wallpaper ─────────────────────────────

SET_WALLPAPER_JXA = r'''
ObjC.import("AppKit");
function run(argv) {
  var url = $.NSURL.fileURLWithPath(argv[0]);
  var ws = $.NSWorkspace.sharedWorkspace;
  var screens = $.NSScreen.screens;
  var n = 0;
  for (var i = 0; i < screens.count; i++) {
    if (ws.setDesktopImageURLForScreenOptionsError(url, screens.objectAtIndex(i), $({}), null)) n++;
  }
  return n + "/" + screens.count;
}
'''


def set_wallpaper(path: Path):
    """Use NSWorkspace (same API as System Settings). Unlike the System Events
    route it also replaces Aerial / dynamic wallpapers and needs no permission."""
    r = subprocess.run(["osascript", "-l", "JavaScript", "-e", SET_WALLPAPER_JXA, str(path)],
                       capture_output=True, text=True)
    result = r.stdout.strip()
    print(f"wallpaper set on {result or '?'} screens")
    if r.returncode != 0 or not result or result.startswith("0/"):
        print("NSWorkspace failed, falling back to System Events:", r.stderr.strip())
        script = (f'tell application "System Events" to tell every desktop '
                  f'to set picture to POSIX file "{path}"')
        subprocess.run(["osascript", "-e", script], check=True)


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    if CONFIG_PATH.exists():
        user = json.loads(CONFIG_PATH.read_text())
        for k, v in user.items():
            cfg[k] = {**cfg[k], **v} if isinstance(cfg.get(k), dict) else v
    return cfg


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preview", metavar="PNG", help="render to this file without applying it")
    ap.add_argument("--size", help="override resolution, e.g. 2940x1912")
    ap.add_argument("--sheet", help="override sheet_path")
    ap.add_argument("--reshuffle", action="store_true", help="pick a new random layout and save it")
    ap.add_argument("--simulate", choices=["unsolved"],
                    help="one-off look as if nothing were solved (next save of the sheet restores it)")
    args = ap.parse_args()

    cfg = load_config()
    cfg["simulate"] = args.simulate
    if args.sheet:
        cfg["sheet_path"] = args.sheet
    if not cfg["sheet_path"]:
        sys.exit("Set sheet_path in config.json (see config.example.json).")
    if args.reshuffle:
        cfg["seed"] = random.randrange(1_000_000)
        saved = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
        saved["seed"] = cfg["seed"]
        CONFIG_PATH.write_text(json.dumps(saved, indent=2, ensure_ascii=False) + "\n")

    res = args.size or cfg["resolution"]
    if res and res != "auto":
        w, h = (int(v) for v in res.lower().split("x"))
        scr = Screen(w, h, scale=2.0, top=74)
    else:
        scr = detect_screen()

    overlay_mode = cfg["mode"] == "overlay" and not args.preview
    cfg["_sheet_text"] = load_sheet(cfg["sheet_path"])
    if cfg.get("progress_path"):
        pp = resolve(cfg["progress_path"])
        cfg["_progress_text"] = pp.read_text(encoding="utf-8") if pp.exists() else None

    # polling a remote sheet runs this every few minutes: skip if nothing changed
    stamp = OUT_DIR / "last-render.txt"
    key = hashlib.sha256(json.dumps(
        [cfg, [scr.w, scr.h, scr.scale, scr.top, scr.bottom, scr.left, scr.right],
         Path(__file__).stat().st_mtime], sort_keys=True, default=str).encode()).hexdigest()
    if (overlay_mode and not args.reshuffle and (OUT_DIR / "overlay.png").exists()
            and stamp.exists() and stamp.read_text() == key):
        print("no changes")
        return

    img, stats = render(cfg, scr, transparent=overlay_mode)
    print(f"{scr.w}x{scr.h} @{scr.scale:g}x  insets t{scr.top} b{scr.bottom}  "
          f"done={stats['clear']} revision={stats['revision']} open={stats['open']}")

    if args.preview:
        img.convert("RGB").save(args.preview)
        return

    OUT_DIR.mkdir(exist_ok=True)
    if overlay_mode:
        # the overlay app polls this file; write atomically so it never reads half a PNG
        tmp = OUT_DIR / "overlay.tmp.png"
        img.save(tmp)
        tmp.replace(OUT_DIR / "overlay.png")
        stamp.write_text(key)
        return

    # macOS caches wallpapers by path, so write a fresh filename each time
    target = OUT_DIR / f"wallpaper-{int(time.time())}.png"
    img.convert("RGB").save(target)
    for old in OUT_DIR.glob("wallpaper-*.png"):
        if old != target:
            old.unlink(missing_ok=True)
    set_wallpaper(target)


if __name__ == "__main__":
    main()
