import os
import sys
import re
import json
import math
import wave
import random
import shutil
import asyncio
import time
import datetime
import textwrap
import subprocess
import requests
from collections import defaultdict

# وضع التحليلات: python main.py analytics   (لا يحتاج مكتبات الفيديو)
ANALYTICS_MODE = len(sys.argv) > 1 and sys.argv[1] == "analytics"

if not ANALYTICS_MODE:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont, ImageChops, ImageFilter, features
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



GRAPH_VERSION = os.getenv("FB_GRAPH_VERSION", "v26.0")
WEIGHTS_FILE = "weights.json"
POSTS_LOG_FILE = "posts_log.json"
CURRENT_META = {}  # يُملأ أثناء توليد السيناريو ويُسجَّل عند النشر


HOOK_STYLES = {
    "question": "سؤال صادم يثير الفضول",
    "myth": "تصحيح خطأ شائع يعتقده كثير من الناس",
    "number": "رقم أو معلومة مفاجئة",
    "challenge": "تحدٍّ مباشر للمشاهد (هل تستطيع...؟)",
}
CATEGORY_BOOST = {"شرح حديث نبوي شريف": 2.0}  # يضاعف احتمال قسم الأحاديث (لتتقدم السلاسل أسرع)
# أصوات رجالية عربية (تُفلتر تلقائياً بما هو متاح فعلاً في edge_tts). للتغيير: REEL_VOICES="voice1,voice2"
VOICE_CANDIDATES = ["ar-SA-HamedNeural", "ar-AE-HamdanNeural", "ar-EG-ShakirNeural", "ar-IQ-BasselNeural"]
RATE_CHOICES = ["+0%", "+4%", "+8%"]


def load_weights():
    """أوزان الأقسام والأطوال التي يحسبها analytics.py من أداء الريلز السابق"""
    try:
        with open(WEIGHTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"category": {}, "length": {}}


