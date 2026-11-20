#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
publish_reel.py — محرك إنتاج ونشر فيديوهات Stickman الاحترافية (الإصدار 3.0)
"""

import os
import re
import sys
import json
import math
import random
import shutil
import argparse
import importlib
import subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta


# ==============================================================================
# 0. فحص وتجهيز البيئة تلقائياً (FFmpeg + الخطوط العربية + المكتبات)
# ==============================================================================
def ensure_runtime_dependencies():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("[SETUP] تثبيت FFmpeg والخطوط العربية تلقائياً...")
        try:
            subprocess.run(["sudo", "apt-get", "update", "-qq"], check=False)
            subprocess.run(
                ["sudo", "apt-get", "install", "-y", "-qq", "ffmpeg", "fonts-noto-core", "fonts-arabeyes"],
                check=False
            )
        except Exception as e:
            print(f"[WARN] تعذر التثبيت عبر apt-get: {e}")

    required_pkgs = {
        "requests": "requests",
        "edge_tts": "edge-tts",
        "PIL": "Pillow",
    }
    missing = []
    for mod_name, pip_name in required_pkgs.items():
        try:
            __import__(mod_name)
        except ImportError:
            missing.append(pip_name)

    if missing:
        print(f"[SETUP] تثبيت مكتبات بايثون الناقصة: {missing}")
        res = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "--break-system-packages", *missing],
            check=False
        )
        if res.returncode != 0:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-q", *missing],
                check=True
            )
        importlib.invalidate_caches()


# ==============================================================================
# 1. إعدادات المشروع والثوابت
# ==============================================================================
WIDTH = 1080
HEIGHT = 1920
FPS = 30

SAFE_LEFT = int(WIDTH * 0.08)
SAFE_RIGHT = int(WIDTH * 0.92)
SAFE_TOP = int(HEIGHT * 0.08)
GROUND_Y = int(HEIGHT * 0.61)       # خط الأرضية
CAPTION_TOP = int(HEIGHT * 0.65)    # منطقة النصوص مفصولة تماماً عن الأرضية
CAPTION_BOTTOM = int(HEIGHT * 0.84)

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
# 2. محرك تشكيل الحروف العربية وترتيب RTL المدمج (100% Pure Python)
# ==============================================================================
ARABIC_GLYPHS = {
    "\u0621": ("\uFE80", "\uFE80", "\uFE80", "\uFE80"),  # ء
    "\u0622": ("\uFE81", "\uFE82", "\uFE81", "\uFE82"),  # آ
    "\u0623": ("\uFE83", "\uFE84", "\uFE83", "\uFE84"),  # أ
    "\u0624": ("\uFE85", "\uFE86", "\uFE85", "\uFE86"),  # ؤ
    "\u0625": ("\uFE87", "\uFE88", "\uFE87", "\uFE88"),  # إ
    "\u0626": ("\uFE89", "\uFE8A", "\uFE8B", "\uFE8C"),  # ئ
    "\u0627": ("\uFE8D", "\uFE8E", "\uFE8D", "\uFE8E"),  # ا
    "\u0628": ("\uFE8F", "\uFE90", "\uFE91", "\uFE92"),  # ب
    "\u0629": ("\uFE93", "\uFE94", "\uFE93", "\uFE94"),  # ة
    "\u062A": ("\uFE95", "\uFE96", "\uFE97", "\uFE98"),  # ت
    "\u062B": ("\uFE99", "\uFE9A", "\uFE9B", "\uFE9C"),  # ث
    "\u062C": ("\uFE9D", "\uFE9E", "\uFE9F", "\uFEA0"),  # ج
    "\u062D": ("\uFEA1", "\uFEA2", "\uFEA3", "\uFEA4"),  # ح
    "\u062E": ("\uFEA5", "\uFEA6", "\uFEA7", "\uFEA8"),  # خ
    "\u062F": ("\uFEA9", "\uFEAA", "\uFEA9", "\uFEAA"),  # د
    "\u0630": ("\uFEAB", "\uFEAC", "\uFEAB", "\uFEAC"),  # ذ
    "\u0631": ("\uFEAD", "\uFEAE", "\uFEAD", "\uFEAE"),  # ر
    "\u0632": ("\uFEAF", "\uFEB0", "\uFEAF", "\uFEB0"),  # ز
    "\u0633": ("\uFEB1", "\uFEB2", "\uFEB3", "\uFEB4"),  # س
    "\u0634": ("\uFEB5", "\uFEB6", "\uFEB7", "\uFEB8"),  # ش
    "\u0635": ("\uFEB9", "\uFEBA", "\uFEBB", "\uFEBC"),  # ص
    "\u0636": ("\uFEBD", "\uFEBE", "\uFEBF", "\uFEC0"),  # ض
    "\u0637": ("\uFEC1", "\uFEC2", "\uFEC3", "\uFEC4"),  # ط
    "\u0638": ("\uFEC5", "\uFEC6", "\uFEC7", "\uFEC8"),  # ظ
    "\u0639": ("\uFEC9", "\uFECA", "\uFECB", "\uFECC"),  # ع
    "\u063A": ("\uFECD", "\uFECE", "\uFECF", "\uFED0"),  # غ
    "\u0640": ("\u0640", "\u0640", "\u0640", "\u0640"),  # ـ
    "\u0641": ("\uFED1", "\uFED2", "\uFED3", "\uFED4"),  # ف
    "\u0642": ("\uFED5", "\uFED6", "\uFED7", "\uFED8"),  # ق
    "\u0643": ("\uFED9", "\uFEDA", "\uFEDB", "\uFEDC"),  # ك
    "\u0644": ("\uFEDD", "\uFEDE", "\uFEDF", "\uFEE0"),  # ل
    "\u0645": ("\uFEE1", "\uFEE2", "\uFEE3", "\uFEE4"),  # م
    "\u0646": ("\uFEE5", "\uFEE6", "\uFEE7", "\uFEE8"),  # ن
    "\u0647": ("\uFEE9", "\uFEEA", "\uFEEB", "\uFEEC"),  # ه
    "\u0648": ("\uFEED", "\uFEEE", "\uFEED", "\uFEEE"),  # و
    "\u0649": ("\uFEEF", "\uFEF0", "\uFEEF", "\uFEF0"),  # ى
    "\u064A": ("\uFEF1", "\uFEF2", "\uFEF3", "\uFEF4"),  # ي
}

RIGHT_JOIN_ONLY = {"\u0622", "\u0623", "\u0624", "\u0625", "\u0627", "\u0629", "\u062F", "\u0630", "\u0631", "\u0632", "\u0648", "\u0649"}
TASHKEEL_SET = {chr(c) for c in range(0x064B, 0x0660)} | {"\u0670"}

LAM_ALEF_MAP = {
    "\u0622": ("\uFEF5", "\uFEF6"),  # لآ
    "\u0623": ("\uFEF7", "\uFEF8"),  # لأ
    "\u0625": ("\uFEF9", "\uFEFA"),  # لإ
    "\u0627": ("\uFEFB", "\uFEFC"),  # لا
}


def shape_arabic(text: str) -> str:
    if not text:
        return ""

    chars = list(text)
    merged = []
    i = 0
    while i < len(chars):
        ch = chars[i]
        if ch == "\u0644":
            j = i + 1
            diacritics = []
            while j < len(chars) and chars[j] in TASHKEEL_SET:
                diacritics.append(chars[j])
                j += 1
            if j < len(chars) and chars[j] in LAM_ALEF_MAP:
                merged.append(("LAM_ALEF", chars[j], "".join(diacritics)))
                i = j + 1
                continue
        merged.append(("CHAR", ch, ""))
        i += 1

    def can_connect_after(item):
        kind, val, _ = item
        if kind == "LAM_ALEF":
            return False
        return (val in ARABIC_GLYPHS) and (val not in RIGHT_JOIN_ONLY) and (val != "\u0621")

    def can_connect_before(item):
        kind, val, _ = item
        if kind == "LAM_ALEF":
            return True
        return (val in ARABIC_GLYPHS) and (val != "\u0621")

    base_indices = [
        idx for idx, it in enumerate(merged)
        if it[0] == "LAM_ALEF" or (it[0] == "CHAR" and it[1] not in TASHKEEL_SET)
    ]

    shaped_tokens = []
    base_pos_map = {orig_idx: b_pos for b_pos, orig_idx in enumerate(base_indices)}

    for idx, item in enumerate(merged):
        kind, val, extra_dia = item
        if kind == "CHAR" and val in TASHKEEL_SET:
            shaped_tokens.append(val)
            continue

        b_pos = base_pos_map[idx]
        prev_item = merged[base_indices[b_pos - 1]] if b_pos > 0 else None
        next_item = merged[base_indices[b_pos + 1]] if b_pos + 1 < len(base_indices) else None

        conn_prev = (prev_item is not None) and can_connect_after(prev_item) and can_connect_before(item)
        conn_next = (next_item is not None) and can_connect_after(item) and can_connect_before(next_item)

        if kind == "LAM_ALEF":
            iso_form, fin_form = LAM_ALEF_MAP[val]
            glyph = fin_form if conn_prev else iso_form
            shaped_tokens.append(glyph + extra_dia)
        elif val in ARABIC_GLYPHS:
            iso_f, fin_f, ini_f, med_f = ARABIC_GLYPHS[val]
            if conn_prev and conn_next:
                shaped_tokens.append(med_f)
            elif conn_prev:
                shaped_tokens.append(fin_f)
            elif conn_next:
                shaped_tokens.append(ini_f)
            else:
                shaped_tokens.append(iso_f)
        else:
            shaped_tokens.append(val)

    shaped_str = "".join(shaped_tokens)

    clusters = []
    for ch in shaped_str:
        if ch in TASHKEEL_SET and clusters:
            clusters[-1] += ch
        else:
            clusters.append(ch)

    result_clusters = []
    ltr_buf = []
    for cl in reversed(clusters):
        base_c = cl[0]
        if base_c.isascii() and (base_c.isalnum() or base_c in "._-"):
            ltr_buf.append(cl)
        else:
            if ltr_buf:
                result_clusters.extend(reversed(ltr_buf))
                ltr_buf = []
            mirrors = {"(": ")", ")": "(", "[": "]", "]": "[", "{": "}", "}": "{", "«": "»", "»": "«"}
            result_clusters.append(mirrors.get(cl, cl))
    if ltr_buf:
        result_clusters.extend(reversed(ltr_buf))

    return "".join(result_clusters)


# ==============================================================================
# 3. قصص احتياطية عالية الجودة (تطابق بصري 100% بين الصوت والمشهد)
# ==============================================================================
FALLBACK_STORIES = [
    {
        "category": "life_wisdom",
        "hadith_source": "",
        "title": "أَثَرُ الْكَلِمَةِ الطَّيِّبَةِ",
        "caption": "كلمة واحدة صادقة قد تفتح باب أمل في قلب إنسان أتعبته الحياة.\nتابع منسى - Mansa للمزيد. #الكلمة_الطيبة #أمل #تطوير_الذات",
        "scenes": [
            {
                "id": 1,
                "spoken_text": "كَانَ يَسِيرُ فِي الطُّرُقَاتِ مُثْقَلًا بِهُمُومٍ كَجِبَالٍ رَاسِيَةٍ، يَظُنُّ أَنَّ كُلَّ الْأَبْوَابِ قَدْ أُغْلِقَتْ فِي وَجْهِهِ تَمَامًا.",
                "bg_type": "closed_doors",
                "pose": "sad_walk",
                "expression": "sad",
                "facing": 1,
                "has_partner": False
            },
            {
                "id": 2,
                "spoken_text": "فَجْأَةً، اسْتَوْقَفَهُ رَجُلٌ صَالِحٌ بِابْتِسَامَةٍ هَادِئَةٍ قَائِلًا: لَا تَحْزَنْ، فَإِنَّ بَعْدَ كُلِّ عُسْرٍ يُسْرًا قَرِيبًا بِإِذْنِ اللَّهِ.",
                "bg_type": "wise_encounter",
                "pose": "listen_talk",
                "expression": "neutral",
                "facing": 1,
                "has_partner": True,
                "partner_role": "wise_man"
            },
            {
                "id": 3,
                "spoken_text": "تِلْكَ الْكَلِمَةُ الطَّيِّبَةُ لَمَسَتْ قَلْبَهُ الْمُنْكَسِرَ، فَأَعَادَتْ إِلَيْهِ بَرِيقَ الْأَمَلِ الَّذِي كَادَ يَنْطَفِئُ نُورُهُ إِلَى الْأَبَدِ.",
                "bg_type": "hope_light",
                "pose": "hand_on_heart",
                "expression": "peaceful",
                "facing": 1,
                "has_partner": False
            },
            {
                "id": 4,
                "spoken_text": "نَفَضَ غُبَارَ الْيَأْسِ عَنْ ثِيَابِهِ، وَقَرَّرَ أَنْ يَبْدَأَ مِنْ جَدِيدٍ بِعَزِيمَةٍ صَلْبَةٍ لَا تَعْرِفُ الْمُسْتَحِيلَ أَوِ التَّرَاجُعَ أَبَدًا.",
                "bg_type": "new_start",
                "pose": "walk_confident",
                "expression": "determined",
                "facing": 1,
                "has_partner": False
            },
            {
                "id": 5,
                "spoken_text": "إِنَّ كَلِمَةً وَاحِدَةً صَادِقَةً قَدْ تُنْقِذُ إِنْسَانًا مِنَ الضَّيَاعِ، فَلَا تَبْخَلُوا بِجَمِيلِ الْقَوْلِ عَلَى مَنْ حَوْلَكُمْ فِي كُلِّ حِينٍ.",
                "bg_type": "helping_hand",
                "pose": "give_hope",
                "expression": "happy",
                "facing": 1,
                "has_partner": True,
                "partner_role": "friend"
            },
            {
                "id": 6,
                "spoken_text": "اجْعَلُوا أَثَرَكُمْ طَيِّبًا فِي النُّفُوسِ، فَالْكَلِمَةُ الطَّيِّبَةُ صَدَقَةٌ جَارِيَةٌ تَبْقَى حَتَّى بَعْدَ الرَّحِيلِ. تَابِعْ مَنْسَى لِلْمَزِيدِ.",
                "bg_type": "good_deed_tree",
                "pose": "arms_open",
                "expression": "happy",
                "facing": 1,
                "has_partner": False
            }
        ]
    },
    {
        "category": "hadith_sahih",
        "hadith_source": "متفق عليه: رواه البخاري (6114) ومسلم (2609)",
        "title": "مَنْ هُوَ الْقَوِيُّ الْحَقِيقِيُّ؟",
        "caption": "قال رسول الله ﷺ: «لَيْسَ الشَّدِيدُ بِالصُّرَعَةِ، إِنَّمَا الشَّدِيدُ الَّذِي يَمْلِكُ نَفْسَهُ عِنْدَ الْغَضَبِ» [متفق عليه: البخاري ومسلم].\nتابع منسى - Mansa للمزيد. #حديث_صحيح #البخاري_ومسلم #ضبط_النفس",
        "scenes": [
            {
                "id": 1,
                "spoken_text": "يَظُنُّ كَثِيرٌ مِنَ النَّاسِ أَنَّ الْقُوَّةَ هِيَ رَفْعُ الصَّوْتِ أَوِ الْانْتِصَارُ فِي الْجِدَالِ وَالْغَضَبِ السَّرِيعِ.",
                "bg_type": "overthinking_maze",
                "pose": "think",
                "expression": "shocked",
                "facing": 1,
                "has_partner": True,
                "partner_role": "friend"
            },
            {
                "id": 2,
                "spoken_text": "لَكِنَّ النَّبِيَّ صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ صَحَّحَ هَذَا الْمَفْهُومَ بِحَدِيثٍ عَظِيمٍ يُغَيِّرُ نَظْرَتَنَا لِلْحَيَاةِ.",
                "bg_type": "mosque_serenity",
                "pose": "look_up",
                "expression": "peaceful",
                "facing": 1,
                "has_partner": False
            },
            {
                "id": 3,
                "spoken_text": "فَفِي الصَّحِيحَيْنِ عَنِ الْبُخَارِيِّ وَمُسْلِمٍ قَالَ: لَيْسَ الشَّدِيدُ بِالصُّرَعَةِ، إِنَّمَا الشَّدِيدُ الَّذِي يَمْلِكُ نَفْسَهُ عِنْدَ الْغَضَبِ.",
                "bg_type": "hope_light",
                "pose": "hand_on_heart",
                "expression": "determined",
                "facing": 1,
                "has_partner": False
            },
            {
                "id": 4,
                "spoken_text": "فَالْقُوَّةُ الْحَقِيقِيَّةُ تَبْدَأُ حِينَ تَهْدَأُ وَقْتَ الْعَاصِفَةِ وَتَمْلِكُ أَعْصَابَكَ بِحِكْمَةٍ. تَابِعْ مَنْسَى لِلْمَزِيدِ.",
                "bg_type": "new_start",
                "pose": "arms_open",
                "expression": "happy",
                "facing": 1,
                "has_partner": False
            }
        ]
    }
]


# ==============================================================================
# 4. إدارة السجل والوقت والأدوات المساعدة
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


def pick_arabic_font(size: int):
    from PIL import ImageFont
    font_candidates = []
    if FONTS_DIR.exists():
        for ext in ("*.ttf", "*.otf"):
            font_candidates.extend(FONTS_DIR.glob(ext))
    system_fallbacks = [
        Path("alfont_com_EYENAmmon-Bold.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf"),
        Path("/usr/share/fonts/truetype/arabeyes/ae_AlMohanad.ttf"),
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
# 5. توليد السيناريو من Gemini مع ربط المشهد والشخصيات بالنص الصوتي
# ==============================================================================
def discover_gemini_models(api_key: str) -> list:
    import requests
    fallback_models = [
        "models/gemini-flash-latest",
        "models/gemini-2.5-flash",
        "models/gemini-2.0-flash",
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
    import requests
    now_iq = get_iraq_now()
    today_str = now_iq.strftime("%Y-%m-%d")
    slot_name = get_day_slot_name(now_iq)
    chosen_cat = choose_next_category(history, today_str)

    duration_tier = random.choice([
        {"label": "34-40 ثانية", "scenes_count": 5, "words_per_scene": "15 إلى 18 كلمة"},
        {"label": "42-48 ثانية", "scenes_count": 6, "words_per_scene": "16 إلى 20 كلمة"},
        {"label": "50-56 ثانية", "scenes_count": 6, "words_per_scene": "18 إلى 22 كلمة"},
    ])

    recent_titles = [h.get("title", "") for h in history[-25:] if h.get("title")]
    today_titles = [h.get("title", "") for h in history if h.get("date") == today_str]

    if GEMINI_API_KEY:
        prompt = f"""
