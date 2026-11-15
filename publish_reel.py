#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ستيك مان ريلز — نسخة مضغوطة، كل الوظائف، مجانية 100%.
المتطلبات: pip install requests pillow edge-tts imageio-ffmpeg
الأسرار: FB_PAGE_ID, FB_ACCESS_TOKEN  (اختياري: GEMINI_API_KEY/GROQ_API_KEY/OPENROUTER_API_KEY)
."""
import argparse, asyncio, json, math, os, re, shutil, subprocess, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, features

E = os.environ.get
W, H = int(E("REEL_WIDTH") or 720), int(E("REEL_HEIGHT") or 1280)
FPS = int(E("REEL_FPS") or 24)
LANG = (E("CONTENT_LANG") or "ar").lower()
LANG_AR = LANG.startswith("ar")
VOICE = E("TTS_VOICE") or "ar-IQ-BasselNeural"
RATE = E("TTS_RATE") or "+8%"
TOPIC = E("TOPIC") or ""
OUT = Path(E("OUT_DIR") or tempfile.gettempdir() + "/reel")
GK, GRK, ORK = E("GEMINI_API_KEY") or "", E("GROQ_API_KEY") or "", E("OPENROUTER_API_KEY") or ""
GMODEL = E("GEMINI_MODEL") or "gemini-2.5-flash"
FBT, FBP = E("FB_ACCESS_TOKEN") or "", E("FB_PAGE_ID") or ""
GRAPH = E("GRAPH_VERSION") or "v21.0"
RAQM = features.check("raqm")
FF = None


def log(m):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {m}", flush=True)


# ───────────────────────── ffmpeg ─────────────────────────
def ffmpeg():
    global FF
    if FF:
        return FF
    exe = shutil.which("ffmpeg")
    if not exe:
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            sys.exit("ffmpeg غير موجود: sudo apt-get install -y ffmpeg  أو  pip install imageio-ffmpeg")
    FF = exe
    return exe


def duration(path):
    p = subprocess.run([ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", p.stderr)
    return (int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])) if m else 0.0


# ───────────────────────── خطوط (تنزيل تلقائي) ─────────────────────────
FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
FONT_URLS = {
    "cairo.ttf": "https://github.com/google/fonts/raw/main/ofl/cairo/Cairo%5Bslnt%2Cwght%5D.ttf",
    "noto_naskh.ttf": "https://github.com/google/fonts/raw/main/ofl/notonaskharabic/NotoNaskhArabic%5Bwght%5D.ttf",
    "amiri.ttf": "https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Regular.ttf",
}
SYS_FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
             "/usr/share/fonts/opentype/noto/NotoNaskhArabic-Regular.ttf",
             "C:/Windows/Fonts/arial.ttf"]
_FONTS = None


def fonts():
    global _FONTS
    if _FONTS is not None:
        return _FONTS
    cache = Path(E("FONT_CACHE_DIR") or (Path.home() / ".cache" / "reel-fonts"))
    _FONTS = {}
    for name, url in FONT_URLS.items():
        for d in (FONT_DIR, cache):
            p = d / name
            if p.exists() and p.stat().st_size > 10000:
                _FONTS[name] = p
                break
        else:
            try:
                import requests
                cache.mkdir(parents=True, exist_ok=True)
                log(f"⬇️  تنزيل خط: {name}")
                r = requests.get(url, timeout=60)
                r.raise_for_status()
                (cache / name).write_bytes(r.content)
                if (cache / name).stat().st_size > 10000:
                    _FONTS[name] = cache / name
            except Exception as e:
                log(f"⚠️ خط {name}: {str(e)[:80]}")
    return _FONTS


def font(size, bold=True):
    for name, p in fonts().items():
        try:
            f = ImageFont.truetype(str(p), size)
            if name == "cairo.ttf" and bold:
                try:
                    f.set_variation_by_axes([0, 700])
                except Exception:
                    pass
            return f
        except Exception:
            continue
    for p in SYS_FONTS:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


# ───────────────────────── نص عربي ─────────────────────────
def is_rtl(t):
    return any("\u0600" <= c <= "\u06FF" for c in t)


def shape(t):
    if RAQM or not LANG_AR:
        return t
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(t))
    except Exception:
        return t


def kw(t):
    return {"direction": "rtl"} if (RAQM and is_rtl(t)) else {}


def measure(f, t):
    try:
        return f.getlength(shape(t), **kw(t))
    except TypeError:
        return f.getlength(shape(t))


def dtext(d, xy, t, f, fill, anchor="la"):
    try:
        d.text(xy, shape(t), font=f, fill=fill, anchor=anchor, **kw(t))
    except TypeError:
        d.text(xy, shape(t), font=f, fill=fill, anchor=anchor)


def wrap(t, f, maxw, maxl=3):
    words, lines, cur = t.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if measure(f, trial) <= maxw or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
            if len(lines) == maxl:
                break
    if cur and len(lines) < maxl:
        lines.append(cur)
    if len(lines) == maxl and measure(f, " ".join(words)) > maxw:
        last = lines[-1]
        while last and measure(f, last + "…") > maxw:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines


# ───────────────────────── عناصر تعبّر عن معنى الجملة ─────────────────────────
PROPS = {
    "clock": ["وقت", "ساعة", "ساعات", "دقيقة", "يوم", "أيام", "سنة", "سنين", "شهر", "شهور", "ليل", "time", "hour", "day", "year"],
    "coins": ["فلوس", "مال", "غني", "فقير", "راتب", "دخل", "money", "rich", "salary"],
    "plant": ["صبر", "زرع", "بذرة", "جذور", "نمو", "تنمو", "تسقي", "شجرة", "استمر", "استمرار", "plant", "seed", "roots", "patience", "grow"],
    "mountain": ["هدف", "قمة", "طموح", "تحدي", "تحدٍّ", "goal", "mountain", "peak", "challenge"],
    "book": ["تعلم", "تعلّم", "درس", "اقرأ", "قراءة", "معرفة", "عقل", "learn", "book", "read"],
    "shadow": ["خوف", "خوفك", "رعب", "قلق", "خايف", "تخاف", "fear", "afraid", "scared"],
    "steps": ["خطوة", "خطوات", "طريق", "بداية", "ابدأ", "إبدأ", "step", "start", "path"],
    "hourglass": ["انتظار", "انتظر", "تنتظر", "مؤجل", "بعدين", "wait", "waiting", "later"],
    "fire": ["شغف", "حماس", "طاقة", "حلم", "أحلام", "إصرار", "passion", "fire", "dream"],
    "heart": ["حب", "قلب", "سعادة", "سلام", "راحة", "love", "heart", "peace"],
    "wall": ["فشل", "غلط", "خسارة", "كسر", "عقبة", "حاجز", "fail", "mistake", "loss", "obstacle"],
    "trophy": ["نجاح", "فوز", "إنجاز", "أنجز", "وصلت", "success", "win", "achieve", "trophy"],
    "star": ["مثالي", "الأفضل", "مميز", "تفوّق", "perfect", "best", "star"],
    "mirror": ["مقارنة", "تقارن", "المقارنة", "نفسك", "مرآة", "قارن", "compare", "yourself", "mirror"],
}
FLAT = None


def detect_prop(text, explicit=""):
    if explicit in PROPS:
        return explicit
    low = text.lower()
    best, ln = "none", 0
    for k, ws in PROPS.items():
        for w in ws:
            if w in low and len(w) > ln:
                best, ln = k, len(w)
    return best


def is_kw(word):
    global FLAT
    if FLAT is None:
        FLAT = sorted({w for ws in PROPS.values() for w in ws}, key=len, reverse=True)
    c = re.sub(r"[^\w\u0600-\u06FF]", "", word).lower()
    if not c:
        return False
    if c in FLAT or (c.startswith("ال") and c[2:] in FLAT):
        return True
    return any(len(k) >= 3 and c.startswith(k) and len(c) - len(k) <= 3 for k in FLAT)


def rich_line(d, cx, y, line, f, base, acc):
    words = line.split()
    if not words:
        return
    sp = measure(f, " ")
    ws = [measure(f, w) for w in words]
    total = sum(ws) + sp * (len(words) - 1)
    if is_rtl(line):
        x = cx + total / 2
        for w, ww in zip(words, ws):
            dtext(d, (int(x), y), w, f, acc if is_kw(w) else base, "ra")
            x -= ww + sp
    else:
        x = cx - total / 2
        for w, ww in zip(words, ws):
            dtext(d, (int(x), y), w, f, acc if is_kw(w) else base, "la")
            x += ww + sp


def draw_prop(d, key, cx, cy, s, t, col, acc, lw):
    if not key or key == "none":
        return
    p = min(1.0, t / 0.45) ** 0.6
    s *= 0.55 + 0.45 * p
    cy = cy + math.sin(t * 1.7) * s * 0.06
    a = int(255 * p)
    w = max(2, int(lw * 0.75))
    C = lambda c: (c[0], c[1], c[2], a)
    flip = math.sin(t * 9) * s * 0.05
    if key == "dots":
        bw, bh = s * 0.62, s * 0.36
        d.rounded_rectangle([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], radius=bh * 0.45, outline=C(col), width=w)
        d.polygon([(cx - bw * 0.22, cy + bh * 0.48), (cx - bw * 0.04, cy + bh * 0.48), (cx - bw * 0.22, cy + bh * 0.86)], fill=C(col))
        for k in range(3):
            rr = max(2.0, s * 0.045)
            dx = (k - 1) * bw * 0.26
            al = int(120 + 135 * abs(math.sin(t * 2.6 - k * 0.7)))
            d.ellipse([cx + dx - rr, cy - rr, cx + dx + rr, cy + rr], fill=(col[0], col[1], col[2], min(a, al)))
    elif key == "clock":
        r = s * 0.5
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=C(col), width=w)
        for k in range(12):
            an = k * math.pi / 6
            d.line([(cx + math.sin(an) * r * 0.84, cy - math.cos(an) * r * 0.84),
                    (cx + math.sin(an) * r * 0.95, cy - math.cos(an) * r * 0.95)], fill=C(acc), width=max(1, w // 2))
        d.line([(cx, cy), (cx, cy - r * 0.55)], fill=C(col), width=w)
        d.line([(cx, cy), (cx + r * 0.42, cy + r * 0.12)], fill=C(col), width=w)
    elif key == "coins":
        for i in range(3):
            yy = cy + s * 0.28 - i * s * 0.22
            d.ellipse([cx - s * 0.3, yy - s * 0.12, cx + s * 0.3, yy + s * 0.12], outline=C(acc if i == 2 else col), width=w)
    elif key == "plant":
        def leaf(x0, y0, an, L, Wd):
            q = []
            for i in range(13):
                u = i / 12
                bx, by = x0 + math.cos(an) * L * u, y0 - math.sin(an) * L * u
                ww = Wd * math.sin(math.pi * u)
                q.append((bx - math.sin(an) * ww, by - math.cos(an) * ww))
            for i in range(12, -1, -1):
                u = i / 12
                bx, by = x0 + math.cos(an) * L * u, y0 - math.sin(an) * L * u
                ww = Wd * math.sin(math.pi * u)
                q.append((bx + math.sin(an) * ww, by + math.cos(an) * ww))
            return q
        by = cy + s * 0.5
        top = (cx + s * 0.05 + flip * 0.4, cy - s * 0.32)
        d.line([(cx, by), (cx + s * 0.02, cy + s * 0.08), top], fill=C(col), width=w, joint="curve")
        for bx, yy2, an, L in ((cx + s * 0.01, cy + s * 0.14, 0.62, s * 0.38), (cx + s * 0.03, cy + s * 0.02, math.pi - 0.62, s * 0.34)):
            d.polygon(leaf(bx, yy2, an, L, s * 0.10), fill=(acc[0], acc[1], acc[2], int(46 * p)), outline=C(acc))
        br = s * 0.09
        d.ellipse([top[0] - br, top[1] - br, top[0] + br, top[1] + br], outline=C(acc), width=w)
        d.line([(cx - s * 0.42, by), (cx + s * 0.42, by)], fill=C(col), width=w)
    elif key == "mountain":
        d.line([(cx - s * 0.6, cy + s * 0.4), (cx - s * 0.18, cy - s * 0.35), (cx + s * 0.05, cy + s * 0.02),
                (cx + s * 0.22, cy - s * 0.25), (cx + s * 0.6, cy + s * 0.4)], fill=C(col), width=w, joint="curve")
        d.line([(cx - s * 0.18, cy - s * 0.35), (cx - s * 0.18, cy - s * 0.57)], fill=C(col), width=w)
        d.polygon([(cx - s * 0.18, cy - s * 0.57), (cx + s * 0.04, cy - s * 0.5), (cx - s * 0.18, cy - s * 0.43)], fill=C(acc))
    elif key == "book":
        d.line([(cx, cy - s * 0.32), (cx - s * 0.45, cy - s * 0.4), (cx - s * 0.45, cy + s * 0.36), (cx, cy + s * 0.44)], fill=C(col), width=w, joint="curve")
        d.line([(cx, cy - s * 0.32), (cx + s * 0.45, cy - s * 0.4), (cx + s * 0.45, cy + s * 0.36), (cx, cy + s * 0.44)], fill=C(col), width=w, joint="curve")
        d.line([(cx, cy - s * 0.32), (cx, cy + s * 0.44)], fill=C(acc), width=w)
    elif key == "shadow":
        d.ellipse([cx - s * 0.35, cy - s * 0.15, cx + s * 0.35, cy + s * 0.45], fill=(0, 0, 0, int(150 * p)))
        for k in (-1, 1):
            d.ellipse([cx + k * s * 0.12 - s * 0.09, cy - s * 0.44, cx + k * s * 0.12 + s * 0.09, cy - s * 0.26], fill=(0, 0, 0, int(150 * p)))
            er = s * 0.045
            d.ellipse([cx + k * s * 0.13 - er, cy + s * 0.02 - er, cx + k * s * 0.13 + er, cy + s * 0.02 + er], fill=(255, 255, 255, a))
    elif key == "steps":
        for i in range(3):
            x0 = cx - s * 0.5 + i * s * 0.33
            top = cy + s * 0.35 - i * s * 0.26
            d.rectangle([x0, top, x0 + s * 0.33, cy + s * 0.42], outline=C(col), width=w)
        d.line([(cx + s * 0.02, cy - s * 0.42), (cx + s * 0.36, cy - s * 0.42)], fill=C(acc), width=w)
        d.polygon([(cx + s * 0.44, cy - s * 0.42), (cx + s * 0.30, cy - s * 0.5), (cx + s * 0.30, cy - s * 0.34)], fill=C(acc))
    elif key == "hourglass":
        d.line([(cx - s * 0.34, cy - s * 0.45), (cx + s * 0.34, cy - s * 0.45)], fill=C(col), width=w)
        d.line([(cx - s * 0.34, cy + s * 0.45), (cx + s * 0.34, cy + s * 0.45)], fill=C(col), width=w)
        d.line([(cx - s * 0.3, cy - s * 0.45), (cx, cy), (cx - s * 0.3, cy + s * 0.45)], fill=C(col), width=w, joint="curve")
        d.line([(cx + s * 0.3, cy - s * 0.45), (cx, cy), (cx + s * 0.3, cy + s * 0.45)], fill=C(col), width=w, joint="curve")
        sd = s * 0.16 * (0.4 + 0.6 * ((t * 0.5) % 1.0))
        d.polygon([(cx - sd, cy + s * 0.45), (cx + sd, cy + s * 0.45), (cx, cy + s * 0.45 - sd)], fill=C(acc))
    elif key == "fire":
        def flame(top, bot, wd):
            q, N = [], 18
            for i in list(range(N + 1)) + list(range(N, -1, -1)):
                u = i / N
                xo = wd * math.sin(math.pi * 0.88 * (u ** 1.7)) + flip * (1 - u)
                q.append((cx + xo, top + (bot - top) * u))
            return q
        top, bot = cy - s * 0.55, cy + s * 0.42
        d.polygon(flame(top, bot, s * 0.3), fill=(acc[0], acc[1], acc[2], int(42 * p)), outline=C(acc))
        for k in range(3):
            rr = max(2.0, s * 0.035)
            ux, uy = (-1) ** k * s * (0.34 + 0.08 * k), cy - s * (0.48 + 0.16 * k) + math.sin(t * 6 + k) * s * 0.04
            d.ellipse([cx + ux - rr, uy - rr, cx + ux + rr, uy + rr], fill=(acc[0], acc[1], acc[2], int(170 * p)))
    elif key == "heart":
        rr = s * 0.42 * (1 + 0.06 * math.sin(t * 6))
        d.arc([cx - rr, cy - rr, cx, cy], 180, 360, fill=C(acc), width=w)
        d.arc([cx, cy - rr, cx + rr, cy], 180, 360, fill=C(acc), width=w)
        d.line([(cx - rr, cy - rr * 0.45), (cx, cy + rr * 0.7), (cx + rr, cy - rr * 0.45)], fill=C(acc), width=w, joint="curve")
    elif key == "wall":
        bw, bh = s * 0.28, s * 0.18
        for row in range(3):
            off = bw / 2 if row % 2 else 0
            for k in range(3):
                x0, y0 = cx - s * 0.42 + k * bw + off, cy - s * 0.3 + row * bh
                d.rectangle([x0, y0, x0 + bw * 0.92, y0 + bh * 0.88], outline=C(col), width=w)
        d.line([(cx - s * 0.05, cy - s * 0.34), (cx + s * 0.06, cy - s * 0.02), (cx - s * 0.02, cy + s * 0.1), (cx + s * 0.1, cy + s * 0.34)], fill=C(acc), width=w, joint="curve")
    elif key == "trophy":
        ct, cb = cy - s * 0.34, cy + s * 0.1
        d.arc([cx - s * 0.26, ct, cx + s * 0.26, cb], 0, 180, fill=C(acc), width=w)
        d.line([(cx - s * 0.26, ct), (cx + s * 0.26, ct)], fill=C(acc), width=w)
        d.arc([cx - s * 0.5, ct + s * 0.02, cx - s * 0.18, ct + s * 0.34], 90, 270, fill=C(col), width=w)
        d.arc([cx + s * 0.18, ct + s * 0.02, cx + s * 0.5, ct + s * 0.34], 270, 90, fill=C(col), width=w)
        d.line([(cx, cb), (cx, cy + s * 0.3)], fill=C(col), width=w)
        d.line([(cx - s * 0.18, cy + s * 0.3), (cx + s * 0.18, cy + s * 0.3)], fill=C(col), width=w)
        d.line([(cx - s * 0.26, cy + s * 0.4), (cx + s * 0.26, cy + s * 0.4)], fill=C(col), width=w)
    elif key == "star":
        q = []
        for i in range(10):
            an = -math.pi / 2 + i * math.pi / 5
            rr = s * 0.45 if i % 2 == 0 else s * 0.19
            q.append((cx + math.cos(an) * rr, cy + math.sin(an) * rr))
        d.polygon(q, outline=C(acc))
        d.line(q + [q[0]], fill=C(acc), width=w, joint="curve")
    elif key == "mirror":
        mw, mh = s * 0.52, s * 0.74
        box = [cx - mw / 2, cy - mh / 2, cx + mw / 2, cy + mh / 2]
        d.rounded_rectangle(box, radius=s * 0.12, outline=C(acc), width=w)
        hr = s * 0.11
        d.ellipse([cx - hr, cy - s * 0.16 - hr, cx + hr, cy - s * 0.16 + hr], outline=C(col), width=w)
        d.line([(cx, cy - s * 0.05), (cx, cy + s * 0.18)], fill=C(col), width=w)


# ───────────────────────── ستيك مان ─────────────────────────
def stickman(d, fx, fy, s, mood, t, col, lw):
    hr, leg, tor, arm = s * 0.135, s * 0.40, s * 0.32, s * 0.30
    ar, al = 0.26, -0.26
    lr, ll = 0.20, -0.20
    lean = bounce = 0.0
    if mood == "walk":
        sw = math.sin(t * 2.6)
        lr, ll, ar, al = 0.30 * sw, -0.30 * sw, -0.35 * sw, 0.35 * sw
        bounce = -abs(sw) * s * 0.015
    elif mood == "run":
        sw = math.sin(t * 6.0)
        lr, ll, ar, al = 0.62 * sw, -0.62 * sw, -0.62 * sw, 0.62 * sw
        lean, bounce = s * 0.05, -abs(sw) * s * 0.03
    elif mood == "talk":
        ar, al, lr, ll = 0.95 + math.sin(t * 5) * 0.22, -0.30, 0.20, -0.20
    elif mood == "point":
        ar, al, lr, ll = 1.55, -0.26, 0.18, -0.18
    elif mood == "think":
        ar, al, lr, ll = 0.55 + math.sin(t * 2) * 0.08, -0.22, 0.15, -0.15
        lean = -s * 0.012
    elif mood == "sad":
        ar, al, lr, ll = 0.10, -0.10, 0.12, -0.12
        lean = -s * 0.02
    elif mood == "happy":
        ar = 1.95 + math.sin(t * 7) * 0.14
        al = -ar
        bounce, lr, ll = -abs(math.sin(t * 4.2)) * s * 0.035, 0.18, -0.18
    elif mood == "jump":
        jp = (math.sin(t * 3) + 1) / 2
        bounce, ar, al, lr, ll = -jp * s * 0.16, 2.05, -2.05, 0.34, -0.34
    hip = (fx + int(lean * 0.5), int(fy + bounce) - int(leg))
    neck = (hip[0] + int(lean), hip[1] - int(tor))
    hc = (neck[0] + int(lean * 0.5), neck[1] - int(hr * 0.95))
    d.line([hip, neck], fill=col, width=lw, joint="curve")
    d.ellipse([hc[0] - hr, hc[1] - hr, hc[0] + hr, hc[1] + hr], outline=col, width=lw)
    er = max(2.0, hr * 0.15)
    for ex in (-hr * 0.32, hr * 0.32):
        d.ellipse([hc[0] + ex - er, hc[1] - hr * 0.16 - er, hc[0] + ex + er, hc[1] - hr * 0.16 + er], fill=col)
    my, mw = hc[1] + hr * 0.40, hr * 0.45
    if mood in ("happy", "jump", "run"):
        d.arc([hc[0] - mw, my - mw * 0.75, hc[0] + mw, my + mw * 0.75], 0, 180, fill=col, width=max(2, lw // 2))
    elif mood == "sad":
        d.arc([hc[0] - mw, my - mw * 0.75, hc[0] + mw, my + mw * 0.75], 180, 360, fill=col, width=max(2, lw // 2))
    else:
        d.line([(hc[0] - mw * 0.7, my), (hc[0] + mw * 0.7, my)], fill=col, width=max(2, lw // 2))
    sh = (neck[0], neck[1] + tor * 0.10)
    for an in (ar, al):
        d.line([sh, (sh[0] + math.sin(an) * arm, sh[1] + math.cos(an) * arm)], fill=col, width=lw, joint="curve")
    for an in (lr, ll):
        d.line([hip, (hip[0] + math.sin(an) * leg, int(fy + bounce))], fill=col, width=lw, joint="curve")


PALETTES = [((16, 20, 40), (46, 60, 120), (255, 214, 102)), ((28, 18, 44), (92, 46, 120), (140, 240, 200)),
            ((12, 34, 34), (22, 92, 96), (255, 168, 120)), ((36, 16, 24), (128, 46, 66), (255, 230, 150)),
            ((14, 24, 36), (36, 84, 140), (255, 120, 160)), ((26, 26, 18), (96, 96, 44), (170, 230, 255))]


def grad(size, c1, c2):
    w, h = size
    base = Image.new("RGB", (1, h))
    px = base.load()
    for y in range(h):
        u = y / max(1, h - 1)
        px[0, y] = tuple(int(c1[i] + (c2[i] - c1[i]) * u) for i in range(3))
    return base.resize((w, h), Image.BILINEAR)


class R:
    def __init__(self):
        self.sc = W / 720.0
        self.lw = max(4, int(7 * self.sc))
        self.fc = font(int(37 * self.sc))
        self.fs = font(int(23 * self.sc))
        self.fh = font(int(28 * self.sc))

    def frame(self, idx, total, scene, t, dur, title):
        s = self.sc
        c1, c2, acc = PALETTES[idx % len(PALETTES)]
        img = grad((W, H), c1, c2)
        d = ImageDraw.Draw(img, "RGBA")
        for k in range(3):
            r = int((95 + k * 58) * s)
            ox = int(W * (0.20 + 0.30 * k) + math.sin(t * 0.6 + k) * 22 * s)
            oy = int(H * (0.20 + 0.08 * k) + math.cos(t * 0.5 + k) * 18 * s)
            d.ellipse([ox - r, oy - r, ox + r, oy + r], outline=(*acc, 26), width=max(2, int(3 * s)))
        fy = int(H * 0.705)
        d.line([(0, fy), (W, fy)], fill=(*acc, 45), width=max(2, int(2 * s)))
        fig = H * 0.36
        cx = int(W * 0.5 + math.sin(t * 0.8) * 14 * s)
        d.ellipse([cx - int(fig * 0.3), fy - int(9 * s), cx + int(fig * 0.3), fy + int(9 * s)], fill=(0, 0, 0, 70))
        stickman(d, cx, fy, fig, scene["mood"], t, (255, 255, 255), self.lw)
        prop = scene.get("prop") or detect_prop(scene["text"])
        if prop in (None, "", "none"):
            prop = "dots"
        if prop != "none":
            side = -1 if idx % 2 == 0 else 1
            px = int(W * 0.5 + side * W * 0.30 + math.sin(t * 1.1) * 6 * s)
            draw_prop(d, prop, px, int(fy - fig * 0.78), fig * 0.58, t, (255, 255, 255), acc, self.lw)
        bw, bx, by = W - int(60 * s), int(30 * s), int(26 * s)
        d.rounded_rectangle([bx, by, bx + bw, by + int(9 * s)], radius=int(5 * s), fill=(255, 255, 255, 50))
        prog = (idx + t / max(dur, 0.01)) / max(total, 1)
        d.rounded_rectangle([bx, by, bx + int(bw * min(1.0, prog)), by + int(9 * s)], radius=int(5 * s), fill=acc)
        tw = measure(self.fh, str(title))
        hh = self.fh.size + int(16 * s)
        hx, hy = W // 2, int(50 * s)
        d.rounded_rectangle([hx - tw / 2 - int(20 * s), hy, hx + tw / 2 + int(20 * s), hy + hh], radius=hh // 2,
                            fill=(0, 0, 0, 110), outline=(*acc, 110), width=max(2, int(2 * s)))
        dtext(d, (hx, hy + hh // 2), str(title), self.fh, (255, 255, 255, 245), "mm")
        lines = wrap(scene["text"], self.fc, int(W - 120 * s), 3)
        lh, pad = int(self.fc.size * 1.5), int(28 * s)
        top = int(H * 0.735)
        box = [int(38 * s), top, W - int(38 * s), top + lh * len(lines) + pad * 2]
        d.rounded_rectangle(box, radius=int(28 * s), fill=(0, 0, 0, 140), outline=(*acc, 130), width=max(2, int(2 * s)))
        cy = box[1] + pad
        for ln in lines:
            rich_line(d, W // 2, cy, ln, self.fc, (255, 255, 255), acc)
            cy += lh
        dtext(d, (W // 2, H - int(32 * s)), "ستيك مان" if LANG_AR else "STICK MAN", self.fs, (255, 255, 255, 130), "md")
        return img


# ───────────────────────── النص: AI ثم قوالب محلية ─────────────────────────
SYS = """أنت كاتب ريلز لشخصية "ستيك مان". أعد JSON فقط:
{"title":"عنوان قصير","caption":"وصف 1-2 جملة ينتهي بسؤال","hashtags":["#وسم"],
 "scenes":[{"text":"جملة قصيرة أقل من 90 حرف","mood":"walk","prop":"clock"}]}
