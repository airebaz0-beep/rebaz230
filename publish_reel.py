import os
import re
import sys
import json
import math
import random
import shutil
import argparse
import subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta


# ==============================================================================
# 0. فحص وتجهيز البيئة تلقائياً (FFmpeg + المكتبات + الخطوط العربية)
# ==============================================================================
def ensure_runtime_dependencies():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("[SETUP] تثبيت FFmpeg والخطوط العربية تلقائياً...")
        try:
            subprocess.run(["sudo", "apt-get", "update", "-qq"], check=False)
            subprocess.run(
                ["sudo", "apt-get", "install", "-y", "-qq", "ffmpeg", "fonts-noto-core"],
                check=False
            )
        except Exception as e:
            print(f"[WARN] تعذر التثبيت عبر apt-get: {e}")

    required_pkgs = {
        "requests": "requests",
        "edge_tts": "edge-tts",
        "PIL": "Pillow",
        "arabic_reshaper": "arabic-reshaper",
        "bidi": "python-bidi",
    }
    missing = []
    for mod_name, pip_name in required_pkgs.items():
        try:
            __import__(mod_name)
        except ImportError:
            missing.append(pip_name)

    if missing:
        print(f"[SETUP] تثبيت مكتبات بايثون الناقصة: {missing}")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", *missing],
            check=True
        )


ensure_runtime_dependencies()

import requests
from PIL import Image, ImageDraw, ImageFont

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
    HAS_ARABIC_SHAPING = True
except ImportError:
    HAS_ARABIC_SHAPING = False


# ==============================================================================
# 1. إعدادات المشروع والثوابت
# ==============================================================================
WIDTH = 1080
HEIGHT = 1920
FPS = 30

SAFE_LEFT = int(WIDTH * 0.10)
SAFE_RIGHT = int(WIDTH * 0.90)
SAFE_TOP = int(HEIGHT * 0.10)
SAFE_BOTTOM = int(HEIGHT * 0.80)

WORK_DIR = Path("stickman_build")
AUDIO_DIR = WORK_DIR / "audio_parts"
FRAMES_DIR = WORK_DIR / "frames"
PREVIEWS_DIR = WORK_DIR / "previews"
EXPORTS_DIR = Path("exports")
FONTS_DIR = Path("fonts")
HISTORY_FILE = Path("published_history.json")

PAGE_DISPLAY_NAME = "منسى - Mansa"
PAGE_SPOKEN_NAME = "مَنْسَى"

VOICE_NAME = os.getenv("TTS_VOICE", "ar-SA-HamedNeural")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
FB_PAGE_ID = os.getenv("FB_PAGE_ID", "").strip()
FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN", "").strip()

CATEGORIES = [
    {
        "id": "hadith_sahih",
        "name": "حديث نبوي صحيح وعبرة",
        "rules": (
            "يجب أن يرتكز الفيديو على حديث نبوي شريف صحيح 100% من (صحيح البخاري) أو (صحيح مسلم) حصراً. "
            "يُمنع منعاً باتاً ذكر أي حديث ضعيف أو موضوع أو غير متأكد من ثبوته في الصحيحين. "
            "اذكر اسم الكتاب ورقم الحديث أو راويه بدقة في حقل hadith_source، واشرح معناه بقصة بصرية واقعية."
        )
    },
    {
        "id": "self_development",
        "name": "تطوير الذات والعادات اليومية",
        "rules": "فكرة عملية عن بناء العادات، الانضباط، إدارة الوقت، أو التغلب على التسويف بقصة إنسانية ملهمة."
    },
    {
        "id": "psychology",
        "name": "علم النفس والهدوء الداخلي",
        "rules": "فكرة نفسية عميقة عن التفكير الزائد، ضبط الانفعال، الثقة بالنفس، أو التعامل مع الضغوط اليومية."
    },
    {
        "id": "life_wisdom",
        "name": "مواقف حياتية واجتماعية",
        "rules": "موقف حياتي واقعي عن الاختيارات المصيرية، أثر الكلمة الطيبة، أو الصبر عند الشدائد."
    }
]