def log_post(video_id, title):
    try:
        try:
            with open(POSTS_LOG_FILE, "r", encoding="utf-8") as f:
                posts = json.load(f)
        except Exception:
            posts = []
        posts.append({
            "video_id": str(video_id),
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "title": title,
            "category": CURRENT_META.get("category", "unknown"),
            "scenes_count": CURRENT_META.get("scenes_count", "?"),
            "hook": CURRENT_META.get("hook", ""),
            "hook_type": CURRENT_META.get("hook_type", ""),
            "voice": CURRENT_META.get("voice", ""),
            "series": CURRENT_META.get("series", ""),
            "final": False
        })
        with open(POSTS_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(posts, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("تعذر تسجيل الريلز في posts_log.json:", e)


def build_first_comment(script_data):
    """نص التعليق الأول تحت الريلز (يُحسب قبل إضافة مشهد الـ Hook)"""
    if script_data.get("first_comment"):
        return script_data["first_comment"]
    for sc in reversed(script_data.get("scenes", [])):
        if sc.get("pose") == "QUESTION" and sc.get("text"):
            return "💬 " + sc["text"]
    return ""


def add_hook(script_data):
    """يضيف مشهد Hook صادم في أول 3 ثواني قبل بقية الريلز"""
    hook = (script_data.get("hook") or "").strip()
    if not hook or len(hook.split()) > 9 or "تابع" in hook:
        return script_data
    pose, visual = random.choice([
        ("WARNING", "WARNING_SIGN"),
        ("THINKING", "MAGNIFIER"),
        ("QUESTION", "IDEA_BULB"),
    ])
    script_data["scenes"].insert(0, {"text": hook, "pose": pose, "visual": visual})
    CURRENT_META["hook"] = hook
    print(f"الـ Hook: {hook}")
    return script_data


def get_page_name():
    if not FB_ACCESS_TOKEN or not FB_PAGE_ID:
        return "إضاءات ومعرفة"
    try:
        res = requests.get(
            f"https://graph.facebook.com/{GRAPH_VERSION}/{FB_PAGE_ID}",
            params={"fields": "name", "access_token": FB_ACCESS_TOKEN},
            timeout=10
        ).json()
        if "error" in res:
            err = res["error"]
            print("خطأ من فيسبوك:", err.get("message"))
            if err.get("code") == 190:
                print("التوكن منتهي أو غير صالح! جدّد FB_ACCESS_TOKEN قبل المتابعة.")
                sys.exit(1)
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


# ======================================================================
# بيانات: أحاديث صحيحة (بنك ثابت)
# ======================================================================
HADITHS = [
 {"id": 1, "narrator": "عن عمر بن الخطاب رضي الله عنه", "text": "إنما الأعمال بالنيات، وإنما لكل امرئ ما نوى", "source": "رواه البخاري ومسلم", "meaning": "قيمة العمل تُقاس بنيته، فاجعل نيتك لله في كل ما تفعله.", "theme": "العبادة والإيمان"},
 {"id": 2, "narrator": "عن أبي هريرة رضي الله عنه", "text": "من كان يؤمن بالله واليوم الآخر فليقل خيراً أو ليصمت", "source": "رواه البخاري ومسلم", "meaning": "قبل أن تتكلم اسأل نفسك: هل في كلامي خير؟ وإلا فالصمت أسلم.", "theme": "اللسان والصدق"},
 {"id": 3, "narrator": "عن أنس بن مالك رضي الله عنه", "text": "لا يؤمن أحدكم حتى يحب لأخيه ما يحب لنفسه", "source": "رواه البخاري ومسلم", "meaning": "كمال الإيمان أن تفرح لغيرك كما تفرح لنفسك.", "theme": "الأخوة وحقوق الناس"},
 {"id": 4, "narrator": "عن أبي هريرة رضي الله عنه", "text": "الكلمة الطيبة صدقة", "source": "رواه البخاري ومسلم", "meaning": "كلمة طيبة منك قد تغيّر يوم إنسان كامل، وتُكتب لك صدقة.", "theme": "اللسان والصدق"},
 {"id": 5, "narrator": "عن أبي هريرة رضي الله عنه", "text": "ليس الشديد بالصرعة، إنما الشديد الذي يملك نفسه عند الغضب", "source": "رواه البخاري ومسلم", "meaning": "القوة الحقيقية أن تتحكم بغضبك، لا أن تنفجر.", "theme": "الأخلاق"},
 {"id": 6, "narrator": "عن تميم الداري رضي الله عنه", "text": "الدين النصيحة", "source": "رواه مسلم", "meaning": "الدين صدق وإخلاص، فانصح الناس بلطف وأرِد لهم الخير.", "theme": "الأخوة وحقوق الناس"},
 {"id": 7, "narrator": "عن أبي هريرة رضي الله عنه", "text": "من لا يرحم لا يرحم", "source": "رواه البخاري ومسلم", "meaning": "الرحمة بالناس والحيوان سبب لرحمة الله بك.", "theme": "الأخلاق"},
 {"id": 8, "narrator": "عن عبد الله بن عمرو رضي الله عنهما", "text": "المسلم من سلم المسلمون من لسانه ويده", "source": "رواه البخاري ومسلم", "meaning": "المسلم الحق من يأمنه الناس على أنفسهم وأعراضهم.", "theme": "الأخوة وحقوق الناس"},
 {"id": 9, "narrator": "عن عائشة رضي الله عنها", "text": "أحب الأعمال إلى الله أدومها وإن قل", "source": "رواه البخاري ومسلم", "meaning": "الاستمرار على القليل أفضل من حماس مؤقت ينقطع.", "theme": "العبادة والإيمان"},
 {"id": 10, "narrator": "عن أبي هريرة رضي الله عنه", "text": "من سلك طريقاً يلتمس فيه علماً سهل الله له به طريقاً إلى الجنة", "source": "رواه مسلم", "meaning": "طلب العلم عبادة، والله ييسر لطالبه الطريق.", "theme": "العلم"},
 {"id": 11, "narrator": "عن أبي مالك الأشعري رضي الله عنه", "text": "الطهور شطر الإيمان", "source": "رواه مسلم", "meaning": "الطهارة والنظافة جزء عظيم من الإيمان.", "theme": "العبادة والإيمان"},
 {"id": 12, "narrator": "عن عثمان بن عفان رضي الله عنه", "text": "خيركم من تعلم القرآن وعلمه", "source": "رواه البخاري", "meaning": "أفضل الناس من يتعلم القرآن ويعلّمه لغيره.", "theme": "العلم"},
 {"id": 13, "narrator": "عن أبي مسعود الأنصاري رضي الله عنه", "text": "من دل على خير فله مثل أجر فاعله", "source": "رواه مسلم", "meaning": "دلالتك لغيرك على خير تكسبك أجراً مثل أجره.", "theme": "العطاء والصدقة"},
 {"id": 14, "narrator": "عن أبي هريرة رضي الله عنه", "text": "إن الله لا ينظر إلى صوركم وأموالكم، ولكن ينظر إلى قلوبكم وأعمالكم", "source": "رواه مسلم", "meaning": "الميزان عند الله صلاح القلب والعمل، لا المظهر ولا المال.", "theme": "العبادة والإيمان"},
 {"id": 15, "narrator": "عن جبير بن مطعم رضي الله عنه", "text": "لا يدخل الجنة قاطع رحم", "source": "رواه البخاري ومسلم", "meaning": "صلة الأقارب واجبة، وقطيعتهم ذنب عظيم.", "theme": "الأخوة وحقوق الناس"},
 {"id": 16, "narrator": "عن عبد الله بن عمر رضي الله عنهما", "text": "الحياء من الإيمان", "source": "رواه البخاري ومسلم", "theme": "الأخلاق", "meaning": "الحياء يمنعك من القبيح، وهو علامة على قوة الإيمان."},
 {"id": 17, "narrator": "عن عمران بن حصين رضي الله عنهما", "text": "الحياء لا يأتي إلا بخير", "source": "رواه البخاري ومسلم", "theme": "الأخلاق", "meaning": "الحياء الصحيح لا يجلب إلا الخير، ويحفظك من التصرفات المشينة."},
 {"id": 18, "narrator": "عن عائشة رضي الله عنها", "text": "إن الله يحب الرفق في الأمر كله", "source": "رواه البخاري ومسلم", "theme": "الأخلاق", "meaning": "اللين في التعامل أقرب لقلوب الناس وأحب إلى الله من الشدة."},
 {"id": 19, "narrator": "عن عائشة رضي الله عنها", "text": "ما كان الرفق في شيء إلا زانه، ولا نزع من شيء إلا شانه", "source": "رواه مسلم", "theme": "الأخلاق", "meaning": "الرفق يجمّل كل شيء، وغيابه يشوّهه."},
 {"id": 20, "narrator": "عن عبد الله بن عمر رضي الله عنهما", "text": "من كان في حاجة أخيه كان الله في حاجته", "source": "رواه البخاري ومسلم", "theme": "الأخوة وحقوق الناس", "meaning": "اقضِ حوائج الناس، والله يقضي حاجتك."},
 {"id": 21, "narrator": "عن عدي بن حاتم رضي الله عنه", "text": "اتقوا النار ولو بشق تمرة", "source": "رواه البخاري ومسلم", "theme": "العطاء والصدقة", "meaning": "لا تستقل أي صدقة مهما صغرت، فقد تكون سبب نجاتك."},
 {"id": 22, "narrator": "عن أبي هريرة رضي الله عنه", "text": "الدنيا سجن المؤمن وجنة الكافر", "source": "رواه مسلم", "theme": "الدنيا والقناعة", "meaning": "المؤمن يصبر على قيود الدنيا لأن نعيمه الحقيقي في الآخرة."},
 {"id": 23, "narrator": "عن عبد الله بن عمر رضي الله عنهما", "text": "كن في الدنيا كأنك غريب أو عابر سبيل", "source": "رواه البخاري", "theme": "الدنيا والقناعة", "meaning": "لا تتعلق بالدنيا، فأنت فيها مسافر والغاية الآخرة."},
 {"id": 24, "narrator": "عن عبد الله بن عباس رضي الله عنهما", "text": "نعمتان مغبون فيهما كثير من الناس: الصحة والفراغ", "source": "رواه البخاري", "theme": "الدنيا والقناعة", "meaning": "كثيرون يضيعون صحتهم ووقتهم ولا ينتبهون إلا بعد فواتهما."},
 {"id": 25, "narrator": "عن أبي هريرة رضي الله عنه", "text": "من غشنا فليس منا", "source": "رواه مسلم", "theme": "الأخلاق", "meaning": "الغش في أي معاملة لا يليق بالمسلم، والأمانة أساس الكسب الحلال."},
 {"id": 26, "narrator": "عن أبي هريرة رضي الله عنه", "text": "إياكم والظن، فإن الظن أكذب الحديث", "source": "رواه البخاري ومسلم", "theme": "اللسان والصدق", "meaning": "لا تبنِ أحكامك على الظنون، فالتثبت يحميك من ظلم الناس."},
 {"id": 27, "narrator": "عن أبي هريرة رضي الله عنه", "text": "من صام رمضان إيماناً واحتساباً غفر له ما تقدم من ذنبه", "source": "رواه البخاري ومسلم", "theme": "العبادة والإيمان", "meaning": "الصيام بإخلاص ونية صادقة سبب لمغفرة الذنوب."},
 {"id": 28, "narrator": "عن أبي مالك الأشعري رضي الله عنه", "text": "الصلاة نور، والصدقة برهان، والصبر ضياء", "source": "رواه مسلم", "theme": "العبادة والإيمان", "meaning": "الصلاة تنوّر القلب، والصدقة دليل صدق الإيمان، والصبر يضيء الطريق."},
 {"id": 29, "narrator": "عن شداد بن أوس رضي الله عنه", "text": "إن الله كتب الإحسان على كل شيء", "source": "رواه مسلم", "theme": "الأخلاق", "meaning": "الإحسان والإتقان مطلوبان في كل عمل وكل تعامل."},
 {"id": 30, "narrator": "عن أبي هريرة رضي الله عنه", "text": "ما نقصت صدقة من مال", "source": "رواه مسلم", "theme": "العطاء والصدقة", "meaning": "الصدقة لا تُنقص مالك، بل تبارك فيه."},
 {"id": 31, "narrator": "عن أنس بن مالك رضي الله عنه", "text": "يسروا ولا تعسروا، وبشروا ولا تنفروا", "source": "رواه البخاري ومسلم", "theme": "الأخلاق", "meaning": "كن ميسّراً ومبشّراً مع الناس، ولا تكن سبباً في تنفيرهم."},
 {"id": 32, "narrator": "عن أبي ذر رضي الله عنه", "text": "لا تحقرن من المعروف شيئاً ولو أن تلقى أخاك بوجه طلق", "source": "رواه مسلم", "theme": "العطاء والصدقة", "meaning": "حتى ابتسامتك في وجه أخيك معروف لا يُستهان به."},
 {"id": 33, "narrator": "عن عبد الله بن عمرو رضي الله عنهما", "text": "بلغوا عني ولو آية", "source": "رواه البخاري", "theme": "العلم", "meaning": "ما تعلمته من الخير بلّغه لغيرك ولو كان قليلاً."},
 {"id": 34, "narrator": "عن عبد الله بن عمر رضي الله عنهما", "text": "الظلم ظلمات يوم القيامة", "source": "رواه البخاري ومسلم", "theme": "الأخوة وحقوق الناس", "meaning": "الظلم يتحول يوم القيامة إلى ظلمات على صاحبه، فاحذر ظلم أحد."},
 {"id": 35, "narrator": "عن عبد الله بن عباس رضي الله عنهما", "text": "واتق دعوة المظلوم، فإنه ليس بينها وبين الله حجاب", "source": "رواه البخاري ومسلم", "theme": "الأخوة وحقوق الناس", "meaning": "دعوة المظلوم لا يحجبها شيء، فاحذر أن تظلم أحداً."},
 {"id": 36, "narrator": "عن عبد الله بن مسعود رضي الله عنه", "text": "إن الصدق يهدي إلى البر، وإن البر يهدي إلى الجنة", "source": "رواه البخاري ومسلم", "theme": "اللسان والصدق", "meaning": "الصدق طريق للخير كله، وينتهي بصاحبه إلى الجنة."},
 {"id": 37, "narrator": "عن أبي هريرة رضي الله عنه", "text": "آية المنافق ثلاث: إذا حدث كذب، وإذا وعد أخلف، وإذا اؤتمن خان", "source": "رواه البخاري ومسلم", "theme": "اللسان والصدق", "meaning": "الصدق بالحديث والوفاء بالوعد وحفظ الأمانة من أخلاق المؤمن."},
 {"id": 38, "narrator": "عن معاوية رضي الله عنه", "text": "من يرد الله به خيراً يفقهه في الدين", "source": "رواه البخاري ومسلم", "theme": "العلم", "meaning": "الفقه في الدين علامة خير أراده الله لك."},
 {"id": 39, "narrator": "عن أنس بن مالك رضي الله عنه", "text": "من أحب أن يبسط له في رزقه، وينسأ له في أثره، فليصل رحمه", "source": "رواه البخاري ومسلم", "theme": "الأخوة وحقوق الناس", "meaning": "صلة الرحم سبب لسعة الرزق وبركة العمر."},
 {"id": 40, "narrator": "عن عبد الله بن عمر رضي الله عنهما", "text": "اليد العليا خير من اليد السفلى", "source": "رواه البخاري ومسلم", "theme": "العطاء والصدقة", "meaning": "المعطي خير من الآخذ، فاجعل نفسك من أهل العطاء."},
 {"id": 41, "narrator": "عن أبي هريرة رضي الله عنه", "text": "ليس الغنى عن كثرة العرض، ولكن الغنى غنى النفس", "source": "رواه البخاري ومسلم", "theme": "الدنيا والقناعة", "meaning": "الغنى الحقيقي هو قناعة القلب لا كثرة المال."},
 {"id": 42, "narrator": "عن أبي هريرة رضي الله عنه", "text": "انظروا إلى من هو أسفل منكم ولا تنظروا إلى من هو فوقكم، فهو أجدر ألا تزدروا نعمة الله عليكم", "source": "رواه مسلم", "theme": "الدنيا والقناعة", "meaning": "قارن نفسك بمن هو أقل منك حالاً لتشكر نعم الله عليك."},
 {"id": 43, "narrator": "عن أبي هريرة رضي الله عنه", "text": "لا تغضب", "source": "رواه البخاري", "theme": "الأخلاق", "meaning": "التحكم بالغضب يجنّبك الندم ويحفظ لك علاقاتك."},
 {"id": 44, "narrator": "عن أبي موسى الأشعري رضي الله عنه", "text": "المؤمن للمؤمن كالبنيان يشد بعضه بعضاً", "source": "رواه البخاري ومسلم", "theme": "الأخوة وحقوق الناس", "meaning": "المؤمنون يتعاونون ويسند بعضهم بعضاً كالبناء المتماسك."}
]


# ======================================================================
# بيانات: بنك المعلومات والحكم والنصائح المالية (مكتوب مسبقاً)
# ======================================================================
KNOWLEDGE = [
 {"id": "f1", "category": "معلومات عامة وعلمية مذهلة", "text": "هل تعلم أن للأخطبوط ثلاثة قلوب؟", "points": ["للأخطبوط ثلاثة قلوب في جسم واحد", "قلبان منها يضخان الدم إلى الخياشيم", "والثالث يضخه إلى بقية الجسم"]},
 {"id": "f2", "category": "معلومات عامة وعلمية مذهلة", "text": "يوم كوكب الزهرة أطول من سنته!", "points": ["يدور الزهرة حول نفسه دورة كل مئتين وثلاثة وأربعين يوماً أرضياً", "ويكمل دورته حول الشمس في نحو مئتين وخمسة وعشرين يوماً", "فاليوم عنده أطول من السنة"]},
 {"id": "f3", "category": "معلومات عامة وعلمية مذهلة", "text": "نحن نرى الشمس كما كانت قبل ثماني دقائق!", "points": ["ضوء الشمس يحتاج نحو ثماني دقائق ليصل إلينا", "وتحديداً نحو ثماني دقائق وعشرين ثانية", "فما نراه هو ماضي الشمس لا لحظتها الحالية"]},
 {"id": "f4", "category": "معلومات عامة وعلمية مذهلة", "text": "دماغك يستهلك خمس طاقة جسمك!", "points": ["وزن الدماغ نحو اثنين بالمئة من وزن الجسم", "لكنه يستهلك نحو عشرين بالمئة من طاقته", "وهو يعمل حتى أثناء النوم"]},
 {"id": "f5", "category": "معلومات عامة وعلمية مذهلة", "text": "جسم الإنسان البالغ فيه مئتان وست عظمات", "points": ["يولد الطفل بعدد أكبر من العظام الصغيرة", "وتلتحم بعضها ببعض مع النمو", "فيستقر العدد عند مئتين وست عظمات"]},
 {"id": "f6", "category": "معلومات عامة وعلمية مذهلة", "text": "الجلد هو أكبر عضو في جسمك!", "points": ["تقارب مساحة جلد البالغ مترين مربعين", "يحميك من الجراثيم ومن فقدان الماء", "ويحس بالحرارة والبرودة واللمس"]},
 {"id": "f7", "category": "معلومات عامة وعلمية مذهلة", "text": "المحيطات تغطي معظم سطح الأرض", "points": ["تغطي المحيطات نحو واحد وسبعين بالمئة من الأرض", "ولهذا تسمى الأرض الكوكب الأزرق", "ومعظم أعماقها لم يُستكشف بعد"]},
 {"id": "f8", "category": "معلومات عامة وعلمية مذهلة", "text": "أعمق نقطة في المحيط تقارب أحد عشر كيلومتراً", "points": ["هذه النقطة تقع في خندق ماريانا", "لو وُضع جبل إيفرست فيه لبقيت قمته تحت الماء", "فالبحر أعمق مما نتخيل"]},
 {"id": "f9", "category": "معلومات عامة وعلمية مذهلة", "text": "العسل النقي لا يفسد بسهولة!", "points": ["قلة الماء في العسل تمنع نمو أغلب الجراثيم", "وارتفاع حموضته يساعد على حفظه", "لذلك يبقى صالحاً سنوات طويلة إذا حُفظ جيداً"]},
 {"id": "f10", "category": "معلومات عامة وعلمية مذهلة", "text": "قلبك ينبض نحو مئة ألف مرة يومياً!", "points": ["يضخ القلب آلاف اللترات من الدم كل يوم", "ويعمل دون توقف طوال العمر", "فاعتنِ به بالحركة والغذاء الصحي"]},
 {"id": "f11", "category": "معلومات عامة وعلمية مذهلة", "text": "حرارة البرق أعلى من حرارة سطح الشمس!", "points": ["قد تبلغ حرارة البرق نحو ثلاثين ألف درجة مئوية", "بينما حرارة سطح الشمس نحو خمسة آلاف ونصف", "لكن البرق يستمر لجزء صغير جداً من الثانية"]},
 {"id": "f12", "category": "معلومات عامة وعلمية مذهلة", "text": "الفراشات تتذوق الطعام بأرجلها!", "points": ["في أرجل الفراشة مستقبلات تحس بالطعم", "فهي تتذوق الورقة عندما تقف عليها", "وهكذا تختار المكان المناسب لوضع بيضها"]},
 {"id": "f13", "category": "معلومات عامة وعلمية مذهلة", "text": "الزرافة لها سبع فقرات في عنقها فقط", "points": ["وهذا العدد هو نفسه عند الإنسان", "لكن كل فقرة عند الزرافة طويلة جداً", "فصار عنقها طويلاً رغم قلة الفقرات"]},
 {"id": "f14", "category": "معلومات عامة وعلمية مذهلة", "text": "أكثر من نصف جسمك ماء!", "points": ["الماء يشكل أكثر من نصف وزن جسم البالغ", "ويحتاجه الجسم في كل وظائفه", "وقلة شرب الماء قد تضعف التركيز"]},
 {"id": "f15", "category": "معلومات عامة وعلمية مذهلة", "text": "الصوت لا ينتقل في الفضاء!", "points": ["الصوت يحتاج وسطاً كالهواء ينتقل خلاله", "والفضاء شبه خالٍ من المادة", "لذلك يسود فيه صمت تام"]},
 {"id": "f16", "category": "معلومات عامة وعلمية مذهلة", "text": "سنام الجمل يخزن الدهون لا الماء", "points": ["يستخدم الجمل هذه الدهون كطاقة عند قلة الطعام", "وجسمه قادر على تحمل العطش مدة طويلة", "ولهذا يسافر في الصحراء أياماً"]},
 {"id": "w1", "category": "مقولات وحكم مفيدة", "text": "من جدّ وجد، ومن زرع حصد", "points": ["هذا مثل عربي مشهور", "النجاح ثمرة الاجتهاد المتواصل", "ابدأ اليوم ولو بخطوة صغيرة"]},
 {"id": "w2", "category": "مقولات وحكم مفيدة", "text": "الوقت كالسيف إن لم تقطعه قطعك", "points": ["هذا قول مشهور عن قيمة الوقت", "كل ساعة تمضي لا تعود أبداً", "نظّم يومك قبل أن يضيع منك"]},
 {"id": "w3", "category": "مقولات وحكم مفيدة", "text": "ما ضاع حق وراءه مطالب", "points": ["هذا مثل عربي مشهور", "الإصرار الهادئ يوصلك إلى حقك", "لا تتنازل عن حقك بسهولة"]},
 {"id": "w4", "category": "مقولات وحكم مفيدة", "text": "رب ضارة نافعة", "points": ["هذا مثل عربي مشهور", "كثير من المواقف المزعجة تحمل خيراً خفياً", "انظر إلى المشكلة كفرصة للتعلم"]},
 {"id": "w5", "category": "مقولات وحكم مفيدة", "text": "الصبر مفتاح الفرج", "points": ["هذا قول مشهور بين الناس", "الصبر الجميل يخفف ثقل الأزمات", "وبعد الضيق يأتي الفرج بإذن الله"]},
 {"id": "w6", "category": "مقولات وحكم مفيدة", "text": "القناعة كنز لا يفنى", "points": ["هذا مثل مشهور عن الرضا", "القانع سعيد بما عنده", "ولا يقارن نفسه بغيره"]},
 {"id": "w7", "category": "مقولات وحكم مفيدة", "text": "الصديق وقت الضيق", "points": ["هذا مثل عربي مشهور", "الصديق الحقيقي يظهر وقت الشدائد", "اختر أصدقاءك بعناية"]},
 {"id": "w8", "category": "مقولات وحكم مفيدة", "text": "الجار قبل الدار", "points": ["هذا مثل عربي قديم", "حسن الجوار أهم من جمال البيت", "فاختر جارك قبل أن تختار دارك"]},
 {"id": "w9", "category": "مقولات وحكم مفيدة", "text": "درهم وقاية خير من قنطار علاج", "points": ["هذا مثل مشهور في أمر الصحة", "الوقاية أسهل وأرخص من العلاج", "اعتنِ بصحتك قبل أن تمرض"]},
 {"id": "w10", "category": "مقولات وحكم مفيدة", "text": "من تأنّى نال ما تمنى", "points": ["هذا مثل عربي مشهور", "التأني يجنبك كثيراً من الأخطاء", "والعجلة كثيراً ما تضيع الفرص"]},
 {"id": "w11", "category": "مقولات وحكم مفيدة", "text": "العلم في الصغر كالنقش على الحجر", "points": ["هذا مثل مشهور", "ما نتعلمه صغاراً يثبت في الذاكرة", "استثمر في التعلم مبكراً"]},
 {"id": "w12", "category": "مقولات وحكم مفيدة", "text": "لا تؤجل عمل اليوم إلى الغد", "points": ["هذا مثل مشهور", "التسويف يراكم الأعمال ويرهقك", "ابدأ الآن ولو بخمس دقائق"]},
 {"id": "m1", "category": "نصائح مالية وإدارة الأموال", "text": "قاعدة بسيطة لتقسيم راتبك: خمسين ثلاثين عشرين", "points": ["خصص خمسين بالمئة من دخلك للضروريات", "وثلاثين بالمئة للرغبات وعشرين للادخار", "قاعدة مشهورة تعطيك ميزانية متوازنة"]},
 {"id": "m2", "category": "نصائح مالية وإدارة الأموال", "text": "ابنِ صندوق طوارئ قبل أي استثمار", "points": ["ادخر ما يغطي مصاريف ثلاثة إلى ستة أشهر", "يحميك عند فقدان العمل أو المرض", "ولا تلمسه إلا للضرورة"]},
 {"id": "m3", "category": "نصائح مالية وإدارة الأموال", "text": "ادفع لنفسك أولاً!", "points": ["اقتطع مبلغ الادخار فور استلام الراتب", "لا تنتظر ما يتبقى في آخر الشهر", "واجعله تحويلاً تلقائياً"]},
 {"id": "m4", "category": "نصائح مالية وإدارة الأموال", "text": "قبل أي شراء غير ضروري انتظر يوماً كاملاً", "points": ["كثير من الرغبات تزول بعد التفكير", "وهكذا تتجنب الشراء العاطفي", "وتوفر مبلغاً كبيراً مع الوقت"]},
 {"id": "m5", "category": "نصائح مالية وإدارة الأموال", "text": "المبلغ الصغير المنتظم يصنع ثروة", "points": ["ادخار مبلغ ثابت كل شهر مهما صغر", "يتحول مع السنين إلى مبلغ كبير", "فالاستمرار أهم من حجم المبلغ"]},
 {"id": "m6", "category": "نصائح مالية وإدارة الأموال", "text": "سجّل مصاريفك شهراً كاملاً", "points": ["ستكتشف مصاريف صغيرة تستنزف راتبك", "وتعرف أين يذهب مالك فعلاً", "فما لا تقيسه لا تستطيع تحسينه"]},
 {"id": "m7", "category": "نصائح مالية وإدارة الأموال", "text": "لا تستدن لشراء ما لا تحتاجه", "points": ["الدين الاستهلاكي يثقل ميزانيتك لأشهر طويلة", "ويحرمك من الادخار", "اشترِ بقدر ما تملك"]},
 {"id": "m8", "category": "نصائح مالية وإدارة الأموال", "text": "لا تعتمد على مصدر دخل واحد", "points": ["مهارة تتقنها قد تتحول إلى دخل إضافي", "ابدأ صغيراً بجانب عملك الأساسي", "وسيمنحك ذلك أماناً أكبر"]},
 {"id": "m9", "category": "نصائح مالية وإدارة الأموال", "text": "قارن الأسعار قبل الشراء الكبير", "points": ["الفرق بين متجر وآخر قد يكون كبيراً", "الصبر في البحث يوفر مالاً حقيقياً", "اسأل وقارن قبل أن تدفع"]},
 {"id": "m10", "category": "نصائح مالية وإدارة الأموال", "text": "أفضل استثمار هو تطوير مهاراتك", "points": ["المهارة الجيدة ترفع دخلك سنوات طويلة", "خصص جزءاً من وقتك ومالك للتعلم", "فهو استثمار لا يضيع"]},
 {"id": "m11", "category": "نصائح مالية وإدارة الأموال", "text": "ضع ميزانية الشهر قبل أن تستلم الراتب", "points": ["حدد وجهة كل مبلغ مسبقاً", "فلا يضيع مالك دون أن تشعر", "وراجع ميزانيتك آخر كل شهر"]}
]


# ======================================================================
# Gemini: نماذج مثبتة + بديل + إعادة محاولة
# ======================================================================
# تقدر تغيّرها من Secrets/Variables بدون تعديل الكود: GEMINI_MODELS="model-a,model-b"
GEMINI_PREFERRED = [
    m.strip()
    for m in os.getenv("GEMINI_MODELS", "gemini-3.8-flash,gemini-3.6-flash").split(",")
    if m.strip()
]
GEMINI_TIMEOUT_SEC = 40
_gem_bad = set()
_gem_discovered = None
_gem_client = None
_gem_backend = None  # "new" (google-genai) أو "legacy" (google-generativeai)


def _gem_init():
    global _gem_client, _gem_backend
    if _gem_backend:
        return
    key = os.getenv("GEMINI_API_KEY")
    try:
        from google import genai
        from google.genai import types
        _gem_client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_SEC * 1000))
        _gem_backend = "new"
    except ImportError:
        import google.generativeai as legacy
        legacy.configure(api_key=key, transport="rest")
        _gem_client, _gem_backend = legacy, "legacy"
        print("تنبيه: تستخدم مكتبة Gemini القديمة، يفضّل: pip install google-genai")


