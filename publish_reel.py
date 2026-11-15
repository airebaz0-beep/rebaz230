# -*- coding: utf-8 -*-
import os
import sys
import re
import json
import math
import wave
import time
import random
import shutil
import asyncio
import textwrap
import subprocess
import glob
import requests
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter, features
import google.generativeai as genai
import edge_tts

# ============================================================
# الإعدادات
# ============================================================
W, H = 1080, 1920
FPS = 30
SR = 44100
CAP_SIZE = 86
VOICE = os.getenv("TTS_VOICE", "ar-SA-HamedNeural")
TTS_RATE = os.getenv("TTS_RATE", "+6%")
GRAPH = os.getenv("FB_GRAPH_VERSION", "v21.0")
PEXELS_KEY = os.getenv("PEXELS_API_KEY")
YELLOW = "#FACC15"
PALETTE = ["#00F0FF", "#F472B6", "#FBBF24", "#34D399", "#A78BFA", "#38BDF8"]

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "arabic-reshaper", "python-bidi"], check=False)
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
    except ImportError:
        arabic_reshaper = None
        get_display = None

HAS_RAQM = features.check("raqm")

FFMPEG_BIN = shutil.which("ffmpeg")
if not FFMPEG_BIN:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "imageio-ffmpeg"], check=False)
    import imageio_ffmpeg
    FFMPEG_BIN = imageio_ffmpeg.get_ffmpeg_exe()

_RS = getattr(Image, "Resampling", Image)
BICUBIC, LANCZOS, BILINEAR = _RS.BICUBIC, _RS.LANCZOS, _RS.BILINEAR


# ============================================================
# أدوات عامة
# ============================================================
def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def clamp01(x):
    return max(0.0, min(1.0, x))


def ease_out_back(t):
    t = clamp01(t)
    c1 = 1.70158
    c3 = c1 + 1.0
    return 1.0 + c3 * ((t - 1.0) ** 3) + c1 * ((t - 1.0) ** 2)


def ease_out_cubic(t):
    t = clamp01(t)
    return 1.0 - ((1.0 - t) ** 3)


def ease_in_out(t):
    t = clamp01(t)
    return t * t * (3 - 2 * t)


def load_font(path, size):
    if path and os.path.exists(path):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def tk(font):
    d = {"font": font}
    if HAS_RAQM:
        d["direction"] = "rtl"
    return d


def select_random_font():
    all_fonts, good = [], []
    for folder in [".", "fonts"]:
        if os.path.isdir(folder):
            for fname in os.listdir(folder):
                if fname.lower().endswith((".ttf", ".otf")):
                    full = os.path.join(folder, fname)
                    all_fonts.append(full)
                    if "extralight" not in fname.lower() and "thin" not in fname.lower() and "light" not in fname.lower():
                        good.append(full)
    candidates = sorted(set(good or all_fonts))
    if candidates:
        chosen = random.choice(candidates)
        print(f"الخطوط المتوفرة ({len(candidates)}) | المختار: {os.path.basename(chosen)}")
        return chosen
    fallback = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    print("تنبيه: ما لقيت خطوط مرفوعة، استخدمت الاحتياطي.")
    return fallback if os.path.exists(fallback) else None


def shape_word(word):
    if HAS_RAQM:
        return word
    if arabic_reshaper and get_display:
        return get_display(arabic_reshaper.reshape(word))
    return word


def shape_text(text):
    return shape_word(text.strip())


def shape_multiline(text, max_chars=22):
    wrapped = textwrap.fill(text.strip(), width=max_chars)
    if HAS_RAQM:
        return wrapped
    if arabic_reshaper and get_display:
        return "\n".join(get_display(arabic_reshaper.reshape(l)) for l in wrapped.split("\n"))
    return wrapped


def clean_speech(text):
    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(r"[\U00010000-\U0010ffff]", "", text)
    return re.sub(r"\s+", " ", text).strip()


# ============================================================
# الصوت
# ============================================================
def decode_audio(path):
    raw = subprocess.check_output(
        [FFMPEG_BIN, "-v", "quiet", "-i", path, "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"]
    )
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def trim_silence(x, lead=0.04, tail=0.14):
    if len(x) == 0:
        return x, 0.0
    win = int(SR * 0.01)
    n = len(x) // win
    if n == 0:
        return x, 0.0
    rms = np.sqrt(np.mean(x[:n * win].reshape(n, win) ** 2, axis=1))
    thr = max(0.004, rms.max() * 0.03)
    idx = np.where(rms > thr)[0]
    if len(idx) == 0:
        return x, 0.0
    s = max(0, int(idx[0] * win - lead * SR))
    e = min(len(x), int((idx[-1] + 1) * win + tail * SR))
    y = x[s:e].copy()
    fade = int(0.008 * SR)
    if len(y) > 2 * fade:
        y[:fade] *= np.linspace(0, 1, fade)
        y[-fade:] *= np.linspace(1, 0, fade)
    return y, s / SR


def frame_envelope(x, n_frames):
    if len(x) == 0:
        return np.zeros(n_frames)
    edges = np.linspace(0, len(x), n_frames + 1).astype(int)
    lv = np.array([np.sqrt(np.mean(x[a:b] ** 2)) if b > a else 0.0 for a, b in zip(edges[:-1], edges[1:])])
    ref = np.percentile(lv, 92) if lv.max() > 0 else 1.0
    env = np.clip(lv / (ref * 1.05 + 1e-9), 0, 1)
    out = np.zeros_like(env)
    cur = 0.0
    for i, v in enumerate(env):
        cur = v if v > cur else cur * 0.55 + v * 0.45
        out[i] = cur
    return out