# ==============================================================================
# 2. قصص احتياطية معتمدة (تتضمن أحاديث صحيحة 100% من البخاري ومسلم + مواضيع متنوعة)
# ==============================================================================
FALLBACK_STORIES = [
    {
        "category": "hadith_sahih",
        "hadith_source": "متفق عليه: رواه البخاري (6114) ومسلم (2609)",
        "title": "مَنْ هُوَ الْقَوِيُّ الْحَقِيقِيُّ؟",
        "caption": "قال رسول الله ﷺ: «لَيْسَ الشَّدِيدُ بِالصُّرَعَةِ، إِنَّمَا الشَّدِيدُ الَّذِي يَمْلِكُ نَفْسَهُ عِنْدَ الْغَضَبِ» [متفق عليه: البخاري ومسلم].\nتابع منسى - Mansa للمزيد. #حديث_صحيح #البخاري_ومسلم #تطوير_الذات",
        "scenes": [
            {
                "id": 1,
                "spoken_text": "يَظُنُّ كَثِيرٌ مِنَ النَّاسِ أَنَّ الْقُوَّةَ هِيَ رَفْعُ الصَّوْتِ أَوِ الْانْتِصَارُ فِي الْجِدَالِ.",
                "bg_type": "crossroads",
                "pose": "think",
                "expression": "neutral",
                "facing": 1
            },
            {
                "id": 2,
                "spoken_text": "لَكِنَّ النَّبِيَّ صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ صَحَّحَ هَذَا الْمَفْهُومَ بِحَدِيثٍ عَظِيمٍ.",
                "bg_type": "dawn_call",
                "pose": "look_up",
                "expression": "peaceful",
                "facing": 1
            },
            {
                "id": 3,
                "spoken_text": "فَفِي الصَّحِيحَيْنِ عَنِ الْبُخَارِيِّ وَمُسْلِمٍ قَالَ: لَيْسَ الشَّدِيدُ بِالصُّرَعَةِ.",
                "bg_type": "mosque",
                "pose": "walk",
                "expression": "determined",
                "facing": 1
            },
            {
                "id": 4,
                "spoken_text": "إِنَّمَا الشَّدِيدُ الَّذِي يَمْلِكُ نَفْسَهُ عِنْدَ الْغَضَبِ.",
                "bg_type": "mosque_light",
                "pose": "arms_open",
                "expression": "peaceful",
                "facing": 1
            },
            {
                "id": 5,
                "spoken_text": "فَالْقُوَّةُ الْحَقِيقِيَّةُ تَبْدَأُ حِينَ تَهْدَأُ وَقْتَ الْعَاصِفَةِ. تَابِعْ مَنْسَى لِلْمَزِيدِ.",
                "bg_type": "paradise",
                "pose": "walk",
                "expression": "happy",
                "facing": 1
            }
        ]
    },
    {
        "category": "hadith_sahih",
        "hadith_source": "صحيح البخاري (رقم 6412)",
        "title": "نِعْمَتَانِ يَغْفُلُ عَنْهُمَا النَّاسُ",
        "caption": "قال رسول الله ﷺ: «نِعْمَتَانِ مَغْبُونٌ فِيهِمَا كَثِيرٌ مِنَ النَّاسِ: الصِّحَّةُ وَالْفَرَاغُ» [رواه البخاري].\nتابع منسى - Mansa للمزيد. #صحيح_البخاري #إدارة_الوقت #تأمل",
        "scenes": [
            {
                "id": 1,
                "spoken_text": "تَمُرُّ السَّاعَاتُ وَالْأَيَّامُ وَنَحْنُ نُؤَجِّلُ أَحْلَامَنَا وَأَعْمَالَنَا إِلَى الْغَدِ.",
                "bg_type": "distraction",
                "pose": "sad_slump",
                "expression": "sad",
                "facing": 1
            },
            {
                "id": 2,
                "spoken_text": "وَقَدْ حَذَّرَنَا النَّبِيُّ صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ مِنْ هَذِهِ الْغَفْلَةِ فِي صَحِيحِ الْبُخَارِيِّ.",
                "bg_type": "dawn_call",
                "pose": "look_up",
                "expression": "shocked",
                "facing": 1
            },
            {
                "id": 3,
                "spoken_text": "حَيْثُ قَالَ: نِعْمَتَانِ مَغْبُونٌ فِيهِمَا كَثِيرٌ مِنَ النَّاسِ: الصِّحَّةُ وَالْفَرَاغُ.",
                "bg_type": "study_desk",
                "pose": "think",
                "expression": "determined",
                "facing": 1
            },
            {
                "id": 4,
                "spoken_text": "فَمَا دُمْتَ تَمْلِكُ عَافِيَتَكَ وَوَقْتَكَ الْيَوْمَ، فَلَا تُضَيِّعْهُمَا فِيمَا لَا يَنْفَعُ.",
                "bg_type": "paradise",
                "pose": "walk",
                "expression": "happy",
                "facing": 1
            },
            {
                "id": 5,
                "spoken_text": "ابْدَأْ خُطْوَتَكَ الْآنَ قَبْلَ فَوَاتِ الْأَوَانِ. تَابِعْ مَنْسَى لِلْمَزِيدِ.",
                "bg_type": "mosque_light",
                "pose": "arms_open",
                "expression": "happy",
                "facing": 1
            }
        ]
    },
    {
        "category": "self_development",
        "hadith_source": "",
        "title": "قَاعِدَةُ الدَّقِيقَتَيْنِ لِكَسْرِ الْكَسَلِ",
        "caption": "أصعب جزء في أي إنجاز هو البداية.. جرب قاعدة الدقيقتين اليوم.\nتابع منسى - Mansa للمزيد. #تطوير_الذات #عادات #إنتاجية",
        "scenes": [
            {
                "id": 1,
                "spoken_text": "هَلْ تَجِدُ صُعُوبَةً كَبِيرَةً فِي بَدْءِ مَهَامِّكَ الْيَوْمِيَّةِ رَغْمَ أَهَمِّيَّتِهَا؟",
                "bg_type": "solitude",
                "pose": "sad_slump",
                "expression": "sad",
                "facing": 1
            },
            {
                "id": 2,
                "spoken_text": "الْعَقْلُ يَهْرُبُ مِنَ الْمَهَامِّ الْكَبِيرَةِ لِأَنَّهُ يَرَاهَا مُرْهِقَةً وَطَوِيلَةً.",
                "bg_type": "crossroads",
                "pose": "think",
                "expression": "neutral",
                "facing": 1
            },
            {
                "id": 3,
                "spoken_text": "الْحَلُّ الْبَسِيطُ هُوَ أَنْ تَلْتَزِمَ بِالْعَمَلِ لِمُدَّةِ دَقِيقَتَيْنِ فَقَطْ دُونَ ضَغْطٍ.",
                "bg_type": "study_desk",
                "pose": "walk",
                "expression": "determined",
                "facing": 1
            },
            {
                "id": 4,
                "spoken_text": "بِمُجَرَّدِ أَنْ تَكْسِرَ حَاجِزَ الْبِدَايَةِ، سَتَجِدُ نَفْسَكَ تَسْتَمِرُّ بِحَمَاسٍ وَسُهُولَةٍ.",
                "bg_type": "paradise",
                "pose": "arms_open",
                "expression": "happy",
                "facing": 1
            },
            {
                "id": 5,
                "spoken_text": "الْإِنْجَازُ الْكَبِيرُ يَبْدَأُ بِدَقِيقَةٍ وَاحِدَةٍ صَادِقَةٍ. تَابِعْ مَنْسَى لِلْمَزِيدِ.",
                "bg_type": "dawn_call",
                "pose": "walk",
                "expression": "happy",
                "facing": 1
            }
        ]
    },
    {
        "category": "psychology",
        "hadith_source": "",
        "title": "لِمَاذَا يُتْعِبُكَ التَّفْكِيرُ الزَّائِدُ؟",
        "caption": "كثرة التفكير في المستقبل تسرق منك هدوء الحاضر.\nتابع منسى - Mansa للمزيد. #علم_النفس #هدوء_نفسي #وعي",
        "scenes": [
            {
                "id": 1,
                "spoken_text": "أَحْيَانًا نُعَانِي فِي خَيَالِنَا أَكْثَرَ مِمَّا نُعَانِي فِي الْوَاقِعِ نَفْسِهِ.",
                "bg_type": "solitude",
                "pose": "think",
                "expression": "sad",
                "facing": 1
            },
            {
                "id": 2,
                "spoken_text": "التَّفْكِيرُ الزَّائِدُ يَجْعَلُكَ تَعِيشُ مَشَاكِلَ لَمْ تَحْدُثْ وَلَنْ تَحْدُثَ أَبَدًا.",
                "bg_type": "crossroads",
                "pose": "fear_back",
                "expression": "shocked",
                "facing": -1
            },
            {
                "id": 3,
                "spoken_text": "تَوَقَّفْ لَحْظَةً، وَافْصِلْ بَيْنَ مَا تَسْتَطِيعُ تَغْيِيرَهُ وَمَا هُوَ خَارِجُ سَيْطَرَتِكَ.",
                "bg_type": "dawn_call",
                "pose": "look_up",
                "expression": "peaceful",
                "facing": 1
            },
            {
                "id": 4,
                "spoken_text": "رَكِّزْ طَاقَتَكَ عَلَى خُطْوَتِكَ الْحَالِيَّةِ فَقَطْ، وَاتْرُكِ الْبَاقِيَ لِتَدْبِيرِ اللَّهِ.",
                "bg_type": "paradise",
                "pose": "walk",
                "expression": "happy",
                "facing": 1
            },
            {
                "id": 5,
                "spoken_text": "رَاحَةُ بَالِكَ تَبْدَأُ حِينَ تَتَوَقَّفُ عَنِ الْقَلَقِ. تَابِعْ مَنْسَى لِلْمَزِيدِ.",
                "bg_type": "mosque_light",
                "pose": "arms_open",
                "expression": "peaceful",
                "facing": 1
            }
        ]
    }
]