def _gem_call(name, prompt):
    if _gem_backend == "new":
        return _gem_client.models.generate_content(model=name, contents=prompt).text
    return _gem_client.GenerativeModel(name).generate_content(
        prompt, request_options={"timeout": GEMINI_TIMEOUT_SEC}
    ).text


def _gem_discover():
    """آخر حل: يسأل Google عن النماذج المتاحة (مرة واحدة فقط) ويأخذ أحدث flash."""
    global _gem_discovered
    if _gem_discovered is not None:
        return _gem_discovered
    names = []
    try:
        skip = ("image", "tts", "live", "audio", "omni", "embedding", "lite", "preview", "native")
        models = _gem_client.models.list() if _gem_backend == "new" else _gem_client.list_models()
        for m in models:
            n = m.name.replace("models/", "")
            actions = getattr(m, "supported_actions", None) or getattr(m, "supported_generation_methods", None) or []
            if "generateContent" in actions and "flash" in n and not any(x in n for x in skip):
                names.append(n)
    except Exception as e:
        print("تعذر جلب قائمة النماذج:", e)
    _gem_discovered = sorted(set(names), reverse=True)
    return _gem_discovered


def gemini_generate(prompt, retries=2):
    """يرجع نص الرد أو None إذا فشلت كل المحاولات."""
    try:
        _gem_init()
    except Exception as e:
        print("تعذر تهيئة Gemini:", e)
        return None
    queue = [m for m in GEMINI_PREFERRED if m not in _gem_bad]
    discovery_done = False
    while True:
        if not queue:
            if discovery_done:
                return None
            discovery_done = True
            queue = [m for m in _gem_discover() if m not in _gem_bad and m not in GEMINI_PREFERRED][:3]
            continue
        name = queue.pop(0)
        for attempt in range(1, retries + 1):
            try:
                text = (_gem_call(name, prompt) or "").strip()
                if text:
                    print(f"النموذج المستخدم: {name}")
                    return text
            except Exception as e:
                msg = str(e).lower()
                print(f"Gemini [{name}] محاولة {attempt}: {str(e)[:120]}")
                if "404" in msg or "not found" in msg or "not_found" in msg:
                    _gem_bad.add(name)  # الاسم غير موجود، انتقل للتالي فوراً
                    break
                time.sleep(3 * attempt)