def align_words(words, boundaries, duration):
    if boundaries and len(boundaries) == len(words):
        res = []
        for i, b in enumerate(boundaries):
            st = b["start"]
            en = b["end"] if b["end"] > st else (boundaries[i + 1]["start"] if i + 1 < len(boundaries) else duration)
            res.append((st, max(st + 0.12, en)))
        return res
    if boundaries:
        lens = [max(1, len(b["text"])) for b in boundaries]
        bchars = np.cumsum([0] + lens)[:-1].astype(float)
        bstarts = np.array([b["start"] for b in boundaries], dtype=float)
        total_b = float(sum(lens))
        wl = [max(1, len(w)) for w in words]
        wcum = np.cumsum([0] + wl)[:-1].astype(float)
        wtot = float(sum(wl))
        starts = np.interp(wcum / wtot * total_b, bchars, bstarts)
        end_all = max(boundaries[-1]["end"], boundaries[-1]["start"] + 0.2)
        res = []
        for i, st in enumerate(starts):
            en = starts[i + 1] if i + 1 < len(starts) else end_all
            res.append((float(st), float(max(st + 0.12, en))))
        return res
    weights = [max(2, len(w)) for w in words]
    total_w = float(sum(weights))
    s0, s1 = 0.05, max(0.3, duration - 0.15)
    span = s1 - s0
    cur = s0
    res = []
    for wt in weights:
        dur = span * wt / total_w
        res.append((cur, cur + dur))
        cur += dur
    return res


async def tts_scene(text, voice, path, tries=3):
    last = None
    for _ in range(tries):
        try:
            try:
                comm = edge_tts.Communicate(text, voice, rate=TTS_RATE, boundary="WordBoundary")
            except TypeError:
                comm = edge_tts.Communicate(text, voice, rate=TTS_RATE)
            bounds = []
            with open(path, "wb") as f:
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary":
                        st = chunk["offset"] / 1e7
                        du = chunk.get("duration", 0) / 1e7
                        bounds.append({"start": st, "end": st + du, "text": chunk.get("text", "")})
            if os.path.getsize(path) > 1000:
                return bounds
        except Exception as e:
            last = e
            await asyncio.sleep(1.5)
    raise RuntimeError(f"فشل توليد الصوت: {last}")


