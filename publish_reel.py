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
from PIL import Image, ImageDraw, ImageFont, features
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
    """البحث تلقائياً عن جميع الخطوط المرفوعة في المستودع واختيار خط عشوائي لكل ريلز"""
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
        print(f"الخطوط المتوفرة ({len(candidates)}): {[os.path.basename(f) for f in candidates]}")
        print(f"الخط المختار لهذا الريلز: {os.path.basename(chosen)}")
        return chosen

    fallback = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    print("تنبيه: لم يتم العثور على خطوط مرفوعة، تم استخدام الخط الاحتياطي.")
    return fallback if os.path.exists(fallback) else None


def shape_word(word):
    """تهيئة الكلمة العربية للرسم الصحيح"""
    if HAS_RAQM:
        return word
    if arabic_reshaper and get_display:
        return get_display(arabic_reshaper.reshape(word))
    return word


def shape_multiline(text, max_chars=22):
    """تقسيم وتهيئة النص متعدد الأسطر للغلاف"""
    wrapped = textwrap.fill(text.strip(), width=max_chars)
    if HAS_RAQM:
        return wrapped
    if arabic_reshaper and get_display:
        return "\n".join(get_display(arabic_reshaper.reshape(line)) for line in wrapped.split("\n"))
    return wrapped


def ease_out_back(t):
    t = max(0.0, min(1.0, t))
    c1 = 1.70158
    c3 = c1 + 1.0
    return 1.0 + c3 * ((t - 1.0) ** 3) + c1 * ((t - 1.0) ** 2)


def ease_out_cubic(t):
    t = max(0.0, min(1.0, t))
    return 1.0 - ((1.0 - t) ** 3)


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
    """استخراج مستوى الصوت لحركة الفم + توقيت ظهور كل كلمة بدقة"""
    try:
        cmd = [
            FFMPEG_BIN, "-i", audio_file,
            "-f", "s16le", "-ac", "1", "-ar", "2000",
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
        envelope = [min(1.0, val / (max_val * 0.75)) for val in levels]

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
    """توليد موسيقى خلفية هادئة ومؤثر انتقال ناعم بين المشاهد"""
    sr = 44100
    total_dur = max(1.0, sum(scene_durations))
    total_samples = int(total_dur * sr)
    t = np.linspace(0, total_dur, total_samples, endpoint=False)

    chords = [
        [220.00, 261.63, 329.63],  # Am
        [174.61, 220.00, 261.63],  # F
        [130.81, 196.00, 261.63],  # C
        [196.00, 246.94, 293.66],  # G
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
            env = np.minimum(1.0, local_t / 0.35) * np.minimum(1.0, (chord_len - local_t) / 0.35)
            audio[mask] += 0.042 * pad * env

    cursor = 0.0
    for s_idx, d in enumerate(scene_durations):
        if s_idx > 0:
            start_sample = int(cursor * sr)
            pop_len = int(0.09 * sr)
            end_sample = min(total_samples, start_sample + pop_len)
            n_s = end_sample - start_sample
            if n_s > 0:
                tp = np.linspace(0, n_s / sr, n_s, endpoint=False)
                freq_sweep = 320.0 + 480.0 * (tp / 0.09)
                pop_wave = np.sin(2 * np.pi * freq_sweep * tp) * np.exp(-tp * 38.0)
                audio[start_sample:end_sample] += 0.14 * pop_wave
        cursor += d

    audio_int16 = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
    with wave.open(output_wav, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(audio_int16.tobytes())
    return output_wav


# 1. إعداد المفاتيح وجلب هوية الصفحة
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN")
FB_PAGE_ID = os.getenv("FB_PAGE_ID")

genai.configure(api_key=GEMINI_API_KEY, transport="rest")


def get_page_name():
    if not FB_ACCESS_TOKEN or not FB_PAGE_ID:
        return "إضاءات ومعرفة"
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
        print("تعذر جلب اسم الصفحة:", e)
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


# 2. توليد السيناريو بتنوع شامل (حديث، معلومات، مقولات، نصائح دينية ومالية ودنيوية)
async def generate_script():
    print("جاري توليد السيناريو والمشاهد البصرية...")
    outro_scene = {
        "text": "تابعوا الصفحة للمزيد من المعلومات",
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
            "instruction": (
                "اذكر حديثاً نبوياً صحيحاً وقصيراً ومشهوراً بدقة، ثم اشرح معناه العميق وكيف نطبقه في حياتنا اليومية بأسلوب مؤثر وواضح."
            ),
            "hashtags": "#أحاديث #السنة_النبوية #تذكير #إسلاميات",
            "preferred_visuals": "BOOK_LIGHT, HEART_GLOW, COMPASS_LIFE, IDEA_BULB"
        },
        {
            "category": "معلومات عامة وعلمية مذهلة",
            "instruction": (
                "قدّم معلومة غريبة ومدهشة في العلوم، التاريخ، الفضاء، جسم الإنسان، أو التكنولوجيا بأسلوب يثير الفضول."
            ),
            "hashtags": "#معلومات #هل_تعلم #ثقافة #حقائق",
            "preferred_visuals": "GLOBE, AI_BRAIN, MAGNIFIER, ROCKET, IDEA_BULB"
        },
        {
            "category": "مقولات وحكم مفيدة",
            "instruction": (
                "اختر مقولة حكيمة أو قاعدة ذهبية ملهمة من تجارب الحكماء، واشرح مغزاها وكيف تغيّر نظرتنا للحياة."
            ),
            "hashtags": "#حكم #مقولات #تطوير_الذات #اقتباسات",
            "preferred_visuals": "BOOK_LIGHT, COMPASS_LIFE, IDEA_BULB, CHART_UP"
        },
        {
            "category": "نصائح دينية وإيمانية",
            "instruction": (
                "قدّم نصيحة إيمانية وروحية لطيفة حول ذكر الله، الطمأنينة، بر الوالدين، الصدقة، أو إصلاح القلب بأسلوب دافئ ومحبب."
            ),
            "hashtags": "#نصائح_دينية #تذكير #طمأنينة #ذكر",
            "preferred_visuals": "HEART_GLOW, BOOK_LIGHT, SECURITY, CLOCK"
        },
        {
            "category": "نصائح مالية وإدارة الأموال",
            "instruction": (
                "قدّم نصيحة مالية عملية حول الادخار الذكي، تجنب التبذير، بناء مصدر دخل إضافي، أو العقلية المالية الناجحة."
            ),
            "hashtags": "#نصائح_مالية #إدارة_المال #نجاح #ادخار",
            "preferred_visuals": "MONEY_GROWTH, CHART_UP, SECURITY, CLOCK"
        },
        {
            "category": "نصائح دنيوية وتطوير الذات",
            "instruction": (
                "قدّم نصيحة حياتية ذكية في فن التعامل مع الناس، بناء العادات الناجحة، الحفاظ على الطاقة النفسية، أو تنظيم الوقت."
            ),
            "hashtags": "#نصائح_حياتية #تطوير_الذات #نجاح #حياة",
            "preferred_visuals": "COMPASS_LIFE, ROCKET, CLOCK, BATTERY, SECURITY"
        }
    ]
    chosen_cat = random.choice(content_categories)

    past_topics = load_topic_history()
    avoid_clause = ""
    if past_topics:
        avoid_clause = f"\nمهم جداً: لا تكرر أياً من هذه المواضيع المنشورة سابقاً: {', '.join(past_topics)}."

    print(f"القسم المختار: {chosen_cat['category']} | النمط: {chosen_len['name']}")

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
                    print(f"تم الاتصال بالنموذج: {m.name}")
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
        },
        {
            "title": "حكمة تغيّر حياتك!",
            "caption": "لا تستهين بالخطوات الصغيرة المستمرة 🌟🧭 #حكم #تطوير_الذات #نجاح",
            "scenes": [
                {"text": "قليل دائم خير من كثير منقطع", "pose": "WELCOME", "visual": "BOOK_LIGHT"},
                {"text": "النجاح الكبير لا يأتي من قفزة واحدة مفاجئة", "pose": "THINKING", "visual": "COMPASS_LIFE"},
                {"text": "بل من عادات بسيطة تكررها كل يوم بإصرار", "pose": "POINTING", "visual": "CLOCK"},
                {"text": "ابدأ اليوم ولو بعشر دقائق وستدهشك النتيجة", "pose": "SUCCESS", "visual": "ROCKET"},
                {"text": "ما العادة التي تتمنى الالتزام بها؟ شاركنا بالتعليقات!", "pose": "QUESTION", "visual": "COMMENT_BUBBLE"},
                outro_scene
            ]
        }
    ]
    return random.choice(fallback_scripts)