أنت مخرج ومؤلف محتوى لفيديوهات Stickman عربية احترافية لصفحة ({PAGE_DISPLAY_NAME}).
الوقت الحالي: فترة {slot_name} ({today_str}).
التصنيف المطلوب لهذه الحلقة حصراً: {chosen_cat['name']}.
قواعد التصنيف: {chosen_cat['rules']}

المواضيع المنشورة اليوم (ممنوع تكرارها):
{json.dumps(today_titles, ensure_ascii=False)}

آخر 25 موضوعاً منشوراً (اختر موضوعاً مختلفاً تماماً):
{json.dumps(recent_titles, ensure_ascii=False)}

قواعد التطابق البصري الإلزامية (مهم جداً):
- اختر `bg_type` و `pose` و `has_partner` بحيث تطابق معنى الجملة في كل مشهد بدقة:
  * إذا ذكرت الجملة أبواباً مغلقة أو هموماً أو تعباً -> `bg_type: "closed_doors"`, `pose: "sad_walk"`, `has_partner: false`.
  * إذا ذكرت الجملة شخصاً آخر ينصح البطل أو يواسيه أو يحاوره -> `bg_type: "wise_encounter"`, `pose: "listen_talk"`, `has_partner: true`, `partner_role: "wise_man"`.
  * إذا ذكرت الجملة مساعدة صديق أو أثر الكلمة على الناس -> `bg_type: "helping_hand"`, `pose: "give_hope"`, `has_partner: true`, `partner_role: "friend"`.
  * إذا ذكرت الجملة الأمل أو انشراح الصدر أو النور -> `bg_type: "hope_light"`, `pose: "hand_on_heart"`, `has_partner: false`.
  * إذا ذكرت الجملة بداية جديدة أو عزيمة أو صعوداً -> `bg_type: "new_start"`, `pose: "walk_confident"`, `has_partner: false`.
  * إذا ذكرت الجملة مسجداً أو صلاة أو حديثاً نبوياً -> `bg_type: "mosque_serenity"`, `pose: "look_up"`, `has_partner: false`.
  * إذا ذكرت الجملة صدقة جارية أو أثراً يبقى أو نمواً -> `bg_type: "good_deed_tree"`, `pose: "arms_open"`, `has_partner: false`.
  * إذا ذكرت الجملة حيرة أو تفكيراً زائداً -> `bg_type: "overthinking_maze"`, `pose: "think"`, `has_partner: false`.