def generate_music(total_dur, transitions):
    n = int(total_dur * SR)
    t = np.arange(n, dtype=np.float64) / SR
    chords = [[220.00, 261.63, 329.63], [174.61, 220.00, 261.63],
              [130.81, 196.00, 261.63], [196.00, 246.94, 293.66]]
    clen = 4.0
    cidx = (t // clen).astype(int) % len(chords)
    loc = t % clen
    env = np.minimum(1.0, loc / 0.6) * np.minimum(1.0, (clen - loc) / 0.6)
    audio = np.zeros(n)
    for ci, fr in enumerate(chords):
        m = cidx == ci
        if not m.any():
            continue
        tt = t[m]
        pad = sum(np.sin(2 * np.pi * f * tt) + 0.6 * np.sin(2 * np.pi * f * 1.003 * tt) for f in fr) / (len(fr) * 1.6)
        bass = np.sin(2 * np.pi * (fr[0] / 2) * tt)
        audio[m] += (0.55 * pad + 0.36 * bass) * env[m]
    audio *= 0.06
    rng = np.random.default_rng(7)
    for tr in transitions:
        s = max(0, int((tr - 0.1) * SR))
        e = min(n, s + int(0.3 * SR))
        if e > s:
            nz = rng.standard_normal(e - s)
            nz = np.convolve(nz, np.ones(24) / 24, mode="same")
            xs = np.linspace(0, 1, e - s)
            audio[s:e] += 0.10 * nz * np.sin(np.pi * xs) ** 2
        s2 = int(tr * SR)
        e2 = min(n, s2 + int(0.09 * SR))
        if e2 > s2:
            tp = np.arange(e2 - s2) / SR
            audio[s2:e2] += 0.10 * np.sin(2 * np.pi * (320 + 480 * tp / 0.09) * tp) * np.exp(-tp * 38.0)
    return audio.astype(np.float32)


def load_music_file(total):
    """إذا حطيت ملفات موسيقى (mp3/wav) بمجلد music يستخدمها بدل الموسيقى المولّدة"""
    files = []
    for ext in ("mp3", "wav", "m4a", "ogg"):
        files += glob.glob(os.path.join("music", f"*.{ext}"))
    if not files:
        return None
    try:
        f = random.choice(files)
        x = decode_audio(f).astype(np.float64)
        if len(x) < SR:
            return None
        reps = int(total // len(x)) + 1
        x = np.tile(x, reps)[:total]
        pk = np.max(np.abs(x))
        if pk > 0:
            x = x / pk * 0.16
        print("موسيقى الخلفية:", os.path.basename(f))
        return x
    except Exception as e:
        print("تعذر تحميل ملف الموسيقى:", e)
        return None


def build_final_audio(tracks, transitions, out_wav):
    voice = np.concatenate(tracks).astype(np.float64)
    pk = np.max(np.abs(voice))
    if pk > 0:
        voice = voice / pk * 0.9
    total = len(voice)
    music = load_music_file(total)
    if music is None:
        music = generate_music(total / SR, transitions).astype(np.float64)
    if len(music) < total:
        music = np.pad(music, (0, total - len(music)))
    music = music[:total]
    a = np.abs(voice)
    c = np.cumsum(np.concatenate([[0.0], a]))
    half = int(0.04 * SR)
    idx = np.arange(total)
    lo = np.clip(idx - half, 0, total)
    hi = np.clip(idx + half, 0, total)
    sm = (c[hi] - c[lo]) / np.maximum(hi - lo, 1)
    duck = 1.0 - 0.55 * np.clip(sm / 0.08, 0, 1)
    mix_a = voice + music * duck
    pk = np.max(np.abs(mix_a))
    if pk > 0.95:
        mix_a *= 0.95 / pk
    pcm = np.clip(mix_a * 32767.0, -32768, 32767).astype(np.int16)
    with wave.open(out_wav, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm.tobytes())
    return out_wav


# ============================================================
# المفاتيح + اسم الصفحة + السجل
# ============================================================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN")
FB_PAGE_ID = os.getenv("FB_PAGE_ID")
genai.configure(api_key=GEMINI_API_KEY, transport="rest")


def get_page_name():
    env_name = os.getenv("PAGE_NAME", "").strip()
    if env_name:
        print(f"اسم الصفحة (من PAGE_NAME): {env_name}")
        return env_name
    if FB_ACCESS_TOKEN and FB_PAGE_ID:
        try:
            res = requests.get(
                f"https://graph.facebook.com/{GRAPH}/{FB_PAGE_ID}",
                params={"fields": "name", "access_token": FB_ACCESS_TOKEN}, timeout=10
            ).json()
            name = res.get("name", "").strip()
            if name:
                print(f"اسم الصفحة المعتمد: {name}")
                return name
            print("ما رجع اسم من فيسبوك:", res)
        except Exception as e:
            print("تعذر جلب اسم الصفحة:", e)
    return "إضاءات ومعرفة"


def load_topic_history(history_file="topics_history.txt", limit=30):
    if not os.path.exists(history_file):
        return []
    with open(history_file, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]
    return lines[-limit:]


def save_topic_history(topic, history_file="topics_history.txt"):
    topic = (topic or "").strip()
    if topic and topic not in load_topic_history(history_file, limit=200):
        with open(history_file, "a", encoding="utf-8") as f:
            f.write(topic + "\n")


# ============================================================
# توليد السيناريو
# ============================================================
def pick_models():
    names = []
    try:
        for m in genai.list_models():
            n = m.name
            if ("generateContent" in m.supported_generation_methods and "flash" in n
                    and not any(x in n for x in ("image", "tts", "embedding", "live", "thinking", "exp", "preview"))):
                names.append(n)
    except Exception as e:
        print("تعذر جلب النماذج:", e)
    pref = ["2.5-flash", "2.0-flash", "1.5-flash"]
    names.sort(key=lambda n: next((i for i, p in enumerate(pref) if p in n), 9) + (0.5 if "lite" in n else 0))
    return names[:4] or ["models/gemini-1.5-flash"]


def sanitize_script(data, outro_scene, cat_queries):
    scenes = []
    for s in data.get("scenes", []):
        text = clean_speech(str(s.get("text", "")))
        if not text or "تابع" in text:
            continue
        q = str(s.get("query", "")).strip()[:60] or random.choice(cat_queries)
        scenes.append({"text": text, "query": q, "outro": False})
    if len(scenes) < 3:
        raise ValueError("عدد المشاهد غير كافٍ")
    scenes.append(outro_scene)
    return {
        "title": str(data.get("title", "إضاءات ومعلومات مفيدة!")).strip(),
        "topic": str(data.get("topic", data.get("title", ""))).strip(),
        "caption": str(data.get("caption", "")).strip(),
        "scenes": scenes,
        "cat_queries": cat_queries,
    }


async def generate_script(page_name):
    print("جاري توليد السيناريو...")
    outro_scene = {"text": f"تابعوا صفحة {page_name} للمزيد من الفوائد",
                   "query": "abstract neon light particles", "outro": True}

    length_profiles = [
        {"name": "سريع", "scenes_count": "4", "words": "5 إلى 7 كلمات",
         "style": "اجعل الطرح سريعاً ومباشراً ومكثف الفائدة."},
        {"name": "متوسط", "scenes_count": "6", "words": "5 إلى 8 كلمات",
         "style": "اشرح الفكرة بوضوح مع ذكر السبب أو الفائدة العملية."},
        {"name": "مفصّل", "scenes_count": "8", "words": "6 إلى 9 كلمات",
         "style": "قدّم شرحاً ممتعاً بأسلوب قصصي أو خطوات عملية متتالية."},
    ]
    prof = random.choices(length_profiles, weights=[3, 5, 2], k=1)[0]

    cats = [
        {"category": "شرح حديث نبوي شريف",
         "instruction": "اذكر حديثاً نبوياً صحيحاً وقصيراً ومشهوراً بدقة (مع ذكر من رواه باختصار)، ثم اشرح معناه وكيف نطبقه في حياتنا اليومية. لا تذكر حديثاً إلا إذا كنت متأكداً تماماً من صحته ونصه.",
         "hashtags": "#أحاديث #السنة_النبوية #تذكير #إسلاميات",
         "queries": ["mosque sunrise", "quran book light", "sky clouds timelapse", "desert sunset", "calm river nature"]},
        {"category": "معلومات عامة وعلمية مذهلة",
         "instruction": "قدّم معلومة غريبة ومدهشة وصحيحة في العلوم أو التاريخ أو الفضاء أو جسم الإنسان أو التكنولوجيا بأسلوب يثير الفضول.",
         "hashtags": "#معلومات #هل_تعلم #ثقافة #حقائق",
         "queries": ["galaxy space stars", "earth from space", "microscope science", "futuristic technology", "ocean deep"]},
        {"category": "مقولات وحكم مفيدة",
         "instruction": "اختر مقولة حكيمة أو قاعدة ذهبية ملهمة، واشرح مغزاها وكيف تغيّر نظرتنا للحياة.",
         "hashtags": "#حكم #مقولات #تطوير_الذات #اقتباسات",
         "queries": ["mountain sunrise", "walking road nature", "old library books", "ocean waves", "forest fog"]},
        {"category": "نصائح دينية وإيمانية",
         "instruction": "قدّم نصيحة إيمانية لطيفة حول ذكر الله أو الطمأنينة أو بر الوالدين أو الصدقة أو إصلاح القلب بأسلوب دافئ، بدون نسبة أي نص للشرع إلا إذا كنت متأكداً منه.",
         "hashtags": "#نصائح_دينية #تذكير #طمأنينة #ذكر",
         "queries": ["mosque night", "rain window calm", "sunrise clouds", "quran pages", "peaceful lake"]},
        {"category": "نصائح مالية وإدارة الأموال",
         "instruction": "قدّم نصيحة مالية عملية عامة (بدون وعود بأرباح) حول الادخار الذكي أو تجنب التبذير أو بناء دخل إضافي أو العقلية المالية.",
         "hashtags": "#نصائح_مالية #إدارة_المال #نجاح #ادخار",
         "queries": ["coins money saving", "city business skyline", "calculator desk", "stock chart screen", "wallet cash"]},
        {"category": "نصائح دنيوية وتطوير الذات",
         "instruction": "قدّم نصيحة حياتية ذكية في فن التعامل مع الناس أو بناء العادات أو الطاقة النفسية أو تنظيم الوقت.",
         "hashtags": "#نصائح_حياتية #تطوير_الذات #نجاح #حياة",
         "queries": ["sunrise running", "clock time lapse", "writing notebook desk", "city walking crowd", "mountain climbing"]},
    ]
    cat = random.choice(cats)
    past = load_topic_history()
    avoid = f"\nمهم جداً: لا تكرر أياً من هذه المواضيع المنشورة سابقاً: {', '.join(past)}." if past else ""
    print(f"القسم: {cat['category']} | النمط: {prof['name']}")

    prompt = f"""
اكتب سيناريو فيديو ريلز شيق ومفيد جداً باللغة العربية الفصحى البسيطة في قسم: ({cat['category']}).
التوجيه: {cat['instruction']}
{prof['style']}{avoid}
يجب أن يكون النص مقسماً إلى {prof['scenes_count']} مشاهد بالضبط (كل مشهد جملة واحدة من {prof['words']}).
المشهد الأول لازم يكون "خطّاف" يشد الانتباه فوراً (سؤال صادم أو معلومة مفاجئة) خلال أول ثانيتين.
المشهد الأخير سؤال تفاعلي قصير يشجع على التعليق، ولا تكتب دعوة لمتابعة الصفحة لأنها تضاف تلقائياً.
لكل مشهد اكتب "query": كلمتان إلى أربع كلمات بالإنجليزية تصف لقطة فيديو واقعية سينمائية مناسبة للمعنى للبحث عنها في Pexels
(مثل: "sunrise mountain", "coins falling", "mosque night"). تجنب وجوه الأشخاص خصوصاً في المحتوى الديني، وفضّل الطبيعة والسماء والمساجد والكتب والمدن.

أرجع JSON فقط:
{{
  "title": "عنوان غلاف جذاب من 3 إلى 5 كلمات",
  "topic": "وصف الموضوع الفعلي في جملة قصيرة (لمنع التكرار)",
  "caption": "وصف الريلز مع هاشتاغات مثل {cat['hashtags']}",
  "scenes": [{{"text": "...", "query": "sunrise mountain"}}]
}}
"""
    for name in pick_models():
        for attempt in range(2):
            try:
                model = genai.GenerativeModel(name)
                try:
                    resp = model.generate_content(
                        prompt,
                        generation_config={"response_mime_type": "application/json", "temperature": 0.9},
                        request_options={"timeout": 60},
                    )
                except TypeError:
                    resp = model.generate_content(prompt, request_options={"timeout": 60})
                text = resp.text.strip()
                if "```" in text:
                    text = text.split("```json")[-1].split("```")[0].strip() if "```json" in text else text.split("```")[1].strip()
                data = sanitize_script(json.loads(text), outro_scene, cat["queries"])
                print(f"تم التوليد بالنموذج: {name}")
                save_topic_history(data["topic"] or data["title"])
                return data
            except Exception as e:
                print(f"محاولة فاشلة ({name} #{attempt + 1}): {e}")
                time.sleep(2)

    print("!!! استخدمت سيناريو احتياطي ثابت (تحقق من مفتاح Gemini)")
    S = lambda t, q: {"text": t, "query": q, "outro": False}
    fallback = [
        {"title": "قاعدة الادخار الذكية!",
         "caption": "كيف تحافظ على راتبك وتبني أمانك المالي؟ 💰📈 #نصائح_مالية #إدارة_المال #نجاح",
         "cat_queries": ["coins money saving", "city business skyline"],
         "scenes": [
             S("أكبر خطأ مالي يقع فيه الأغلبية كل شهر", "wallet cash"),
             S("هو إنفاق الراتب أولاً ثم ادخار ما يتبقى", "coins falling"),
             S("القاعدة الذهبية تقول ادخر لنفسك أولاً", "stock chart screen"),
             S("استقطع عشرة بالمئة فور استلام دخلك", "calculator desk"),
             S("كم نسبة ادخارك الشهرية؟ شاركنا بالتعليقات!", "city business skyline"),
             outro_scene]},
        {"title": "حكمة تغيّر حياتك!",
         "caption": "لا تستهين بالخطوات الصغيرة المستمرة 🌟🧭 #حكم #تطوير_الذات #نجاح",
         "cat_queries": ["mountain sunrise", "forest fog"],
         "scenes": [
             S("قليل دائم خير من كثير منقطع", "mountain sunrise"),
             S("النجاح الكبير لا يأتي من قفزة واحدة", "walking road nature"),
             S("بل من عادات بسيطة تكررها كل يوم", "clock time lapse"),
             S("ابدأ اليوم ولو بعشر دقائق", "sunrise running"),
             S("ما العادة التي تتمنى الالتزام بها؟ شاركنا!", "ocean waves"),
             outro_scene]},
    ]
    return random.choice(fallback)


# ============================================================
# فيديوهات الخلفية (Pexels)
# ============================================================
def pexels_fetch(query, path, used):
    if not PEXELS_KEY or not query:
        return False
    try:
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": PEXELS_KEY},
            params={"query": query, "orientation": "portrait", "size": "medium", "per_page": 15},
            timeout=20,
        )
        if r.status_code != 200:
            print(f"  Pexels رد {r.status_code} للكلمة '{query}'")
            return False
        vids = [v for v in r.json().get("videos", []) if v.get("id") not in used and v.get("duration", 0) >= 3]
        random.shuffle(vids)
        for v in vids[:6]:
            files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("height")]
            port = [f for f in files if f["height"] >= f.get("width", 0)]
            files = port or files
            if not files:
                continue
            ge = [f for f in files if f["height"] >= 1600]
            best = min(ge, key=lambda f: f["height"]) if ge else max(files, key=lambda f: f["height"])
            try:
                with requests.get(best["link"], stream=True, timeout=60) as resp:
                    resp.raise_for_status()
                    with open(path, "wb") as fh:
                        for chunk in resp.iter_content(chunk_size=1 << 16):
                            fh.write(chunk)
                if os.path.getsize(path) > 20000:
                    used.add(v["id"])
                    return True
            except Exception as e:
                print("  فشل تنزيل لقطة:", e)
        return False
    except Exception as e:
        print("  خطأ Pexels:", e)
        return False