def draw_joint(draw, pt, radius, color):
    x, y = int(pt[0]), int(pt[1])
    r = max(1, int(radius))
    draw.ellipse([(x - r, y - r), (x + r, y + r)], fill=color)


# 3. رسم الجزيئات الضوئية المتحركة بالخلفية
PARTICLES = [
    {
        "x": (i * 173 + 60) % 1080,
        "y": (i * 293 + 110) % 1920,
        "r": 3 + (i % 4),
        "speed_y": 1.4 + (i % 5) * 0.45,
        "phase": i * 0.7
    }
    for i in range(28)
]


def draw_ambient_particles(draw, global_frame, accent):
    for idx, p in enumerate(PARTICLES):
        px = int((p["x"] + math.sin(global_frame * 0.04 + p["phase"]) * 28) % 1080)
        py = int((p["y"] - global_frame * p["speed_y"]) % 1920)
        r = p["r"]
        col = accent if idx % 3 == 0 else "#1E293B"
        draw.ellipse([(px - r, py - r), (px + r, py + r)], fill=col)


# 4. رسم المشهد التوضيحي المتحرك المعبر عن الجملة
def draw_visual_prop(draw, visual_type, vx, vy, frame_num, accent_color):
    scale = min(1.0, (frame_num + 1) / 8.0)
    pulse = int(math.sin(frame_num * 0.25) * 8)

    ring_r = int(175 * scale)
    if ring_r > 10:
        draw.ellipse(
            [(vx - ring_r, vy - ring_r), (vx + ring_r, vy + ring_r)],
            fill="#111A2E",
            outline=accent_color,
            width=4
        )
        inner_r = max(5, ring_r - 22 + pulse // 2)
        draw.ellipse(
            [(vx - inner_r, vy - inner_r), (vx + inner_r, vy + inner_r)],
            outline="#1E293B",
            width=2
        )

    if scale < 0.4:
        return

    v = (visual_type or "IDEA_BULB").upper()

    if "BOOK" in v:
        bob = int(math.sin(frame_num * 0.25) * 6)
        by = vy + 15 + bob
        left_page = [(vx - 95, by - 35), (vx - 4, by - 18), (vx - 4, by + 62), (vx - 95, by + 45)]
        right_page = [(vx + 4, by - 18), (vx + 95, by - 35), (vx + 95, by + 45), (vx + 4, by + 62)]
        draw.polygon(left_page, fill="#F8FAFC", outline="#38BDF8")
        draw.polygon(right_page, fill="#E2E8F0", outline="#38BDF8")
        draw.line([(vx, by - 18), (vx, by + 65)], fill="#0284C7", width=6)
        for row in range(3):
            ry = by - 5 + row * 16
            draw.line([(vx - 76, ry - 4), (vx - 22, ry + 3)], fill="#94A3B8", width=4)
            draw.line([(vx + 22, ry + 3), (vx + 76, ry - 4)], fill="#94A3B8", width=4)
        for ang in [-50, -25, 0, 25, 50]:
            rad = math.radians(ang - 90)
            r1 = 35 + (frame_num % 6) * 2
            r2 = 75 + (frame_num % 6) * 3
            x1, y1 = vx + int(math.cos(rad) * r1), by - 25 + int(math.sin(rad) * r1)
            x2, y2 = vx + int(math.cos(rad) * r2), by - 25 + int(math.sin(rad) * r2)
            draw.line([(x1, y1), (x2, y2)], fill="#FACC15", width=5)

    elif "HEART" in v:
        beat = int(abs(math.sin(frame_num * 0.28)) * 8)
        hy = vy - 5
        draw.ellipse([(vx - 58 - beat, hy - 52 - beat), (vx + 4, hy + 10)], fill="#10B981")
        draw.ellipse([(vx - 4, hy - 52 - beat), (vx + 58 + beat, hy + 10)], fill="#10B981")
        tri = [(vx - 55 - beat, hy - 8), (vx + 55 + beat, hy - 8), (vx, hy + 68 + beat)]
        draw.polygon(tri, fill="#10B981")
        halo_r = 88 + beat
        draw.ellipse([(vx - halo_r, hy - halo_r + 8), (vx + halo_r, hy + halo_r + 8)], outline="#6EE7B7", width=3)

    elif "MONEY" in v:
        bob = int(math.sin(frame_num * 0.28) * 6)
        cx_c, cy_c = vx - 18, vy + 8 + bob
        draw.ellipse([(cx_c - 64, cy_c - 64), (cx_c + 64, cy_c + 64)], fill="#F59E0B", outline="#FEF08A", width=6)
        draw.ellipse([(cx_c - 46, cy_c - 46), (cx_c + 46, cy_c + 46)], outline="#FEF08A", width=3)
        draw.line([(cx_c, cy_c - 30), (cx_c, cy_c + 30)], fill="#451A03", width=6)
        draw.arc([(cx_c - 18, cy_c - 22), (cx_c + 18, cy_c + 2)], start=45, end=270, fill="#451A03", width=6)
        draw.arc([(cx_c - 18, cy_c - 2), (cx_c + 18, cy_c + 22)], start=225, end=90, fill="#451A03", width=6)
        ax, ay = vx + 58, vy - 35 - bob
        draw.line([(vx + 22, vy + 35), (ax, ay)], fill="#10B981", width=11)
        draw.polygon([(ax - 18, ay - 6), (ax + 12, ay - 16), (ax + 8, ay + 16)], fill="#10B981")

    elif "COMPASS" in v:
        draw.ellipse([(vx - 96, vy - 96), (vx + 96, vy + 96)], fill="#0F172A", outline="#00F0FF", width=8)
        draw.ellipse([(vx - 76, vy - 76), (vx + 76, vy + 76)], outline="#1E293B", width=2)
        ang = math.sin(frame_num * 0.16) * 0.55 - (math.pi / 4.0)
        nx = int(math.cos(ang) * 66)
        ny = int(math.sin(ang) * 66)
        px = int(math.cos(ang + math.pi / 2.0) * 16)
        py = int(math.sin(ang + math.pi / 2.0) * 16)
        draw.polygon([(vx + nx, vy + ny), (vx + px, vy + py), (vx - px, vy - py)], fill="#EF4444")
        draw.polygon([(vx - nx, vy - ny), (vx + px, vy + py), (vx - px, vy - py)], fill="#F8FAFC")
        draw.ellipse([(vx - 10, vy - 10), (vx + 10, vy + 10)], fill="#FACC15")

    elif "COMMENT" in v or "QUESTION" in v:
        bob = int(math.sin(frame_num * 0.3) * 7)
        bx, by = vx, vy - 10 + bob
        draw.rounded_rectangle(
            [(bx - 105, by - 65), (bx + 105, by + 55)],
            radius=30,
            fill="#1E1B4B",
            outline="#A855F7",
            width=6
        )
        tail = [(bx - 45, by + 52), (bx - 70, by + 88), (bx - 10, by + 52)]
        draw.polygon(tail, fill="#1E1B4B", outline="#A855F7")
        draw.line([(bx - 42, by + 52), (bx - 13, by + 52)], fill="#1E1B4B", width=8)

        for d_idx in range(3):
            dx = bx - 44 + d_idx * 44
            dy = by - 5 + int(math.sin(frame_num * 0.45 - d_idx * 0.9) * 8)
            dot_col = "#FACC15" if d_idx == (frame_num // 5) % 3 else "#E9D5FF"
            draw.ellipse([(dx - 11, dy - 11), (dx + 11, dy + 11)], fill=dot_col)

        qx, qy = bx + 85, by - 58
        draw.ellipse([(qx - 26, qy - 26), (qx + 26, qy + 26)], fill="#FACC15", outline="#090D16", width=4)
        draw.arc([(qx - 10, qy - 14), (qx + 10, qy + 4)], start=180, end=45, fill="#090D16", width=5)
        draw.ellipse([(qx - 3, qy + 10), (qx + 3, qy + 16)], fill="#090D16")

    elif "FOLLOW" in v:
        card_w, card_h = 210, 110
        bounce = int(math.sin(frame_num * 0.35) * 6)
        cy_card = vy + bounce
        draw.rounded_rectangle(
            [(vx - card_w // 2, cy_card - card_h // 2), (vx + card_w // 2, cy_card + card_h // 2)],
            radius=28,
            fill="#0284C7",
            outline="#F8FAFC",
            width=5
        )
        px, py = vx + 45, cy_card
        draw.ellipse([(px - 26, py - 26), (px + 26, py + 26)], fill="#F8FAFC")
        draw.line([(px - 13, py), (px + 13, py)], fill="#0284C7", width=6)
        draw.line([(px, py - 13), (px, py + 13)], fill="#0284C7", width=6)

        bx, by = vx - 35, cy_card
        swing = int(math.sin(frame_num * 0.5) * 5)
        draw.chord([(bx - 22 + swing, by - 24), (bx + 22 + swing, by + 18)], start=180, end=360, fill="#FACC15")
        draw.rectangle([(bx - 22 + swing, by - 4), (bx + 22 + swing, by + 12)], fill="#FACC15")
        draw.rounded_rectangle([(bx - 28 + swing, by + 10), (bx + 28 + swing, by + 18)], radius=4, fill="#FACC15")
        draw.ellipse([(bx - 7 - swing, by + 18), (bx + 7 - swing, by + 30)], fill="#FEF08A")

        ripple_r = 25 + (frame_num * 5) % 55
        if ripple_r < 75:
            draw.ellipse(
                [(px - ripple_r, py - ripple_r), (px + ripple_r, py + ripple_r)],
                outline="#38BDF8",
                width=3
            )

    elif "WIFI" in v:
        base_y = vy + 65
        draw.ellipse([(vx - 16, base_y - 16), (vx + 16, base_y + 16)], fill="#00F0FF")
        active_wave = (frame_num // 4) % 4
        for w_idx, r_arc in enumerate([55, 95, 135]):
            col = "#00F0FF" if w_idx <= active_wave else "#1E293B"
            draw.arc(
                [(vx - r_arc, base_y - r_arc), (vx + r_arc, base_y + r_arc)],
                start=225,
                end=315,
                fill=col,
                width=12
            )

    elif "MAGNIFIER" in v:
        ox = int(math.cos(frame_num * 0.2) * 18)
        oy = int(math.sin(frame_num * 0.25) * 14)
        gx, gy = vx - 15 + ox, vy - 15 + oy
        draw.line([(gx + 45, gy + 45), (gx + 98, gy + 98)], fill="#94A3B8", width=18)
        draw.ellipse([(gx - 68, gy - 68), (gx + 68, gy + 68)], fill="#0F172A", outline="#00F0FF", width=10)
        draw.arc([(gx - 46, gy - 46), (gx + 46, gy + 46)], start=200, end=270, fill="#38BDF8", width=5)

    elif "BATTERY" in v:
        bw, bh = 110, 190
        draw.rounded_rectangle([(vx - 30, vy - bh // 2 - 18), (vx + 30, vy - bh // 2)], radius=6, fill="#94A3B8")
        draw.rounded_rectangle([(vx - bw // 2, vy - bh // 2), (vx + bw // 2, vy + bh // 2)], radius=18, outline="#F8FAFC", width=8)
        level = 0.25 + 0.55 * (0.5 + 0.5 * math.cos(frame_num * 0.15))
        fill_h = int((bh - 24) * level)
        b_color = "#EF4444" if level < 0.45 else "#10B981"
        draw.rounded_rectangle(
            [(vx - bw // 2 + 12, vy + bh // 2 - 12 - fill_h), (vx + bw // 2 - 12, vy + bh // 2 - 12)],
            radius=10,
            fill=b_color
        )
        bolt = [(vx + 8, vy - 45), (vx - 22, vy + 5), (vx + 2, vy + 5), (vx - 12, vy + 50), (vx + 24, vy - 5), (vx - 2, vy - 5)]
        draw.polygon(bolt, fill="#FACC15")

    elif "PHONE" in v:
        pw, ph = 115, 205
        draw.rounded_rectangle([(vx - pw // 2, vy - ph // 2), (vx + pw // 2, vy + ph // 2)], radius=22, fill="#0F172A", outline="#F8FAFC", width=7)
        draw.line([(vx - 22, vy - ph // 2 + 16), (vx + 22, vy - ph // 2 + 16)], fill="#64748B", width=5)
        for idx, col in enumerate(["#00F0FF", "#F43F5E", "#10B981", "#F59E0B"]):
            ox = -26 if idx % 2 == 0 else 26
            oy = -32 if idx < 2 else 32
            dy = int(math.sin(frame_num * 0.3 + idx) * 6)
            draw.rounded_rectangle([(vx + ox - 18, vy + oy - 18 + dy), (vx + ox + 18, vy + oy + 18 + dy)], radius=8, fill=col)

    elif "WARNING" in v:
        s = 115 + pulse
        tri = [(vx, vy - s), (vx - s, vy + int(s * 0.75)), (vx + s, vy + int(s * 0.75))]
        draw.polygon(tri, fill="#450A0A", outline="#EF4444")
        draw.line([tri[0], tri[1], tri[2], tri[0]], fill="#EF4444", width=10)
        draw.line([(vx, vy - 40), (vx, vy + 22)], fill="#FACC15", width=14)
        draw.ellipse([(vx - 8, vy + 38), (vx + 8, vy + 54)], fill="#FACC15")

    elif "ROCKET" in v:
        ry = vy + int(math.sin(frame_num * 0.4) * 12)
        body = [(vx, ry - 105), (vx - 42, ry + 35), (vx + 42, ry + 35)]
        draw.polygon(body, fill="#F8FAFC", outline="#00F0FF")
        draw.polygon([(vx - 42, ry + 5), (vx - 72, ry + 55), (vx - 42, ry + 35)], fill="#F43F5E")
        draw.polygon([(vx + 42, ry + 5), (vx + 72, ry + 55), (vx + 42, ry + 35)], fill="#F43F5E")
        draw.ellipse([(vx - 18, ry - 45), (vx + 18, ry - 9)], fill="#0284C7", outline="#00F0FF", width=4)
        flame_h = 45 + (frame_num % 4) * 12
        draw.polygon([(vx - 22, ry + 38), (vx + 22, ry + 38), (vx, ry + 38 + flame_h)], fill="#F59E0B")

    elif "SECURITY" in v:
        shield = [
            (vx, vy - 105), (vx + 85, vy - 70), (vx + 72, vy + 35),
            (vx, vy + 105), (vx - 72, vy + 35), (vx - 85, vy - 70)
        ]
        draw.polygon(shield, fill="#064E3B", outline="#10B981")
        draw.line(shield + [shield[0]], fill="#10B981", width=9)
        draw.line([(vx - 32, vy + 2), (vx - 8, vy + 28), (vx + 38, vy - 25)], fill="#F8FAFC", width=12)

    elif "CHART" in v:
        bars = [55, 95, 145, 195]
        for i, bh in enumerate(bars):
            anim_h = int(bh * min(1.0, (frame_num + 4) / (10 + i * 3)))
            bx = vx - 85 + i * 45
            col = "#00F0FF" if i < 3 else "#10B981"
            draw.rounded_rectangle([(bx, vy + 95 - anim_h), (bx + 32, vy + 95)], radius=6, fill=col)

    elif "CLOCK" in v:
        draw.ellipse([(vx - 105, vy - 105), (vx + 105, vy + 105)], fill="#0F172A", outline="#00F0FF", width=9)
        angle = frame_num * 0.35
        hx = vx + int(math.cos(angle) * 70)
        hy = vy + int(math.sin(angle) * 70)
        draw.line([(vx, vy), (vx, vy - 55)], fill="#F8FAFC", width=8)
        draw.line([(vx, vy), (hx, hy)], fill="#F43F5E", width=6)
        draw.ellipse([(vx - 8, vy - 8), (vx + 8, vy + 8)], fill="#00F0FF")

    elif "GLOBE" in v:
        draw.ellipse([(vx - 105, vy - 105), (vx + 105, vy + 105)], outline="#00F0FF", width=7)
        rx = max(15, int(abs(math.cos(frame_num * 0.12)) * 95))
        draw.ellipse([(vx - rx, vy - 105), (vx + rx, vy + 105)], outline="#38BDF8", width=4)
        draw.line([(vx - 105, vy), (vx + 105, vy)], fill="#38BDF8", width=4)
        draw.line([(vx - 90, vy - 48), (vx + 90, vy - 48)], fill="#38BDF8", width=3)
        draw.line([(vx - 90, vy + 48), (vx + 90, vy + 48)], fill="#38BDF8", width=3)

    elif "AI" in v or "BRAIN" in v:
        nodes = [
            (vx, vy - 80), (vx - 80, vy - 25), (vx + 80, vy - 25),
            (vx - 55, vy + 65), (vx + 55, vy + 65), (vx, vy + 5)
        ]
        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                draw.line([nodes[i], nodes[j]], fill="#0284C7", width=4)
        for idx, (nx, ny) in enumerate(nodes):
            nr = 14 + int(math.sin(frame_num * 0.3 + idx) * 4)
            draw.ellipse([(nx - nr, ny - nr), (nx + nr, ny + nr)], fill="#00F0FF", outline="#F8FAFC", width=3)

    else:
        draw.ellipse([(vx - 65 - pulse // 2, vy - 85 - pulse // 2), (vx + 65 + pulse // 2, vy + 45 + pulse // 2)], fill="#FACC15", outline="#FEF08A", width=5)
        draw.rounded_rectangle([(vx - 30, vy + 40), (vx + 30, vy + 80)], radius=8, fill="#94A3B8")
        for ang in range(0, 360, 45):
            rad = math.radians(ang + frame_num * 3)
            x1, y1 = vx + int(math.cos(rad) * 80), vy - 20 + int(math.sin(rad) * 80)
            x2, y2 = vx + int(math.cos(rad) * 105), vy - 20 + int(math.sin(rad) * 105)
            draw.line([(x1, y1), (x2, y2)], fill="#FACC15", width=5)


# 5. رسم الستيك مان المطوّر (نظارات تقنية ذكية + تفاصيل ياقة)
def draw_upgraded_stickman(draw, cx, cy, pose, frame_num, mouth_open, accent, has_side_visual):
    p = (pose or "POINTING").upper()
    body_color = "#F8FAFC"
    dark_detail = "#0F172A"
    line_width = 18
    r = line_width // 2

    draw.line([(cx, cy), (cx, cy + 230)], fill=body_color, width=line_width)
    draw.polygon([(cx - 24, cy + 16), (cx + 24, cy + 16), (cx, cy + 52)], fill=accent)
    draw.line([(cx, cy + 230), (cx - 72, cy + 420)], fill=body_color, width=line_width)
    draw.line([(cx, cy + 230), (cx + 72, cy + 420)], fill=body_color, width=line_width)
    draw_joint(draw, (cx - 72, cy + 420), r, body_color)
    draw_joint(draw, (cx + 72, cy + 420), r, body_color)

    draw.ellipse([(cx - 94, cy - 188), (cx + 94, cy)], fill="#131C31", outline=body_color, width=14)

    draw.arc([(cx - 107, cy - 203), (cx + 107, cy - 82)], start=180, end=360, fill=accent, width=12)
    draw.rounded_rectangle([(cx - 120, cy - 134), (cx - 90, cy - 62)], radius=12, fill=accent, outline=dark_detail, width=4)
    draw.rounded_rectangle([(cx + 90, cy - 134), (cx + 120, cy - 62)], radius=12, fill=accent, outline=dark_detail, width=4)

    eye_shift = 12 if has_side_visual else 0
    gx = cx + eye_shift
    gy = cy - 110
    draw.rounded_rectangle([(gx - 62, gy - 20), (gx - 8, gy + 16)], radius=10, fill="#090D16", outline=accent, width=5)
    draw.rounded_rectangle([(gx + 8, gy - 20), (gx + 62, gy + 16)], radius=10, fill="#090D16", outline=accent, width=5)
    draw.line([(gx - 8, gy - 4), (gx + 8, gy - 4)], fill=accent, width=6)
    draw.ellipse([(gx - 42, gy - 8), (gx - 26, gy + 8)], fill="#FFFFFF")
    draw.ellipse([(gx + 26, gy - 8), (gx + 42, gy + 8)], fill="#FFFFFF")
    draw.line([(gx - 50, gy - 12), (gx - 38, gy + 6)], fill=accent, width=3)
    draw.line([(gx + 20, gy - 12), (gx + 32, gy + 6)], fill=accent, width=3)

    mx = gx
    my = cy - 58
    if mouth_open > 0.25:
        mh = int(8 + mouth_open * 22)
        mw = int(20 + mouth_open * 10)
        draw.ellipse([(mx - mw, my - mh // 2), (mx + mw, my + mh // 2)], fill="#090D16", outline=body_color, width=5)
    else:
        draw.arc([(mx - 28, my - 15), (mx + 28, my + 15)], start=10, end=170, fill=body_color, width=7)

    if p == "WELCOME":
        draw.line([(cx, cy + 45), (cx - 95, cy + 165)], fill=body_color, width=line_width)
        draw_joint(draw, (cx - 95, cy + 165), r, body_color)
        wave = int(math.sin(frame_num * 0.4) * 28)
        draw.line([(cx, cy + 45), (cx + 95, cy - 15), (cx + 135 + wave, cy - 95)], fill=body_color, width=line_width)
        draw_joint(draw, (cx + 95, cy - 15), r, body_color)
        draw_joint(draw, (cx + 135 + wave, cy - 95), r + 3, accent)

    elif p == "FOLLOW":
        wave_l = int(math.sin(frame_num * 0.4) * 22)
        draw.line([(cx, cy + 45), (cx - 85, cy - 15), (cx - 120 - wave_l, cy - 90)], fill=body_color, width=line_width)
        draw_joint(draw, (cx - 120 - wave_l, cy - 90), r + 3, "#FACC15")

        point_y = 685 + int(math.sin(frame_num * 0.35) * 12)
        draw.line([(cx, cy + 45), (cx + 110, cy + 15), (cx + 225, point_y)], fill=body_color, width=line_width)
        draw_joint(draw, (cx + 225, point_y), r + 4, accent)

    elif p == "WARNING":
        shake = int(math.sin(frame_num * 0.6) * 10)
        draw.line([(cx, cy + 45), (cx - 75, cy - 10), (cx - 65, cy - 85 + shake)], fill=body_color, width=line_width)
        draw.line([(cx, cy + 45), (cx + 95, cy - 15), (cx + 165, cy - 65 - shake)], fill=body_color, width=line_width)
        draw_joint(draw, (cx - 65, cy - 85 + shake), r + 2, accent)
        draw_joint(draw, (cx + 165, cy - 65 - shake), r + 2, accent)

    elif p == "SUCCESS":
        pump = int(abs(math.sin(frame_num * 0.35)) * 22)
        draw.line([(cx, cy + 45), (cx - 85, cy - 25), (cx - 110, cy - 105 - pump)], fill=body_color, width=line_width)
        draw.line([(cx, cy + 45), (cx + 95, cy - 20), (cx + 145, cy - 95 - pump)], fill=body_color, width=line_width)
        draw_joint(draw, (cx - 110, cy - 105 - pump), r + 3, accent)
        draw_joint(draw, (cx + 145, cy - 95 - pump), r + 3, accent)

    else:
        draw.line([(cx, cy + 45), (cx - 75, cy + 120), (cx - 45, cy + 185)], fill=body_color, width=line_width)
        draw_joint(draw, (cx - 45, cy + 185), r, body_color)

        point_y = 695 + int(math.sin(frame_num * 0.35) * 14)
        elbow_x = cx + 110
        elbow_y = cy + 20
        hand_x = cx + 225
        draw.line([(cx, cy + 45), (elbow_x, elbow_y), (hand_x, point_y)], fill=body_color, width=line_width)
        draw_joint(draw, (elbow_x, elbow_y), r, body_color)
        draw_joint(draw, (hand_x, point_y), r + 4, accent)

        for step in range(3):
            lx = hand_x + 25 + step * 35 + ((frame_num * 4) % 25)
            if lx < 555:
                draw.line([(lx, point_y), (lx + 16, point_y)], fill=accent, width=5)


def apply_camera_zoom(img, zoom):
    """تقريب كاميرا ناعم مع الحفاظ على أبعاد الفيديو بدقة"""
    if zoom <= 1.001:
        return img
    w, h = img.size
    zw = int(w * zoom)
    zh = int(h * zoom)
    resized = img.resize((zw, zh), Image.Resampling.BILINEAR)
    left = (zw - w) // 2
    top = (zh - h) // 2
    return resized.crop((left, top, left + w, top + h))


# 6. رسم إطار الغلاف الاحترافي مع ضبط حجم الخط تلقائياً ليتناسب مع أي خط عربي
def draw_cover_frame(title, page_name, frame_num, font_path, font_badge, size=(1080, 1920)):
    accent = "#00F0FF"
    img = Image.new("RGB", size, "#090D16")
    draw = ImageDraw.Draw(img)

    for i in range(0, size[0], 120):
        draw.line([(i, 0), (i, size[1])], fill="#121A2B", width=1)
    for j in range(0, size[1], 120):
        draw.line([(0, j), (size[0], j)], fill="#121A2B", width=1)

    draw_ambient_particles(draw, frame_num, accent)

    if page_name:
        shaped_name = shape_multiline(page_name, max_chars=30)
        b_kwargs = {"font": font_badge}
        if HAS_RAQM:
            b_kwargs["direction"] = "rtl"
        bb = draw.textbbox((0, 0), shaped_name, **b_kwargs)
        bw = (bb[2] - bb[0]) + 56
        bh = (bb[3] - bb[1]) + 26
        bx1 = (size[0] - bw) // 2
        by1 = 150
        draw.rounded_rectangle([(bx1, by1), (bx1 + bw, by1 + bh)], radius=bh // 2, fill="#111A2E", outline=accent, width=3)
        draw.text((bx1 + 28, by1 + (bh - (bb[3] - bb[1])) // 2 - 2), shaped_name, fill="#FFFFFF", **b_kwargs)

    card_left, card_top, card_right, card_bottom = 90, 320, 990, 680
    draw.rounded_rectangle(
        [(card_left, card_top), (card_right, card_bottom)],
        radius=38,
        fill="#0F172A",
        outline=accent,
        width=6
    )

    shaped_title = shape_multiline(title, max_chars=20)
    chosen_font_cover = ImageFont.load_default()
    if font_path and os.path.exists(font_path):
        for test_size in range(68, 42, -4):
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

    draw_upgraded_stickman(draw, 540, 1020, "WELCOME", frame_num, 0.4, accent, has_side_visual=False)
    return img


# 7. رسم المشهد الكامل مع الهوية والجزيئات وحركة الكاميرا
def draw_scene_frame(pose, visual_type, frame_num, total_frames, global_frame, mouth_open, global_progress, page_name, font_badge, size=(1080, 1920)):
    p = (pose or "POINTING").upper()

    if p == "WARNING":
        accent = "#EF4444"
        bg_grid = "#241219"
    elif p == "SUCCESS":
        accent = "#10B981"
        bg_grid = "#0F241E"
    elif p == "FOLLOW":
        accent = "#38BDF8"
        bg_grid = "#0F2136"
    elif p == "QUESTION":
        accent = "#A855F7"
        bg_grid = "#1E1433"
    else:
        accent = "#00F0FF"
        bg_grid = "#121A2B"

    img = Image.new("RGB", size, "#090D16")
    draw = ImageDraw.Draw(img)

    offset_grid = (global_frame * 2) % 120
    for i in range(-120, size[0] + 120, 120):
        draw.line([(i + offset_grid, 0), (i + offset_grid, size[1])], fill=bg_grid, width=1)
    for j in range(0, size[1], 120):
        draw.line([(0, j), (size[0], j)], fill=bg_grid, width=1)

    draw_ambient_particles(draw, global_frame, accent)

    has_side_visual = p != "WELCOME"
    slide_t = min(1.0, (frame_num + 1) / 7.0)
    target_cx = 310 if has_side_visual else 540
    cx = int(540 + (target_cx - 540) * slide_t)
    cy = 700 + int(math.sin(frame_num * 0.25) * 8)

    if has_side_visual:
        draw_visual_prop(draw, visual_type, 740, 680, frame_num, accent)
    else:
        draw_visual_prop(draw, visual_type, 540, 340, frame_num, accent)

    draw_upgraded_stickman(draw, cx, cy, p, frame_num, mouth_open, accent, has_side_visual)

    zoom = 1.0 + 0.035 * (frame_num / float(max(1, total_frames)))
    img = apply_camera_zoom(img, zoom)
    draw = ImageDraw.Draw(img)

    draw.rectangle([(0, 0), (size[0], 12)], fill="#1E293B")
    prog_w = int(size[0] * max(0.0, min(1.0, global_progress)))
    if prog_w > 0:
        draw.rectangle([(0, 0), (prog_w, 12)], fill=accent)

    if page_name:
        shaped_name = shape_multiline(page_name, max_chars=30)
        badge_kwargs = {"font": font_badge}
        if HAS_RAQM:
            badge_kwargs["direction"] = "rtl"
        bb = draw.textbbox((0, 0), shaped_name, **badge_kwargs)
        bw = (bb[2] - bb[0]) + 56
        bh = (bb[3] - bb[1]) + 26
        bx1 = (size[0] - bw) // 2
        by1 = 135
        draw.rounded_rectangle(
            [(bx1, by1), (bx1 + bw, by1 + bh)],
            radius=bh // 2,
            fill="#111A2E",
            outline="#1E293B",
            width=3
        )
        draw.ellipse([(bx1 + 18, by1 + bh // 2 - 6), (bx1 + 30, by1 + bh // 2 + 6)], fill=accent)
        draw.text(
            (bx1 + 42, by1 + (bh - (bb[3] - bb[1])) // 2 - 2),
            shaped_name,
            fill="#E2E8F0",
            **badge_kwargs
        )

    return img, accent


# 8. نظام النصوص المتحركة (Kinetic Motion Typography)
def build_word_layout(draw, words, font, max_line_width=820, center_x=500, start_y=1190, line_height=86):
    text_kwargs = {"font": font}
    if HAS_RAQM:
        text_kwargs["direction"] = "rtl"

    space_bbox = draw.textbbox((0, 0), " ", **text_kwargs)
    space_w = max(14, space_bbox[2] - space_bbox[0])

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


def draw_kinetic_captions(draw, positioned_words, word_timings, current_time, total_duration, pose, accent, font):
    p = (pose or "POINTING").upper()
    text_kwargs = {"font": font}
    if HAS_RAQM:
        text_kwargs["direction"] = "rtl"

    exit_progress = 0.0
    if p != "FOLLOW" and (total_duration - current_time < 0.16):
        exit_progress = max(0.0, min(1.0, (0.16 - (total_duration - current_time)) / 0.16))

    for item in positioned_words:
        idx = item["idx"]
        w_start, w_end = word_timings[idx]

        if current_time < w_start - 0.03:
            continue

        enter_t = min(1.0, max(0.0, (current_time - (w_start - 0.03)) / 0.18))
        dx, dy = 0, 0

        if p in ("WELCOME", "SUCCESS", "FOLLOW", "QUESTION"):
            dy = int((1.0 - ease_out_back(enter_t)) * 48)
        elif p == "WARNING":
            dy = int((1.0 - ease_out_cubic(enter_t)) * 30)
            if enter_t < 1.0:
                dx = int(math.sin(enter_t * 28.0) * (1.0 - enter_t) * 16)
        elif p == "THINKING":
            dy = int((1.0 - ease_out_cubic(enter_t)) * 32)
        else:
            dx = int((1.0 - ease_out_back(enter_t)) * 55)
            dy = int((1.0 - ease_out_cubic(enter_t)) * 18)

        dy -= int(exit_progress * 28)

        is_speaking = (w_start <= current_time <= w_end + 0.06) and exit_progress == 0.0
        if is_speaking:
            dy -= 8

        x = item["x"] + dx
        y = item["y"] + dy
        shaped = item["shaped"]

        draw.text(
            (x + 4, y + 5),
            shaped,
            fill="#030712",
            stroke_width=3,
            stroke_fill="#030712",
            **text_kwargs
        )

        if is_speaking:
            active_color = "#FACC15" if p in ("WARNING", "FOLLOW", "QUESTION") else accent
            draw.text(
                (x, y),
                shaped,
                fill=active_color,
                stroke_width=2,
                stroke_fill=active_color,
                **text_kwargs
            )
            bar_y = y + item["h"] + 14
            bar_prog = min(1.0, max(0.2, (current_time - w_start) / max(0.08, (w_end - w_start))))
            bar_w = int(item["w"] * bar_prog)
            bar_x2 = x + item["w"]
            bar_x1 = bar_x2 - bar_w
            draw.rounded_rectangle([(bar_x1, bar_y), (bar_x2, bar_y + 6)], radius=3, fill=active_color)
        else:
            draw.text(
                (x, y),
                shaped,
                fill="#FFFFFF",
                stroke_width=1,
                stroke_fill="#FFFFFF",
                **text_kwargs
            )


# 9. توليد الصوت مع التقاط توقيت الكلمات
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


# 10. تجميع الفيديو النهائي
async def make_video(script_data, page_name):
    print("بدء صناعة ريلز الستيك مان الاحترافي...")
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
        font_title = ImageFont.truetype(font_path, 56)
        font_badge = ImageFont.truetype(font_path, 34)
    else:
        font_title = ImageFont.load_default()
        font_badge = ImageFont.load_default()

    video_clips = []
    fps = 24

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
        total_frames = max(int(duration * fps), 24)
        envelope, word_timings = analyze_audio_and_timings(
            audio_file, words, total_frames, duration, scene_boundaries[idx]
        )
        positioned_words = build_word_layout(
            dummy_draw, words, font_title, max_line_width=820, center_x=500, start_y=1190, line_height=86
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
            "-preset", "ultrafast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-ac", "1",
            "-ar", "44100",
            "-shortest",
            output_scene
        ]

        proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

        for f in range(total_frames):
            current_time = f / float(fps)
            global_progress = (elapsed_offset + current_time) / total_video_duration

            if idx == 0 and f < 6:
                frame_img = draw_cover_frame(cover_title, page_name, f, font_path, font_badge)
            else:
                frame_img, accent = draw_scene_frame(
                    pose, visual, f, total_frames, global_frame_counter,
                    envelope[f], global_progress, page_name, font_badge
                )
                draw = ImageDraw.Draw(frame_img)
                draw_kinetic_captions(
                    draw, positioned_words, word_timings, current_time, duration, pose, accent, font_title
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
        "-filter_complex", "[0:a]volume=1.15[v];[1:a]volume=1.0[bg];[v][bg]amix=inputs=2:duration=first:dropout_transition=2[aout]",
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

    print(f"اكتمل بناء الفيديو النهائي بنجاح: {final_output}")
    return final_output


# 11. نشر الفيديو على بيج فيسبوك
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
    script_data = await generate_script()
    final_video = await make_video(script_data, page_name)
    publish_to_facebook(final_video, script_data["title"], script_data["caption"])


if __name__ == "__main__":
    asyncio.run(main())