4-6 مشاهد. mood من: walk, talk, think, sad, happy, run, jump, point.
prop من: none, clock, coins, plant, mountain, book, shadow, steps, hourglass, fire, heart, wall, trophy, star, mirror.
المشهد الأول خطاف، الأخير خلاصة. بلا إيموجي ولا مواضيع سياسية أو دينية حساسة."""

TPL = [
 {"title": "البداية الصعبة", "caption": "كلنا مرينا بهذي اللحظة. صح لو لا؟", "hashtags": ["#تحفيز", "#ستيك_مان", "#ريلز"],
  "scenes": [{"text": "شنو الفرق بينك وبين اللي نجح؟", "mood": "think"}, {"text": "الفرق مو بالذكاء ولا بالحظ ولا بالفلوس.", "mood": "talk"},
             {"text": "الفرق إنه هو بدأ وهو مو جاهز.", "mood": "point"}, {"text": "وإنت بعدك تنتظر اللحظة المثالية.", "mood": "sad"},
             {"text": "ابدأ اليوم، وخلّي اللحظة المثالية تلحقك.", "mood": "run"}]},
 {"title": "الصبر", "caption": "الصبر مو انتظار، الصبر شغل. توافق؟", "hashtags": ["#صبر", "#تحفيز", "#ريلز"],
  "scenes": [{"text": "الناس تحسب الصبر يعني تنتظر.", "mood": "walk"}, {"text": "لكن الصبر الحقيقي يعني تشتغل وما تشوف نتيجة.", "mood": "talk"},
             {"text": "تزرع كل يوم، وتسقي، وتسكت.", "mood": "think"}, {"text": "وبعد شهور يطلع الشي اللي الكل يستغرب منه.", "mood": "jump"},
             {"text": "استمر. الجذور تنمو قبل ما تشوفها.", "mood": "happy"}]},
 {"title": "الخوف", "caption": "شنو الشي اللي تخاف منه أكثر شي؟", "hashtags": ["#خوف", "#شجاعة", "#ريلز"],
  "scenes": [{"text": "الخوف مو عدوّك، الخوف يخبرك إنه هذا الشي يهمك.", "mood": "think"}, {"text": "الشجاعة مو إنك ما تخاف.", "mood": "talk"},
             {"text": "الشجاعة إنك تخاف وتقدّم خطوة وحدة.", "mood": "walk"}, {"text": "خطوة وحدة كل يوم تكفي.", "mood": "run"},
             {"text": "وبعد سنة راح تشكر نفسك إنك ما رجعت.", "mood": "happy"}]},
 {"title": "الوقت", "caption": "وين يصرف أغلب وقتك؟ بصراحة", "hashtags": ["#وقت", "#إنتاجية", "#ريلز"],
  "scenes": [{"text": "كل الناس عندها نفس الأربع وعشرين ساعة.", "mood": "talk"}, {"text": "الغني والفقير، الناجح والفاشل، نفس العدد.", "mood": "think"},
             {"text": "الفرق بس وين تحطها.", "mood": "point"}, {"text": "ساعة كل يوم تصير ثلاث مية وخمس وستين ساعة بالسنة.", "mood": "jump"},
             {"text": "اختار شي واحد، وأعطه ساعة اليوم.", "mood": "walk"}]},
 {"title": "المقارنة", "caption": "بطلت تقارن نفسك بغيرك؟ شلون؟", "hashtags": ["#مقارنة", "#سلام_داخلي", "#ريلز"],
  "scenes": [{"text": "أكبر سرقة تسرقها من نفسك إنك تقارن نفسك بغيرك.", "mood": "sad"}, {"text": "تشوف نتيجتهم، ما تشوف سنين تعبهم.", "mood": "think"},
             {"text": "ولا تشوف ليلهم الطويل.", "mood": "talk"}, {"text": "قارن نفسك بشخص واحد فقط: نفسك قبل سنة.", "mood": "point"},
             {"text": "هذا هو المقياس الوحيد العادل.", "mood": "happy"}]},
 {"title": "الفشل", "caption": "شنو أكثر فشل تعلمت منه؟", "hashtags": ["#فشل", "#تعلم", "#ريلز"],
  "scenes": [{"text": "الفشل مو نهاية الطريق.", "mood": "talk"}, {"text": "الفشل درس، ودفعت ثمنه وقتك.", "mood": "think"},
             {"text": "الغلط الوحيد إنك تكرر نفس الغلط.", "mood": "point"}, {"text": "قف، اكتب شنو صار، وغيّر شي واحد.", "mood": "walk"},
             {"text": "هيك تتحول الخسارة إلى استثمار.", "mood": "jump"}]},
]


def js(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    return json.loads(text[text.find("{"):text.rfind("}") + 1])


def validate(sc):
    out = []
    for s in sc.get("scenes") or []:
        t = re.sub(r"\s+", " ", str(s.get("text", "")).strip())[:130]
        if not t:
            continue
        mood = str(s.get("mood", "talk")).lower()
        mood = mood if mood in ("walk", "talk", "think", "sad", "happy", "run", "jump", "point") else "talk"
        prop = str(s.get("prop", "")).lower()
        out.append({"text": t, "mood": mood, "prop": prop if prop in PROPS else detect_prop(t)})
    if len(out) < 3:
        raise ValueError("مشاهد قليلة")
    tags = sc.get("hashtags") or []
    if isinstance(tags, str):
        tags = tags.split()
    return {"title": str(sc.get("title", "")).strip()[:60] or "ريل", "caption": str(sc.get("caption", "")).strip()[:400],
            "hashtags": [t if str(t).startswith("#") else f"#{t}" for t in tags][:8], "scenes": out[:6]}


def gen_script():
    import random
    topic = TOPIC.strip()
    prompt = f"اكتب سكربت ريل باللغة {'العربية' if LANG_AR else 'English'}." + (f" الموضوع: {topic}." if topic else " اختر موضوعاً تحفيزياً جديداً.")
    srcs = []
    if GK:
        srcs.append(("gemini", lambda: js("".join(p.get("text", "") for p in __import__("requests").post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{GMODEL}:generateContent?key={GK}",
            json={"system_instruction": {"parts": [{"text": SYS}]}, "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                  "generationConfig": {"temperature": 0.9, "responseMimeType": "application/json"}}, timeout=60).json()
            ["candidates"][0]["content"]["parts"]))))
    for name, url, key, mdl in (("groq", "https://api.groq.com/openai/v1/chat/completions", GRK, E("GROQ_MODEL") or "llama-3.3-70b-versatile"),
                                ("openrouter", "https://openrouter.ai/api/v1/chat/completions", ORK, E("OPENROUTER_MODEL") or "meta-llama/llama-3.3-70b-instruct:free")):
        if key:
            srcs.append((name, (lambda u, k, m: lambda: js(__import__("requests").post(
                u, headers={"Authorization": f"Bearer {k}"}, timeout=60,
                json={"model": m, "temperature": 0.9, "response_format": {"type": "json_object"},
                      "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": prompt}]})
                .json()["choices"][0]["message"]["content"]))(url, key, mdl)))
    for name, fn in srcs:
        for at in (1, 2):
            try:
                log(f"توليد النص عبر {name}…")
                s = validate(fn())
                log(f"✅ النص من {name}: «{s['title']}»")
                return s, name
            except Exception as e:
                log(f"⚠️ {name}: {type(e).__name__}: {str(e)[:120]}")
    log("↩️ المولّد المحلي (بلا API)")
    bank = TPL
    hit = [x for x in bank if topic and topic in json.dumps(x, ensure_ascii=False)] if topic else []
    sc = json.loads(json.dumps(random.choice(hit or bank)))
    if topic and not hit and LANG_AR:
        sc["scenes"][0]["text"], sc["title"] = f"موضوع اليوم: {topic}.", topic
    for x in sc["scenes"]:
        x.setdefault("prop", detect_prop(x["text"]))
    return validate(sc), "local"


# ───────────────────────── صوت ─────────────────────────
async def _tts(text, out):
    import edge_tts
    await edge_tts.Communicate(text, VOICE, rate=RATE).save(str(out))


def voice(text, out, i):
    try:
        asyncio.run(_tts(text, out))
        d = duration(out)
        if d > 0.2:
            return d
        raise RuntimeError("ملف فارغ")
    except Exception as e:
        log(f"⚠️ TTS مشهد {i + 1}: {type(e).__name__} — صوت صامت")
        d = max(2.5, min(6.0, len(text) / 14 + 1.2))
        subprocess.run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", "anullsrc=r=44100:cl=stereo", "-t", f"{d:.2f}", "-c:a", "aac", str(out)], check=True)
        return d


# ───────────────────────── إنتاج ─────────────────────────
def produce(script):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "audio").mkdir(exist_ok=True)
    r, seeds, total = R(), [], len(script["scenes"])
    log(f"🎬 {total} مشاهد {W}x{H}@{FPS}fps")
    for i, sc in enumerate(script["scenes"]):
        a = OUT / "audio" / f"s{i:02d}.m4a"
        d = voice(sc["text"], a, i)
        extra = f" · عنصر: {sc['prop']}" if sc.get("prop") not in (None, "", "none") else ""
        log(f"   مشهد {i + 1}/{total} · {d:.1f}s · {sc['mood']}{extra} · «{sc['text'][:40]}…»")
        dur = max(2.0, min(d + 0.45, 10.0))
        seg = OUT / f"seg{i:02d}.mp4"
        p = subprocess.Popen([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                              "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-i", str(a), "-filter:a", "apad",
                              "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p",
                              "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2", "-t", f"{dur:.3f}",
                              "-movflags", "+faststart", str(seg)], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for f in range(max(int(dur * FPS), 2)):
            try:
                p.stdin.write(r.frame(i, total, sc, f / FPS, dur, script["title"]).tobytes())
            except BrokenPipeError:
                break
        p.stdin.close()
        err = p.stderr.read().decode("utf-8", "ignore")
        if p.wait() != 0:
            raise RuntimeError(f"ffmpeg مشهد {i + 1}: {err[-300:]}")
        seeds.append(seg)
    lst = OUT / "list.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in seeds), encoding="utf-8")
    final = OUT / "reel.mp4"
    subprocess.run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(final)], check=True)
    for p in seeds:
        p.unlink(missing_ok=True)
    shutil.rmtree(OUT / "audio", ignore_errors=True)
    log(f"✅ {final} · {duration(final):.1f}s · {final.stat().st_size / 1e6:.1f}MB")
    return final


# ───────────────────────── فيسبوك ─────────────────────────
def publish(video, caption):
    import requests
    size = video.stat().st_size
    log("📤 نشر على فيسبوك…")

    def post(path, data):
        r = requests.post(f"https://graph.facebook.com/{GRAPH}/{path}", data=data, timeout=120)
        j = r.json()
        if r.status_code >= 400 or "error" in j:
            raise RuntimeError(json.dumps(j, ensure_ascii=False)[:400])
        return j

    st = post(f"{FBP}/video_reels", {"upload_phase": "start", "access_token": FBT})
    vid, url = st["video_id"], st.get("upload_url") or f"https://rupload.facebook.com/video-upload/{GRAPH}/{st['video_id']}"
    with video.open("rb") as fh:
        up = requests.post(url, headers={"Authorization": f"OAuth {FBT}", "offset": "0", "file_size": str(size)}, data=fh, timeout=600)
    if up.status_code >= 400:
        raise RuntimeError(f"upload {up.status_code}: {up.text[:300]}")
    post(f"{FBP}/video_reels", {"upload_phase": "finish", "video_id": vid, "video_state": "PUBLISHED",
                                "description": caption, "access_token": FBT})
    for _ in range(20):
        __import__("time").sleep(15)
        r = requests.get(f"https://graph.facebook.com/{GRAPH}/{vid}",
                         params={"fields": "status,permalink_url", "access_token": FBT}, timeout=60).json()
        log(f"   الحالة: {(r.get('status') or {}).get('video_status')}")
        if r.get("permalink_url"):
            return r["permalink_url"]
    return f"https://www.facebook.com/reel/{vid}"


# ───────────────────────── فحص ─────────────────────────
def check():
    ok = True
    print("\n=== فحص الإعدادات ===\n")
    try:
        print(f"✅ ffmpeg: {subprocess.run([ffmpeg(), '-version'], capture_output=True, text=True).stdout.splitlines()[0][:50]}")
    except SystemExit as e:
        ok = False
        print(f"❌ {e}")
    fs = fonts()
    print(("✅ الخطوط: " + ", ".join(fs)) if fs else "⚠️ لا خطوط — سيُستخدم خط النظام")
    print(f"✅ Pillow {Image.__version__} · العربية: {'libraqm' if RAQM else 'reshaper'}")
    try:
        p = OUT / "_p.m4a"
        OUT.mkdir(parents=True, exist_ok=True)
        asyncio.run(_tts("مرحبا", p))
        print(f"✅ الصوت {VOICE} ({duration(p):.1f}s)")
        p.unlink(missing_ok=True)
    except Exception as e:
        ok = False
        print(f"❌ الصوت: {type(e).__name__}: {str(e)[:100]}")
    for n, k in (("Gemini", GK), ("Groq", GRK), ("OpenRouter", ORK)):
        print(f"{'✅' if k else '⚪️'} {n}: {'مضبوط' if k else 'غير مضبوط (يوجد مولّد محلي)'}")
    if FBT and FBP:
        try:
            r = __import__("requests").get(f"https://graph.facebook.com/{GRAPH}/{FBP}",
                                           params={"fields": "id,name", "access_token": FBT}, timeout=45).json()
            if "error" in r:
                ok = False
                print(f"❌ فيسبوك: {r['error'].get('message')}")
            else:
                print(f"✅ فيسبوك: «{r.get('name')}»")
        except Exception as e:
            ok = False
            print(f"❌ فيسبوك: {str(e)[:150]}")
    else:
        print("⚪️ فيسبوك: بلا مفاتيح — سيبني الفيديو فقط")
    print("\n" + ("✅ كل شي جاهز\n" if ok else "⚠️ أصلح ❌ ثم أعد الفحص\n"))
    return 0 if ok else 1


def main():
    global TOPIC
    ap = argparse.ArgumentParser(description="ريلز ستيك مان — مجاني 100%")
    ap.add_argument("--no-publish", action="store_true")
    ap.add_argument("--topic", default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check:
        return check()
    if a.topic:
        TOPIC = a.topic
    if a.seed is not None:
        import random
        random.seed(a.seed)
    ffmpeg()
    log(f"مفاتيح: gemini={bool(GK)} groq={bool(GRK)} openrouter={bool(ORK)} fb={bool(FBT and FBP)}")
    script, src = gen_script()
    video = produce(script)
    if a.out:
        t = Path(a.out)
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(video, t)
        video = t
    cap = script["caption"] + ("\n\n" + " ".join(script["hashtags"]) if script["hashtags"] else "")
    meta = {"title": script["title"], "caption": cap, "source": src, "video": str(video),
            "duration_sec": round(duration(video), 2), "size_mb": round(video.stat().st_size / 1e6, 2),
            "generated_at": datetime.now(timezone.utc).isoformat()}
    rc = 0
    if FBT and FBP and not a.no_publish:
        try:
            meta["permalink"] = publish(video, cap)
            log(f"🎉 منشور: {meta['permalink']}")
        except Exception as e:
            log(f"❌ فشل النشر: {str(e)[:400]}")
            rc = 1
    else:
        log("ℹ️ بلا نشر — الفيديو محفوظ")
    (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: meta[k] for k in ("title", "source", "duration_sec", "size_mb")}, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    sys.exit(main())