def fetch_clips(scenes, cat_queries):
    if not PEXELS_KEY:
        print("!!! PEXELS_API_KEY غير موجود: راح تُستخدم خلفية متدرجة بدل الفيديو")
        return [None] * len(scenes)
    used, clips = set(), []
    for i, sc in enumerate(scenes):
        path = f"clip_{i}.mp4"
        qs = [sc.get("query", "")] + random.sample(cat_queries, min(2, len(cat_queries)))
        ok = False
        for q in qs:
            if pexels_fetch(q, path, used):
                print(f"  لقطة المشهد {i + 1}: '{q}'")
                ok = True
                break
        clips.append(path if ok else None)
        if not ok:
            print(f"  ما لقيت لقطة للمشهد {i + 1}، خلفية بديلة")
    return clips


class BG:
    """يقرأ فريمات الخلفية من ffmpeg (مع تكرار اللقطة إذا أقصر من المشهد)"""

    def __init__(self, clip, dur, fallback):
        self.fallback = fallback
        self.last = None
        self.proc = None
        if clip and os.path.exists(clip):
            vf = (f"fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,"
                  f"crop={W}:{H},setsar=1")
            cmd = [FFMPEG_BIN, "-v", "quiet", "-stream_loop", "-1", "-i", clip,
                   "-t", f"{dur + 1:.2f}", "-an", "-vf", vf,
                   "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
            try:
                self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                             bufsize=W * H * 3 * 2)
            except Exception as e:
                print("تعذر تشغيل ffmpeg للقطة:", e)
                self.proc = None

    def read(self):
        n = W * H * 3
        if self.proc:
            buf = self.proc.stdout.read(n)
            if buf and len(buf) == n:
                self.last = np.frombuffer(buf, dtype=np.uint8).reshape(H, W, 3)
                return self.last, True
            self._kill()
        if self.last is not None:
            return self.last, True
        return self.fallback, False

    def _kill(self):
        if self.proc:
            try:
                self.proc.kill()
                self.proc.wait()
            except Exception:
                pass
            self.proc = None

    def close(self):
        self._kill()