# ==============================================================================
# 3. إدارة سجل المواضيع المنشورة لمنع التكرار
# ==============================================================================
def get_iraq_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=3)))


def get_day_slot_name(dt: datetime) -> str:
    h = dt.hour
    if 5 <= h < 11:
        return "الصباح"
    elif 11 <= h < 15:
        return "الظهر"
    elif 15 <= h < 18:
        return "العصر"
    return "العشاء"


def load_history() -> list:
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            pass
    return []


def save_history(history: list):
    trimmed = history[-60:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, ensure_ascii=False, indent=2)


def choose_next_category(history: list, today_str: str) -> dict:
    used_today = {item.get("category") for item in history if item.get("date") == today_str}
    available = [c for c in CATEGORIES if c["id"] not in used_today]
    if not available:
        available = CATEGORIES
    return random.choice(available)


# ==============================================================================
# 4. أدوات اللغة العربية، تنظيف النطق، والخطوط
# ==============================================================================
def sanitize_spoken_text(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"منسى\s*[-–—/|]\s*mansa", "مَنْسَى", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"مَنْسَى\s*[-–—/|]\s*mansa", "مَنْسَى", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bmansa\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"(مَنْسَى|منسى)(\s+\1)+", r"\1", cleaned)
    cleaned = re.sub(r"\[[^\]]*\]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def strip_tashkeel(text: str) -> str:
    return re.sub(r"[\u064B-\u065F\u0670]", "", text)


def shape_arabic(text: str) -> str:
    if not text:
        return ""
    if HAS_ARABIC_SHAPING:
        reshaped = arabic_reshaper.reshape(text)
        return get_display(reshaped)
    return text


def pick_arabic_font(size: int) -> ImageFont.FreeTypeFont:
    font_candidates = []
    if FONTS_DIR.exists():
        for ext in ("*.ttf", "*.otf"):
            font_candidates.extend(FONTS_DIR.glob(ext))
    system_fallbacks = [
        Path("alfont_com_EYENAmmon-Bold.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf"),
    ]
    if font_candidates:
        chosen = random.choice(font_candidates)
        try:
            return ImageFont.truetype(str(chosen), size)
        except Exception:
            pass
    for fb in system_fallbacks:
        if fb.exists():
            try:
                return ImageFont.truetype(str(fb), size)
            except Exception:
                pass
    return ImageFont.load_default()


def get_audio_duration(audio_path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(res.stdout.strip())


# ==============================================================================
# 5. الاتصال الذكي بـ Gemini API (يكتشف النماذج المتاحة تلقائياً بدون 404)
# ==============================================================================
def discover_gemini_models(api_key: str) -> list:
    fallback_models = [
        "models/gemini-flash-latest",
        "models/gemini-2.5-flash",
        "models/gemini-2.0-flash",
        "models/gemini-pro-latest",
    ]
    try:
        resp = requests.get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
            timeout=15
        )
        if resp.status_code == 200:
            models = resp.json().get("models", [])
            valid = []
            for m in models:
                name = m.get("name", "")
                methods = m.get("supportedGenerationMethods", [])
                if "generateContent" in methods and "gemini" in name.lower():
                    if not any(x in name.lower() for x in ("image", "tts", "embedding", "aqa", "vision")):
                        valid.append(name)
            flash_first = sorted(valid, key=lambda n: (0 if "flash" in n.lower() else 1, n))
            if flash_first:
                return flash_first
    except Exception:
        pass
    return fallback_models


def generate_story(history: list) -> dict:
    now_iq = get_iraq_now()
    today_str = now_iq.strftime("%Y-%m-%d")
    slot_name = get_day_slot_name(now_iq)
    chosen_cat = choose_next_category(history, today_str)

    duration_tier = random.choice([
        {"label": "32-38 ثانية", "scenes_count": 5, "words_per_scene": "14 إلى 17 كلمة"},
        {"label": "42-48 ثانية", "scenes_count": 6, "words_per_scene": "16 إلى 20 كلمة"},
        {"label": "52-58 ثانية", "scenes_count": 7, "words_per_scene": "17 إلى 22 كلمة"},
    ])

    recent_titles = [h.get("title", "") for h in history[-25:] if h.get("title")]
    today_titles = [h.get("title", "") for h in history if h.get("date") == today_str]

    if GEMINI_API_KEY:
        prompt = f"""
أنت مخرج ومؤلف محتوى لفيديوهات Stickman عربية احترافية لصفحة ({PAGE_DISPLAY_NAME}).
الوقت الحالي: فترة {slot_name} ({today_str}).
التصنيف المطلوب لهذه الحلقة حصراً: {chosen_cat['name']}.
قواعد التصنيف: {chosen_cat['rules']}

المواضيع المنشورة اليوم (ممنوع تكرارها أو الاقتراب من فكرتها نهائياً):
{json.dumps(today_titles, ensure_ascii=False)}

آخر 25 موضوعاً منشوراً في الصفحة (ابتعد عنها تماماً واختر زاوية جديدة كلياً):
{json.dumps(recent_titles, ensure_ascii=False)}

المواصفات الإلزامية للنص والمدة:
1. المدة المستهدفة للفيديو: {duration_tier['label']} (يجب ألا تقل المدة عن 30 ثانية ولا تزيد عن 60 ثانية).
2. قسّم القصة إلى {duration_tier['scenes_count']} مشاهد متسلسلة، وطول الجملة المنطوقة في كل مشهد هو ({duration_tier['words_per_scene']}) بتشكيل عربي كامل وصحيح.
3. في المشهد الأخير فقط، اختم بعبارة: «تَابِعْ مَنْسَى لِلْمَزِيدِ.» (اكتب كلمة مَنْسَى مرة واحدة فقط ولا تكتب Mansa في النص المنطوق أبداً).
4. إذا كان التصنيف حديثاً نبوياً: يجب أن يكون الحديث صحيحاً 100% ومثبتاً في صحيح البخاري أو صحيح مسلم حصراً، واكتب التخريج الدقيق في حقل hadith_source. وإذا لم يكن حديثاً اترك hadith_source فارغاً "".

أعد النتيجة بصيغة JSON فقط بدون أي شرح إضافي:
{{
  "category": "{chosen_cat['id']}",
  "hadith_source": "اسم المصدر ورقم الحديث إن وجد أو فارغ",
  "title": "عنوان قصير مشكول",
  "caption": "وصف قصير وجذاب لمنشور فيسبوك يذكر المصدر إن كان حديثاً مع 3 وسوم",
  "scenes": [
    {{
      "id": 1,
      "spoken_text": "جملة عربية فصحى مشكولة بالكامل وسلسة النطق",
      "bg_type": "اختر واحداً فقط من: mosque, mosque_light, dawn_call, distraction, solitude, crossroads, fire, paradise, study_desk",
      "pose": "اختر واحداً فقط من: run, walk, sad_slump, look_up, sujood, think, fear_back, arms_open",
      "expression": "اختر واحداً فقط من: happy, sad, shocked, determined, peaceful, neutral",
      "facing": 1
    }}
  ]
}}
"""
        candidate_models = discover_gemini_models(GEMINI_API_KEY)
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.85, "responseMimeType": "application/json"}
        }

        for model_name in candidate_models[:4]:
            url = f"https://generativelanguage.googleapis.com/v1beta/{model_name}:generateContent?key={GEMINI_API_KEY}"
            try:
                resp = requests.post(url, json=payload, timeout=35)
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                data = resp.json()
                raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
                story = json.loads(raw_text)

                if story.get("category") == "hadith_sahih":
                    src = story.get("hadith_source", "")
                    if not ("البخاري" in src or "مسلم" in src):
                        raise ValueError(f"تم رفض حديث غير موثق من البخاري أو مسلم: {src}")

                if "scenes" in story and len(story["scenes"]) >= 4:
                    for sc in story["scenes"]:
                        sc["spoken_text"] = sanitize_spoken_text(sc.get("spoken_text", ""))
                    print(f"[OK] تم توليد حلقة جديدة عبر {model_name} ({chosen_cat['name']}): {story.get('title')}")
                    return story
            except Exception as e:
                print(f"[WARN] محاولة {model_name} لم تكتمل ({e})")

    unused_fallbacks = [
        st for st in FALLBACK_STORIES
        if strip_tashkeel(st["title"]) not in {strip_tashkeel(t) for t in recent_titles[-10:]}
    ]
    chosen_fb = random.choice(unused_fallbacks if unused_fallbacks else FALLBACK_STORIES)
    for sc in chosen_fb["scenes"]:
        sc["spoken_text"] = sanitize_spoken_text(sc.get("spoken_text", ""))
    print(f"[INFO] اختيار حلقة معتمدة غير مكررة: {chosen_fb.get('title')}")
    return chosen_fb