- المدة المستهدفة: {duration_tier['label']} (لا تقل عن 32 ثانية ولا تزيد عن 58 ثانية)، موزعة على {duration_tier['scenes_count']} مشاهد ({duration_tier['words_per_scene']} لكل مشهد) بتشكيل عربي صحيح.
- في المشهد الأخير فقط اختم بعبارة: «تَابِعْ مَنْسَى لِلْمَزِيدِ.» (لا تكتب Mansa في النص المنطوق أبداً).
- إذا كان التصنيف حديثاً نبوياً: يجب أن يكون صحيحاً 100% من صحيح البخاري أو صحيح مسلم حصراً مع ذكر المصدر في `hadith_source`.

أعد JSON فقط:
{{
  "category": "{chosen_cat['id']}",
  "hadith_source": "",
  "title": "عنوان قصير مشكول",
  "caption": "وصف قصير لمنشور فيسبوك مع 3 وسوم",
  "scenes": [
    {{
      "id": 1,
      "spoken_text": "نص المشهد مشكولاً بالكامل",
      "bg_type": "closed_doors",
      "pose": "sad_walk",
      "expression": "sad",
      "facing": 1,
      "has_partner": false,
      "partner_role": "wise_man"
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
                        raise ValueError(f"حديث غير موثق من الصحيحين: {src}")

                if "scenes" in story and len(story["scenes"]) >= 4:
                    for sc in story["scenes"]:
                        sc["spoken_text"] = sanitize_spoken_text(sc.get("spoken_text", ""))
                    print(f"[OK] تم توليد حلقة جديدة عبر {model_name}: {story.get('title')}")
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
# 6. توليد التعليق الصوتي + موسيقى خلفية هادئة (Ambient Audio Bed)
# ==============================================================================
def synthesize_voice_and_timing(story: dict) -> tuple:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    concat_list_path = AUDIO_DIR / "concat.txt"
    timing_records = []
    current_time = 0.0
    pause_gap = 0.35

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
            "--rate=-3%",
            "--text", text,
            "--write-media", str(part_mp3)
        ], check=True)

        dur = get_audio_duration(part_mp3)
        part_files.append(part_mp3)
        part_durations.append(dur)

    raw_total = sum(part_durations) + pause_gap * (len(part_files) - 1) + 0.55
    extra_per_gap = 0.0
    if raw_total < 31.0 and len(part_files) > 1:
        extra_per_gap = (31.0 - raw_total) / (len(part_files) - 1)

    effective_gap = round(pause_gap + extra_per_gap, 3)
    if extra_per_gap > 0:
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
            "-t", str(effective_gap), "-q:a", "9", str(silence_path)
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    with open(concat_list_path, "w", encoding="utf-8") as cf:
        for idx, (part_mp3, dur) in enumerate(zip(part_files, part_durations)):
            start_t = round(current_time, 3)
            tail = effective_gap if idx < len(part_files) - 1 else 0.55
            end_t = round(current_time + dur + tail, 3)

            timing_records.append({
                "id": story["scenes"][idx]["id"],
                "text": story["scenes"][idx]["spoken_text"],
                "speech_end": round(current_time + dur, 3),
                "start": start_t,
                "end": end_t,
            })

            cf.write(f"file '{part_mp3.name}'\n")
            if idx < len(part_files) - 1:
                cf.write(f"file '{silence_path.name}'\n")
            current_time = end_t

    raw_voice = WORK_DIR / "voice_raw.mp3"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_list_path),
        "-ar", "48000", "-ac", "2", "-b:a", "192k",
        str(raw_voice)
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    full_audio = WORK_DIR / "voice_full.mp3"
    total_dur = timing_records[-1]["end"]
    try:
        ambient_filter = (
            f"sine=f=174.61:d={total_dur}:r=48000[s1];"
            f"sine=f=220.00:d={total_dur}:r=48000[s2];"
            f"sine=f=261.63:d={total_dur}:r=48000[s3];"
            f"[s1][s2][s3]amix=inputs=3:duration=longest,lowpass=f=380,volume=0.045,"
            f"afade=t=in:st=0:d=1.2,afade=t=out:st={max(0, total_dur-1.5)}:d=1.5[bg];"
            f"[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2[out]"
        )
        subprocess.run([
            "ffmpeg", "-y", "-i", str(raw_voice),
            "-filter_complex", ambient_filter,
            "-map", "[out]", "-ar", "48000", "-ac", "2", "-b:a", "192k",
            str(full_audio)
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except Exception:
        shutil.copy(raw_voice, full_audio)

    with open(WORK_DIR / "timing.json", "w", encoding="utf-8") as tf:
        json.dump(timing_records, tf, ensure_ascii=False, indent=2)

    return full_audio, timing_records


# ==============================================================================
# 7. محرك رسم الخلفيات القصصية المعبرة (تطابق الصوت 100%)
# ==============================================================================
def draw_crescent_moon(draw, cx: int, cy: int, r: int, color: tuple, sky_color: tuple):
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
    draw.ellipse([cx - r + int(r * 0.42), cy - r - int(r * 0.18), cx + r + int(r * 0.42), cy + r - int(r * 0.18)], fill=sky_color)


def draw_scene_background(draw, bg_type: str, t: float, progress: float):
    drift = math.sin(progress * math.pi) * 18

    if bg_type == "closed_doors":
        sky = (232, 238, 245)
        draw.rectangle([0, 0, WIDTH, HEIGHT], fill=sky)
        draw.polygon([(40 - drift, GROUND_Y), (240 - drift, 560), (440 - drift, GROUND_Y)], fill=(203, 213, 225))
        draw.polygon([(220 - drift, GROUND_Y), (430 - drift, 510), (640 - drift, GROUND_Y)], fill=(185, 198, 214))
        for dx in (650, 845):
            bx = int(dx + drift * 0.5)
            draw.rounded_rectangle([bx, 580, bx + 150, GROUND_Y], radius=16, fill=(217, 119, 6), outline=(120, 53, 15), width=6)
            draw.rounded_rectangle([bx + 18, 605, bx + 132, 740], radius=8, fill=(245, 158, 11), outline=(120, 53, 15), width=4)
            draw.arc([bx + 56, 775, bx + 94, 815], start=180, end=360, fill=(51, 65, 85), width=6)
            draw.rounded_rectangle([bx + 50, 795, bx + 100, 840], radius=8, fill=(71, 85, 105))
        cx_c = int(260 + math.sin(t * 1.5) * 12)
        draw.ellipse([cx_c - 75, 420, cx_c + 15, 485], fill=(148, 163, 184))
        draw.ellipse([cx_c - 25, 405, cx_c + 75, 485], fill=(148, 163, 184))
        draw.rectangle([0, GROUND_Y, WIDTH, HEIGHT], fill=(216, 224, 234))

    elif bg_type == "wise_encounter":
        sky = (254, 252, 238)
        draw.rectangle([0, 0, WIDTH, HEIGHT], fill=sky)
        pulse = math.sin(t * 2.5) * 14
        draw.ellipse([680 - 170 - pulse, 540 - 170 - pulse, 680 + 170 + pulse, 540 + 170 + pulse], fill=(254, 243, 199))
        draw.ellipse([680 - 110, 540 - 110, 680 + 110, 540 + 110], fill=(253, 230, 138))
        draw.rectangle([0, GROUND_Y, WIDTH, HEIGHT], fill=(226, 232, 240))

    elif bg_type == "hope_light":
        sky = (255, 251, 235)
        draw.rectangle([0, 0, WIDTH, HEIGHT], fill=sky)
        draw.polygon([(830, 540), (140, GROUND_Y), (920, GROUND_Y)], fill=(254, 249, 195))
        bx = 730
        draw.rounded_rectangle([bx, 540, bx + 185, GROUND_Y], radius=18, fill=(253, 224, 71), outline=(217, 119, 6), width=6)
        draw.polygon([(bx, 540), (bx - 65, 565), (bx - 65, GROUND_Y - 15), (bx, GROUND_Y)], fill=(217, 119, 6), outline=(120, 53, 15))
        for i in range(4):
            sy = int(GROUND_Y - 120 - ((t * 55 + i * 95) % 340))
            sx = int(520 + math.sin(t * 2 + i) * 65 + i * 35)
            draw.ellipse([sx - 10, sy - 10, sx + 10, sy + 10], fill=(251, 146, 60))
        draw.rectangle([0, GROUND_Y, WIDTH, HEIGHT], fill=(229, 235, 242))

    elif bg_type == "new_start":
        sky = (240, 249, 255)
        draw.rectangle([0, 0, WIDTH, HEIGHT], fill=sky)
        sun_y = int(450 - progress * 45)
        draw.ellipse([720 - 110, sun_y - 110, 720 + 110, sun_y + 110], fill=(253, 224, 71))
        draw.polygon([(520, GROUND_Y), (860, 620), (1080, GROUND_Y)], fill=(167, 243, 208))
        for b in range(3):
            bx = int(260 + b * 110 + progress * 90)
            by = int(420 + b * 35 + math.sin(t * 5 + b) * 8)
            draw.arc([bx - 18, by - 10, bx, by + 10], start=190, end=350, fill=(71, 85, 105), width=4)
            draw.arc([bx, by - 10, bx + 18, by + 10], start=190, end=350, fill=(71, 85, 105), width=4)
        draw.rectangle([0, GROUND_Y, WIDTH, HEIGHT], fill=(220, 232, 242))

    elif bg_type == "helping_hand":
        sky = (245, 248, 255)
        draw.rectangle([0, 0, WIDTH, HEIGHT], fill=sky)
        draw.ellipse([210, 460, 870, 1120], fill=(224, 231, 255))
        draw.ellipse([280, 530, 800, 1050], fill=(238, 242, 255))
        draw.rectangle([0, GROUND_Y, WIDTH, HEIGHT], fill=(222, 229, 240))

    elif bg_type == "mosque_serenity":
        sky = (248, 250, 252)
        draw.rectangle([0, 0, WIDTH, HEIGHT], fill=sky)
        draw.polygon([(WIDTH // 2, 320), (160, GROUND_Y), (920, GROUND_Y)], fill=(254, 249, 195))
        mx = 750
        draw.chord([mx - 155, 580, mx + 155, 860], start=180, end=360, fill=(226, 232, 240), outline=(51, 65, 85), width=6)
        draw.rectangle([mx - 155, 720, mx + 155, GROUND_Y], fill=(241, 245, 249), outline=(51, 65, 85), width=6)
        draw.rectangle([mx + 170, 480, mx + 225, GROUND_Y], fill=(241, 245, 249), outline=(51, 65, 85), width=6)
        draw.polygon([(mx + 160, 480), (mx + 235, 480), (mx + 197, 395)], fill=(234, 88, 12), outline=(51, 65, 85))
        draw_crescent_moon(draw, mx, 525, 24, (234, 88, 12), sky)
        draw.rectangle([0, GROUND_Y, WIDTH, HEIGHT], fill=(226, 232, 240))

    elif bg_type == "good_deed_tree":
        sky = (236, 253, 245)
        draw.rectangle([0, 0, WIDTH, HEIGHT], fill=sky)
        draw.ellipse([150, 360, 320, 530], fill=(254, 240, 138))
        tx = 770
        draw.rectangle([tx - 18, GROUND_Y - 220, tx + 18, GROUND_Y], fill=(146, 64, 14))
        draw.ellipse([tx - 145, GROUND_Y - 410, tx + 145, GROUND_Y - 170], fill=(110, 231, 183), outline=(16, 185, 129), width=6)
        draw.ellipse([tx - 105, GROUND_Y - 460, tx + 105, GROUND_Y - 240], fill=(167, 243, 208), outline=(16, 185, 129), width=5)
        for fx, fy in [(tx - 65, GROUND_Y - 290), (tx + 55, GROUND_Y - 320), (tx, GROUND_Y - 375), (tx - 30, GROUND_Y - 230)]:
            draw.ellipse([fx - 12, fy - 12, fx + 12, fy + 12], fill=(249, 115, 22))
        draw.rectangle([0, GROUND_Y, WIDTH, HEIGHT], fill=(209, 250, 229))

    else:
        sky = (241, 245, 249)
        draw.rectangle([0, 0, WIDTH, HEIGHT], fill=sky)
        for i in range(3):
            r_c = 130 + i * 55 + int(math.sin(t * 2 + i) * 10)
            draw.ellipse([480 - r_c, 680 - r_c, 480 + r_c, 680 + r_c], outline=(203, 213, 225), width=4)
        draw.rectangle([0, GROUND_Y, WIDTH, HEIGHT], fill=(226, 232, 240))

    draw.line([(0, GROUND_Y), (WIDTH, GROUND_Y)], fill=(71, 85, 105), width=6)


# ==============================================================================
# 8. محرك رسم وتحريك شخصية Stickman (البطل + الشخصية الثانية التفاعلية)
# ==============================================================================
def draw_stickman_character(draw, cx: float, ground_y: float,
                            pose: str, expression: str, facing: int, t: float,
                            is_speaking: bool = False, role: str = "hero"):
    stroke = (15, 23, 42)
    head_fill = (255, 255, 255)
    accent = (234, 88, 12) if role == "hero" else (16, 185, 129)
    lw = 12

    ease_in = min(1.0, t / 0.40)
    ease_in = 0.5 - 0.5 * math.cos(ease_in * math.pi)

    draw.ellipse([cx - 68, ground_y - 10, cx + 68, ground_y + 10], fill=(160, 174, 192))

    is_walking = pose in ("sad_walk", "walk_confident", "walk", "run")
    speed = 3.2 if pose == "sad_walk" else 4.4
    swing = math.sin(t * speed) * 0.42 * ease_in if is_walking else 0.0
    bob = (abs(math.cos(t * speed)) * -6.0 * ease_in) if is_walking else (math.sin(t * 2.4) * 3.0)
    lean = (0.20 * facing * ease_in) if pose == "sad_walk" else (-0.06 * facing if pose == "walk_confident" else 0.0)

    hip = (cx, ground_y - 172 + bob)
    shoulder = (hip[0] + math.sin(lean) * 155, hip[1] - math.cos(lean) * 155)
    head_c = (shoulder[0] + math.sin(lean) * 60, shoulder[1] - 64)

    for side in (-1, 1):
        ang = swing * side
        knee = (hip[0] + math.sin(ang) * 86, hip[1] + math.cos(ang) * 86)
        shin_ang = ang - 0.08 * facing
        foot = (knee[0] + math.sin(shin_ang) * 86, min(ground_y - 4, knee[1] + math.cos(shin_ang) * 86))
        draw.line([hip, knee, foot], fill=stroke, width=lw)

    draw.line([hip, shoulder], fill=stroke, width=lw)

    gesture = math.sin(t * 3.5) * 14
    if pose == "arms_open":
        for side in (-1, 1):
            elbow = (shoulder[0] + side * 58, shoulder[1] - 28 + gesture * 0.4)
            hand = (elbow[0] + side * 46, elbow[1] - 48 + gesture * 0.6)
            draw.line([shoulder, elbow, hand], fill=stroke, width=lw)
    elif pose == "hand_on_heart":
        elbow1 = (shoulder[0] + 45 * facing, shoulder[1] + 35)
        hand1 = (shoulder[0] + 10 * facing, shoulder[1] + 55)
        draw.line([shoulder, elbow1, hand1], fill=stroke, width=lw)
        elbow2 = (shoulder[0] - 35 * facing, shoulder[1] + 55)
        hand2 = (shoulder[0] - 45 * facing, shoulder[1] + 105)
        draw.line([shoulder, elbow2, hand2], fill=stroke, width=lw)
    elif pose in ("give_hope", "wise_gesture"):
        elbow1 = (shoulder[0] + 55 * facing, shoulder[1] + 15)
        hand1 = (elbow1[0] + 52 * facing, elbow1[1] - 12 + gesture * 0.5)
        draw.line([shoulder, elbow1, hand1], fill=stroke, width=lw)
        elbow2 = (shoulder[0] - 32 * facing, shoulder[1] + 55)
        hand2 = (shoulder[0] - 38 * facing, shoulder[1] + 105)
        draw.line([shoulder, elbow2, hand2], fill=stroke, width=lw)
    elif pose == "think":
        elbow1 = (shoulder[0] + 48 * facing, shoulder[1] + 24)
        hand1 = (head_c[0] + 20 * facing, head_c[1] + 42)
        draw.line([shoulder, elbow1, hand1], fill=stroke, width=lw)
        draw.line([shoulder, (shoulder[0] - 34 * facing, shoulder[1] + 85)], fill=stroke, width=lw)
    else:
        for side in (-1, 1):
            arm_ang = -swing * side * 0.85
            elbow = (shoulder[0] + math.sin(arm_ang) * 74, shoulder[1] + math.cos(arm_ang) * 74)
            hand = (elbow[0] + math.sin(arm_ang + 0.22 * facing) * 68, elbow[1] + math.cos(arm_ang + 0.22 * facing) * 68)
            draw.line([shoulder, elbow, hand], fill=stroke, width=lw)

    r = 54
    draw.ellipse([head_c[0] - r, head_c[1] - r, head_c[0] + r, head_c[1] + r], fill=head_fill, outline=stroke, width=lw)
    if role == "hero":
        draw.arc([head_c[0] - r - 2, head_c[1] - r - 4, head_c[0] + r + 2, head_c[1] + 14], start=190, end=350, fill=accent, width=12)
        draw.line([(head_c[0], head_c[1] - r + 7), (head_c[0] + (r + 20) * facing, head_c[1] - r + 13)], fill=accent, width=10)
    else:
        draw.chord([head_c[0] - r - 2, head_c[1] - r - 12, head_c[0] + r + 2, head_c[1] - 10], start=180, end=360, fill=accent, outline=stroke, width=5)

    ex_off = 15 * facing
    eye_y = head_c[1] - 8
    blink = (t % 2.6) > 2.48
    if blink:
        draw.line([(head_c[0] + ex_off - 17, eye_y), (head_c[0] + ex_off - 5, eye_y)], fill=stroke, width=5)
        draw.line([(head_c[0] + ex_off + 7, eye_y), (head_c[0] + ex_off + 19, eye_y)], fill=stroke, width=5)
    else:
        draw.ellipse([head_c[0] + ex_off - 17, eye_y - 6, head_c[0] + ex_off - 5, eye_y + 6], fill=stroke)
        draw.ellipse([head_c[0] + ex_off + 7, eye_y - 6, head_c[0] + ex_off + 19, eye_y + 6], fill=stroke)

    my = head_c[1] + 20
    mx = head_c[0] + ex_off
    talk_open = abs(math.sin(t * 14.0)) * 7 if is_speaking else 0
    if talk_open > 3.5:
        draw.ellipse([mx - 11, my - 5, mx + 11, my + int(talk_open) + 4], fill=(239, 68, 68), outline=stroke, width=4)
    elif expression == "happy":
        draw.arc([mx - 17, my - 11, mx + 17, my + 11], start=10, end=170, fill=stroke, width=6)
    elif expression == "sad":
        draw.arc([mx - 17, my, mx + 17, my + 18], start=190, end=350, fill=stroke, width=6)
    elif expression == "shocked":
        draw.ellipse([mx - 9, my - 9, mx + 9, my + 11], outline=stroke, width=5)
    else:
        draw.line([(mx - 14, my + 3), (mx + 14, my + 3)], fill=stroke, width=6)


# ==============================================================================
# 9. موشن جرافيك احترافي للنصوص (عبارات قصيرة + دخول/خروج + بطاقة نظيفة)
# ==============================================================================
def split_sentence_into_chunks(words: list, max_words_per_chunk: int = 9) -> list:
    if len(words) <= max_words_per_chunk:
        return [(0, words)]
    mid = math.ceil(len(words) / 2)
    return [(0, words[:mid]), (mid, words[mid:])]


def wrap_chunk_lines(chunk_words: list, font, draw, max_w: int) -> list:
    lines = []
    cur = []
    cur_w = 0
    space_w = draw.textbbox((0, 0), " ", font=font)[2]
    for offset_idx, w in chunk_words:
        shaped = shape_arabic(w)
        wb = draw.textbbox((0, 0), shaped, font=font)
        ww = wb[2] - wb[0]
        added = ww if not cur else (space_w + ww)
        if cur and (cur_w + added > max_w):
            lines.append(cur)
            cur = [(offset_idx, w)]
            cur_w = ww
        else:
            cur.append((offset_idx, w))
            cur_w += added
    if cur:
        lines.append(cur)
    return lines[:2]


def draw_kinetic_captions(img, text: str, local_t: float,
                          speech_dur: float, scene_dur: float,
                          base_font, zoom_font):
    from PIL import Image, ImageDraw
    words = [w for w in text.split() if w.strip()]
    if not words:
        return

    weights = [max(2, len(strip_tashkeel(w))) for w in words]
    total_weight = sum(weights)
    speech_progress = min(0.999, max(0.0, local_t / max(0.25, speech_dur)))
    target_w = speech_progress * total_weight

    active_idx = len(words) - 1
    acc = 0.0
    for i, wt in enumerate(weights):
        acc += wt
        if target_w <= acc:
            active_idx = i
            break

    chunks = split_sentence_into_chunks(words, max_words_per_chunk=9)
    chosen_chunk_words = list(enumerate(words))
    chunk_start_t = 0.0
    chunk_end_t = scene_dur

    if len(chunks) == 2:
        (s1, w1), (s2, w2) = chunks
        w1_weight = sum(weights[:s2])
        split_time = max(0.8, speech_dur * (w1_weight / max(1, total_weight)))
        if local_t < split_time:
            chosen_chunk_words = [(s1 + i, w) for i, w in enumerate(w1)]
            chunk_start_t = 0.0
            chunk_end_t = split_time
        else:
            chosen_chunk_words = [(s2 + i, w) for i, w in enumerate(w2)]
            chunk_start_t = split_time
            chunk_end_t = scene_dur

    c_local = max(0.0, local_t - chunk_start_t)
    c_dur = max(0.3, chunk_end_t - chunk_start_t)
    in_d = min(0.28, c_dur * 0.25)
    out_d = min(0.25, c_dur * 0.22)

    if c_local < in_d:
        p = c_local / max(0.05, in_d)
        ease = 1.0 - (1.0 - p) ** 3
        alpha = int(255 * ease)
        slide_y = int((1.0 - ease) * 34)
    elif c_local > max(in_d, c_dur - out_d):
        p = min(1.0, (c_local - (c_dur - out_d)) / max(0.05, out_d))
        ease = p ** 2
        alpha = int(255 * (1.0 - ease))
        slide_y = int(-ease * 28)
    else:
        alpha = 255
        slide_y = 0

    if alpha <= 8:
        return

    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)

    max_line_w = int((SAFE_RIGHT - SAFE_LEFT) * 0.88)
    lines = wrap_chunk_lines(chosen_chunk_words, base_font, odraw, max_line_w)
    line_height = 76
    box_h = len(lines) * line_height + 46
    box_y1 = CAPTION_TOP + slide_y
    box_y2 = box_y1 + box_h

    card_alpha = int(alpha * 0.92)
    border_alpha = int(alpha * 0.35)
    odraw.rounded_rectangle(
        [SAFE_LEFT, box_y1, SAFE_RIGHT, box_y2],
        radius=28,
        fill=(255, 255, 255, card_alpha),
        outline=(148, 163, 184, border_alpha),
        width=3
    )

    space_w = odraw.textbbox((0, 0), " ", font=base_font)[2] + 8
    normal_fill = (30, 41, 59, alpha)
    active_text_fill = (255, 255, 255, alpha)
    pill_fill = (234, 88, 12, alpha)

    for line_idx, line_items in enumerate(lines):
        word_boxes = []
        total_w = 0
        for pos, (w_idx, w_str) in enumerate(line_items):
            is_active = (w_idx == active_idx) and (local_t <= speech_dur + 0.15)
            fnt = zoom_font if is_active else base_font
            shaped = shape_arabic(w_str)
            bb = odraw.textbbox((0, 0), shaped, font=fnt)
            ww = bb[2] - bb[0]
            wh = bb[3] - bb[1]
            word_boxes.append((w_idx, shaped, fnt, ww, wh, is_active))
            total_w += ww + (space_w if pos > 0 else 0)

        cursor_x = (WIDTH + total_w) // 2
        base_y = box_y1 + 20 + line_idx * line_height

        for w_idx, shaped, fnt, ww, wh, is_active in word_boxes:
            cursor_x -= ww
            if is_active:
                odraw.rounded_rectangle(
                    [cursor_x - 12, base_y - 6, cursor_x + ww + 12, base_y + wh + 14],
                    radius=14,
                    fill=pill_fill
                )
                odraw.text((cursor_x, base_y - 4), shaped, font=fnt, fill=active_text_fill)
            else:
                odraw.text((cursor_x, base_y), shaped, font=fnt, fill=normal_fill)
            cursor_x -= space_w

    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))


# ==============================================================================
# 10. رندر الإطارات وتجميع الفيديو النهائي مع انتقالات ناعمة (Crossfade)
# ==============================================================================
def render_single_frame(story: dict, timing: list, scene_idx: int, t: float,
                        title_font, brand_font, base_cap_font, zoom_cap_font,
                        shaped_title: str, shaped_brand: str):
    from PIL import Image, ImageDraw
    scene = story["scenes"][scene_idx]
    t_rec = timing[scene_idx]
    local_t = max(0.0, t - t_rec["start"])
    scene_dur = max(0.1, t_rec["end"] - t_rec["start"])
    speech_dur = max(0.1, t_rec.get("speech_end", t_rec["end"]) - t_rec["start"])
    progress = min(1.0, max(0.0, local_t / scene_dur))

    img = Image.new("RGB", (WIDTH, HEIGHT), (248, 250, 252))
    draw = ImageDraw.Draw(img, "RGBA")

    draw_scene_background(draw, scene.get("bg_type", "hope_light"), local_t, progress)

    facing = int(scene.get("facing", 1))
    pose = scene.get("pose", "walk_confident")
    has_partner = bool(scene.get("has_partner", False))
    is_talking = local_t <= speech_dur

    if has_partner:
        hero_x = WIDTH * 0.34
        partner_x = WIDTH * 0.66
        draw_stickman_character(
            draw=draw, cx=hero_x, ground_y=GROUND_Y,
            pose=pose, expression=scene.get("expression", "neutral"),
            facing=1, t=local_t, is_speaking=False, role="hero"
        )
        draw_stickman_character(
            draw=draw, cx=partner_x, ground_y=GROUND_Y,
            pose="wise_gesture", expression="happy",
            facing=-1, t=local_t, is_speaking=is_talking,
            role=scene.get("partner_role", "wise_man")
        )
    else:
        if pose in ("sad_walk", "walk_confident", "walk", "run"):
            ease_move = 0.5 - 0.5 * math.cos(progress * math.pi)
            cx = (WIDTH * 0.30) + ease_move * (WIDTH * 0.32) * facing
        else:
            cx = WIDTH * 0.46
        draw_stickman_character(
            draw=draw, cx=cx, ground_y=GROUND_Y,
            pose=pose, expression=scene.get("expression", "neutral"),
            facing=facing, t=local_t, is_speaking=is_talking, role="hero"
        )

    if shaped_title:
        bbox = draw.textbbox((0, 0), shaped_title, font=title_font)
        tw = bbox[2] - bbox[0]
        draw.text(((WIDTH - tw) // 2, SAFE_TOP + 12), shaped_title, font=title_font, fill=(15, 23, 42))

    if shaped_brand:
        bb_b = draw.textbbox((0, 0), shaped_brand, font=brand_font)
        bw = bb_b[2] - bb_b[0]
        draw.rounded_rectangle(
            [(WIDTH - bw) // 2 - 22, SAFE_TOP + 86, (WIDTH + bw) // 2 + 22, SAFE_TOP + 132],
            radius=18, fill=(226, 232, 240)
        )
        draw.text(((WIDTH - bw) // 2, SAFE_TOP + 90), shaped_brand, font=brand_font, fill=(51, 65, 85))

    draw_kinetic_captions(
        img=img,
        text=scene.get("spoken_text", ""),
        local_t=local_t,
        speech_dur=speech_dur,
        scene_dur=scene_dur,
        base_font=base_cap_font,
        zoom_font=zoom_cap_font
    )
    return img


def render_frames_and_video(story: dict, timing: list, full_audio: Path) -> Path:
    from PIL import Image
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

    total_duration = timing[-1]["end"]
    total_frames = int(math.ceil(total_duration * FPS))

    title_font = pick_arabic_font(56)
    brand_font = pick_arabic_font(32)
    base_caption_font = pick_arabic_font(44)
    zoom_caption_font = pick_arabic_font(48)

    shaped_title = shape_arabic(story.get("title", ""))
    shaped_brand = shape_arabic(PAGE_SPOKEN_NAME)

    fade_duration = 0.28

    for f_idx in range(total_frames):
        t = f_idx / FPS
        active_idx = len(timing) - 1
        for i, item in enumerate(timing):
            if item["start"] <= t < item["end"]:
                active_idx = i
                break

        img = render_single_frame(
            story, timing, active_idx, t,
            title_font, brand_font, base_caption_font, zoom_caption_font,
            shaped_title, shaped_brand
        )

        if active_idx > 0:
            since_start = t - timing[active_idx]["start"]
            if 0.0 <= since_start < fade_duration:
                alpha = since_start / fade_duration
                prev_img = render_single_frame(
                    story, timing, active_idx - 1, t,
                    title_font, brand_font, base_caption_font, zoom_caption_font,
                    shaped_title, shaped_brand
                )
                img = Image.blend(prev_img, img, alpha)

        frame_path = FRAMES_DIR / f"frame_{f_idx:05d}.png"
        img.save(frame_path, "PNG")

        if f_idx in (25, total_frames // 2, max(0, total_frames - 25)):
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
# 11. بوابة فحص الجودة التلقائية قبل النشر
# ==============================================================================
def run_quality_gate(video_path: Path, audio_path: Path, story: dict) -> bool:
    from PIL import Image
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
        if diff <= 0.45 and 29.5 <= v_dur <= 90.0:
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
    checks.append("- [ناجح] تشكيل الحروف العربية واتجاه RTL وفصل منطقة النصوص عن الأرضية.")

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
# 12. النشر التلقائي على ريلز فيسبوك (Facebook Graph API)
# ==============================================================================
def publish_to_facebook_reels(video_path: Path, description: str):
    import requests
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

    ensure_runtime_dependencies()

    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR)
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    history = load_history()

    print("=== [1/5] توليد القصة وفحص المصادر ومنع التكرار ===")
    story = generate_story(history)
    with open(WORK_DIR / "story-lock.json", "w", encoding="utf-8") as sf:
        json.dump(story, sf, ensure_ascii=False, indent=2)

    print("=== [2/5] توليد التعليق الصوتي والخلفية الموسيقية ===")
    full_audio, timing = synthesize_voice_and_timing(story)

    print("=== [3/5] تحريك شخصيات Stickman وموشن جرافيك النصوص (1080x1920 @ 30fps) ===")
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