def make_fallback_bg(accent):
    yy = np.arange(H, dtype=np.float32)[:, None]
    xx = np.arange(W, dtype=np.float32)[None, :]
    top = np.array(hex_rgb("#0B1226"), np.float32)
    bot = np.array(hex_rgb("#05070F"), np.float32)
    t = (yy / H)[..., None]
    base = top * (1 - t) + bot * t
    ac = np.array(hex_rgb(accent), np.float32)
    gx, gy = W * random.choice([0.3, 0.7]), H * 0.45
    glow = np.exp(-(((xx - gx) ** 2 + (yy - gy) ** 2) / (520.0 ** 2)))[..., None] * 0.35
    base = base * (1 - glow) + ac * glow
    return np.clip(base, 0, 255).astype(np.uint8)


def build_shade():
    yy = np.linspace(-1, 1, H, dtype=np.float32)[:, None]
    xx = np.linspace(-1, 1, W, dtype=np.float32)[None, :]
    r2 = (xx ** 2 + yy ** 2) / 2.0
    shade = 0.68 - 0.30 * r2
    shade = shade * (1.0 - 0.18 * np.clip((np.abs(yy) - 0.7) / 0.3, 0, 1))
    return shade[..., None].astype(np.float32)


SHADE = build_shade()


# ============================================================
# صور النصوص والعناصر
# ============================================================
def make_word_sprite(shaped, font, fill, stroke=6, glow=None, shadow=True):
    kw = tk(font)
    probe = ImageDraw.Draw(Image.new("L", (4, 4)))
    bb = probe.textbbox((0, 0), shaped, stroke_width=stroke, **kw)
    pad = stroke * 3 + 28 + (44 if glow else 0)
    bw, bh = bb[2] - bb[0], bb[3] - bb[1]
    w, h = bw + 2 * pad, bh + 2 * pad
    ox, oy = pad - bb[0], pad - bb[1]

    def mask(sw):
        m = Image.new("L", (w, h), 0)
        ImageDraw.Draw(m).text((ox, oy), shaped, fill=255, stroke_width=sw, **kw)
        return m

    def layer(color, m, alpha=1.0):
        if alpha < 1:
            m = m.point(lambda v: int(v * alpha))
        l = Image.new("RGBA", (w, h), hex_rgb(color) + (255,))
        l.putalpha(m)
        return l

    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if glow:
        out = Image.alpha_composite(out, layer(glow, mask(stroke + 8).filter(ImageFilter.GaussianBlur(18)), 0.9))
    if shadow:
        sm = Image.new("L", (w, h), 0)
        sm.paste(mask(stroke), (stroke, int(stroke * 1.6)))
        sm = sm.filter(ImageFilter.GaussianBlur(stroke * 0.8 + 1))
        out = Image.alpha_composite(out, layer("#000000", sm, 0.8))
    if stroke > 0:
        out = Image.alpha_composite(out, layer("#050816", mask(stroke)))
    out = Image.alpha_composite(out, layer(fill, mask(0)))
    return {"img": out, "pad": pad, "oy": oy, "bw": bw, "bh": bh, "w": w, "h": h}