# ==============================================================================
# 6. توليد التعليق الصوتي وبناء timing.json (مع ضمان الحد الأدنى 30 ثانية)
# ==============================================================================
def synthesize_voice_and_timing(story: dict) -> tuple:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    concat_list_path = AUDIO_DIR / "concat.txt"
    timing_records = []
    current_time = 0.0
    pause_gap = 0.40

    silence_path = AUDIO_DIR / "silence.mp3"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
        "-t", str(pause_gap), "-q:a", "9", str(silence_path)
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    part_files = []
    part_durations = []

    for idx, scene in enumerate(story["scenes"]):
        part_mp3 = AUDIO_DIR / f"scene_{idx+1:02d}.mp3"
        text = sanitize_spoken_text(scene["spoken_text"])
        scene["spoken_text"] = text

        subprocess.run([
            sys.executable, "-m", "edge_tts",
            "--voice", VOICE_NAME,
            "--rate=-4%",
            "--text", text,
            "--write-media", str(part_mp3)
        ], check=True)

        dur = get_audio_duration(part_mp3)
        part_files.append(part_mp3)
        part_durations.append(dur)

    raw_total = sum(part_durations) + pause_gap * (len(part_files) - 1) + 0.6
    extra_per_gap = 0.0
    if raw_total < 30.5 and len(part_files) > 1:
        extra_per_gap = (30.5 - raw_total) / (len(part_files) - 1)

    effective_gap = round(pause_gap + extra_per_gap, 3)
    if extra_per_gap > 0:
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
            "-t", str(effective_gap), "-q:a", "9", str(silence_path)
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    with open(concat_list_path, "w", encoding="utf-8") as cf:
        for idx, (part_mp3, dur) in enumerate(zip(part_files, part_durations)):
            start_t = round(current_time, 3)
            tail = effective_gap if idx < len(part_files) - 1 else 0.65
            end_t = round(current_time + dur + tail, 3)

            timing_records.append({
                "id": story["scenes"][idx]["id"],
                "text": story["scenes"][idx]["spoken_text"],
                "speech_end": round(current_time + dur, 3),
                "start": start_t,
                "end": end_t,
                "method": "sentence_exact_tts",
                "reviewStatus": "auto_verified"
            })

            cf.write(f"file '{part_mp3.name}'\n")
            if idx < len(part_files) - 1:
                cf.write(f"file '{silence_path.name}'\n")
            current_time = end_t

    full_audio = WORK_DIR / "voice_full.mp3"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_list_path),
        "-ar", "48000", "-ac", "2", "-b:a", "192k",
        str(full_audio)
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    with open(WORK_DIR / "timing.json", "w", encoding="utf-8") as tf:
        json.dump(timing_records, tf, ensure_ascii=False, indent=2)

    return full_audio, timing_records


