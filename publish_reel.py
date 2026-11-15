import os
import sys
import re
import json
import math
import wave
import random
import shutil
import asyncio
import textwrap
import subprocess
import requests
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops, features
import google.generativeai as genai
import edge_tts

# تثبيت المكتبات المطلوبة تلقائياً
subprocess.run(
    [sys.executable, "-m", "pip", "install", "-q", "arabic-reshaper", "python-bidi", "mutagen"],
    check=False
)

HAS_RAQM = features.check("raqm")

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except ImportError:
    arabic_reshaper = None
    get_display = None

FFMPEG_BIN = shutil.which("ffmpeg")
if not FFMPEG_BIN:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "imageio-ffmpeg"], check=False)
    import imageio_ffmpeg
    FFMPEG_BIN = imageio_ffmpeg.get_ffmpeg_exe()


def select_random_font():
    all_fonts = []
    bold_or_regular_fonts = []
    search_dirs = [".", "fonts"]

    for folder in search_dirs:
        if os.path.isdir(folder):
            for fname in os.listdir(folder):
                if fname.lower().endswith((".ttf", ".otf")):
                    full_path = os.path.join(folder, fname)
                    all_fonts.append(full_path)
                    if "extralight" not in fname.lower() and "thin" not in fname.lower():
                        bold_or_regular_fonts.append(full_path)

    candidates = sorted(list(set(bold_or_regular_fonts or all_fonts)))
    if candidates:
        chosen = random.choice(candidates)
        print(f"الخط المختار لهذا الريلز: {os.path.basename(chosen)}")
        return chosen

    auto_font = "Cairo-Bold.ttf"
    if not os.path.exists(auto_font):
        try:
            url = "https://github.com/google/fonts/raw/main/ofl/cairo/static/Cairo-Bold.ttf"
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                with open(auto_font, "wb") as f:
                    f.write(r.content)
                return auto_font
        except Exception:
            pass

    fallback = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    return fallback if os.path.exists(fallback) else None


def shape_word(word):
    if HAS_RAQM:
        return word
    if arabic_reshaper and get_display:
        return get_display(arabic_reshaper.reshape(word))
    return word


def shape_multiline(text, max_chars=22):
    wrapped = textwrap.fill(text.strip(), width=max_chars)
    if HAS_RAQM:
        return wrapped
    if arabic_reshaper and get_display:
        return "\n".join(get_display(arabic_reshaper.reshape(line)) for line in wrapped.split("\n"))
    return wrapped


def hex_to_rgb(hex_col):
    h = hex_col.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def ease_out_back(t):
    t = max(0.0, min(1.0, t))
    c1 = 1.70158
    c3 = c1 + 1.0
    return 1.0 + c3 * ((t - 1.0) ** 3) + c1 * ((t - 1.0) ** 2)


def ease_out_cubic(t):
    t = max(0.0, min(1.0, t))
    return 1.0 - ((1.0 - t) ** 3)


BG_CACHE = {}


def get_studio_background(accent_hex, size=(1080, 1920)):
    if accent_hex in BG_CACHE:
        return BG_CACHE[accent_hex].copy()

    w, h = size
    ar, ag, ab = hex_to_rgb(accent_hex)

    y, x = np.ogrid[0:h, 0:w]
    cx, cy = w * 0.5, h * 0.42
    dist_sq = ((x - cx) / (w * 0.62)) ** 2 + ((y - cy) / (h * 0.48)) ** 2
    glow = np.exp(-dist_sq * 1.45)

    vig_dist = ((x - w * 0.5) / (w * 0.55)) ** 2 + ((y - h * 0.5) / (h * 0.55)) ** 2
    vignette = np.clip(1.0 - vig_dist * 0.38, 0.25, 1.0)

    r = np.clip((8 + glow * (ar * 0.28)) * vignette, 0, 255).astype(np.uint8)
    g = np.clip((13 + glow * (ag * 0.28)) * vignette, 0, 255).astype(np.uint8)
    b = np.clip((26 + glow * (ab * 0.32)) * vignette, 0, 255).astype(np.uint8)

    rgb = np.dstack((r, g, b))
    bg_img = Image.fromarray(rgb, "RGB")
    BG_CACHE[accent_hex] = bg_img
    return bg_img.copy()