def half(img):
    return img.resize((max(1, img.width // 2), max(1, img.height // 2)), LANCZOS)


def make_badge(page_name, font2, accent, k=1.0):
    sp = make_word_sprite(shape_text(page_name), font2, "#F1F5F9", stroke=2, shadow=False)
    txt = half(sp["img"])
    tw, th = int(sp["bw"] / 2), int(sp["bh"] / 2)
    w, h = tw + 110, max(th + 36, 70)
    big = Image.new("RGBA", (w * 2, h * 2), (0, 0, 0, 0))
    bd = ImageDraw.Draw(big)
    bd.rounded_rectangle([(2, 2), (w * 2 - 3, h * 2 - 3)], radius=h, fill=(10, 16, 34, 225),
                         outline=hex_rgb(accent) + (255,), width=5)
    bd.ellipse([(w * 2 - 74, h - 14), (w * 2 - 46, h + 14)], fill=hex_rgb(accent) + (255,))
    badge = big.resize((w, h), LANCZOS)
    badge.paste(txt, (int((w - 48) / 2 - txt.width / 2), (h - txt.height) // 2), txt)
    if k != 1.0:
        badge = badge.resize((int(badge.width * k), int(badge.height * k)), LANCZOS)
    if badge.width > 1000:
        r = 1000 / badge.width
        badge = badge.resize((1000, int(badge.height * r)), LANCZOS)
    return badge


def make_button(text, font2, color):
    sp = make_word_sprite(shape_text(text), font2, "#FFFFFF", stroke=2, shadow=False)
    txt = half(sp["img"])
    tw, th = int(sp["bw"] / 2), int(sp["bh"] / 2)
    w, h = tw + 120, max(th + 50, 100)
    big = Image.new("RGBA", (w * 2, h * 2), (0, 0, 0, 0))
    ImageDraw.Draw(big).rounded_rectangle([(2, 2), (w * 2 - 3, h * 2 - 3)], radius=h,
                                          fill=hex_rgb(color) + (255,), outline=(255, 255, 255, 255), width=6)
    btn = big.resize((w, h), LANCZOS)
    btn.paste(txt, ((w - txt.width) // 2, (h - txt.height) // 2), txt)
    return btn


def make_title_card(title, font_path, accent):
    cw, ch, K = 920, 250, 2
    big = Image.new("RGBA", (cw * K, ch * K), (0, 0, 0, 0))
    bd = ImageDraw.Draw(big)
    bd.rounded_rectangle([(4, 4), (cw * K - 5, ch * K - 5)], radius=70, fill=(8, 12, 28, 235),
                         outline=hex_rgb(accent) + (255,), width=10)
    text = shape_multiline(title, max_chars=20)
    chosen = None
    for size in range(74 * K, 40 * K, -4 * K):
        f = load_font(font_path, size)
        kw = {"font": f, "align": "center", "spacing": 14 * K}
        if HAS_RAQM:
            kw["direction"] = "rtl"
        tb = bd.multiline_textbbox((0, 0), text, **kw)
        if tb[2] - tb[0] <= (cw - 70) * K and tb[3] - tb[1] <= (ch - 50) * K:
            chosen = (kw, tb)
            break
    if chosen is None:
        f = load_font(font_path, 40 * K)
        kw = {"font": f, "align": "center", "spacing": 14 * K}
        if HAS_RAQM:
            kw["direction"] = "rtl"
        chosen = (kw, bd.multiline_textbbox((0, 0), text, **kw))
    kw, tb = chosen
    x = (cw * K - (tb[2] - tb[0])) // 2 - tb[0]
    y = (ch * K - (tb[3] - tb[1])) // 2 - tb[1]
    bd.multiline_text((x + 6, y + 8), text, fill=(2, 6, 23, 255), **kw)
    bd.multiline_text((x, y), text, fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(255, 255, 255, 255), **kw)
    return big.resize((cw, ch), LANCZOS)


# ============================================================
# الكابشن: مجموعات من 2-3 كلمات، الكلمة المنطوقة تتلوّن وتكبر
# ============================================================
PROBE = ImageDraw.Draw(Image.new("L", (4, 4)))


def layout_words(widths, max_w=900, sw=26):
    lines, cur, cw = [], [], 0.0
    for i, w in enumerate(widths):
        add = w + (sw if cur else 0)
        if cur and cw + add > max_w:
            lines.append((cur, cw))
            cur, cw = [(i, w)], w
        else:
            cur.append((i, w))
            cw += add
    if cur:
        lines.append((cur, cw))
    pos = {}
    for li, (line, tw) in enumerate(lines):
        right = tw / 2.0
        for i, w in line:
            pos[i] = (right - w / 2.0, li)
            right -= w + sw
    return pos, len(lines)


def build_chunks(words, timings, dur, font2):
    n = len(words)
    groups = max(1, math.ceil(n / 3))
    size = math.ceil(n / groups)
    chunks = []
    i = 0
    while i < n:
        idxs = list(range(i, min(n, i + size)))
        i += size
        shaped = [shape_word(words[k]) for k in idxs]
        widths = []
        for s in shaped:
            bb = PROBE.textbbox((0, 0), s, **tk(font2))
            widths.append((bb[2] - bb[0]) / 2.0)
        pos, nl = layout_words(widths)
        chunks.append({"idxs": idxs, "shaped": shaped, "pos": pos, "n": nl,
                       "start": timings[idxs[0]][0], "end": dur})
    for k in range(len(chunks) - 1):
        chunks[k]["end"] = chunks[k + 1]["start"]
    if chunks:
        chunks[0]["start"] = 0.0
    return chunks


def draw_captions(out, c, t, env, beat):
    chunks = c["chunks"]
    ch = None
    for k in chunks:
        if k["start"] <= t:
            ch = k
    if ch is None:
        return
    timings = c["timings"]
    cur = None
    for i in ch["idxs"]:
        if timings[i][0] - 0.02 <= t:
            cur = i
    local = t - ch["start"]
    e = clamp01(local / 0.22)
    sc_chunk = 0.72 + 0.28 * ease_out_back(e)
    alpha = ease_out_cubic(e)
    lh = CAP_SIZE * 1.55
    base_y = c["cap_y"] + math.sin(t * 1.8 + c["idx"]) * 6
    n = ch["n"]

    for j, i in enumerate(ch["idxs"]):
        speaking = (i == cur)
        key = (i, speaking)
        if key not in c["sprites"]:
            if speaking:
                c["sprites"][key] = make_word_sprite(ch["shaped"][j], c["font2"], YELLOW, stroke=7, glow=c["accent"])
            else:
                c["sprites"][key] = make_word_sprite(ch["shaped"][j], c["font2"], "#FFFFFF", stroke=7)
        spr = c["sprites"][key]
        rel_x, line = ch["pos"][j]
        s = 0.5 * sc_chunk * ((1.12 + 0.07 * beat + 0.03 * env) if speaking else 1.0)
        im = spr["img"]
        rs = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), LANCZOS)
        if alpha < 0.99:
            rs.putalpha(rs.getchannel("A").point(lambda v: int(v * alpha)))
        cx = 540 + rel_x * sc_chunk
        anchor = base_y + (line - (n - 1) / 2.0) * lh - CAP_SIZE * 0.55 + (1 - sc_chunk) * 40
        py = anchor - spr["oy"] * s - (7 if speaking else 0)
        out.paste(rs, (int(cx - rs.width / 2), int(py)), rs)


# ============================================================
# رسم الفريم
# ============================================================
def render_frame(c, f, bg_arr, is_video, g_prog):
    t = f / FPS
    env = float(c["env"][min(f, len(c["env"]) - 1)])
    beat = float(c["beat"][min(f, len(c["beat"]) - 1)])
    accent = c["accent"]

    if is_video:
        arr = (bg_arr.astype(np.float32) * SHADE).astype(np.uint8)
    else:
        arr = bg_arr
    img = Image.fromarray(arr)

    p = ease_in_out(t / max(0.1, c["duration"]))
    punch = 0.09 * max(0.0, 1 - t / 0.28) ** 2
    z = 1.0 + 0.05 * p + punch + 0.012 * beat
    if z > 1.002:
        cw, chh = W / z, H / z
        l, tp = (W - cw) / 2, (H - chh) / 2
        img = img.resize((W, H), BILINEAR, box=(l, tp, l + cw, tp + chh))

    if c["idx"] > 0 and f < 4:
        img = Image.blend(img, Image.new("RGB", (W, H), "white"), 0.25 * (1 - f / 4.0))

    badge = c["badge"]
    img.paste(badge, ((W - badge.width) // 2, 118), badge)

    if c["title_card"] is not None and t < 2.9:
        pp = clamp01((t - 2.3) / 0.6)
        enter = ease_out_back(clamp(t / 0.4)) if False else ease_out_back(clamp01(t / 0.4))
        card = c["title_card"]
        if pp > 0:
            card = card.copy()
            card.putalpha(card.getchannel("A").point(lambda v: int(v * (1 - pp))))
        y = int(260 - (1 - enter) * 220 - pp * pp * 300)
        img.paste(card, ((W - card.width) // 2, y), card)

    if c["outro"]:
        pulse = 1.0 + 0.05 * math.sin(t * 6)
        big = c["big_badge"]
        bs = big.resize((int(big.width * (0.9 + 0.1 * ease_out_back(t / 0.5))), int(big.height * (0.9 + 0.1 * ease_out_back(t / 0.5)))), LANCZOS)
        img.paste(bs, ((W - bs.width) // 2, 700), bs)
        btn = c["button"]
        bt = btn.resize((int(btn.width * pulse), int(btn.height * pulse)), LANCZOS)
        img.paste(bt, ((W - bt.width) // 2, 880 - (bt.height - btn.height) // 2), bt)

    draw_captions(img, c, t, env, beat)

    dr = ImageDraw.Draw(img)
    dr.rectangle([(0, 0), (W, 10)], fill="#1E293B")
    pw = int(W * clamp01(g_prog))
    if pw > 0:
        dr.rectangle([(0, 0), (pw, 10)], fill=accent)
    return img


# ============================================================
# تجميع الفيديو
# ============================================================
async def make_video(script, page_name):
    print("بدء صناعة الريلز...")
    scenes = script["scenes"]
    title = script.get("title", "إضاءات ومعلومات مفيدة!")

    # 1) الصوت
    tracks, scene_words, scene_bounds = [], [], []
    for idx, sc in enumerate(scenes):
        text = clean_speech(sc["text"])
        mp3 = f"scene_{idx}.mp3"
        bounds = await tts_scene(text, VOICE, mp3)
        x = decode_audio(mp3)
        os.remove(mp3)
        x, off = trim_silence(x)
        if len(x) < SR * 0.3:
            raise RuntimeError(f"صوت المشهد {idx} فارغ")
        for b in bounds:
            b["start"] = max(0.0, b["start"] - off)
            b["end"] = max(b["start"], b["end"] - off)
        tracks.append(x)
        scene_words.append([w for w in text.split() if w.strip()])
        scene_bounds.append(bounds)
        print(f"  مشهد {idx + 1}/{len(scenes)}: {len(x) / SR:.2f}ث | boundaries={len(bounds)} كلمات={len(scene_words[-1])}")

    durations = [len(x) / SR for x in tracks]
    total_dur = sum(durations)
    print(f"المدة الكلية: {total_dur:.1f} ثانية")

    transitions, cum = [], 0.0
    for dd in durations:
        if cum > 0:
            transitions.append(cum)
        cum += dd
    audio_wav = build_final_audio(tracks, transitions, "final_audio.wav")

    # 2) لقطات الخلفية
    print("جلب لقطات الفيديو...")
    clips = fetch_clips(scenes, script.get("cat_queries") or ["abstract light"])

    # 3) الخطوط
    font_path = select_random_font()
    font_cap2 = load_font(font_path, CAP_SIZE * 2)
    font_badge2 = load_font(font_path, 34 * 2)
    font_btn2 = load_font(font_path, 48 * 2)

    # 4) ffmpeg
    final_output = "final_reel.mp4"
    log = open("ffmpeg_log.txt", "wb")
    cmd = [FFMPEG_BIN, "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-i", audio_wav, "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-profile:v", "high",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
           "-shortest", final_output]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=log)

    total_frames = max(1, int(round(total_dur * FPS)))
    cum, g_frame = 0.0, 0
    prev_y = None
    cap_ys = [860, 1020, 1180, 1300]
    rc = -1

    try:
        for idx, sc in enumerate(scenes):
            is_outro = bool(sc.get("outro"))
            start_f = int(round(cum * FPS))
            cum += durations[idx]
            end_f = int(round(cum * FPS))
            n_frames = max(1, end_f - start_f)

            words = scene_words[idx]
            timings = align_words(words, scene_bounds[idx], durations[idx])
            env = frame_envelope(tracks[idx], n_frames)
            starts = np.array([s for s, _ in timings])
            beat = np.zeros(n_frames)
            for f in range(n_frames):
                tt = f / FPS
                prior = starts[starts <= tt]
                beat[f] = math.exp(-(tt - prior[-1]) * 9.0) if len(prior) else 0.0

            accent = PALETTE[idx % len(PALETTE)]
            if is_outro:
                cap_y = 1330
            elif idx == 0:
                cap_y = 1150
            else:
                cap_y = random.choice([y for y in cap_ys if y != prev_y])
            prev_y = cap_y

            c = {
                "idx": idx, "accent": accent, "env": env, "beat": beat, "timings": timings,
                "duration": n_frames / FPS, "outro": is_outro, "cap_y": cap_y,
                "chunks": build_chunks(words, timings, durations[idx], font_cap2),
                "sprites": {}, "font2": font_cap2,
                "badge": make_badge(page_name, font_badge2, accent, 1.0),
                "big_badge": make_badge(page_name, font_badge2, accent, 1.9) if is_outro else None,
                "button": make_button("تابع الصفحة", font_btn2, "#1877F2") if is_outro else None,
                "title_card": make_title_card(title, font_path, accent) if idx == 0 else None,
            }

            bg = BG(clips[idx], durations[idx], make_fallback_bg(accent))
            print(f"ريندر المشهد {idx + 1}/{len(scenes)} | موضع النص={cap_y}")
            try:
                for f in range(n_frames):
                    bg_arr, is_video = bg.read()
                    frame = render_frame(c, f, bg_arr, is_video, g_frame / float(total_frames))
                    if idx == 0 and f == min(n_frames - 1, int(1.3 * FPS)):
                        frame.save("cover.jpg", quality=92)
                    proc.stdin.write(frame.tobytes())
                    g_frame += 1
            finally:
                bg.close()
    except BrokenPipeError:
        print("ffmpeg وقف فجأة، شوف ffmpeg_log.txt")
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        rc = proc.wait()
        log.close()

    for p in clips:
        if p and os.path.exists(p):
            os.remove(p)

    if rc != 0 or not os.path.exists(final_output):
        with open("ffmpeg_log.txt", "rb") as lf:
            print(lf.read()[-1500:].decode("utf-8", "ignore"))
        raise RuntimeError("فشل ffmpeg")

    if os.path.exists(audio_wav):
        os.remove(audio_wav)
    print(f"اكتمل الفيديو: {final_output} | الغلاف: cover.jpg")
    return final_output


# ============================================================
# النشر على فيسبوك
# ============================================================
def fb_post(url, **kw):
    for attempt in range(3):
        try:
            return requests.post(url, timeout=120, **kw)
        except requests.RequestException as e:
            print(f"خطأ شبكة ({attempt + 1}/3): {e}")
            time.sleep(3 * (attempt + 1))
    return None


def wait_for_video(video_id, timeout=240):
    url = f"https://graph.facebook.com/{GRAPH}/{video_id}"
    end = time.time() + timeout
    while time.time() < end:
        try:
            res = requests.get(url, params={"fields": "status", "access_token": FB_ACCESS_TOKEN}, timeout=30).json()
        except Exception:
            res = {}
        st = res.get("status", {}) if isinstance(res, dict) else {}
        vs = st.get("video_status")
        print("حالة الفيديو:", vs)
        if vs == "ready":
            return True
        if vs == "error":
            print(json.dumps(st, ensure_ascii=False))
            return False
        time.sleep(10)
    return None


def publish_to_facebook(video_path, title, caption):
    print("بدء النشر على فيسبوك ريلز...")
    if not FB_ACCESS_TOKEN or not FB_PAGE_ID:
        print("خطأ: FB_ACCESS_TOKEN أو FB_PAGE_ID غير موجود!")
        sys.exit(1)

    base = f"https://graph.facebook.com/{GRAPH}/{FB_PAGE_ID}/video_reels"
    r = fb_post(base, params={"access_token": FB_ACCESS_TOKEN, "upload_phase": "start"})
    init = r.json() if r is not None else {}
    print("بدء الجلسة:", init)
    video_id, upload_url = init.get("video_id"), init.get("upload_url")
    if not video_id or not upload_url:
        print("فشل بدء الرفع! راجع صلاحيات التوكن.")
        sys.exit(1)

    headers = {"Authorization": f"OAuth {FB_ACCESS_TOKEN}", "offset": "0",
               "file_size": str(os.path.getsize(video_path)), "Content-Type": "application/octet-stream"}
    with open(video_path, "rb") as f:
        up = requests.post(upload_url, headers=headers, data=f, timeout=900)
    print("رفع الملف:", up.status_code, up.text[:300])
    try:
        up_ok = up.ok and bool(up.json().get("success"))
    except Exception:
        up_ok = False
    if not up_ok:
        print("فشل رفع الملف!")
        sys.exit(1)

    r = fb_post(base, params={"access_token": FB_ACCESS_TOKEN, "video_id": video_id, "upload_phase": "finish",
                              "video_state": "PUBLISHED", "title": title, "description": caption})
    fin = r.json() if r is not None else {}
    print("النشر النهائي:", fin)
    if not (fin.get("success") or "video_id" in fin):
        print("فشل النشر النهائي!")
        sys.exit(1)

    status = wait_for_video(video_id)
    if status is False:
        print("فيسبوك رفض الفيديو أثناء المعالجة!")
        sys.exit(1)
    if status is None:
        print("انتهت مهلة الانتظار، الحالة غير معروفة (راجع الصفحة يدوياً).")
    else:
        print("تم نشر الريلز بنجاح!")
    return True


async def main():
    page_name = get_page_name()
    script = await generate_script(page_name)
    video = await make_video(script, page_name)
    if os.getenv("DRY_RUN") == "1":
        print("DRY_RUN: تم تخطي النشر.")
        return
    publish_to_facebook(video, script["title"], script["caption"])


if __name__ == "__main__":
    asyncio.run(main())