def parse_json_text(text):
    text = text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    return json.loads(text)


# ======================================================================
# وحدة الأحاديث والسلاسل
# ======================================================================
HADITH_USED_FILE = "hadith_used.txt"
SERIES_FILE = "series_state.json"
SERIES_SIZE = 5  # عدد حلقات السلسلة الواحدة (أو أقل إذا نفدت أحاديث الموضوع)
HADITH_HASHTAGS = "#أحاديث #السنة_النبوية #تذكير #إسلاميات"
HADITH_FALLBACK_HOOKS = [
    "حديث واحد قد يغيّر حياتك!",
    "هل تعرف هذا الحديث العظيم؟",
    "لا تمرّ على هذا الحديث بسرعة!",
    "كلمات قليلة ومعناها عميق جداً!",
]


def _hadith_load_bank():
    return HADITHS


def _hadith_load_used():
    if not os.path.exists(HADITH_USED_FILE):
        return set()
    with open(HADITH_USED_FILE, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def _hadith_mark_used(hid):
    with open(HADITH_USED_FILE, "a", encoding="utf-8") as f:
        f.write(f"{hid}\n")


def _load_series():
    try:
        with open(SERIES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_series(state):
    with open(SERIES_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _start_series(bank, used):
    """يبدأ سلسلة جديدة من أحاديث موضوع واحد لم تُستخدم بعد."""
    unused = [h for h in bank if str(h["id"]) not in used]
    if not unused:  # خلص البنك كله، نبدأ دورة جديدة
        open(HADITH_USED_FILE, "w").close()
        unused = bank
    by_theme = defaultdict(list)
    for h in unused:
        by_theme[h.get("theme", "منوعات")].append(h)
    candidates = [t for t, items in by_theme.items() if len(items) >= 3] or list(by_theme)
    theme = random.choice(candidates)
    items = random.sample(by_theme[theme], min(SERIES_SIZE, len(by_theme[theme])))
    return {"theme": theme, "total": len(items), "part": 0, "queue": [h["id"] for h in items]}


def pick_hadith():
    """يرجع (الحديث، معلومات السلسلة أو None) ويكمل السلسلة الحالية إن وُجدت."""
    bank = _hadith_load_bank()
    by_id = {h["id"]: h for h in bank}
    used = _hadith_load_used()
    state = _load_series()

    queue = [i for i in state.get("queue", []) if str(i) not in used and i in by_id]
    if not queue:
        state = _start_series(bank, used)
        queue = list(state["queue"])

    hid = queue.pop(0)
    state["queue"] = queue
    state["part"] = state.get("part", 0) + 1
    hadith = by_id[hid]
    series = None
    if state["total"] >= 2:
        series = {"theme": state["theme"], "part": state["part"], "total": state["total"]}
    _save_series(state if queue else {})  # آخر حلقة => نمسح الحالة لتبدأ سلسلة جديدة
    return hadith, series


def _hadith_ask_explanation(h, explain_count, hook_style):
    """Gemini يشرح فقط، ولا يُسمح له بنقل أو تغيير نص الحديث."""
    prompt = f"""
    الحديث النبوي الصحيح التالي ثابت ولا يجوز لك تغييره أو إعادة صياغته أو ذكر أي حديث آخر:
    "{h['text']}" ({h['narrator']}، {h['source']}).
    مهمتك فقط شرح معناه وتطبيقه العملي بالعربية الفصحى البسيطة.
    اكتب {explain_count} جمل شرح (كل جملة من 6 إلى 9 كلمات) ثم سؤال تفاعلي قصير يدعو للتعليق.
    ممنوع نسبة أي قول للنبي صلى الله عليه وسلم غير نص الحديث أعلاه.
    أضف حقل hook: جملة افتتاحية صادمة وقصيرة (3 إلى 7 كلمات) من نوع: {hook_style}، تثير الفضول، دون ذكر نص الحديث ودون نسبة أي قول للنبي صلى الله عليه وسلم.
    أرجع JSON فقط:
    {{"title": "عنوان غلاف من 3 إلى 5 كلمات",
      "hook": "جملة افتتاحية صادمة",
      "explain": ["جملة1", "جملة2"],
      "question": "سؤال قصير؟ شاركنا في التعليقات!"}}
    """
    try:
        text = gemini_generate(prompt)
        if text:
            data = parse_json_text(text)
            if data.get("explain") and data.get("question"):
                return data
    except Exception as e:
        print("تعذر شرح Gemini، سيُستخدم الشرح المخزّن:", e)
    return None


def build_hadith_script(scenes_count, outro_scene, hook_style="سؤال صادم يثير الفضول"):
    h, series = pick_hadith()
    explain_count = max(1, int(scenes_count) - 3)
    ai = _hadith_ask_explanation(h, explain_count, hook_style)

    if ai:
        title = ai.get("title") or "حديث نبوي عظيم!"
        explain = [s for s in ai["explain"] if s.strip()][:explain_count]
        question = ai["question"].strip()
    else:  # احتياطي: الشرح الجاهز من الملف
        title = "حديث نبوي عظيم!"
        explain = [h["meaning"]]
        question = "هل تطبق هذا الحديث في حياتك؟ شاركنا في التعليقات!"

    scenes = [
        {"text": f"{h['narrator']}، أن النبي صلى الله عليه وسلم قال", "pose": "WELCOME", "visual": "BOOK_LIGHT"},
        {"text": h["text"], "pose": "POINTING", "visual": "BOOK_LIGHT"},
        {"text": h["source"], "pose": "EXPLAINING", "visual": "BOOK_LIGHT"},
    ]
    poses = ["EXPLAINING", "SUCCESS", "THINKING", "POINTING"]
    visuals = ["HEART_GLOW", "COMPASS_LIFE", "IDEA_BULB", "HEART_GLOW"]
    for i, s in enumerate(explain):
        scenes.append({"text": s, "pose": poses[i % 4], "visual": visuals[i % 4]})
    scenes.append({"text": question, "pose": "QUESTION", "visual": "COMMENT_BUBBLE"})

    caption = f"«{h['text']}»\n{h['narrator']} - {h['source']}\n"
    series_label = ""
    badge = "حديث شريف"
    outro = dict(outro_scene)
    if series:
        title = f"سلسلة أحاديث {series['theme']}"
        badge = f"الحلقة {series['part']} من {series['total']}"
        caption += f"\n📚 سلسلة أحاديث {series['theme']} (الحلقة {series['part']} من {series['total']})\n"
        series_label = f"{series['theme']}:{series['part']}/{series['total']}"
        if series["part"] < series["total"]:
            outro["text"] = "تابعوا الصفحة لتصلكم الحلقة القادمة"
    scenes.append(outro)
    caption += f"\n{HADITH_HASHTAGS}" + (" #سلسلة_أحاديث" if series else "")

    _hadith_mark_used(h["id"])
    hook = (ai.get("hook") if ai else None) or random.choice(HADITH_FALLBACK_HOOKS)
    first_comment = f"«{h['text']}»\n{h['narrator']} - {h['source']}\n\n💬 {question}"
    return {
        "title": title, "hook": hook, "caption": caption, "scenes": scenes,
        "badge": badge, "first_comment": first_comment,
        "topic_key": f"hadith:{h['id']}", "series_label": series_label,
    }


# ======================================================================
# وحدة بنك المعلومات والحكم والنصائح
# ======================================================================
KNOWLEDGE_USED_FILE = "knowledge_used.txt"

KB_CATEGORY_CONFIG = {
    "معلومات عامة وعلمية مذهلة": {
        "hashtags": "#معلومات #هل_تعلم #ثقافة #حقائق",
        "visuals": ["GLOBE", "AI_BRAIN", "MAGNIFIER", "ROCKET", "IDEA_BULB"],
        "badge": "هل تعلم؟",
        "title": "هل تعلم؟",
        "hooks": ["معلومة ستدهشك فعلاً!", "هل كنت تعرف هذا من قبل؟", "حقيقة غريبة لكنها صحيحة!"],
    },
    "مقولات وحكم مفيدة": {
        "hashtags": "#حكم #أمثال #تطوير_الذات #اقتباسات",
        "visuals": ["BOOK_LIGHT", "COMPASS_LIFE", "IDEA_BULB", "CHART_UP"],
        "badge": "حكمة",
        "title": "حكمة تستحق التأمل",
        "hooks": ["جملة واحدة قد تغيّر نظرتك!", "حكمة قديمة ما زالت صحيحة!", "هل تعمل بهذه الحكمة؟"],
    },
    "نصائح مالية وإدارة الأموال": {
        "hashtags": "#نصائح_مالية #إدارة_المال #ادخار #نجاح",
        "visuals": ["MONEY_GROWTH", "CHART_UP", "SECURITY", "CLOCK"],
        "badge": "نصيحة مالية",
        "title": "نصيحة مالية مهمة",
        "hooks": ["خطأ مالي يكرره الكثيرون!", "نصيحة تحفظ مالك فعلاً!", "هل تفعل هذا بمالك؟"],
    },
}
KB_POSES = ["EXPLAINING", "SUCCESS", "THINKING", "POINTING", "EXPLAINING", "SUCCESS"]


def _kb_load_bank():
    return KNOWLEDGE


def _kb_load_used():
    if not os.path.exists(KNOWLEDGE_USED_FILE):
        return set()
    with open(KNOWLEDGE_USED_FILE, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def has_bank(category):
    if category not in KB_CATEGORY_CONFIG:
        return False
    try:
        return any(e["category"] == category for e in _kb_load_bank())
    except Exception:
        return False


def _kb_pick_entry(category):
    bank = [e for e in _kb_load_bank() if e["category"] == category]
    used = _kb_load_used()
    fresh = [e for e in bank if e["id"] not in used]
    if not fresh:  # خلص قسم كامل: نصفّر استخدامه فقط
        ids = {e["id"] for e in bank}
        with open(KNOWLEDGE_USED_FILE, "w", encoding="utf-8") as f:
            f.write("".join(f"{i}\n" for i in used - ids))
        fresh = bank
    entry = random.choice(fresh)
    with open(KNOWLEDGE_USED_FILE, "a", encoding="utf-8") as f:
        f.write(entry["id"] + "\n")
    return entry


def _kb_ask(entry, explain_count, hook_style):
    points = "\n".join(f"- {p}" for p in entry["points"])
    prompt = f"""
    الموضوع الثابت: "{entry['text']}"
    النقاط الصحيحة الوحيدة المسموح بالاعتماد عليها:
    {points}
    اكتب {explain_count} جمل شرح بالعربية الفصحى البسيطة (كل جملة من 6 إلى 9 كلمات) تعتمد فقط على هذه النقاط.
    ممنوع إضافة أي رقم أو معلومة أو اسم أو نسبة قول لأي شخص غير موجود في النقاط أعلاه.
    ثم اكتب سؤالاً تفاعلياً قصيراً يدعو للتعليق، وجملة hook افتتاحية صادمة (3 إلى 7 كلمات) من نوع: {hook_style}، دون كشف الإجابة.
    أرجع JSON فقط:
    {{"title": "عنوان غلاف من 3 إلى 5 كلمات",
      "hook": "جملة افتتاحية",
      "explain": ["جملة1", "جملة2"],
      "question": "سؤال قصير؟ شاركنا في التعليقات!"}}
    """
    try:
        text = gemini_generate(prompt)
        if text:
            data = parse_json_text(text)
            if data.get("explain") and data.get("question"):
                return data
    except Exception as e:
        print("تعذر شرح Gemini، سيُستخدم الشرح المخزّن:", e)
    return None


def build_bank_script(category, scenes_count, outro_scene, hook_style="سؤال صادم يثير الفضول"):
    cfg = KB_CATEGORY_CONFIG[category]
    entry = _kb_pick_entry(category)
    explain_count = max(1, int(scenes_count) - 2)
    ai = _kb_ask(entry, explain_count, hook_style)

    if ai:
        explain = [s.strip() for s in ai["explain"] if s.strip()][:explain_count]
        question = ai["question"].strip()
        title = ai.get("title") or cfg["title"]
        hook = ai.get("hook") or random.choice(cfg["hooks"])
    else:
        explain = entry["points"][:explain_count]
        question = "ما رأيك بهذه المعلومة؟ شاركنا في التعليقات!"
        title = cfg["title"]
        hook = random.choice(cfg["hooks"])

    visuals = cfg["visuals"]
    scenes = [{"text": entry["text"], "pose": "WELCOME", "visual": visuals[0]}]
    for i, s in enumerate(explain):
        scenes.append({"text": s, "pose": KB_POSES[i % len(KB_POSES)], "visual": visuals[(i + 1) % len(visuals)]})
    scenes.append({"text": question, "pose": "QUESTION", "visual": "COMMENT_BUBBLE"})
    scenes.append(dict(outro_scene))

    bullets = "\n".join(f"• {p}" for p in entry["points"])
    return {
        "title": title,
        "hook": hook,
        "badge": cfg["badge"],
        "caption": f"{entry['text']}\n\n{bullets}\n\n{cfg['hashtags']}",
        "first_comment": f"{entry['text']}\n\n{bullets}\n\n💬 {question}",
        "scenes": scenes,
        "topic_key": f"bank:{entry['id']}",
    }


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
    weights_data = load_weights()
    base_len_w = [3, 5, 2]
    len_w = [
        base_len_w[i] * weights_data.get("length", {}).get(lp["scenes_count"], 1.0)
        for i, lp in enumerate(length_profiles)
    ]
    chosen_len = random.choices(length_profiles, weights=len_w, k=1)[0]

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
                "قدّم نصيحة إيمانية وروحية لطيفة حول ذكر الله، الطمأنينة، بر الوالدين، الصدقة، أو إصلاح القلب بأسلوب دافئ ومحبب. ممنوع ذكر نص حديث أو آية أو نسبة أي قول لأي شخص؛ قدّم النصيحة بكلامك أنت دون أي اقتباس."
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
    cat_w = [
        CATEGORY_BOOST.get(c["category"], 1.0) * weights_data.get("category", {}).get(c["category"], 1.0)
        for c in content_categories
    ]
    chosen_cat = random.choices(content_categories, weights=cat_w, k=1)[0]
    CURRENT_META.update({"category": chosen_cat["category"], "scenes_count": chosen_len["scenes_count"], "series": ""})
    hook_style = random.choice(list(HOOK_STYLES))
    CURRENT_META["hook_type"] = hook_style

    # ---- جديد: قسم الأحاديث يمر من البنك الصحيح بدل توليد Gemini ----
    if chosen_cat["category"] == "شرح حديث نبوي شريف":
        try:
            h_style = random.choice(["question", "challenge"])  # أنسب لقسم الأحاديث
            CURRENT_META["hook_type"] = h_style
            data = build_hadith_script(chosen_len["scenes_count"], outro_scene, HOOK_STYLES[h_style])
            CURRENT_META["series"] = data.get("series_label", "")
            save_topic_history(data.get("title", ""))
            print(f"القسم المختار: {chosen_cat['category']} | من بنك الأحاديث الصحيحة")
            return data
        except Exception as e:
            print("تعذر استخدام بنك الأحاديث، سيتم اختيار قسم آخر:", e)
            chosen_cat = random.choice(content_categories[1:])
            CURRENT_META["category"] = chosen_cat["category"]
            CURRENT_META["hook_type"] = hook_style
    # ------------------------------------------------------------------

    # ---- بنك المعلومات/الحكم/النصائح المالية: محتوى مكتوب مسبقاً، Gemini يشرحه فقط ----
    if has_bank(chosen_cat["category"]):
        try:
            if chosen_cat["category"] != "معلومات عامة وعلمية مذهلة":
                hook_style = random.choice(["question", "challenge"])
                CURRENT_META["hook_type"] = hook_style
            data = build_bank_script(
                chosen_cat["category"], chosen_len["scenes_count"], outro_scene, HOOK_STYLES[hook_style]
            )
            save_topic_history(data.get("title", ""))
            print(f"القسم المختار: {chosen_cat['category']} | من بنك المحتوى المدقق")
            return data
        except Exception as e:
            print("تعذر استخدام بنك المحتوى، سيُولَّد المحتوى بـ Gemini:", e)
    # ------------------------------------------------------------------

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
    
    وأضف حقل "hook": جملة افتتاحية صادمة وقصيرة (3 إلى 7 كلمات) من نوع: {HOOK_STYLES[hook_style]}، تجعل المشاهد يكمل الفيديو، ولا تكشف فيها الإجابة، وليست من المشاهد.

    لكل مشهد، اختر:
    1) "pose" من القائمة: [WELCOME, POINTING, THINKING, EXPLAINING, WARNING, SUCCESS, QUESTION]
    2) "visual" من القائمة (يفضل التركيز على: {chosen_cat['preferred_visuals']}):
       [BOOK_LIGHT, HEART_GLOW, MONEY_GROWTH, COMPASS_LIFE, BATTERY, PHONE_APPS, AI_BRAIN, ROCKET, SECURITY, CHART_UP, WARNING_SIGN, CLOCK, GLOBE, WIFI, MAGNIFIER, IDEA_BULB, COMMENT_BUBBLE]
    
    أرجع النتيجة بصيغة JSON فقط بدون أي كلام إضافي:
    {{
      "title": "عنوان غلاف قصير وجذاب من 3 إلى 5 كلمات",
      "hook": "جملة افتتاحية صادمة من 3 إلى 7 كلمات",
      "caption": "وصف الريلز مع هاشتاغات مناسبة مثل {chosen_cat['hashtags']}",
      "scenes": [
        {{"text": "الجملة الأولى هنا", "pose": "WELCOME", "visual": "BOOK_LIGHT"}},
        {{"text": "ما رأيك بهذا المعنى؟ شاركنا في التعليقات!", "pose": "QUESTION", "visual": "COMMENT_BUBBLE"}}
      ]
    }}
    """
    try:
        text = gemini_generate(prompt)
        if text:
            data = parse_json_text(text)
            scenes_ok = [
                sc for sc in data.get("scenes", [])
                if isinstance(sc, dict) and str(sc.get("text", "")).strip()
            ]
            if scenes_ok:
                data["scenes"] = scenes_ok
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
            "hook": "راتبك يختفي كل شهر؟ السبب هنا!",
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
            "hook": "كنز من الجنة بكلمة واحدة!",
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
            "hook": "سر النجاح أبسط مما تتخيل!",
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
    CURRENT_META["category"] = "fallback"
    CURRENT_META["hook_type"] = "fallback"
    CURRENT_META["series"] = ""
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


# 3.5 خلفيات مرسومة بالكود (بدون مفاتيح API ولا صور خارجية ولا مشاكل حقوق)
BG_STYLE_BY_CATEGORY = {
    "شرح حديث نبوي شريف": "geometric",       # زخرفة هندسية إسلامية
    "نصائح دينية وإيمانية": "geometric",
    "معلومات عامة وعلمية مذهلة": "stars",    # نجوم متحركة
    "مقولات وحكم مفيدة": "bokeh",            # دوائر ضوئية ناعمة
    "نصائح دنيوية وتطوير الذات": "bokeh",
    "نصائح مالية وإدارة الأموال": "chart",   # شموع بيانية خافتة
}
BG_GRADIENTS = {
    "geometric": ((9, 13, 22), (12, 30, 52)),
    "stars": ((4, 7, 18), (10, 14, 42)),
    "bokeh": ((16, 10, 30), (8, 26, 42)),
    "chart": ((8, 14, 22), (9, 34, 40)),
}
BG_LAYERS = {
    "stars": [("stars_far", 0.35), ("stars_near", 0.9)],
    "bokeh": [("bokeh_far", 0.5), ("bokeh_near", 1.1)],
    "chart": [("candles", 0.7)],
}
_BG_CACHE = {}  # يُبنى مرة واحدة لكل عملية ريندر ثم يُعاد استخدامه بكل الفريمات


def _hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _dim(color_hex, k):
    r, g, b = _hex_rgb(color_hex)
    return (int(r * k), int(g * k), int(b * k))


def _bg_base(style, size):
    key = ("base", style)
    if key not in _BG_CACHE:
        top, bottom = (np.array(c, dtype=np.float32) for c in BG_GRADIENTS[style])
        grad = np.linspace(0.0, 1.0, size[1], dtype=np.float32)[:, None, None]
        arr = np.repeat((top + (bottom - top) * grad).astype(np.uint8), size[0], axis=1)
        _BG_CACHE[key] = Image.fromarray(arr, "RGB")
    return _BG_CACHE[key]


def _loop_layer(kind, accent, size):
    """طبقة على خلفية سوداء ارتفاعها ضعف الشاشة وتتكرر بسلاسة (للتمرير العمودي)"""
    if kind.startswith("bokeh") or kind == "candles":
        accent = "#00F0FF"  # لا تتأثر بلون الـ pose، فتُبنى مرة واحدة فقط
    key = ("layer", kind, accent)
    if key in _BG_CACHE:
        return _BG_CACHE[key]
    w, h = size
    rng = random.Random(sum(ord(c) for c in kind))
    tile = Image.new("RGB", (w, h), (0, 0, 0))
    d = ImageDraw.Draw(tile)

    def twice(fn):  # يرسم العنصر وصورته المزاحة حتى لا يظهر قطع عند التكرار
        for dy in (0, -h, h):
            fn(dy)

    if kind == "stars_far":
        for _ in range(230):
            x, y, r = rng.randint(0, w), rng.randint(0, h), rng.choice([1, 1, 2])
            v = rng.randint(90, 170)
            twice(lambda dy: d.ellipse([(x - r, y + dy - r), (x + r, y + dy + r)], fill=(v, v, min(255, v + 40))))
    elif kind == "stars_near":
        tint = _dim(accent, 0.55)
        for _ in range(55):
            x, y, r = rng.randint(0, w), rng.randint(0, h), rng.choice([2, 3, 4])
            col = (200 + tint[0] // 8, 200 + tint[1] // 8, 220)
            def star(dy, x=x, y=y, r=r, col=col):
                d.ellipse([(x - r, y + dy - r), (x + r, y + dy + r)], fill=col)
                if r >= 3:
                    d.line([(x - r * 3, y + dy), (x + r * 3, y + dy)], fill=tint, width=1)
                    d.line([(x, y + dy - r * 3), (x, y + dy + r * 3)], fill=tint, width=1)
            twice(star)
    elif kind in ("bokeh_far", "bokeh_near"):
        far = kind == "bokeh_far"
        k = 0.20 if far else 0.32
        canvas3 = Image.new("RGB", (w, h * 3), (0, 0, 0))  # نمو الطبقة 3 أضعاف حتى لا تظهر حافة عند التمويه
        d3 = ImageDraw.Draw(canvas3)
        for _ in range(10 if far else 8):
            x, y = rng.randint(0, w), rng.randint(0, h)
            r = rng.randint(90, 190) if far else rng.randint(40, 110)
            col = _dim(rng.choice([accent, "#A855F7", "#38BDF8", "#FACC15"]), k)
            for dy in (-h, 0, h):
                d3.ellipse([(x - r, y + h + dy - r), (x + r, y + h + dy + r)], fill=col)
        tile = canvas3.filter(ImageFilter.GaussianBlur(26 if far else 18)).crop((0, h, w, h * 2))
    elif kind == "candles":
        for gy in range(0, h, 160):
            d.line([(0, gy), (w, gy)], fill=_dim(accent, 0.12), width=1)
        green, red = _dim("#10B981", 0.40), _dim("#EF4444", 0.32)
        for x in range(30, w, 54):
            cy, body = rng.randint(100, h - 100), rng.randint(30, 150)
            wick = rng.randint(20, 60)
            col = green if rng.random() < 0.62 else red
            def candle(dy, x=x, cy=cy, body=body, wick=wick, col=col):
                d.line([(x, cy + dy - body // 2 - wick), (x, cy + dy + body // 2 + wick)], fill=col, width=3)
                d.rectangle([(x - 12, cy + dy - body // 2), (x + 12, cy + dy + body // 2)], fill=col)
            twice(candle)
    big = Image.new("RGB", (w, h * 2))
    big.paste(tile, (0, 0))
    big.paste(tile, (0, h))
    _BG_CACHE[key] = big
    return big


def _geo_canvas(accent, size):
    """زخرفة نجمة ثمانية هندسية متكررة"""
    key = ("geo", accent)
    if key in _BG_CACHE:
        return _BG_CACHE[key]
    t = 160
    tile = Image.new("RGB", (t, t), (0, 0, 0))
    d = ImageDraw.Draw(tile)
    c = _dim(accent, 0.30)
    cx = cy = t // 2
    r, rr = 54, 76
    sq = [(cx - r, cy - r), (cx + r, cy - r), (cx + r, cy + r), (cx - r, cy + r)]
    dia = [(cx, cy - rr), (cx + rr, cy), (cx, cy + rr), (cx - rr, cy)]
    d.line(sq + [sq[0]], fill=c, width=2)
    d.line(dia + [dia[0]], fill=c, width=2)
    d.ellipse([(cx - 22, cy - 22), (cx + 22, cy + 22)], outline=c, width=2)
    for px, py in ((0, 0), (t, 0), (0, t), (t, t)):
        d.ellipse([(px - 12, py - 12), (px + 12, py + 12)], outline=c, width=2)
    w, h = size
    canvas = Image.new("RGB", (w + t, h + t))
    for x in range(0, w + t, t):
        for y in range(0, h + t, t):
            canvas.paste(tile, (x, y))
    _BG_CACHE[key] = canvas
    return canvas


def make_background(style, global_frame, accent, size=(1080, 1920)):
    """يرجع صورة الخلفية المتحركة للنمط، أو None ليُرسم الشبكة الافتراضية"""
    if style not in BG_GRADIENTS:
        return None
    w, h = size
    img = _bg_base(style, size).copy()
    if style == "geometric":
        t = 160
        ox, oy = int(global_frame * 0.5) % t, int(global_frame * 0.8) % t
        return ImageChops.add(img, _geo_canvas(accent, size).crop((ox, oy, ox + w, oy + h)))
    for kind, speed in BG_LAYERS[style]:
        off = int(global_frame * speed) % h
        img = ImageChops.add(img, _loop_layer(kind, accent, size).crop((0, off, w, off + h)))
    return img


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
    """تقريب كاميرا ناعم بعملية واحدة (قص + تكبير) بدل تكبير الصورة كلها ثم قصها"""
    if zoom <= 1.001:
        return img
    w, h = img.size
    cw, ch = w / zoom, h / zoom
    left, top = (w - cw) / 2.0, (h - ch) / 2.0
    return img.resize((w, h), Image.Resampling.BILINEAR, box=(left, top, left + cw, top + ch))


# 6. رسم إطار الغلاف الاحترافي (خلفية متدرجة + عنوان كبير + شارة الحلقة/النوع)
def draw_cover_frame(title, page_name, frame_num, font_path, font_badge, size=(1080, 1920), subtitle=None):
    accent = "#00F0FF"
    grad = np.linspace(0.0, 1.0, size[1], dtype=np.float32)[:, None, None]
    top = np.array([9, 13, 22], dtype=np.float32)
    bottom = np.array([13, 40, 68], dtype=np.float32)
    arr = np.repeat((top + (bottom - top) * grad).astype(np.uint8), size[0], axis=1)
    img = Image.fromarray(arr, "RGB")
    draw = ImageDraw.Draw(img)

    for i in range(0, size[0], 120):
        draw.line([(i, 0), (i, size[1])], fill="#14233A", width=1)
    for j in range(0, size[1], 120):
        draw.line([(0, j), (size[0], j)], fill="#14233A", width=1)

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

    card_left, card_top, card_right, card_bottom = 90, 300, 990, 700
    draw.rounded_rectangle(
        [(card_left - 10, card_top - 10), (card_right + 10, card_bottom + 10)],
        radius=46, outline="#0E5663", width=8
    )
    draw.rounded_rectangle(
        [(card_left, card_top), (card_right, card_bottom)],
        radius=38, fill="#0F172A", outline=accent, width=6
    )

    shaped_title = shape_multiline(title, max_chars=18)
    chosen_font_cover = ImageFont.load_default()
    if font_path and os.path.exists(font_path):
        for test_size in range(88, 48, -4):
            candidate_font = ImageFont.truetype(font_path, test_size)
            test_kwargs = {"font": candidate_font, "align": "center", "spacing": 18}
            if HAS_RAQM:
                test_kwargs["direction"] = "rtl"
            tb = draw.multiline_textbbox((0, 0), shaped_title, **test_kwargs)
            if (tb[2] - tb[0]) <= (card_right - card_left - 60) and (tb[3] - tb[1]) <= (card_bottom - card_top - 50):
                chosen_font_cover = candidate_font
                break
        else:
            chosen_font_cover = ImageFont.truetype(font_path, 48)

    t_kwargs = {"font": chosen_font_cover, "align": "center", "spacing": 18}
    if HAS_RAQM:
        t_kwargs["direction"] = "rtl"
    tb = draw.multiline_textbbox((0, 0), shaped_title, **t_kwargs)
    tw = tb[2] - tb[0]
    th = tb[3] - tb[1]
    tx = (card_left + card_right - tw) // 2
    ty = (card_top + card_bottom - th) // 2

    draw.multiline_text((tx + 5, ty + 6), shaped_title, fill="#020617", **t_kwargs)
    draw.multiline_text((tx, ty), shaped_title, fill="#FFFFFF", stroke_width=2, stroke_fill="#FFFFFF", **t_kwargs)

    if subtitle:
        shaped_sub = shape_multiline(subtitle, max_chars=30)
        s_kwargs = {"font": font_badge}
        if HAS_RAQM:
            s_kwargs["direction"] = "rtl"
        sb = draw.textbbox((0, 0), shaped_sub, **s_kwargs)
        sw = (sb[2] - sb[0]) + 72
        sh = (sb[3] - sb[1]) + 30
        sx = (size[0] - sw) // 2
        sy = card_bottom + 30
        draw.rounded_rectangle([(sx, sy), (sx + sw, sy + sh)], radius=sh // 2, fill="#FACC15")
        draw.text((sx + 36, sy + (sh - (sb[3] - sb[1])) // 2 - 2), shaped_sub, fill="#0B1220", **s_kwargs)

    draw_upgraded_stickman(draw, 540, 1050, "WELCOME", frame_num, 0.4, accent, has_side_visual=False)
    return img


# 7. رسم المشهد الكامل مع الهوية والجزيئات وحركة الكاميرا
def draw_scene_frame(pose, visual_type, frame_num, total_frames, global_frame, mouth_open, global_progress, page_name, font_badge, size=(1080, 1920), bg_style=None):
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

    img = make_background(bg_style, global_frame, accent, size)
    if img is None:  # الشبكة الافتراضية القديمة
        img = Image.new("RGB", size, "#090D16")
        draw = ImageDraw.Draw(img)
        offset_grid = (global_frame * 2) % 120
        for i in range(-120, size[0] + 120, 120):
            draw.line([(i + offset_grid, 0), (i + offset_grid, size[1])], fill=bg_grid, width=1)
        for j in range(0, size[1], 120):
            draw.line([(0, j), (size[0], j)], fill=bg_grid, width=1)
    draw = ImageDraw.Draw(img)

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
async def pick_voice():
    """صوت وسرعة عشوائيين لكل ريلز (نفس الصوت لكل مشاهد الريلز الواحد)"""
    env = os.getenv("REEL_VOICES", "")
    candidates = [v.strip() for v in env.split(",") if v.strip()] or VOICE_CANDIDATES
    pool = []
    try:
        names = {v["ShortName"] for v in await edge_tts.list_voices()}
        pool = [c for c in candidates if c in names]
    except Exception as e:
        print("تعذر جلب قائمة الأصوات:", e)
    if not pool:
        pool = ["ar-SA-HamedNeural"]
    voice, rate = random.choice(pool), random.choice(RATE_CHOICES)
    print(f"الصوت المختار: {voice} | السرعة: {rate}")
    return voice, rate


async def generate_scene_audio(text, voice, audio_fn, retries=3, rate="+0%"):
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            try:
                communicate = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
            except TypeError:  # إصدار قديم من edge_tts
                communicate = edge_tts.Communicate(text, voice, rate=rate)
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
            if os.path.getsize(audio_fn) == 0:
                raise RuntimeError("ملف الصوت فارغ")
            return boundaries
        except Exception as e:
            last_err = e
            print(f"تعذر توليد الصوت (محاولة {attempt}/{retries}): {e}")
            await asyncio.sleep(2 * attempt)
    raise last_err


# 10. تجميع الفيديو النهائي (المشاهد تُرندر بالتوازي)
def render_scene(job):
    """يرندر مشهداً واحداً إلى ملف mp4. يشتغل داخل عملية مستقلة لتسريع الريندر."""
    idx = job["idx"]
    sc = job["scene"]
    fps = job["fps"]
    font_path = job["font_path"]
    duration = job["duration"]
    total_frames = job["total_frames"]

    if font_path and os.path.exists(font_path):
        font_title = ImageFont.truetype(font_path, 56)
        font_badge = ImageFont.truetype(font_path, 34)
    else:
        font_title = ImageFont.load_default()
        font_badge = ImageFont.load_default()

    dummy_draw = ImageDraw.Draw(Image.new("RGB", (1080, 1920)))

    raw_text = re.sub(r"\[.*?\]", "", sc["text"]).strip()
    words = [w for w in raw_text.split() if w.strip()]
    pose = sc.get("pose", "POINTING")
    visual = sc.get("visual", "IDEA_BULB")
    audio_file = f"scene_{idx}.mp3"

    envelope, word_timings = analyze_audio_and_timings(
        audio_file, words, total_frames, duration, job["boundaries"]
    )
    positioned_words = build_word_layout(
        dummy_draw, words, font_title, max_line_width=820, center_x=500, start_y=1190, line_height=86
    )

    print(f"ريندر المشهد {idx + 1}/{job['n_scenes']} [{pose} + {visual}]: {raw_text}", flush=True)
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

    try:
        for f in range(total_frames):
            current_time = f / float(fps)
            global_progress = (job["elapsed_offset"] + current_time) / job["total_duration"]
            gframe = job["global_frame_start"] + f

            if idx == 0 and f < 6:
                frame_img = draw_cover_frame(job["cover_title"], job["page_name"], f, font_path, font_badge,
                                             subtitle=job.get("cover_subtitle"))
            else:
                frame_img, accent = draw_scene_frame(
                    pose, visual, f, total_frames, gframe,
                    envelope[f], global_progress, job["page_name"], font_badge,
                    bg_style=job.get("bg_style")
                )
                draw = ImageDraw.Draw(frame_img)
                draw_kinetic_captions(
                    draw, positioned_words, word_timings, current_time, duration, pose, accent, font_title
                )

            proc.stdin.write(frame_img.tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        raise RuntimeError(f"ffmpeg توقف أثناء ريندر المشهد {idx + 1}")
    finally:
        code = proc.wait()
    if code != 0:
        raise RuntimeError(f"ffmpeg فشل بالمشهد {idx + 1} (code {code})")
    return output_scene


async def make_video(script_data, page_name):
    print("بدء صناعة ريلز الستيك مان الاحترافي...")
    scenes = script_data["scenes"]
    cover_title = script_data.get("title", "إضاءات ومعلومات مفيدة!")
    voice, rate = await pick_voice()
    CURRENT_META["voice"] = f"{voice}@{rate}"
    fps = 24

    # الصوت: 3 مشاهد بالتوازي كحد أقصى حتى ما نضغط على خدمة edge_tts
    sem = asyncio.Semaphore(3)

    async def tts_scene(idx, sc):
        audio_fn = f"scene_{idx}.mp3"
        clean_text = re.sub(r"\[.*?\]", "", sc["text"]).strip()
        async with sem:
            boundaries = await generate_scene_audio(clean_text, voice, audio_fn, rate=rate)
        return audio_fn, boundaries

    results = await asyncio.gather(*[tts_scene(i, sc) for i, sc in enumerate(scenes)])
    audio_paths = [r[0] for r in results]
    scene_boundaries = [r[1] for r in results]
    scene_durations = [get_audio_duration(a) for a in audio_paths]

    total_video_duration = max(1.0, sum(scene_durations))
    print(f"المدة الكلية للريلز: {total_video_duration:.1f} ثانية")

    font_path = select_random_font()
    bg_style = os.getenv("REEL_BG") or BG_STYLE_BY_CATEGORY.get(CURRENT_META.get("category"), "grid")
    print(f"نمط الخلفية: {bg_style}")

    jobs = []
    elapsed_offset = 0.0
    global_frame_start = 0
    for idx, sc in enumerate(scenes):
        duration = scene_durations[idx]
        total_frames = max(int(duration * fps), 24)
        jobs.append({
            "idx": idx, "scene": sc, "n_scenes": len(scenes), "fps": fps,
            "font_path": font_path, "duration": duration, "total_frames": total_frames,
            "boundaries": scene_boundaries[idx], "elapsed_offset": elapsed_offset,
            "global_frame_start": global_frame_start, "total_duration": total_video_duration,
            "cover_title": cover_title, "page_name": page_name,
            "cover_subtitle": script_data.get("badge"), "bg_style": bg_style,
        })
        elapsed_offset += duration
        global_frame_start += total_frames

    workers = max(1, min(os.cpu_count() or 1, 4, len(jobs)))
    print(f"ريندر {len(jobs)} مشاهد بـ {workers} عملية متوازية...")
    video_clips = None
    if workers > 1:
        try:
            import multiprocessing
            from concurrent.futures import ProcessPoolExecutor
            ctx = multiprocessing.get_context("fork")
            with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as ex:
                video_clips = list(ex.map(render_scene, jobs))
        except Exception as e:
            print("فشل الريندر المتوازي، سيتم الريندر بالتتابع:", e)
            video_clips = None
    if video_clips is None:
        video_clips = [render_scene(j) for j in jobs]

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


# 11. نشر الفيديو على بيج فيسبوك (مع إعادة محاولة وانتظار اكتمال الرفع)
def _fb_json(method, url, retries=3, **kwargs):
    timeout = kwargs.pop("timeout", 60)
    last = {"error": {"message": "unknown"}}
    for attempt in range(1, retries + 1):
        try:
            r = requests.request(method, url, timeout=timeout, **kwargs)
            try:
                last = r.json()
            except ValueError:
                last = {"error": {"message": r.text[:200]}}
            if r.status_code >= 500 and attempt < retries:
                time.sleep(5 * attempt)
                continue
            return last
        except requests.RequestException as e:
            last = {"error": {"message": str(e)}}
            print(f"خطأ اتصال بفيسبوك (محاولة {attempt}/{retries}): {e}")
            if attempt < retries:
                time.sleep(5 * attempt)
    return last


def _reel_status(video_id):
    res = _fb_json(
        "GET", f"https://graph.facebook.com/{GRAPH_VERSION}/{video_id}",
        retries=2, params={"fields": "status", "access_token": FB_ACCESS_TOKEN}
    )
    return res.get("status", {}) if isinstance(res, dict) else {}


def _wait_phase(video_id, phase, done=("complete",), timeout=180, interval=6):
    """True = اكتملت، False = خطأ، None = ما قدرنا نتأكد خلال المهلة"""
    start = time.time()
    while time.time() - start < timeout:
        ph = _reel_status(video_id).get(phase, {}) or {}
        state = ph.get("status")
        if state in done:
            return True
        if state == "error":
            print(f"فيسبوك أبلغ عن خطأ بمرحلة {phase}:", ph)
            return False
        time.sleep(interval)
    return None


def post_first_comment(video_id, message):
    """تعليق أول تحت الريلز (الـ API لا يدعم التثبيت، ثبّته يدوياً من الصفحة إذا أردت)"""
    if not message:
        return
    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{video_id}/comments"
    for attempt in range(1, 4):
        res = _fb_json("POST", url, retries=1, data={"message": message, "access_token": FB_ACCESS_TOKEN})
        if "id" in res:
            print("تم نشر التعليق الأول.")
            return
        print(f"تعذر نشر التعليق (محاولة {attempt}/3):", res.get("error", res))
        time.sleep(10 * attempt)
    print("تخطيت التعليق الأول (لا يؤثر على نشر الريلز). تأكد من صلاحية pages_manage_engagement.")


def publish_to_facebook(video_path, title, caption, first_comment=None):
    print("بدء عملية النشر على فيسبوك ريلز...")
    if not FB_ACCESS_TOKEN or not FB_PAGE_ID:
        print("خطأ: FB_ACCESS_TOKEN أو FB_PAGE_ID غير موجود في Secrets!")
        sys.exit(1)

    reels_url = f"https://graph.facebook.com/{GRAPH_VERSION}/{FB_PAGE_ID}/video_reels"

    init_res = _fb_json("POST", reels_url, params={"access_token": FB_ACCESS_TOKEN, "upload_phase": "start"})
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
    uploaded = False
    for attempt in range(1, 4):
        try:
            with open(video_path, "rb") as f:
                upload_res = requests.post(upload_url, headers=headers, data=f, timeout=600)
            print(f"رد فيسبوك (رفع الملف، محاولة {attempt}):", upload_res.status_code, upload_res.text[:200])
            if upload_res.status_code == 200:
                uploaded = True
                break
        except requests.RequestException as e:
            print(f"تعذر رفع الملف (محاولة {attempt}/3): {e}")
        time.sleep(8 * attempt)
    if not uploaded:
        print("فشل رفع الملف بعد 3 محاولات!")
        sys.exit(1)

    state = _wait_phase(video_id, "uploading_phase", timeout=180)
    if state is False:
        sys.exit(1)
    if state is None:
        print("تنبيه: لم أتأكد من اكتمال الرفع، سأكمل النشر على أي حال.")

    publish_params = {
        "access_token": FB_ACCESS_TOKEN,
        "video_id": video_id,
        "upload_phase": "finish",
        "video_state": "PUBLISHED",
        "title": title,
        "description": caption
    }
    publish_res = _fb_json("POST", reels_url, params=publish_params)
    print("رد فيسبوك (مرحلة النشر النهائي):", publish_res)

    if publish_res.get("success") or "video_id" in publish_res:
        print("تم نشر الريلز على صفحة فيسبوك بنجاح!")
        log_post(video_id, title)
        pub = _wait_phase(video_id, "publishing_phase", timeout=90)
        print("حالة النشر النهائية:", {True: "مكتمل", False: "فيه خطأ", None: "قيد المعالجة"}[pub])
        post_first_comment(video_id, first_comment)
        return True

    print("فشل النشر النهائي على فيسبوك!")
    sys.exit(1)


# ======================================================================
# التحليلات (python main.py analytics)
# ======================================================================
MIN_AGE_HOURS = 24      # لا نقيس قبل مرور 24 ساعة
FINAL_AGE_HOURS = 72    # بعد 72 ساعة نثبّت الرقم ولا نعيد القياس
MIN_POSTS = 3           # أقل عدد ريلز لكل قسم/طول قبل تعديل وزنه
W_MIN, W_MAX = 0.4, 2.5  # حدود الوزن حتى ما يختفي قسم نهائياً

METRICS = [
    "fb_reels_total_plays",
    "post_video_avg_time_watched",
    "post_video_social_actions",
]


def fetch_metric(video_id, metric):
    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{video_id}/video_insights"
    try:
        res = requests.get(
            url, params={"metric": metric, "access_token": FB_ACCESS_TOKEN}, timeout=20
        ).json()
        if "error" in res:
            print(f"  [{metric}] خطأ:", res["error"].get("message"))
            return None
        data = res.get("data", [])
        if not data or not data[0].get("values"):
            return None
        val = data[0]["values"][0].get("value")
        if isinstance(val, dict):  # مثل social_actions: {COMMENT: x, SHARE: y}
            val = sum(v for v in val.values() if isinstance(v, (int, float)))
        return val
    except Exception as e:
        print(f"  [{metric}] تعذر الجلب:", e)
        return None


def age_hours(iso_ts):
    ts = datetime.datetime.fromisoformat(iso_ts)
    return (datetime.datetime.now(datetime.timezone.utc) - ts).total_seconds() / 3600.0


def update_posts(posts):
    for p in posts:
        if p.get("final"):
            continue
        age = age_hours(p["ts"])
        if age < MIN_AGE_HOURS:
            continue
        print(f"قياس {p['video_id']} ({p.get('category')}) عمره {age:.0f} ساعة")
        m = {name: fetch_metric(p["video_id"], name) for name in METRICS}
        if m["fb_reels_total_plays"] is None:
            continue  # ما توفرت الأرقام بعد
        p["metrics"] = m
        p["score"] = float(m["fb_reels_total_plays"])
        if age >= FINAL_AGE_HOURS:
            p["final"] = True


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def compute_weights(posts):
    scored = [p for p in posts if "score" in p and p.get("category") not in ("fallback", "unknown")]
    if not scored:
        return None
    global_mean = mean([p["score"] for p in scored])
    if global_mean <= 0:
        return None

    def build(key):
        groups = defaultdict(list)
        for p in scored:
            groups[str(p.get(key))].append(p["score"])
        out = {}
        for k, xs in groups.items():
            if len(xs) >= MIN_POSTS:
                out[k] = round(max(W_MIN, min(W_MAX, mean(xs) / global_mean)), 2)
            else:
                out[k] = 1.0
        return out, groups

    cat_w, cat_groups = build("category")
    len_w, len_groups = build("scenes_count")

    print("\n=== تقرير الأداء (متوسط المشاهدات) ===")
    print(f"المتوسط العام: {global_mean:.0f} | عدد الريلز المقاس: {len(scored)}")
    for k, xs in sorted(cat_groups.items(), key=lambda kv: -mean(kv[1])):
        print(f"  {k}: {mean(xs):.0f} ({len(xs)} ريلز) -> وزن {cat_w[k]}")
    for k, xs in len_groups.items():
        print(f"  طول {k} مشاهد: {mean(xs):.0f} ({len(xs)} ريلز) -> وزن {len_w[k]}")

    return {
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "category": cat_w,
        "length": len_w,
    }


def print_dimension_reports(posts):
    """تقارير فقط (بدون تعديل الأوزان): أي صوت / نوع Hook / سلسلة يجيب مشاهدات أكثر"""
    scored = [p for p in posts if "score" in p]
    if not scored:
        return
    dims = [("voice", "الصوت"), ("hook_type", "نوع الـ Hook")]
    for key, label in dims:
        groups = defaultdict(list)
        for p in scored:
            if p.get(key):
                groups[p[key]].append(p["score"])
        if groups:
            print(f"\n=== حسب {label} ===")
            for k, xs in sorted(groups.items(), key=lambda kv: -mean(kv[1])):
                print(f"  {k}: {mean(xs):.0f} ({len(xs)} ريلز)")
    in_s = [p["score"] for p in scored if p.get("series")]
    out_s = [p["score"] for p in scored if not p.get("series") and p.get("category") == "شرح حديث نبوي شريف"]
    if in_s and out_s:
        print("\n=== الأحاديث: ضمن سلسلة مقابل منفردة ===")
        print(f"  ضمن سلسلة: {mean(in_s):.0f} ({len(in_s)}) | منفردة: {mean(out_s):.0f} ({len(out_s)})")


def print_best_hours(posts):
    """أفضل ساعات النشر (UTC) حسب متوسط المشاهدات، مع اقتراح cron. تقرير فقط."""
    finals = [p for p in posts if p.get("final") and "score" in p]
    if len(finals) < 8:
        print(f"\n=== أفضل وقت للنشر === بيانات غير كافية بعد ({len(finals)} من 8 ريلز مثبتة)")
        return None
    by_hour = defaultdict(list)
    for p in finals:
        h = datetime.datetime.fromisoformat(p["ts"]).hour
        by_hour[h].append(p["score"])
    ranked = sorted(
        ((h, mean(xs), len(xs)) for h, xs in by_hour.items() if len(xs) >= 2),
        key=lambda t: -t[1]
    )
    if not ranked:
        print("\n=== أفضل وقت للنشر === لا توجد ساعة فيها ريلزان على الأقل بعد")
        return None
    print("\n=== أفضل وقت للنشر (توقيت UTC، وبتوقيت العراق +3) ===")
    for h, m, n in ranked[:5]:
        print(f"  {h:02d}:00 UTC ({(h + 3) % 24:02d}:00 بغداد): {m:.0f} مشاهدة ({n} ريلز)")
    best = sorted(h for h, _, _ in ranked[:3])
    print(f'  اقتراح cron: "0 {",".join(str(h) for h in best)} * * *"')
    return best


def analytics_main():
    if not FB_ACCESS_TOKEN:
        print("FB_ACCESS_TOKEN غير موجود")
        return
    if not os.path.exists(POSTS_LOG_FILE):
        print("لا يوجد posts_log.json بعد")
        return
    with open(POSTS_LOG_FILE, "r", encoding="utf-8") as f:
        posts = json.load(f)

    update_posts(posts)

    with open(POSTS_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False, indent=2)

    weights = compute_weights(posts)
    print_dimension_reports(posts)
    print_best_hours(posts)
    if weights:
        with open(WEIGHTS_FILE, "w", encoding="utf-8") as f:
            json.dump(weights, f, ensure_ascii=False, indent=2)
        print("\nتم تحديث weights.json")
    else:
        print("لا توجد بيانات كافية لحساب الأوزان بعد")



async def main():
    page_name = get_page_name()
    script_data = await generate_script()
    first_comment = build_first_comment(script_data)
    script_data = add_hook(script_data)
    final_video = await make_video(script_data, page_name)
    publish_to_facebook(final_video, script_data["title"], script_data["caption"], first_comment)


if __name__ == "__main__":
    if ANALYTICS_MODE:
        analytics_main()
    else:
        asyncio.run(main())