# ==============================================================================
# 7. محرك رسم الخلفيات المضيئة + شخصية Stickman بلون ثابت وحركة ناعمة
# ==============================================================================
def draw_scene_background(draw: ImageDraw.ImageDraw, bg_type: str, t: float):
    ground_y = int(HEIGHT * 0.70)

    if bg_type == "distraction":
        draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(238, 242, 255))
        for i, col in enumerate([(199, 210, 254), (221, 214, 254), (186, 230, 253)]):
            sway = math.sin(t * 2.0 + i * 1.5) * 140
            draw.polygon([(WIDTH // 2, 180), (140 + i * 280 + sway, ground_y), (310 + i * 280 + sway, ground_y)], fill=col)
        draw.rounded_rectangle([160, 470, 920, ground_y], radius=24, fill=(224, 231, 255), outline=(99, 102, 241), width=5)
        draw.rectangle([0, ground_y, WIDTH, HEIGHT], fill=(203, 213, 225))

    elif bg_type == "solitude":
        draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(241, 245, 249))
        draw.polygon([(WIDTH // 2, 220), (260, ground_y), (820, ground_y)], fill=(226, 232, 240))
        draw.rectangle([0, ground_y, WIDTH, HEIGHT], fill=(203, 213, 225))

    elif bg_type == "dawn_call":
        draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(248, 250, 252))
        sun_y = 470 - int(math.sin(min(t, 4.0) * 0.35) * 28)
        draw.ellipse([WIDTH // 2 - 135, sun_y - 135, WIDTH // 2 + 135, sun_y + 135], fill=(254, 240, 138))
        for r in range(1, 4):
            pulse = (t * 40 + r * 65) % 210
            draw.arc([WIDTH // 2 - 150 - pulse, sun_y - 150 - pulse, WIDTH // 2 + 150 + pulse, sun_y + 150 + pulse],
                     start=200, end=340, fill=(251, 146, 60), width=4)
        draw.rectangle([0, ground_y, WIDTH, HEIGHT], fill=(226, 232, 240))

    elif bg_type in ("mosque", "mosque_light"):
        bg_col = (254, 252, 232) if bg_type == "mosque_light" else (248, 250, 252)
        draw.rectangle([0, 0, WIDTH, HEIGHT], fill=bg_col)
        if bg_type == "mosque_light":
            draw.polygon([(WIDTH // 2, 150), (140, ground_y), (940, ground_y)], fill=(254, 249, 195))
        mx = 740
        draw.chord([mx - 165, 440, mx + 165, 730], start=180, end=360, fill=(226, 232, 240), outline=(51, 65, 85), width=6)
        draw.rectangle([mx - 165, 585, mx + 165, ground_y], fill=(241, 245, 249), outline=(51, 65, 85), width=6)
        draw.rectangle([mx + 180, 345, mx + 238, ground_y], fill=(241, 245, 249), outline=(51, 65, 85), width=6)
        draw.polygon([(mx + 170, 345), (mx + 248, 345), (mx + 209, 255)], fill=(234, 88, 12), outline=(51, 65, 85))
        draw.arc([mx - 22, 375, mx + 22, 420], start=40, end=300, fill=(234, 88, 12), width=6)
        draw.rectangle([0, ground_y, WIDTH, HEIGHT], fill=(226, 232, 240))

    elif bg_type == "fire":
        draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(255, 247, 237))
        for i in range(6):
            fx = 165 + i * 150
            fh = 150 + int(math.sin(t * 4.5 + i) * 30)
            draw.polygon([(fx - 50, ground_y), (fx + 50, ground_y), (fx, ground_y - fh)], fill=(252, 165, 165))
            draw.polygon([(fx - 28, ground_y), (fx + 28, ground_y), (fx, ground_y - int(fh * 0.6))], fill=(253, 224, 71))
        draw.rectangle([0, ground_y, WIDTH, HEIGHT], fill=(226, 232, 240))

    elif bg_type == "paradise":
        draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(236, 253, 245))
        draw.ellipse([130, 220, 330, 420], fill=(254, 240, 138))
        for tx in (240, 840):
            draw.rectangle([tx - 14, ground_y - 150, tx + 14, ground_y], fill=(146, 64, 14))
            draw.ellipse([tx - 105, ground_y - 295, tx + 105, ground_y - 115], fill=(167, 243, 208), outline=(16, 185, 129), width=5)
        draw.rectangle([0, ground_y, WIDTH, HEIGHT], fill=(209, 250, 229))

    elif bg_type == "study_desk":
        draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(248, 250, 252))
        draw.ellipse([720, 290, 860, 430], fill=(255, 255, 255), outline=(71, 85, 105), width=6)
        ang = t * 1.2
        draw.line([(790, 360), (790 + math.cos(ang) * 45, 360 + math.sin(ang) * 45)], fill=(234, 88, 12), width=5)
        draw.rectangle([620, ground_y - 110, 920, ground_y - 92], fill=(148, 163, 184))
        draw.rectangle([650, ground_y - 92, 670, ground_y], fill=(100, 116, 139))
        draw.rectangle([870, ground_y - 92, 890, ground_y], fill=(100, 116, 139))
        draw.rectangle([0, ground_y, WIDTH, HEIGHT], fill=(226, 232, 240))

    else:
        draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(248, 250, 252))
        draw.rectangle([WIDTH // 2 - 8, 420, WIDTH // 2 + 8, ground_y], fill=(100, 116, 139))
        draw.polygon([(WIDTH // 2, 450), (WIDTH // 2 + 195, 450), (WIDTH // 2 + 225, 482), (WIDTH // 2 + 195, 514), (WIDTH // 2, 514)], fill=(167, 243, 208), outline=(16, 185, 129))
        draw.polygon([(WIDTH // 2, 535), (WIDTH // 2 - 195, 535), (WIDTH // 2 - 225, 567), (WIDTH // 2 - 195, 599), (WIDTH // 2, 599)], fill=(254, 202, 202), outline=(239, 68, 68))
        draw.rectangle([0, ground_y, WIDTH, HEIGHT], fill=(226, 232, 240))

    draw.line([(0, ground_y), (WIDTH, ground_y)], fill=(71, 85, 105), width=6)


def draw_stickman_rig(draw: ImageDraw.ImageDraw, cx: float, ground_y: float,
                      pose: str, expression: str, facing: int, t: float):
    stroke = (15, 23, 42)
    head_fill = (255, 255, 255)
    accent = (234, 88, 12)
    lw = 13

    ease_in = min(1.0, t / 0.45)
    ease_in = 0.5 - 0.5 * math.cos(ease_in * math.pi)

    draw.ellipse([cx - 72, ground_y - 12, cx + 72, ground_y + 12], fill=(148, 163, 184))

    if pose == "sujood":
        knee = (cx - 40 * facing, ground_y - 12)
        foot = (cx - 115 * facing, ground_y - 10)
        hip = (cx - 35 * facing, ground_y - 95)
        shoulder = (cx + 75 * facing, ground_y - 38)
        head_c = (cx + 135 * facing, ground_y - 45)
        hand = (cx + 125 * facing, ground_y - 10)

        draw.line([hip, knee, foot], fill=stroke, width=lw)
        draw.line([hip, shoulder], fill=stroke, width=lw)
        draw.line([shoulder, hand], fill=stroke, width=lw)
        draw.ellipse([head_c[0] - 48, head_c[1] - 48, head_c[0] + 48, head_c[1] + 48], fill=head_fill, outline=stroke, width=lw)
        draw.arc([head_c[0] - 50, head_c[1] - 52, head_c[0] + 50, head_c[1] + 30], start=190, end=350, fill=accent, width=11)
        return

    bob = math.sin(t * 2.2) * 3.5
    swing = 0.0
    if pose == "walk":
        swing = math.sin(t * 3.8) * 0.45 * ease_in
        bob = abs(math.cos(t * 3.8)) * -6.0 * ease_in
    elif pose == "run":
        swing = math.sin(t * 5.6) * 0.65 * ease_in
        bob = abs(math.cos(t * 5.6)) * -10.0 * ease_in

    target_lean = 0.18 * facing if pose in ("run", "sad_slump") else (-0.12 * facing if pose == "fear_back" else 0.0)
    lean = target_lean * ease_in

    hip = (cx, ground_y - 182 + bob)
    shoulder = (hip[0] + math.sin(lean) * 165, hip[1] - math.cos(lean) * 165)
    head_c = (shoulder[0] + math.sin(lean) * 65, shoulder[1] - 68)

    for side in (-1, 1):
        ang = swing * side
        knee = (hip[0] + math.sin(ang) * 92, hip[1] + math.cos(ang) * 92)
        shin_ang = ang - (0.25 * facing if pose == "run" else 0.08 * facing)
        foot = (knee[0] + math.sin(shin_ang) * 92, min(ground_y - 6, knee[1] + math.cos(shin_ang) * 92))
        draw.line([hip, knee, foot], fill=stroke, width=lw)
        draw.ellipse([knee[0] - 6, knee[1] - 6, knee[0] + 6, knee[1] + 6], fill=stroke)

    draw.line([hip, shoulder], fill=stroke, width=lw)

    if pose == "arms_open":
        lift = ease_in
        for side in (-1, 1):
            elbow = (shoulder[0] + side * (45 + 20 * lift), shoulder[1] + 20 - 60 * lift)
            hand = (elbow[0] + side * (35 + 20 * lift), elbow[1] + 20 - 70 * lift)
            draw.line([shoulder, elbow, hand], fill=stroke, width=lw)
    elif pose == "think":
        elbow = (shoulder[0] + 52 * facing, shoulder[1] + 28)
        hand = (head_c[0] + 24 * facing, head_c[1] + 45)
        draw.line([shoulder, elbow, hand], fill=stroke, width=lw)
        draw.line([shoulder, (shoulder[0] - 36 * facing, shoulder[1] + 85)], fill=stroke, width=lw)
    else:
        for side in (-1, 1):
            arm_ang = -swing * side * 0.85
            elbow = (shoulder[0] + math.sin(arm_ang) * 78, shoulder[1] + math.cos(arm_ang) * 78)
            hand = (elbow[0] + math.sin(arm_ang + 0.25 * facing) * 72, elbow[1] + math.cos(arm_ang + 0.25 * facing) * 72)
            draw.line([shoulder, elbow, hand], fill=stroke, width=lw)

    r = 58
    draw.ellipse([head_c[0] - r, head_c[1] - r, head_c[0] + r, head_c[1] + r], fill=head_fill, outline=stroke, width=lw)
    draw.arc([head_c[0] - r - 2, head_c[1] - r - 4, head_c[0] + r + 2, head_c[1] + 15], start=190, end=350, fill=accent, width=13)
    draw.line([(head_c[0], head_c[1] - r + 8), (head_c[0] + (r + 22) * facing, head_c[1] - r + 14)], fill=accent, width=11)

    ex_off = 16 * facing
    eye_y = head_c[1] - 8
    draw.ellipse([head_c[0] + ex_off - 18, eye_y - 7, head_c[0] + ex_off - 6, eye_y + 7], fill=stroke)
    draw.ellipse([head_c[0] + ex_off + 8, eye_y - 7, head_c[0] + ex_off + 20, eye_y + 7], fill=stroke)

    my = head_c[1] + 22
    mx = head_c[0] + ex_off
    if expression == "happy":
        draw.arc([mx - 18, my - 12, mx + 18, my + 12], start=10, end=170, fill=stroke, width=6)
    elif expression == "sad":
        draw.arc([mx - 18, my, mx + 18, my + 20], start=190, end=350, fill=stroke, width=6)
    elif expression == "shocked":
        draw.ellipse([mx - 10, my - 10, mx + 10, my + 12], outline=stroke, width=6)
    else:
        draw.line([(mx - 15, my + 4), (mx + 15, my + 4)], fill=stroke, width=6)


# ==============================================================================
# 8. محرك الموشن جرافيك للنصوص (دخول/خروج انسيابي + Zoom على الكلمة المنطوقة)
# ==============================================================================
def wrap_words_into_lines(words: list, font: ImageFont.FreeTypeFont, draw: ImageDraw.ImageDraw, max_width: int) -> list:
    lines = []
    current_line = []
    current_w = 0
    space_w = draw.textbbox((0, 0), " ", font=font)[2]

    for idx, w in enumerate(words):
        shaped = shape_arabic(w)
        wb = draw.textbbox((0, 0), shaped, font=font)
        ww = wb[2] - wb[0]
        added = ww if not current_line else (space_w + ww)
        if current_line and (current_w + added > max_width):
            lines.append(current_line)
            current_line = [(idx, w)]
            current_w = ww
        else:
            current_line.append((idx, w))
            current_w += added

    if current_line:
        lines.append(current_line)
    return lines


def draw_motion_graphics_captions(img: Image.Image, text: str, local_t: float,
                                  speech_dur: float, scene_dur: float,
                                  base_font: ImageFont.FreeTypeFont,
                                  zoom_font: ImageFont.FreeTypeFont):
    words = [w for w in text.split() if w.strip()]
    if not words:
        return

    in_dur = 0.35
    out_dur = 0.30
    if local_t < in_dur:
        p = local_t / in_dur
        ease = 1.0 - (1.0 - p) ** 3
        alpha = int(255 * ease)
        slide_y = int((1.0 - ease) * 32)
    elif local_t > max(in_dur, scene_dur - out_dur):
        p = min(1.0, (local_t - (scene_dur - out_dur)) / out_dur)
        ease = p ** 2
        alpha = int(255 * (1.0 - ease))
        slide_y = int(-ease * 24)
    else:
        alpha = 255
        slide_y = 0

    if alpha <= 5:
        return

    weights = [max(2, len(strip_tashkeel(w))) for w in words]
    total_weight = sum(weights)
    speech_progress = min(0.999, max(0.0, local_t / max(0.2, speech_dur)))
    target_weight = speech_progress * total_weight

    active_word_idx = len(words) - 1
    acc = 0.0
    for i, wt in enumerate(weights):
        acc += wt
        if target_weight <= acc:
            active_word_idx = i
            break

    txt_layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    tdraw = ImageDraw.Draw(txt_layer)

    max_line_w = int((SAFE_RIGHT - SAFE_LEFT) * 0.94)
    lines = wrap_words_into_lines(words, base_font, tdraw, max_line_w)
    space_w = tdraw.textbbox((0, 0), " ", font=base_font)[2] + 6
    line_height = 76

    start_y = SAFE_BOTTOM - (len(lines) * line_height) - 18 + slide_y

    normal_fill = (30, 41, 59, alpha)
    active_fill = (15, 23, 42, alpha)
    accent_bar = (234, 88, 12, alpha)

    for line_idx, line_items in enumerate(lines):
        word_boxes = []
        total_w = 0
        for item_pos, (w_idx, w_str) in enumerate(line_items):
            is_active = (w_idx == active_word_idx) and (local_t <= speech_dur + 0.15)
            fnt = zoom_font if is_active else base_font
            shaped = shape_arabic(w_str)
            bb = tdraw.textbbox((0, 0), shaped, font=fnt)
            ww = bb[2] - bb[0]
            wh = bb[3] - bb[1]
            word_boxes.append((w_idx, shaped, fnt, ww, wh, is_active))
            total_w += ww + (space_w if item_pos > 0 else 0)

        cursor_x = (WIDTH + total_w) // 2
        base_y = start_y + line_idx * line_height

        for w_idx, shaped, fnt, ww, wh, is_active in word_boxes:
            cursor_x -= ww
            y_pos = base_y - (8 if is_active else 0)
            tdraw.text((cursor_x, y_pos), shaped, font=fnt, fill=(active_fill if is_active else normal_fill))

            if is_active:
                bar_y = y_pos + wh + 14
                tdraw.rounded_rectangle(
                    [cursor_x + 4, bar_y, cursor_x + ww - 4, bar_y + 5],
                    radius=3,
                    fill=accent_bar
                )
            cursor_x -= space_w

    img.paste(Image.alpha_composite(img.convert("RGBA"), txt_layer).convert("RGB"))


# ==============================================================================
# 9. رندر الإطارات وتجميع الفيديو النهائي (1080x1920 @ 30fps)
# ==============================================================================
def render_frames_and_video(story: dict, timing: list, full_audio: Path) -> Path:
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

    total_duration = timing[-1]["end"]
    total_frames = int(math.ceil(total_duration * FPS))

    title_font = pick_arabic_font(56)
    brand_font = pick_arabic_font(34)
    base_caption_font = pick_arabic_font(48)
    zoom_caption_font = pick_arabic_font(58)

    shaped_title = shape_arabic(story.get("title", ""))
    shaped_brand = shape_arabic(PAGE_SPOKEN_NAME)

    for f_idx in range(total_frames):
        t = f_idx / FPS
        active_idx = len(timing) - 1
        for i, item in enumerate(timing):
            if item["start"] <= t < item["end"]:
                active_idx = i
                break

        scene = story["scenes"][active_idx]
        t_rec = timing[active_idx]
        local_t = t - t_rec["start"]
        scene_dur = max(0.1, t_rec["end"] - t_rec["start"])
        speech_dur = max(0.1, t_rec.get("speech_end", t_rec["end"]) - t_rec["start"])
        progress = min(1.0, max(0.0, local_t / scene_dur))

        bg_type = scene.get("bg_type", "dawn_call")

        img = Image.new("RGB", (WIDTH, HEIGHT), (248, 250, 252))
        draw = ImageDraw.Draw(img, "RGBA")

        draw_scene_background(draw, bg_type, local_t)

        facing = int(scene.get("facing", 1))
        pose = scene.get("pose", "walk")
        if pose in ("walk", "run"):
            ease_move = 0.5 - 0.5 * math.cos(progress * math.pi)
            cx = (WIDTH * 0.34) + ease_move * (WIDTH * 0.28) * facing
        else:
            cx = WIDTH * 0.45

        draw_stickman_rig(
            draw=draw,
            cx=cx,
            ground_y=int(HEIGHT * 0.70),
            pose=pose,
            expression=scene.get("expression", "neutral"),
            facing=facing,
            t=local_t
        )

        if shaped_title:
            bbox = draw.textbbox((0, 0), shaped_title, font=title_font)
            tw = bbox[2] - bbox[0]
            draw.text(((WIDTH - tw) // 2, SAFE_TOP + 16), shaped_title, font=title_font, fill=(15, 23, 42))

        if shaped_brand:
            bb_b = draw.textbbox((0, 0), shaped_brand, font=brand_font)
            bw = bb_b[2] - bb_b[0]
            draw.text(((WIDTH - bw) // 2, SAFE_TOP + 88), shaped_brand, font=brand_font, fill=(100, 116, 139))

        draw_motion_graphics_captions(
            img=img,
            text=scene.get("spoken_text", ""),
            local_t=local_t,
            speech_dur=speech_dur,
            scene_dur=scene_dur,
            base_font=base_caption_font,
            zoom_font=zoom_caption_font
        )

        frame_path = FRAMES_DIR / f"frame_{f_idx:05d}.png"
        img.save(frame_path, "PNG")

        if f_idx in (20, total_frames // 2, max(0, total_frames - 20)):
            img.save(PREVIEWS_DIR / f"preview_{f_idx:05d}.png", "PNG")

    final_mp4 = EXPORTS_DIR / "final.mp4"
    subprocess.run([
        "ffmpeg", "-y",
        "-framerate", str(FPS),
        "-i", str(FRAMES_DIR / "frame_%05d.png"),
        "-i", str(full_audio),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        "-shortest",
        str(final_mp4)
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    return final_mp4


# ==============================================================================
# 10. بوابة فحص الجودة التلقائية قبل النشر
# ==============================================================================
def run_quality_gate(video_path: Path, audio_path: Path, story: dict) -> bool:
    checks = []
    passed = True

    if not video_path.exists() or video_path.stat().st_size < 50_000:
        checks.append("- [فشل] ملف الفيديو النهائي مفقود أو حجمه صغير جداً.")
        passed = False
    else:
        checks.append(f"- [ناجح] حجم الملف سليم ({video_path.stat().st_size // 1024} KB).")

    try:
        v_dur = get_audio_duration(video_path)
        a_dur = get_audio_duration(audio_path)
        diff = abs(v_dur - a_dur)
        if diff <= 0.40 and 29.5 <= v_dur <= 62.0:
            checks.append(f"- [ناجح] المدة مطابقة للمطلوب ({v_dur:.2f} ثانية، فرق التزامن {diff:.2f} ثانية).")
        else:
            checks.append(f"- [فشل] المدة خارج النطاق المطلوب (30-60 ثانية): {v_dur:.2f} ثانية.")
            passed = False
    except Exception as e:
        checks.append(f"- [فشل] تعذر فحص الترميز عبر ffprobe: {e}")
        passed = False

    if story.get("category") == "hadith_sahih":
        src = story.get("hadith_source", "")
        if "البخاري" in src or "مسلم" in src:
            checks.append(f"- [ناجح] توثيق الحديث من الصحيحين ({src}).")
        else:
            checks.append("- [فشل] الحديث غير موثق من البخاري أو مسلم.")
            passed = False

    for p_img in PREVIEWS_DIR.glob("*.png"):
        im = Image.open(p_img).convert("L")
        if im.getextrema() == (0, 0):
            checks.append(f"- [فشل] إطار أسود مكتشف في {p_img.name}.")
            passed = False
    checks.append("- [ناجح] ثبات لون شخصية Stickman وسلامة الطبقات وعدم وجود إطارات سوداء.")

    report = (
        f"# تقرير فحص الجودة (QA Report)\n"
        f"- التاريخ: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"- التصنيف: {story.get('category', 'general')}\n"
        f"- النتيجة العامة: {'ناجح وجاهز للنشر' if passed else 'فشل — تم إيقاف النشر'}\n\n"
        + "\n".join(checks) + "\n"
    )
    with open(WORK_DIR / "qa-report.md", "w", encoding="utf-8") as rf:
        rf.write(report)

    print(report)
    return passed


# ==============================================================================
# 11. النشر التلقائي على ريلز فيسبوك (Facebook Graph API)
# ==============================================================================
def publish_to_facebook_reels(video_path: Path, description: str):
    if not FB_PAGE_ID or not FB_ACCESS_TOKEN:
        raise RuntimeError("متغيرات البيئة FB_PAGE_ID أو FB_ACCESS_TOKEN غير مضبوطة في GitHub Secrets.")

    base_url = f"https://graph.facebook.com/v21.0/{FB_PAGE_ID}/video_reels"

    init_resp = requests.post(
        base_url,
        params={"upload_phase": "start", "access_token": FB_ACCESS_TOKEN},
        timeout=30
    )
    init_resp.raise_for_status()
    init_data = init_resp.json()
    video_id = init_data["video_id"]
    upload_url = init_data["upload_url"]

    file_size = video_path.stat().st_size
    with open(video_path, "rb") as vf:
        up_resp = requests.post(
            upload_url,
            headers={
                "Authorization": f"OAuth {FB_ACCESS_TOKEN}",
                "offset": "0",
                "file_size": str(file_size),
            },
            data=vf,
            timeout=180
        )
    up_resp.raise_for_status()

    fin_resp = requests.post(
        base_url,
        params={
            "upload_phase": "finish",
            "video_id": video_id,
            "video_state": "PUBLISHED",
            "description": description,
            "access_token": FB_ACCESS_TOKEN,
        },
        timeout=30
    )
    fin_resp.raise_for_status()
    print(f"[SUCCESS] تم نشر الريلز على صفحة فيسبوك بنجاح! Video ID: {video_id}")


# ==============================================================================
# نقطة التشغيل الرئيسية
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="محرك إنتاج ونشر ريلز ستيك مان - منسى")
    parser.add_argument("--local", action="store_true", help="توليد الفيديو وفحصه محلياً فقط بدون نشره على فيسبوك")
    args = parser.parse_args()

    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR)
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    history = load_history()

    print("=== [1/5] توليد القصة وفحص المصادر ومنع التكرار ===")
    story = generate_story(history)
    with open(WORK_DIR / "story-lock.json", "w", encoding="utf-8") as sf:
        json.dump(story, sf, ensure_ascii=False, indent=2)

    print("=== [2/5] توليد التعليق الصوتي وضبط timing.json ===")
    full_audio, timing = synthesize_voice_and_timing(story)

    print("=== [3/5] تحريك شخصية Stickman وموشن جرافيك النصوص (1080x1920 @ 30fps) ===")
    final_video = render_frames_and_video(story, timing, full_audio)

    print("=== [4/5] فحص الجودة التلقائي (QA Gate) ===")
    if not run_quality_gate(final_video, full_audio, story):
        sys.exit(1)

    now_iq = get_iraq_now()
    history.append({
        "date": now_iq.strftime("%Y-%m-%d"),
        "time": now_iq.strftime("%H:%M"),
        "slot": get_day_slot_name(now_iq),
        "category": story.get("category", "general"),
        "title": story.get("title", ""),
        "hadith_source": story.get("hadith_source", "")
    })
    save_history(history)

    if args.local:
        print(f"[DONE] اكتمل الإنتاج المحلي بنجاح: {final_video}")
        return

    print("=== [5/5] النشر التلقائي على ريلز فيسبوك ===")
    caption = f"{story.get('title', '')}\n\n{story.get('caption', '')}".strip()
    publish_to_facebook_reels(final_video, caption)


if __name__ == "__main__":
    main()