def apply_neon_bloom(img, intensity=0.45):
    w, h = img.size
    small = img.resize((w // 4, h // 4), Image.Resampling.BILINEAR)
    blurred = small.filter(ImageFilter.GaussianBlur(radius=9))
    glow = blurred.resize((w, h), Image.Resampling.BILINEAR)
    screened = ImageChops.screen(img, glow)
    return Image.blend(img, screened, intensity)


def get_audio_duration(audio_file):
    try:
        from mutagen.mp3 import MP3
        return float(MP3(audio_file).info.length)
    except Exception:
        res = subprocess.run(
            [FFMPEG_BIN, "-i", audio_file],
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True
        )
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", res.stderr)
        if m:
            h, mins, secs = map(float, m.groups())
            return h * 3600 + mins * 60 + secs
        return 3.5


def analyze_audio_and_timings(audio_file, words, total_frames, duration, raw_boundaries):
    try:
        cmd = [
            FFMPEG_BIN, "-i", audio_file,
            "-f", "s16le", "-ac", "1", "-ar", "4000",
            "-loglevel", "quiet", "-"
        ]
        raw = subprocess.check_output(cmd)
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    except Exception:
        samples = np.array([], dtype=np.float32)

    if len(samples) > 0:
        chunks = np.array_split(np.abs(samples), total_frames)
        levels = [float(np.mean(c)) if len(c) > 0 else 0.0 for c in chunks]
        max_val = max(levels) if max(levels) > 0 else 1.0
        raw_env = [min(1.0, val / (max_val * 0.70)) for val in levels]

        envelope = []
        for i in range(len(raw_env)):
            left = raw_env[max(0, i - 1)]
            curr = raw_env[i]
            right = raw_env[min(len(raw_env) - 1, i + 1)]
            envelope.append(curr * 0.6 + (left + right) * 0.2)

        active_indices = [i for i, lvl in enumerate(envelope) if lvl > 0.12]
        if active_indices:
            speech_start = max(0.0, (active_indices[0] / total_frames) * duration - 0.05)
            speech_end = min(duration, ((active_indices[-1] + 1) / total_frames) * duration + 0.05)
        else:
            speech_start, speech_end = 0.05, max(0.2, duration - 0.05)
    else:
        envelope = [0.5] * total_frames
        speech_start, speech_end = 0.05, max(0.2, duration - 0.05)

    word_timings = []
    if raw_boundaries and len(raw_boundaries) == len(words):
        for idx, b in enumerate(raw_boundaries):
            st = b["start"]
            en = b["end"] if b["end"] > st else (
                raw_boundaries[idx + 1]["start"] if idx + 1 < len(raw_boundaries) else speech_end
            )
            word_timings.append((st, max(st + 0.15, en)))
    else:
        weights = [max(2, len(w)) for w in words]
        total_w = sum(weights) if sum(weights) > 0 else 1
        span = max(0.25, speech_end - speech_start)
        cursor = speech_start
        for w_weight in weights:
            w_dur = span * (w_weight / total_w)
            word_timings.append((cursor, cursor + w_dur))
            cursor += w_dur

    return envelope, word_timings


def generate_ambient_audio(scene_durations, output_wav="bg_audio.wav"):
    sr = 44100
    total_dur = max(1.0, sum(scene_durations))
    total_samples = int(total_dur * sr)
    t = np.linspace(0, total_dur, total_samples, endpoint=False)

    chords = [
        [220.00, 261.63, 329.63, 440.00],
        [174.61, 220.00, 261.63, 349.23],
        [130.81, 196.00, 261.63, 329.63],
        [196.00, 246.94, 293.66, 392.00],
    ]
    audio = np.zeros(total_samples, dtype=np.float32)
    chord_len = 4.0
    chord_idx = (t // chord_len).astype(int) % len(chords)

    for idx_c, freqs in enumerate(chords):
        mask = (chord_idx == idx_c)
        if np.any(mask):
            t_seg = t[mask]
            pad = sum(np.sin(2 * np.pi * f * t_seg) for f in freqs) / len(freqs)
            local_t = t_seg % chord_len
            env = np.minimum(1.0, local_t / 0.4) * np.minimum(1.0, (chord_len - local_t) / 0.4)
            audio[mask] += 0.040 * pad * env

    np.random.seed(42)
    cursor = 0.0
    for s_idx, d in enumerate(scene_durations):
        if s_idx > 0:
            start_sample = max(0, int((cursor - 0.08) * sr))
            sfx_len = int(0.24 * sr)
            end_sample = min(total_samples, start_sample + sfx_len)
            n_s = end_sample - start_sample
            if n_s > 0:
                tp = np.linspace(0, n_s / sr, n_s, endpoint=False)
                sub_sweep = 160.0 * np.exp(-tp * 14.0) + 45.0
                bass_hit = np.sin(2 * np.pi * sub_sweep * tp) * np.exp(-tp * 16.0)
                noise = np.random.normal(0, 1, n_s).astype(np.float32)
                window = np.sin(np.pi * (tp / (n_s / sr))) ** 2
                swoosh = np.convolve(noise, np.ones(18) / 18.0, mode="same") * window
                chime = np.sin(2 * np.pi * (520.0 + 380.0 * tp) * tp) * np.exp(-tp * 28.0)
                audio[start_sample:end_sample] += (0.16 * bass_hit + 0.07 * swoosh + 0.09 * chime)
        cursor += d

    audio_int16 = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
    with wave.open(output_wav, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(audio_int16.tobytes())
    return output_wav


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN")
FB_PAGE_ID = os.getenv("FB_PAGE_ID")
CUSTOM_PAGE_NAME = os.getenv("FB_PAGE_NAME", "").strip()

genai.configure(api_key=GEMINI_API_KEY, transport="rest")


def get_page_name():
    if CUSTOM_PAGE_NAME:
        return CUSTOM_PAGE_NAME
    if FB_ACCESS_TOKEN and FB_PAGE_ID:
        try:
            res = requests.get(
                f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}",
                params={"fields": "name", "access_token": FB_ACCESS_TOKEN},
                timeout=10
            ).json()
            name = res.get("name", "").strip()
            if name:
                print(f"اسم الصفحة المعتمد: {name}")
                return name
        except Exception as e:
            print("تعذر جلب اسم الصفحة تلقائياً:", e)
    return "إضاءات ومعرفة"


def load_topic_history(history_file="topics_history.txt", limit=30):
    if not os.path.exists(history_file):
        return []
    with open(history_file, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]
    return lines[-limit:]


def save_topic_history(title, history_file="topics_history.txt"):
    clean_title = title.strip()
    if not clean_title:
        return
    existing = load_topic_history(history_file, limit=120)
    if clean_title not in existing:
        with open(history_file, "a", encoding="utf-8") as f:
            f.write(clean_title + "\n")


async def generate_script(page_name):
    print("جاري توليد السيناريو والمشاهد البصرية...")
    outro_scene = {
        "text": f"تابعوا صفحة {page_name} للمزيد من الفائدة",
        "pose": "FOLLOW",
        "visual": "FOLLOW_CARD"
    }

    length_profiles = [
        {
            "name": "سريع (18-22 ثانية)",
            "scenes_count": "4",
            "words_per_scene": "4 إلى 6 كلمات",
            "style_instruction": "اجعل الطرح سريعاً ومباشراً ومكثف الفائدة."
        },
        {
            "name": "متوسط (28-35 ثانية)",
            "scenes_count": "6",
            "words_per_scene": "5 إلى 8 كلمات",
            "style_instruction": "اشرح الفكرة بوضوح وعمق مع ذكر السبب أو الفائدة العملية."
        },
        {
            "name": "مفصّل (38-45 ثانية)",
            "scenes_count": "8",
            "words_per_scene": "6 إلى 9 كلمات",
            "style_instruction": "قدّم شرحاً وافياً وممتعاً بأسلوب قصصي أو خطوات عملية متتالية."
        }
    ]
    chosen_len = random.choices(length_profiles, weights=[3, 5, 2], k=1)[0]

    content_categories = [
        {
            "category": "شرح حديث نبوي شريف",
            "instruction": "اذكر حديثاً نبوياً صحيحاً وقصيراً ومشهوراً بدقة، ثم اشرح معناه العميق وكيف نطبقه في حياتنا اليومية بأسلوب مؤثر وواضح.",
            "hashtags": "#أحاديث #السنة_النبوية #تذكير #إسلاميات",
            "preferred_visuals": "BOOK_LIGHT, HEART_GLOW, COMPASS_LIFE, IDEA_BULB"
        },
        {
            "category": "معلومات عامة وعلمية مذهلة",
            "instruction": "قدّم معلومة غريبة ومدهشة في العلوم، التاريخ، الفضاء، جسم الإنسان، أو التكنولوجيا بأسلوب يثير الفضول.",
            "hashtags": "#معلومات #هل_تعلم #ثقافة #حقائق",
            "preferred_visuals": "GLOBE, AI_BRAIN, MAGNIFIER, ROCKET, IDEA_BULB"
        },
        {
            "category": "مقولات وحكم مفيدة",
            "instruction": "اختر مقولة حكيمة أو قاعدة ذهبية ملهمة من تجارب الحكماء، واشرح مغزاها وكيف تغيّر نظرتنا للحياة.",
            "hashtags": "#حكم #مقولات #تطوير_الذات #اقتباسات",
            "preferred_visuals": "BOOK_LIGHT, COMPASS_LIFE, IDEA_BULB, CHART_UP"
        },
        {
            "category": "نصائح دينية وإيمانية",
            "instruction": "قدّم نصيحة إيمانية وروحية لطيفة حول ذكر الله، الطمأنينة، بر الوالدين، الصدقة، أو إصلاح القلب بأسلوب دافئ ومحبب.",
            "hashtags": "#نصائح_دينية #تذكير #طمأنينة #ذكر",
            "preferred_visuals": "HEART_GLOW, BOOK_LIGHT, SECURITY, CLOCK"
        },
        {
            "category": "نصائح مالية وإدارة الأموال",
            "instruction": "قدّم نصيحة مالية عملية حول الادخار الذكي، تجنب التبذير، بناء مصدر دخل إضافي، أو العقلية المالية الناجحة.",
            "hashtags": "#نصائح_مالية #إدارة_المال #نجاح #ادخار",
            "preferred_visuals": "MONEY_GROWTH, CHART_UP, SECURITY, CLOCK"
        },
        {
            "category": "نصائح دنيوية وتطوير الذات",
            "instruction": "قدّم نصيحة حياتية ذكية في فن التعامل مع الناس، بناء العادات الناجحة، الحفاظ على الطاقة النفسية، أو تنظيم الوقت.",
            "hashtags": "#نصائح_حياتية #تطوير_الذات #نجاح #حياة",
            "preferred_visuals": "COMPASS_LIFE, ROCKET, CLOCK, BATTERY, SECURITY"
        }
    ]
    chosen_cat = random.choice(content_categories)

    past_topics = load_topic_history()
    avoid_clause = ""
    if past_topics:
        avoid_clause = f"\nمهم جداً: لا تكرر أياً من هذه المواضيع المنشورة سابقاً: {', '.join(past_topics)}."

    prompt = f"""
    اكتب سيناريو فيديو ريلز (Reel) شيق ومفيد جداً باللغة العربية الفصحى البسيطة والواضحة في قسم: ({chosen_cat['category']}).
    التوجيه الخاص بالمحتوى: {chosen_cat['instruction']}
    {chosen_len['style_instruction']}{avoid_clause}
    يجب أن يكون النص مقسماً إلى {chosen_len['scenes_count']} مشاهد بالضبط (كل مشهد جملة واحدة من {chosen_len['words_per_scene']}).
    مهم: اجعل المشهد الأخير في قائمتك عبارة عن سؤال تفاعلي قصير يشجع المشاهدين على التعليق حول الموضوع (مع اختيار pose="QUESTION" و visual="COMMENT_BUBBLE")، ولا تكتب دعوة لمتابعة الصفحة لأنها ستضاف تلقائياً.
    
    لكل مشهد، اختر:
    1) "pose" من القائمة: [WELCOME, POINTING, THINKING, EXPLAINING, WARNING, SUCCESS, QUESTION]
    2) "visual" من القائمة (يفضل التركيز على: {chosen_cat['preferred_visuals']}):
       [BOOK_LIGHT, HEART_GLOW, MONEY_GROWTH, COMPASS_LIFE, BATTERY, PHONE_APPS, AI_BRAIN, ROCKET, SECURITY, CHART_UP, WARNING_SIGN, CLOCK, GLOBE, WIFI, MAGNIFIER, IDEA_BULB, COMMENT_BUBBLE]
    
    أرجع النتيجة بصيغة JSON فقط بدون أي كلام إضافي:
    {{
      "title": "عنوان غلاف قصير وجذاب من 3 إلى 5 كلمات",
      "caption": "وصف الريلز مع هاشتاغات مناسبة مثل {chosen_cat['hashtags']}",
      "scenes": [
        {{"text": "الجملة الأولى هنا", "pose": "WELCOME", "visual": "BOOK_LIGHT"}},
        {{"text": "ما رأيك بهذا المعنى؟ شاركنا في التعليقات!", "pose": "QUESTION", "visual": "COMMENT_BUBBLE"}}
      ]
    }}
    """
    try:
        model = None
        for m in genai.list_models():
            if (
                "generateContent" in m.supported_generation_methods
                and "flash" in m.name
                and "image" not in m.name
                and "tts" not in m.name
            ):
                try:
                    candidate = genai.GenerativeModel(m.name)
                    candidate.generate_content("1", request_options={"timeout": 15})
                    model = candidate
                    break
                except Exception:
                    continue

        if model:
            response = model.generate_content(prompt, request_options={"timeout": 25})
            text = response.text.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
            data = json.loads(text)
            if "scenes" in data and len(data["scenes"]) > 0:
                if "تابع" in data["scenes"][-1].get("text", ""):
                    data["scenes"].pop()
                data["scenes"].append(outro_scene)
                save_topic_history(data.get("title", ""))
                return data
    except Exception as e:
        print("تم استخدام السيناريو الاحتياطي بسبب:", e)

    fallback_scripts = [
        {
            "title": "قاعدة الادخار الذكية!",
            "caption": "كيف تحافظ على راتبك وتبني أمانك المالي؟ 💰📈 #نصائح_مالية #إدارة_المال #نجاح",
            "scenes": [
                {"text": "أكبر خطأ مالي يقع فيه الأغلبية كل شهر", "pose": "WARNING", "visual": "WARNING_SIGN"},
                {"text": "هو إنفاق الراتب أولاً ثم ادخار ما يتبقى", "pose": "EXPLAINING", "visual": "MONEY_GROWTH"},
                {"text": "القاعدة الذهبية تقول ادخر لنفسك أولاً", "pose": "POINTING", "visual": "CHART_UP"},
                {"text": "استقطع عشرة بالمئة فور استلام دخلك مباشرة", "pose": "SUCCESS", "visual": "SECURITY"},
                {"text": "كم نسبة ادخارك الشهرية؟ شاركنا تجربتك بالتعليقات!", "pose": "QUESTION", "visual": "COMMENT_BUBBLE"},
                outro_scene
            ]
        },
        {
            "title": "كنز من كنوز الجنة!",
            "caption": "حديث نبوي عظيم يملأ قلبك بالسكينة والقوة 🤍✨ #أحاديث #تذكير #إسلاميات",
            "scenes": [
                {"text": "قال رسول الله صلى الله عليه وسلم", "pose": "WELCOME", "visual": "BOOK_LIGHT"},
                {"text": "ألا أدلك على كنز من كنوز الجنة", "pose": "POINTING", "visual": "HEART_GLOW"},
                {"text": "لا حول ولا قوة إلا بالله", "pose": "EXPLAINING", "visual": "BOOK_LIGHT"},
                {"text": "كلمة عظيمة تفتح بها الأبواب المغلقة وتزيل الهم", "pose": "SUCCESS", "visual": "COMPASS_LIFE"},
                {"text": "هل تداوم على هذا الذكر يومياً؟ اكتب لنا بالتعليقات!", "pose": "QUESTION", "visual": "COMMENT_BUBBLE"},
                outro_scene
            ]
        }
    ]
    return random.choice(fallback_scripts)


def draw_joint(draw, pt, radius, color):
    x, y = int(pt[0]), int(pt[1])
    r = max(1, int(radius))
    draw.ellipse([(x - r, y - r), (x + r, y + r)], fill=color)


def draw_thick_limb(draw, pts, width, color):
    for i in range(len(pts) - 1):
        draw.line([pts[i], pts[i + 1]], fill=color, width=width)
    for p in pts:
        draw_joint(draw, p, width // 2, color)


PARTICLES = [
    {
        "x": (i * 173 + 60) % 1080,
        "y": (i * 293 + 110) % 1920,
        "r": 3 + (i % 6),
        "speed_y": 1.5 + (i % 5) * 0.5,
        "phase": i * 0.7
    }
    for i in range(36)
]


def draw_ambient_particles(draw, global_frame, accent, audio_level=0.2):
    boost = int(audio_level * 5)
    for idx, p in enumerate(PARTICLES):
        px = int((p["x"] + math.sin(global_frame * 0.04 + p["phase"]) * (28 + boost * 3)) % 1080)
        py = int((p["y"] - global_frame * (p["speed_y"] + audio_level * 1.5)) % 1920)
        r = p["r"] + (boost // 2 if idx % 3 == 0 else 0)
        if idx % 4 == 0:
            draw.ellipse([(px - r - 3, py - r - 3), (px + r + 3, py + r + 3)], outline="#1E293B", width=2)
            draw.ellipse([(px - r, py - r), (px + r, py + r)], fill=accent)
        else:
            draw.ellipse([(px - r, py - r), (px + r, py + r)], fill="#1E293B")


def draw_audio_reactive_bars(draw, center_x, base_y, audio_level, frame_num, accent):
    num_bars = 25
    bar_w = 10
    gap = 18
    total_w = num_bars * gap
    start_x = center_x - total_w // 2

    for i in range(num_bars):
        dist_from_center = abs(i - num_bars // 2) / (num_bars / 2.0)
        wave_factor = 0.45 + 0.55 * math.sin(frame_num * 0.5 + i * 0.6)
        h = int(10 + (1.0 - dist_from_center * 0.65) * audio_level * 90 * wave_factor)
        bx = start_x + i * gap
        col = "#FACC15" if h > 52 else (accent if h > 22 else "#334155")
        draw.rounded_rectangle(
            [(bx, base_y - h // 2), (bx + bar_w, base_y + h // 2)],
            radius=5,
            fill=col
        )


def draw_page_branding_header(draw, page_name, font_badge, accent, audio_level, size=(1080, 1920)):
    if not page_name:
        return
    shaped_name = shape_multiline(page_name, max_chars=32)
    badge_kwargs = {"font": font_badge}
    if HAS_RAQM:
        badge_kwargs["direction"] = "rtl"
    bb = draw.textbbox((0, 0), shaped_name, **badge_kwargs)
    text_w = bb[2] - bb[0]
    text_h = bb[3] - bb[1]

    bw = text_w + 105
    bh = max(68, text_h + 32)
    bx1 = (size[0] - bw) // 2
    by1 = 108

    glow_pad = int(5 + audio_level * 10)
    draw.rounded_rectangle(
        [(bx1 - glow_pad, by1 - glow_pad), (bx1 + bw + glow_pad, by1 + bh + glow_pad)],
        radius=(bh + glow_pad * 2) // 2,
        outline=accent,
        width=2
    )
    draw.rounded_rectangle(
        [(bx1, by1), (bx1 + bw, by1 + bh)],
        radius=bh // 2,
        fill="#0B1325",
        outline=accent,
        width=4
    )

    vx = bx1 + 38
    vy = by1 + bh // 2
    vr = 19
    draw.ellipse([(vx - vr, vy - vr), (vx + vr, vy + vr)], fill=accent)
    draw.line([(vx - 8, vy + 1), (vx - 2, vy + 7), (vx + 9, vy - 6)], fill="#090D16", width=4)

    tx = bx1 + 72
    ty = by1 + (bh - text_h) // 2 - 3
    draw.text((tx + 2, ty + 2), shaped_name, fill="#020617", **badge_kwargs)
    draw.text((tx, ty), shaped_name, fill="#FFFFFF", **badge_kwargs)


def draw_hologram_projector(img, draw, vx, vy, frame_num, accent_color, audio_level, scale):
    if scale < 0.3:
        return
    ar, ag, ab = hex_to_rgb(accent_color)
    proj_y = vy + 225

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)

    cone_w = int((185 + audio_level * 25) * scale)
    beam_alpha = int(32 + audio_level * 28)
    beam_poly = [
        (vx - 55, proj_y),
        (vx + 55, proj_y),
        (vx + cone_w, vy - 120),
        (vx - cone_w, vy - 120)
    ]
    odraw.polygon(beam_poly, fill=(ar, ag, ab, beam_alpha))

    for s_i in range(4):
        scan_y = proj_y - ((frame_num * 7 + s_i * 85) % 330)
        ratio = (proj_y - scan_y) / 330.0
        sw = int(55 + (cone_w - 55) * ratio)
        odraw.ellipse(
            [(vx - sw, scan_y - 12), (vx + sw, scan_y + 12)],
            outline=(ar, ag, ab, 95),
            width=2
        )

    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))

    draw.ellipse([(vx - 82, proj_y - 18), (vx + 82, proj_y + 18)], fill="#0F172A", outline=accent_color, width=4)
    draw.ellipse([(vx - 48, proj_y - 9), (vx + 48, proj_y + 9)], fill=accent_color)


def draw_visual_prop(img, draw, visual_type, vx, vy, frame_num, accent_color, audio_level=0.3, page_name="", font_small=None):
    scale = ease_out_back(min(1.0, (frame_num + 1) / 9.0))
    audio_boost = int(audio_level * 26)
    pulse = int(math.sin(frame_num * 0.25) * 8) + audio_boost

    draw_hologram_projector(img, draw, vx, vy, frame_num, accent_color, audio_level, scale)
    draw = ImageDraw.Draw(img)

    ring_r = int(168 * scale) + (audio_boost // 2)
    if ring_r > 10:
        outer_r = ring_r + 16 + (audio_boost // 2)
        draw.ellipse([(vx - outer_r, vy - outer_r), (vx + outer_r, vy + outer_r)], outline="#1E293B", width=3)
        draw.ellipse([(vx - ring_r, vy - ring_r), (vx + ring_r, vy + ring_r)], fill="#0D162A", outline=accent_color, width=5)

        rot = (frame_num * 6) % 360
        draw.arc([(vx - outer_r, vy - outer_r), (vx + outer_r, vy + outer_r)], start=rot, end=rot + 75, fill=accent_color, width=6)
        draw.arc([(vx - outer_r, vy - outer_r), (vx + outer_r, vy + outer_r)], start=rot + 180, end=rot + 255, fill="#FACC15", width=6)

    if scale < 0.35:
        return

    v = (visual_type or "IDEA_BULB").upper()
    vy_react = vy - int(audio_level * 12) + int(math.sin(frame_num * 0.22) * 8)

    if "BOOK" in v:
        by = vy_react + 15
        left_page = [(vx - 95, by - 35), (vx - 4, by - 18), (vx - 4, by + 62), (vx - 95, by + 45)]
        right_page = [(vx + 4, by - 18), (vx + 95, by - 35), (vx + 95, by + 45), (vx + 4, by + 62)]
        draw.polygon(left_page, fill="#F8FAFC", outline="#38BDF8")
        draw.polygon(right_page, fill="#E2E8F0", outline="#38BDF8")
        draw.line([(vx, by - 18), (vx, by + 65)], fill="#0284C7", width=6)
        for ang in [-50, -25, 0, 25, 50]:
            rad = math.radians(ang - 90)
            r1, r2 = 35, 80 + int(audio_level * 32)
            x1, y1 = vx + int(math.cos(rad) * r1), by - 25 + int(math.sin(rad) * r1)
            x2, y2 = vx + int(math.cos(rad) * r2), by - 25 + int(math.sin(rad) * r2)
            draw.line([(x1, y1), (x2, y2)], fill="#FACC15", width=6)

    elif "HEART" in v:
        beat = int(abs(math.sin(frame_num * 0.3)) * 10) + int(audio_level * 14)
        hy = vy_react - 5
        draw.ellipse([(vx - 58 - beat, hy - 52 - beat), (vx + 4, hy + 10)], fill="#10B981")
        draw.ellipse([(vx - 4, hy - 52 - beat), (vx + 58 + beat, hy + 10)], fill="#10B981")
        tri = [(vx - 55 - beat, hy - 8), (vx + 55 + beat, hy - 8), (vx, hy + 68 + beat)]
        draw.polygon(tri, fill="#10B981")
        halo_r = 90 + beat
        draw.ellipse([(vx - halo_r, hy - halo_r + 8), (vx + halo_r, hy + halo_r + 8)], outline="#6EE7B7", width=4)

    elif "MONEY" in v:
        cx_c, cy_c = vx - 18, vy_react + 8
        draw.ellipse([(cx_c - 64, cy_c - 64), (cx_c + 64, cy_c + 64)], fill="#F59E0B", outline="#FEF08A", width=6)
        draw.ellipse([(cx_c - 46, cy_c - 46), (cx_c + 46, cy_c + 46)], outline="#FEF08A", width=3)
        draw.line([(cx_c, cy_c - 30), (cx_c, cy_c + 30)], fill="#451A03", width=6)
        ax, ay = vx + 62, vy_react - 42 - int(audio_level * 15)
        draw.line([(vx + 22, vy_react + 35), (ax, ay)], fill="#10B981", width=12)
        draw.polygon([(ax - 18, ay - 6), (ax + 14, ay - 18), (ax + 10, ay + 16)], fill="#10B981")

    elif "COMPASS" in v:
        draw.ellipse([(vx - 96, vy_react - 96), (vx + 96, vy_react + 96)], fill="#0F172A", outline="#00F0FF", width=8)
        ang = math.sin(frame_num * 0.16) * 0.55 - (math.pi / 4.0) + (audio_level * 0.4)
        nx, ny = int(math.cos(ang) * 68), int(math.sin(ang) * 68)
        px, py = int(math.cos(ang + math.pi / 2.0) * 16), int(math.sin(ang + math.pi / 2.0) * 16)
        draw.polygon([(vx + nx, vy_react + ny), (vx + px, vy_react + py), (vx - px, vy_react - py)], fill="#EF4444")
        draw.polygon([(vx - nx, vy_react - ny), (vx + px, vy_react + py), (vx - px, vy_react - py)], fill="#F8FAFC")
        draw.ellipse([(vx - 10, vy_react - 10), (vx + 10, vy_react + 10)], fill="#FACC15")

    elif "COMMENT" in v or "QUESTION" in v:
        bx, by = vx, vy_react - 10
        draw.rounded_rectangle([(bx - 105, by - 65), (bx + 105, by + 55)], radius=30, fill="#1E1B4B", outline="#A855F7", width=6)
        tail = [(bx - 45, by + 52), (bx - 70, by + 88), (bx - 10, by + 52)]
        draw.polygon(tail, fill="#1E1B4B", outline="#A855F7")
        for d_idx in range(3):
            dx = bx - 44 + d_idx * 44
            dy = by - 5 + int(math.sin(frame_num * 0.45 - d_idx * 0.9) * 10)
            dot_col = "#FACC15" if d_idx == (frame_num // 5) % 3 else "#E9D5FF"
            draw.ellipse([(dx - 12, dy - 12), (dx + 12, dy + 12)], fill=dot_col)

    elif "FOLLOW" in v:
        card_w, card_h = 235, 135
        cy_card = vy_react
        draw.rounded_rectangle(
            [(vx - card_w // 2, cy_card - card_h // 2), (vx + card_w // 2, cy_card + card_h // 2)],
            radius=28, fill="#0284C7", outline="#F8FAFC", width=5
        )
        px, py = vx + 55, cy_card - 12
        draw.ellipse([(px - 24, py - 24), (px + 24, py + 24)], fill="#F8FAFC")
        draw.line([(px - 12, py), (px + 12, py)], fill="#0284C7", width=6)
        draw.line([(px, py - 12), (px, py + 12)], fill="#0284C7", width=6)

        bx, by = vx - 45, cy_card - 12
        swing = int(math.sin(frame_num * 0.5) * 6)
        draw.chord([(bx - 20 + swing, by - 22), (bx + 20 + swing, by + 16)], start=180, end=360, fill="#FACC15")
        draw.rectangle([(bx - 20 + swing, by - 4), (bx + 20 + swing, by + 10)], fill="#FACC15")
        draw.rounded_rectangle([(bx - 26 + swing, by + 8), (bx + 26 + swing, by + 16)], radius=4, fill="#FACC15")

        if page_name and font_small:
            short_p = shape_multiline(page_name[:18], max_chars=20)
            k_args = {"font": font_small}
            if HAS_RAQM:
                k_args["direction"] = "rtl"
            sb = draw.textbbox((0, 0), short_p, **k_args)
            sw = sb[2] - sb[0]
            draw.text((vx - sw // 2, cy_card + 22), short_p, fill="#FEF08A", **k_args)

    elif "WARNING" in v:
        s = 110 + (pulse // 2)
        tri = [(vx, vy_react - s), (vx - s, vy_react + int(s * 0.75)), (vx + s, vy_react + int(s * 0.75))]
        draw.polygon(tri, fill="#450A0A", outline="#EF4444")
        draw.line([tri[0], tri[1], tri[2], tri[0]], fill="#EF4444", width=10)
        draw.line([(vx, vy_react - 40), (vx, vy_react + 22)], fill="#FACC15", width=14)
        draw.ellipse([(vx - 8, vy_react + 38), (vx + 8, vy_react + 54)], fill="#FACC15")

    elif "ROCKET" in v:
        ry = vy_react
        body = [(vx, ry - 105), (vx - 42, ry + 35), (vx + 42, ry + 35)]
        draw.polygon(body, fill="#F8FAFC", outline="#00F0FF")
        draw.polygon([(vx - 42, ry + 5), (vx - 72, ry + 55), (vx - 42, ry + 35)], fill="#F43F5E")
        draw.polygon([(vx + 42, ry + 5), (vx + 72, ry + 55), (vx + 42, ry + 35)], fill="#F43F5E")
        flame_h = 45 + int(audio_level * 55)
        draw.polygon([(vx - 24, ry + 38), (vx + 24, ry + 38), (vx, ry + 38 + flame_h)], fill="#FACC15")

    elif "SECURITY" in v:
        shield = [
            (vx, vy_react - 105), (vx + 85, vy_react - 70), (vx + 72, vy_react + 35),
            (vx, vy_react + 105), (vx - 72, vy_react + 35), (vx - 85, vy_react - 70)
        ]
        draw.polygon(shield, fill="#064E3B", outline="#10B981")
        draw.line(shield + [shield[0]], fill="#10B981", width=9)
        draw.line([(vx - 32, vy_react + 2), (vx - 8, vy_react + 28), (vx + 38, vy_react - 25)], fill="#F8FAFC", width=12)

    elif "CHART" in v:
        bars = [55, 95, 145, 195]
        for i, bh in enumerate(bars):
            anim_h = int((bh + audio_level * 28) * min(1.0, (frame_num + 4) / (10 + i * 3)))
            bx = vx - 85 + i * 45
            col = "#00F0FF" if i < 3 else "#10B981"
            draw.rounded_rectangle([(bx, vy_react + 95 - anim_h), (bx + 32, vy_react + 95)], radius=6, fill=col)

    elif "CLOCK" in v:
        draw.ellipse([(vx - 105, vy_react - 105), (vx + 105, vy_react + 105)], fill="#0F172A", outline="#00F0FF", width=9)
        angle = frame_num * 0.35
        hx, hy = vx + int(math.cos(angle) * 70), vy_react + int(math.sin(angle) * 70)
        draw.line([(vx, vy_react), (vx, vy_react - 55)], fill="#F8FAFC", width=8)
        draw.line([(vx, vy_react), (hx, hy)], fill="#F43F5E", width=6)

    elif "GLOBE" in v or "AI" in v or "BRAIN" in v:
        nodes = [
            (vx, vy_react - 80), (vx - 80, vy_react - 25), (vx + 80, vy_react - 25),
            (vx - 55, vy_react + 65), (vx + 55, vy_react + 65), (vx, vy_react + 5)
        ]
        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                draw.line([nodes[i], nodes[j]], fill="#0284C7", width=4)
        for idx, (nx, ny) in enumerate(nodes):
            nr = 14 + int(audio_level * 10)
            draw.ellipse([(nx - nr, ny - nr), (nx + nr, ny + nr)], fill="#00F0FF", outline="#F8FAFC", width=3)

    else:
        draw.ellipse([(vx - 65 - pulse // 3, vy_react - 85 - pulse // 3), (vx + 65 + pulse // 3, vy_react + 45 + pulse // 3)], fill="#FACC15", outline="#FEF08A", width=5)
        draw.rounded_rectangle([(vx - 30, vy_react + 40), (vx + 30, vy_react + 80)], radius=8, fill="#94A3B8")


def draw_upgraded_stickman(draw, cx, cy, pose, frame_num, mouth_open, accent, has_side_visual):
    p = (pose or "POINTING").upper()
    body_color = "#F8FAFC"
    suit_dark = "#111C35"
    suit_mid = "#1E293B"
    limb_w = 22

    breath = int(math.sin(frame_num * 0.18) * 6)
    voice_bob = int(mouth_open * 12 * math.sin(frame_num * 0.8))
    head_tilt = int(math.sin(frame_num * 0.15) * 6)
    cy_body = cy + breath

    shadow_y = cy + 432
    shadow_rx = 110 + int(mouth_open * 18)
    draw.ellipse([(cx - shadow_rx, shadow_y - 18), (cx + shadow_rx, shadow_y + 18)], fill="#0D172A", outline=accent, width=3)
    draw.ellipse([(cx - shadow_rx // 2, shadow_y - 8), (cx + shadow_rx // 2, shadow_y + 8)], fill=accent)

    knee_sway = int(math.sin(frame_num * 0.2) * 7)
    hip_l = (cx - 28, cy_body + 215)
    hip_r = (cx + 28, cy_body + 215)
    l_knee = (cx - 46 + knee_sway, cy_body + 320)
    r_knee = (cx + 46 + knee_sway, cy_body + 320)
    l_foot = (cx - 68, cy + 420)
    r_foot = (cx + 68, cy + 420)

    draw_thick_limb(draw, [hip_l, l_knee, l_foot], limb_w, body_color)
    draw_thick_limb(draw, [hip_r, r_knee, r_foot], limb_w, body_color)

    draw.rounded_rectangle([(l_foot[0] - 32, l_foot[1] - 14), (l_foot[0] + 18, l_foot[1] + 14)], radius=10, fill=suit_dark, outline=accent, width=4)
    draw.rounded_rectangle([(r_foot[0] - 18, r_foot[1] - 14), (r_foot[0] + 32, r_foot[1] + 14)], radius=10, fill=suit_dark, outline=accent, width=4)

    sh_y = cy_body + 28
    torso_poly = [
        (cx - 68, sh_y),
        (cx + 68, sh_y),
        (cx + 44, cy_body + 222),
        (cx - 44, cy_body + 222)
    ]
    draw.polygon(torso_poly, fill=suit_dark, outline=body_color, width=6)

    draw.polygon([(cx - 38, sh_y), (cx, sh_y + 48), (cx + 38, sh_y)], fill=suit_mid, outline=accent, width=4)
    core_r = 18 + int(mouth_open * 10)
    draw.ellipse([(cx - core_r, sh_y + 72 - core_r), (cx + core_r, sh_y + 72 + core_r)], fill=accent, outline="#FFFFFF", width=3)
    draw.line([(cx, sh_y + 100), (cx, cy_body + 215)], fill=accent, width=4)

    hx = cx + head_tilt
    hy = cy_body + voice_bob
    draw.rounded_rectangle([(cx - 18, hy - 12), (cx + 18, sh_y + 15)], radius=8, fill=body_color)

    draw.ellipse([(hx - 98, hy - 196), (hx + 98, hy)], fill=suit_dark, outline=body_color, width=12)

    ear_pad_w = 32 + int(mouth_open * 8)
    draw.arc([(hx - 112, hy - 210), (hx + 112, hy - 80)], start=180, end=360, fill=accent, width=14)
    draw.rounded_rectangle([(hx - 94 - ear_pad_w, hy - 138), (hx - 92, hy - 58)], radius=14, fill=accent, outline="#FFFFFF", width=3)
    draw.rounded_rectangle([(hx + 92, hy - 138), (hx + 94 + ear_pad_w, hy - 58)], radius=14, fill=accent, outline="#FFFFFF", width=3)

    eye_shift = 14 if has_side_visual else 0
    gx = hx + eye_shift
    gy = hy - 112
    draw.rounded_rectangle([(gx - 72, gy - 30), (gx + 72, gy + 22)], radius=18, fill="#050811", outline=accent, width=5)
    draw.line([(gx - 55, gy - 20), (gx - 25, gy + 10)], fill="#1E293B", width=6)

    is_blinking = (frame_num % 75) in (0, 1, 2)
    if is_blinking:
        draw.line([(gx - 48, gy - 4), (gx - 18, gy - 4)], fill=accent, width=6)
        draw.line([(gx + 18, gy - 4), (gx + 48, gy - 4)], fill=accent, width=6)
    elif p == "SUCCESS":
        draw.arc([(gx - 50, gy - 16), (gx - 16, gy + 14)], start=190, end=350, fill="#10B981", width=7)
        draw.arc([(gx + 16, gy - 16), (gx + 50, gy + 14)], start=190, end=350, fill="#10B981", width=7)
    elif p == "WARNING":
        draw.ellipse([(gx - 46, gy - 14), (gx - 20, gy + 10)], fill="#EF4444")
        draw.ellipse([(gx + 20, gy - 14), (gx + 46, gy + 10)], fill="#EF4444")
        draw.line([(gx - 52, gy - 20), (gx - 14, gy - 8)], fill="#FACC15", width=6)
        draw.line([(gx + 14, gy - 8), (gx + 52, gy - 20)], fill="#FACC15", width=6)
    else:
        eye_r = 12 + int(mouth_open * 3)
        draw.ellipse([(gx - 34 - eye_r, gy - 4 - eye_r), (gx - 34 + eye_r, gy - 4 + eye_r)], fill=accent)
        draw.ellipse([(gx + 34 - eye_r, gy - 4 - eye_r), (gx + 34 + eye_r, gy - 4 + eye_r)], fill=accent)
        draw.ellipse([(gx - 38, gy - 10), (gx - 30, gy - 2)], fill="#FFFFFF")
        draw.ellipse([(gx + 30, gy - 10), (gx + 38, gy - 2)], fill="#FFFFFF")

    mx, my = gx, hy - 50
    if mouth_open > 0.18:
        mh = int(10 + mouth_open * 28)
        mw = int(22 + mouth_open * 14)
        draw.rounded_rectangle([(mx - mw, my - mh // 2), (mx + mw, my + mh // 2)], radius=mh // 2, fill="#050811", outline=accent, width=4)
        draw.line([(mx - mw + 8, my), (mx + mw - 8, my)], fill="#FACC15", width=3)
    else:
        draw.arc([(mx - 26, my - 14), (mx + 26, my + 12)], start=15, end=165, fill=body_color, width=6)

    l_sh = (cx - 64, sh_y + 10)
    r_sh = (cx + 64, sh_y + 10)
    voice_arm_move = int(mouth_open * 22)

    if p == "WELCOME":
        l_pts = [l_sh, (cx - 105, sh_y + 85), (cx - 95, sh_y + 155)]
        wave = int(math.sin(frame_num * 0.45) * 34)
        r_pts = [r_sh, (cx + 115, sh_y - 35), (cx + 155 + wave, sh_y - 135)]
        draw_thick_limb(draw, l_pts, limb_w, body_color)
        draw_thick_limb(draw, r_pts, limb_w, body_color)
        draw_joint(draw, l_pts[-1], 14, accent)
        draw_joint(draw, r_pts[-1], 17, "#FACC15")

    elif p == "THINKING":
        chin_pt = (hx - 20, hy - 28)
        l_pts = [l_sh, (cx - 105, sh_y + 45), chin_pt]
        hand_y = sh_y + 35 - voice_arm_move
        r_pts = [r_sh, (cx + 110, sh_y + 65), (cx + 185, hand_y)]
        draw_thick_limb(draw, l_pts, limb_w, body_color)
        draw_thick_limb(draw, r_pts, limb_w, body_color)
        draw_joint(draw, chin_pt, 15, "#FACC15")
        draw_joint(draw, r_pts[-1], 15, accent)

    elif p == "EXPLAINING":
        lh_y = sh_y + 25 - voice_arm_move + int(math.sin(frame_num * 0.3) * 16)
        rh_y = sh_y + 5 - voice_arm_move + int(math.cos(frame_num * 0.3) * 16)
        l_pts = [l_sh, (cx - 105, sh_y + 75), (cx - 150, lh_y)]
        r_pts = [r_sh, (cx + 115, sh_y + 55), (cx + 220, rh_y)]
        draw_thick_limb(draw, l_pts, limb_w, body_color)
        draw_thick_limb(draw, r_pts, limb_w, body_color)
        draw_joint(draw, l_pts[-1], 15, accent)
        draw_joint(draw, r_pts[-1], 16, accent)

    elif p == "QUESTION":
        shrug = int(math.sin(frame_num * 0.35) * 14) + voice_arm_move
        l_pts = [l_sh, (cx - 105, sh_y + 55), (cx - 155, sh_y - 30 - shrug)]
        r_pts = [r_sh, (cx + 105, sh_y + 55), (cx + 180, sh_y - 35 - shrug)]
        draw_thick_limb(draw, l_pts, limb_w, body_color)
        draw_thick_limb(draw, r_pts, limb_w, body_color)
        draw_joint(draw, l_pts[-1], 16, "#FACC15")
        draw_joint(draw, r_pts[-1], 16, accent)

    elif p == "FOLLOW":
        wave_l = int(math.sin(frame_num * 0.4) * 26)
        l_pts = [l_sh, (cx - 105, sh_y - 35), (cx - 140 - wave_l, sh_y - 125)]
        point_y = 650 + int(math.sin(frame_num * 0.35) * 14) - voice_arm_move
        r_pts = [r_sh, (cx + 125, sh_y - 10), (cx + 240, point_y)]
        draw_thick_limb(draw, l_pts, limb_w, body_color)
        draw_thick_limb(draw, r_pts, limb_w, body_color)
        draw_joint(draw, l_pts[-1], 16, "#FACC15")
        draw_joint(draw, r_pts[-1], 17, accent)

    elif p == "WARNING":
        shake = int(math.sin(frame_num * 0.65) * 14)
        l_pts = [l_sh, (cx - 100, sh_y - 30), (cx - 85, sh_y - 125 + shake)]
        r_pts = [r_sh, (cx + 115, sh_y - 35), (cx + 175, sh_y - 110 - shake)]
        draw_thick_limb(draw, l_pts, limb_w, body_color)
        draw_thick_limb(draw, r_pts, limb_w, body_color)
        draw_joint(draw, l_pts[-1], 16, "#EF4444")
        draw_joint(draw, r_pts[-1], 16, "#EF4444")

    elif p == "SUCCESS":
        pump = int(abs(math.sin(frame_num * 0.38)) * 28) + voice_arm_move
        l_pts = [l_sh, (cx - 105, sh_y - 45), (cx - 130, sh_y - 145 - pump)]
        r_pts = [r_sh, (cx + 110, sh_y - 45), (cx + 155, sh_y - 140 - pump)]
        draw_thick_limb(draw, l_pts, limb_w, body_color)
        draw_thick_limb(draw, r_pts, limb_w, body_color)
        draw_joint(draw, l_pts[-1], 17, "#10B981")
        draw_joint(draw, r_pts[-1], 17, "#10B981")

    else:
        l_pts = [l_sh, (cx - 95, sh_y + 75), (cx - 70, sh_y + 145)]
        point_y = 660 + int(math.sin(frame_num * 0.35) * 14) - voice_arm_move
        r_pts = [r_sh, (cx + 125, sh_y - 5), (cx + 235, point_y)]
        draw_thick_limb(draw, l_pts, limb_w, body_color)
        draw_thick_limb(draw, r_pts, limb_w, body_color)
        draw_joint(draw, l_pts[-1], 14, accent)
        draw_joint(draw, r_pts[-1], 17, accent)

        for step in range(3):
            lx = r_pts[-1][0] + 24 + step * 36 + ((frame_num * 6) % 28)
            if lx < 555:
                draw.line([(lx, point_y), (lx + 18, point_y)], fill=accent, width=6)


def apply_camera_motion(img, zoom, pan_x=0, pan_y=0):
    if zoom <= 1.001 and pan_x == 0 and pan_y == 0:
        return img
    w, h = img.size
    zw = int(w * max(1.02, zoom))
    zh = int(h * max(1.02, zoom))
    resized = img.resize((zw, zh), Image.Resampling.BILINEAR)
    left = max(0, min(zw - w, (zw - w) // 2 + int(pan_x)))
    top = max(0, min(zh - h, (zh - h) // 2 + int(pan_y)))
    return resized.crop((left, top, left + w, top + h))


def draw_cover_frame(title, page_name, frame_num, font_path, font_badge, size=(1080, 1920)):
    accent = "#00F0FF"
    img = get_studio_background(accent, size=size)
    draw = ImageDraw.Draw(img)

    draw_ambient_particles(draw, frame_num, accent, audio_level=0.6)
    draw_page_branding_header(draw, page_name, font_badge, accent, audio_level=0.6, size=size)

    card_left, card_top, card_right, card_bottom = 80, 280, 1000, 650
    draw.rounded_rectangle(
        [(card_left, card_top), (card_right, card_bottom)],
        radius=40, fill="#0B1325", outline=accent, width=6
    )

    shaped_title = shape_multiline(title, max_chars=20)
    chosen_font_cover = ImageFont.load_default()
    if font_path and os.path.exists(font_path):
        for test_size in range(72, 42, -4):
            candidate_font = ImageFont.truetype(font_path, test_size)
            test_kwargs = {"font": candidate_font, "align": "center", "spacing": 16}
            if HAS_RAQM:
                test_kwargs["direction"] = "rtl"
            tb = draw.multiline_textbbox((0, 0), shaped_title, **test_kwargs)
            if (tb[2] - tb[0]) <= (card_right - card_left - 60) and (tb[3] - tb[1]) <= (card_bottom - card_top - 40):
                chosen_font_cover = candidate_font
                break
        else:
            chosen_font_cover = ImageFont.truetype(font_path, 44)

    t_kwargs = {"font": chosen_font_cover, "align": "center", "spacing": 16}
    if HAS_RAQM:
        t_kwargs["direction"] = "rtl"
    tb = draw.multiline_textbbox((0, 0), shaped_title, **t_kwargs)
    tw = tb[2] - tb[0]
    th = tb[3] - tb[1]
    tx = (card_left + card_right - tw) // 2
    ty = (card_top + card_bottom - th) // 2

    draw.multiline_text((tx + 4, ty + 5), shaped_title, fill="#020617", **t_kwargs)
    draw.multiline_text((tx, ty), shaped_title, fill="#FFFFFF", stroke_width=1, stroke_fill="#FFFFFF", **t_kwargs)

    draw_upgraded_stickman(draw, 540, 1020, "WELCOME", frame_num, 0.5, accent, has_side_visual=False)
    return apply_neon_bloom(img, intensity=0.50)


def draw_scene_frame(scene_idx, pose, visual_type, frame_num, total_frames, global_frame, audio_level, global_progress, page_name, font_badge, font_small, size=(1080, 1920)):
    p = (pose or "POINTING").upper()

    if p == "WARNING":
        accent = "#EF4444"
    elif p == "SUCCESS":
        accent = "#10B981"
    elif p == "FOLLOW":
        accent = "#38BDF8"
    elif p == "QUESTION":
        accent = "#A855F7"
    elif scene_idx % 2 == 1:
        accent = "#06B6D4"
    else:
        accent = "#00F0FF"

    img = get_studio_background(accent, size=size)
    draw = ImageDraw.Draw(img)

    horizon_y = 1130
    draw.line([(0, horizon_y), (size[0], horizon_y)], fill="#1E293B", width=2)

    draw_ambient_particles(draw, global_frame, accent, audio_level=audio_level)

    has_side_visual = p != "WELCOME"
    slide_t = ease_out_cubic(min(1.0, (frame_num + 1) / 10.0))
    target_cx = 295 if has_side_visual else 540
    cx = int(540 + (target_cx - 540) * slide_t)
    cy = 670 + int(math.sin(frame_num * 0.22) * 8)

    if has_side_visual:
        draw_visual_prop(img, draw, visual_type, 755, 640, frame_num, accent, audio_level=audio_level, page_name=page_name, font_small=font_small)
    else:
        draw_visual_prop(img, draw, visual_type, 540, 340, frame_num, accent, audio_level=audio_level, page_name=page_name, font_small=font_small)

    draw = ImageDraw.Draw(img)
    draw_upgraded_stickman(draw, cx, cy, p, frame_num, audio_level, accent, has_side_visual)

    img = apply_neon_bloom(img, intensity=0.45 + audio_level * 0.15)

    prog_scene = frame_num / float(max(1, total_frames))
    transition_zoom = 0.0
    if frame_num < 5 and scene_idx > 0:
        transition_zoom = ((5 - frame_num) / 5.0) ** 2 * 0.18
    elif total_frames - frame_num <= 4:
        transition_zoom = ((4 - (total_frames - frame_num)) / 4.0) ** 2 * 0.14

    base_zoom = 1.01 + 0.035 * prog_scene + (audio_level * 0.015) + transition_zoom
    pan_dir = 1 if scene_idx % 2 == 0 else -1
    pan_x = pan_dir * int(math.sin(prog_scene * math.pi) * 14)
    img = apply_camera_motion(img, base_zoom, pan_x=pan_x, pan_y=0)
    draw = ImageDraw.Draw(img)

    draw.rectangle([(0, 0), (size[0], 14)], fill="#0F172A")
    prog_w = int(size[0] * max(0.0, min(1.0, global_progress)))
    if prog_w > 0:
        draw.rectangle([(0, 0), (prog_w, 14)], fill=accent)

    draw_page_branding_header(draw, page_name, font_badge, accent, audio_level, size=size)
    draw_audio_reactive_bars(draw, 540, 1745, audio_level, global_frame, accent)

    if page_name and font_small:
        footer_text = shape_multiline(f"@ {page_name}", max_chars=35)
        f_kwargs = {"font": font_small}
        if HAS_RAQM:
            f_kwargs["direction"] = "rtl"
        fb = draw.textbbox((0, 0), footer_text, **f_kwargs)
        fw = fb[2] - fb[0]
        draw.text(((size[0] - fw) // 2, 1665), footer_text, fill="#94A3B8", **f_kwargs)

    return img, accent


SCENE_TEXT_ZONES = [
    {"cx": 540, "sy": 1220},
    {"cx": 495, "sy": 1315},
    {"cx": 585, "sy": 1175},
    {"cx": 540, "sy": 1350},
    {"cx": 515, "sy": 1260},
]


def build_word_layout(draw, words, font, scene_idx=0, max_line_width=820, line_height=96):
    zone = SCENE_TEXT_ZONES[scene_idx % len(SCENE_TEXT_ZONES)]
    center_x = zone["cx"]
    start_y = zone["sy"]

    text_kwargs = {"font": font}
    if HAS_RAQM:
        text_kwargs["direction"] = "rtl"

    space_bbox = draw.textbbox((0, 0), " ", **text_kwargs)
    space_w = max(18, space_bbox[2] - space_bbox[0])

    word_items = []
    for idx, w in enumerate(words):
        shaped = shape_word(w)
        bbox = draw.textbbox((0, 0), shaped, **text_kwargs)
        ww = bbox[2] - bbox[0]
        wh = bbox[3] - bbox[1]
        word_items.append({"idx": idx, "shaped": shaped, "w": ww, "h": wh})

    lines = []
    current_line = []
    current_w = 0
    for item in word_items:
        added_w = item["w"] + (space_w if current_line else 0)
        if current_line and (current_w + added_w > max_line_width):
            lines.append((current_line, current_w))
            current_line = [item]
            current_w = item["w"]
        else:
            current_line.append(item)
            current_w += added_w
    if current_line:
        lines.append((current_line, current_w))

    positioned = []
    for line_idx, (line_items, total_w) in enumerate(lines):
        cursor_x = center_x + (total_w // 2)
        y = start_y + line_idx * line_height
        for item in line_items:
            cursor_x -= item["w"]
            positioned.append({
                "idx": item["idx"],
                "shaped": item["shaped"],
                "x": cursor_x,
                "y": y,
                "w": item["w"],
                "h": item["h"]
            })
            cursor_x -= space_w

    return positioned


def draw_kinetic_captions(img, draw, positioned_words, word_timings, current_time, total_duration, pose, accent, font, font_active, frame_num, audio_level):
    if not positioned_words:
        return

    p = (pose or "POINTING").upper()
    text_kwargs = {"font": font}
    active_kwargs = {"font": font_active}
    if HAS_RAQM:
        text_kwargs["direction"] = "rtl"
        active_kwargs["direction"] = "rtl"

    float_dx = int(math.sin(frame_num * 0.14) * 14)
    float_dy = int(math.cos(frame_num * 0.18) * 10) - int(audio_level * 8)

    exit_progress = 0.0
    if p != "FOLLOW" and (total_duration - current_time < 0.16):
        exit_progress = max(0.0, min(1.0, (0.16 - (total_duration - current_time)) / 0.16))

    min_x = min(item["x"] for item in positioned_words) + float_dx - 44
    max_x = max(item["x"] + item["w"] for item in positioned_words) + float_dx + 44
    min_y = min(item["y"] for item in positioned_words) + float_dy - 28
    max_y = max(item["y"] + item["h"] for item in positioned_words) + float_dy + 38

    draw.rounded_rectangle([(min_x + 8, min_y + 10), (max_x + 8, max_y + 10)], radius=34, fill="#030712")
    border_w = 3 + int(audio_level * 4)
    draw.rounded_rectangle([(min_x, min_y), (max_x, max_y)], radius=34, fill="#0B1325", outline=accent, width=border_w)

    active_item_to_draw = None

    for item in positioned_words:
        idx = item["idx"]
        w_start, w_end = word_timings[idx]

        if current_time < w_start - 0.04:
            continue

        enter_t = min(1.0, max(0.0, (current_time - (w_start - 0.04)) / 0.18))
        dx, dy = float_dx, float_dy

        if p in ("WELCOME", "SUCCESS", "FOLLOW", "QUESTION"):
            dy += int((1.0 - ease_out_back(enter_t)) * 48)
        elif p == "WARNING":
            dy += int((1.0 - ease_out_cubic(enter_t)) * 30)
            if enter_t < 1.0:
                dx += int(math.sin(enter_t * 28.0) * (1.0 - enter_t) * 16)
        else:
            dx += int((1.0 - ease_out_back(enter_t)) * 55)
            dy += int((1.0 - ease_out_cubic(enter_t)) * 18)

        dy -= int(exit_progress * 28)

        is_speaking = (w_start <= current_time <= w_end + 0.06) and exit_progress == 0.0
        if is_speaking:
            active_item_to_draw = (item, dx, dy)
            continue

        x = item["x"] + dx
        y = item["y"] + dy
        shaped = item["shaped"]

        draw.text((x + 4, y + 5), shaped, fill="#020617", stroke_width=4, stroke_fill="#020617", **text_kwargs)
        draw.text((x, y), shaped, fill="#F8FAFC", stroke_width=1, stroke_fill="#F8FAFC", **text_kwargs)

    if active_item_to_draw:
        item, dx, dy = active_item_to_draw
        shaped = item["shaped"]
        abbox = draw.textbbox((0, 0), shaped, **active_kwargs)
        aw = abbox[2] - abbox[0]
        ah = abbox[3] - abbox[1]

        cx_w = item["x"] + dx + item["w"] // 2
        cy_w = item["y"] + dy + item["h"] // 2 - (12 + int(audio_level * 10))

        ax = cx_w - aw // 2
        ay = cy_w - ah // 2

        active_bg = "#FACC15" if p in ("WARNING", "FOLLOW", "QUESTION") else accent
        pad_x, pad_y = 18, 10

        draw.rounded_rectangle(
            [(ax - pad_x + 5, ay - pad_y + 6), (ax + aw + pad_x + 5, ay + ah + pad_y + 10)],
            radius=20, fill="#020617"
        )
        draw.rounded_rectangle(
            [(ax - pad_x, ay - pad_y), (ax + aw + pad_x, ay + ah + pad_y + 6)],
            radius=20, fill=active_bg, outline="#FFFFFF", width=3
        )
        draw.text(
            (ax, ay - 4),
            shaped,
            fill="#050811",
            stroke_width=2,
            stroke_fill="#050811",
            **active_kwargs
        )


async def generate_scene_audio(text, voice, audio_fn):
    communicate = edge_tts.Communicate(text, voice)
    boundaries = []
    with open(audio_fn, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start_sec = chunk["offset"] / 10_000_000.0
                dur_sec = chunk.get("duration", 0) / 10_000_000.0
                boundaries.append({
                    "start": start_sec,
                    "end": start_sec + dur_sec,
                    "text": chunk.get("text", "")
                })
    return boundaries


async def make_video(script_data, page_name):
    print("بدء صناعة ريلز الاستوديو الاحترافي (Studio Glow V3 - 30 FPS)...")
    scenes = script_data["scenes"]
    cover_title = script_data.get("title", "إضاءات ومعلومات مفيدة!")
    audio_paths = []
    scene_boundaries = []
    scene_durations = []
    voice = "ar-SA-HamedNeural"

    for idx, sc in enumerate(scenes):
        audio_fn = f"scene_{idx}.mp3"
        clean_text = re.sub(r"\[.*?\]", "", sc["text"]).strip()
        boundaries = await generate_scene_audio(clean_text, voice, audio_fn)
        audio_paths.append(audio_fn)
        scene_boundaries.append(boundaries)
        scene_durations.append(get_audio_duration(audio_fn))

    total_video_duration = max(1.0, sum(scene_durations))
    print(f"المدة الكلية للريلز: {total_video_duration:.1f} ثانية")

    font_path = select_random_font()
    if font_path and os.path.exists(font_path):
        font_title = ImageFont.truetype(font_path, 54)
        font_active = ImageFont.truetype(font_path, 68)
        font_badge = ImageFont.truetype(font_path, 36)
        font_small = ImageFont.truetype(font_path, 26)
    else:
        font_title = ImageFont.load_default()
        font_active = ImageFont.load_default()
        font_badge = ImageFont.load_default()
        font_small = ImageFont.load_default()

    video_clips = []
    fps = 30

    dummy_img = Image.new("RGB", (1080, 1920))
    dummy_draw = ImageDraw.Draw(dummy_img)

    elapsed_offset = 0.0
    global_frame_counter = 0

    for idx, sc in enumerate(scenes):
        raw_text = re.sub(r"\[.*?\]", "", sc["text"]).strip()
        words = [w for w in raw_text.split() if w.strip()]
        pose = sc.get("pose", "POINTING")
        visual = sc.get("visual", "IDEA_BULB")
        audio_file = f"scene_{idx}.mp3"

        duration = scene_durations[idx]
        total_frames = max(int(duration * fps), 30)
        envelope, word_timings = analyze_audio_and_timings(
            audio_file, words, total_frames, duration, scene_boundaries[idx]
        )
        positioned_words = build_word_layout(
            dummy_draw, words, font_title, scene_idx=idx, max_line_width=820, line_height=96
        )

        print(f"ريندر المشهد {idx+1}/{len(scenes)} [{pose} + {visual}]: {raw_text}")
        output_scene = f"scene_{idx}.mp4"

        ffmpeg_cmd = [
            FFMPEG_BIN, "-y",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-pix_fmt", "rgb24",
            "-s", "1080x1920",
            "-r", str(fps),
            "-i", "-",
            "-i", audio_file,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "17",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ac", "1",
            "-ar", "44100",
            "-shortest",
            output_scene
        ]

        proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

        for f in range(total_frames):
            current_time = f / float(fps)
            global_progress = (elapsed_offset + current_time) / total_video_duration
            audio_lvl = envelope[f]

            if idx == 0 and f < 8:
                frame_img = draw_cover_frame(cover_title, page_name, f, font_path, font_badge)
            else:
                frame_img, accent = draw_scene_frame(
                    idx, pose, visual, f, total_frames, global_frame_counter,
                    audio_lvl, global_progress, page_name, font_badge, font_small
                )
                draw = ImageDraw.Draw(frame_img)
                draw_kinetic_captions(
                    frame_img, draw, positioned_words, word_timings, current_time, duration,
                    pose, accent, font_title, font_active, f, audio_lvl
                )

            proc.stdin.write(frame_img.tobytes())
            global_frame_counter += 1

        proc.stdin.close()
        proc.wait()
        video_clips.append(output_scene)
        elapsed_offset += duration

    with open("inputs.txt", "w") as file:
        for clip in video_clips:
            file.write(f"file '{clip}'\n")

    raw_merged = "raw_reel.mp4"
    merge_cmd = [
        FFMPEG_BIN, "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", "inputs.txt",
        "-c", "copy",
        raw_merged
    ]
    subprocess.run(merge_cmd, check=True)

    bg_wav = generate_ambient_audio(scene_durations, "bg_audio.wav")
    final_output = "final_reel.mp4"
    mix_cmd = [
        FFMPEG_BIN, "-y",
        "-i", raw_merged,
        "-i", bg_wav,
        "-filter_complex", "[0:a]volume=1.18[v];[1:a]volume=1.0[bg];[v][bg]amix=inputs=2:duration=first:dropout_transition=2[aout]",
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        final_output
    ]
    subprocess.run(mix_cmd, check=True)

    for clip in video_clips:
        os.remove(clip)
    for audio in audio_paths:
        os.remove(audio)
    for tmp in ["inputs.txt", raw_merged, bg_wav]:
        if os.path.exists(tmp):
            os.remove(tmp)

    print(f"اكتمل بناء الفيديو النهائي بنجاح وبجودة عالية: {final_output}")
    return final_output


def publish_to_facebook(video_path, title, caption):
    print("بدء عملية النشر على فيسبوك ريلز...")
    if not FB_ACCESS_TOKEN or not FB_PAGE_ID:
        print("خطأ: FB_ACCESS_TOKEN أو FB_PAGE_ID غير موجود في Secrets!")
        sys.exit(1)

    init_url = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/video_reels"
    init_params = {"access_token": FB_ACCESS_TOKEN, "upload_phase": "start"}
    init_res = requests.post(init_url, params=init_params).json()
    print("رد فيسبوك (مرحلة البدء):", init_res)

    video_id = init_res.get("video_id")
    upload_url = init_res.get("upload_url")

    if not video_id or not upload_url:
        print("فشل بدء جلسة الرفع! راجع صلاحيات التوكن أعلاه.")
        sys.exit(1)

    file_size = os.path.getsize(video_path)
    headers = {
        "Authorization": f"OAuth {FB_ACCESS_TOKEN}",
        "offset": "0",
        "file_size": str(file_size),
        "Content-Type": "application/octet-stream"
    }
    with open(video_path, "rb") as f:
        upload_res = requests.post(upload_url, headers=headers, data=f)
    print("رد فيسبوك (مرحلة رفع الملف):", upload_res.status_code, upload_res.text)

    publish_url = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/video_reels"
    publish_params = {
        "access_token": FB_ACCESS_TOKEN,
        "video_id": video_id,
        "upload_phase": "finish",
        "video_state": "PUBLISHED",
        "title": title,
        "description": caption
    }
    publish_res = requests.post(publish_url, params=publish_params).json()
    print("رد فيسبوك (مرحلة النشر النهائي):", publish_res)

    if publish_res.get("success") or "video_id" in publish_res:
        print("تم نشر الريلز على صفحة فيسبوك بنجاح!")
        return True

    print("فشل النشر النهائي على فيسبوك!")
    sys.exit(1)


async def main():
    page_name = get_page_name()
    script_data = await generate_script(page_name)
    final_video = await make_video(script_data, page_name)
    publish_to_facebook(final_video, script_data["title"], script_data["caption"])


if __name__ == "__main__":
    asyncio.run(main())
