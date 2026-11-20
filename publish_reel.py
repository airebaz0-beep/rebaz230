#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mansa Reels Studio v4.0 — PyCairo Flat-Vector Engine, True RTL Arabic,
Verified Hadith Bank, Pipe FFmpeg Render & Studio Audio Mastering.
"""

import os
import sys
import json
import math
import time
import wave
import glob
import random
import struct
import shutil
import asyncio
import subprocess
from datetime import datetime, timezone, timedelta

import cairo
import requests
import edge_tts

# ==============================================================================
# 1. إعدادات النظام والهوية البصرية (Mansa Brand Identity)
# ==============================================================================
WIDTH, HEIGHT = 1080, 1920
FPS = 30
IRAQ_TZ = timezone(timedelta(hours=3))
FB_GRAPH_VERSION = os.getenv("FB_GRAPH_VERSION", "v22.0")

# لوحة ألوان منسى (Ink, Bone, Rust, Lichen, Signal Lime)
PALETTE = {
    "ink": (0x12 / 255.0, 0x14 / 255.0, 0x1A / 255.0),
    "ink_soft": (0x1E / 255.0, 0x22 / 255.0, 0x2D / 255.0),
    "bone": (0xEC / 255.0, 0xE7 / 255.0, 0xDB / 255.0),
    "bone_card": (0xF7 / 255.0, 0xF4 / 255.0, 0xEC / 255.0),
    "rust": (0xB8 / 255.0, 0x47 / 255.0, 0x2A / 255.0),
    "lichen": (0xBF / 255.0, 0xC8 / 255.0, 0xA5 / 255.0),
    "lime": (0xD7 / 255.0, 0xF2 / 255.0, 0x4B / 255.0),
    "skin": (0xE8 / 255.0, 0xB8 / 255.0, 0x9A / 255.0),
    "skin_shadow": (0xD1 / 255.0, 0x9B / 255.0, 0x7B / 255.0),
    "white": (1.0, 1.0, 1.0),
}

BUILD_DIR = "mansa_build_v4"
EXPORT_DIR = "exports"
HISTORY_FILE = "published_history.json"

# ==============================================================================
# 2. بنك الأحاديث الصحيحة الموثقة بالتشكيل (الصحيحين)
# ==============================================================================
VERIFIED_HADITH_BANK = [
    {
        "id": "hadith_01",
        "title": "طَرِيقُ الجَنَّةِ يَبْدَأُ بِخُطْوَةِ عِلْمٍ",
        "hadith_text": "قَالَ رَسُولُ اللَّهِ ﷺ: «مَنْ سَلَكَ طَرِيقًا يَلْتَمِسُ فِيهِ عِلْمًا، سَهَّلَ اللَّهُ لَهُ بِهِ طَرِيقًا إِلَى الجَنَّةِ»",
        "source": "رَوَاهُ مُسْلِمٌ (٢٦٩٩)",
        "theme": "طلب العلم والتعلّم المستمر في الحياة اليومية",
    },
    {
        "id": "hadith_02",
        "title": "القُوَّةُ الحَقِيقِيَّةُ عِنْدَ الغَضَبِ",
        "hadith_text": "قَالَ رَسُولُ اللَّهِ ﷺ: «لَيْسَ الشَّدِيدُ بِالصُّرَعَةِ، إِنَّمَا الشَّدِيدُ الَّذِي يَمْلِكُ نَفْسَهُ عِنْدَ الغَضَبِ»",
        "source": "مُتَّفَقٌ عَلَيْهِ (البُخَارِيُّ ٦١١٤ وَمُسْلِمٌ ٢٦٠٩)",
        "theme": "ضبط النفس والهدوء في المواقف المستفزة",
    },
    {
        "id": "hadith_03",
        "title": "الكَلِمَةُ الطَّيِّبَةُ تَفْتَحُ القُلُوبَ",
        "hadith_text": "قَالَ رَسُولُ اللَّهِ ﷺ: «اتَّقُوا النَّارَ وَلَوْ بِشِقِّ تَمْرَةٍ، فَمَنْ لَمْ يَجِدْ فَبِكَلِمَةٍ طَيِّبَةٍ»",
        "source": "مُتَّفَقٌ عَلَيْهِ (البُخَارِيُّ ٦٠٢٣ وَمُسْلِمٌ ١٠١٦)",
        "theme": "أثر الكلمة اللطيفة وجبر الخواطر بين الناس",
    },
    {
        "id": "hadith_04",
        "title": "أَحَبُّ الأَعْمَالِ أَدْوَمُهَا وَإِنْ قَلَّ",
        "hadith_text": "قَالَ رَسُولُ اللَّهِ ﷺ: «أَحَبُّ الأَعْمَالِ إِلَى اللَّهِ تَعَالَى أَدْوَمُهَا وَإِنْ قَلَّ»",
        "source": "مُتَّفَقٌ عَلَيْهِ (البُخَارِيُّ ٦٤٦٤ وَمُسْلِمٌ ٧٨٣)",
        "theme": "الاستمرارية في العادات الصغيرة النافعة بدل الانقطاع",
    },
    {
        "id": "hadith_05",
        "title": "لا تَحْقِرَنَّ مِنَ المَعْرُوفِ شَيْئًا",
        "hadith_text": "قَالَ رَسُولُ اللَّهِ ﷺ: «لا تَحْقِرَنَّ مِنَ المَعْرُوفِ شَيْئًا، وَلَوْ أَنْ تَلْقَى أَخَاكَ بِوَجْهٍ طَلْقٍ»",
        "source": "رَوَاهُ مُسْلِمٌ (٢٦٢٦)",
        "theme": "قيمة الابتسامة والبشاشة وتبسيط الخير للآخرين",
    },
    {
        "id": "hadith_06",
        "title": "حُسْنُ إِسْلامِ المَرْءِ تَرْكُ مَا لا يَعْنِيهِ",
        "hadith_text": "قَالَ رَسُولُ اللَّهِ ﷺ: «مِنْ حُسْنِ إِسْلامِ المَرْءِ تَرْكُهُ مَا لا يَعْنِيهِ»",
        "source": "حَدِيثٌ حَسَنٌ رَوَاهُ التِّرْمِذِيُّ (٢٣١٧)",
        "theme": "التركيز على الأولويات وترك الفضول والجدال العقيم",
    },
]

# ==============================================================================
# 3. معالج النصوص العربية الصحيح (True Arabic Shaping + BiDi)
# ==============================================================================
def ensure_arabic_libs():
    try:
        import arabic_reshaper  # noqa: F401
        from bidi.algorithm import get_display  # noqa: F401
    except ImportError:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "arabic-reshaper", "python-bidi"],
            check=True,
        )


def shape_ar(text: str) -> str:
    import arabic_reshaper
    from bidi.algorithm import get_display

    if not text:
        return ""
    configuration = {
        "delete_harakat": False,
        "support_ligatures": True,
        "RIAL SIGN": False,
    }
    reshaper = arabic_reshaper.ArabicReshaper(configuration=configuration)
    reshaped = reshaper.reshape(text)
    return get_display(reshaped)


def find_project_fonts():
    bold_candidates = (
        glob.glob("*Bold*.ttf")
        + glob.glob("*Bold*.otf")
        + ["/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf"]
    )
    reg_candidates = (
        glob.glob("*Medium*.ttf")
        + glob.glob("*Regular*.ttf")
        + ["/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf"]
    )
    bold_font = next((f for f in bold_candidates if os.path.exists(f)), None)
    reg_font = next((f for f in reg_candidates if os.path.exists(f)), bold_font)
    return bold_font, reg_font


def make_cairo_font_face(font_path: str):
    import ctypes

    if not font_path or not os.path.exists(font_path):
        return None
    try:
        ft_lib = ctypes.CDLL("libfreetype.so.6")
        cairo_lib = ctypes.CDLL("libcairo.so.2")

        ft_library = ctypes.c_void_p()
        if ft_lib.FT_Init_FreeType(ctypes.byref(ft_library)) != 0:
            return None

        ft_face = ctypes.c_void_p()
        if ft_lib.FT_New_Face(ft_library, font_path.encode("utf-8"), 0, ctypes.byref(ft_face)) != 0:
            return None

        cairo_lib.cairo_ft_font_face_create_for_ft_face.argtypes = [ctypes.c_void_p, ctypes.c_int]
        cairo_lib.cairo_ft_font_face_create_for_ft_face.restype = ctypes.c_void_p
        face_ptr = cairo_lib.cairo_ft_font_face_create_for_ft_face(ft_face, 0)
        if not face_ptr:
            return None

        # تغليف المؤشر كـ Pycairo FontFace
        import pycairo  # noqa: F401
        return None
    except Exception:
        return None


# ==============================================================================
# 4. توليد القصة من Gemini مع بنك الأحاديث وإعادة المحاولة
# ==============================================================================
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"published_ids": [], "titles": [], "entries": []}


def save_history(story_meta: dict, video_id: str = ""):
    history = load_history()
    if story_meta.get("hadith_id") and story_meta["hadith_id"] not in history["published_ids"]:
        history["published_ids"].append(story_meta["hadith_id"])
    history["titles"].append(story_meta["title"])
    history["titles"] = history["titles"][-40:]
    history["entries"].append(
        {
            "date": datetime.now(IRAQ_TZ).strftime("%Y-%m-%d %H:%M"),
            "title": story_meta["title"],
            "hadith_id": story_meta.get("hadith_id", ""),
            "video_id": video_id,
        }
    )
    history["entries"] = history["entries"][-60:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def pick_next_hadith():
    history = load_history()
    used = set(history.get("published_ids", []))
    available = [h for h in VERIFIED_HADITH_BANK if h["id"] not in used]
    if not available:
        available = VERIFIED_HADITH_BANK
    return random.choice(available)


def generate_story_with_gemini(hadith_item: dict) -> dict:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    now_iraq = datetime.now(IRAQ_TZ)
    slot = "العصر" if now_iraq.hour < 17 else "المساء"

    prompt = f"""
أنت كاتب محتوى قصصي وثائقي قصير لصفحة «مَنْسَى - Mansa».
لدينا هذا الحديث النبوي الصحيح الموثق (لا تغير حرفاً في نص الحديث ولا في مصدره):
- الحديث: {hadith_item['hadith_text']}
- المصدر: {hadith_item['source']}
- الفكرة: {hadith_item['theme']}
- وقت النشر: {slot}

المطلوب: اكتب سيناريو ريلز عربي فصيح سلس مشكول جزئياً للنطق الصحيح، يتكون من 4 مشاهد قصيرة، مدة قراءته الإجمالية حوالي 35 إلى 45 ثانية:
- المشهد 1 (Hook): سؤال أو موقف يومي واقعي يجذب المشاهد في أول ثانيتين.
- المشهد 2 (تطور الموقف): كيف يتصرف الإنسان في هذا الموقف.
- المشهد 3 (الحديث النبوي): ذكر نص الحديث الصحيح ومصدره بدقة كما هو أعلاه تماماً.
- المشهد 4 (الخلاصة): تطبيق عملي هادئ يترك أثراً طيباً ويدعو للتأمل.

أعد النتيجة بصيغة JSON فقط بدون أي نص إضافي خارج JSON، بهذا الهيكل الدقيق:
{{
  "title": "{hadith_item['title']}",
  "caption": "وصف قصير ملهم لمنشور فيسبوك مع 4 هاشتاغات عربية مناسبة",
  "scenes": [
    {{
      "narration": "نص التعليق الصوتي للمشهد الأول",
      "mood": "thinking",
      "prop": "question"
    }},
    {{
      "narration": "نص التعليق الصوتي للمشهد الثاني",
      "mood": "focused",
      "prop": "steps"
    }},
    {{
      "narration": "{hadith_item['hadith_text']} — {hadith_item['source']}",
      "mood": "calm_smile",
      "prop": "light"
    }},
    {{
      "narration": "نص التعليق الصوتي للمشهد الرابع",
      "mood": "happy",
      "prop": "heart"
    }}
  ]
}}
القيم المسموحة لـ mood هي فقط: thinking, focused, calm_smile, happy.
القيم المسموحة لـ prop هي فقط: question, steps, light, heart.
"""

    models = ["gemini-2.5-flash", "gemini-2.0-flash"]
    if api_key:
        for model in models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            headers = {
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            }
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.75,
                    "responseMimeType": "application/json",
                },
            }
            for attempt in range(3):
                try:
                    resp = requests.post(url, headers=headers, json=payload, timeout=35)
                    if resp.status_code == 200:
                        data = resp.json()
                        raw = data["candidates"][0]["content"]["parts"][0]["text"]
                        parsed = json.loads(raw)
                        if isinstance(parsed.get("scenes"), list) and len(parsed["scenes"]) >= 3:
                            # ضمان سلامة نص الحديث في المشهد الثالث
                            parsed["scenes"][2]["narration"] = (
                                f"{hadith_item['hadith_text']} — {hadith_item['source']}"
                            )
                            parsed["hadith_id"] = hadith_item["id"]
                            parsed["title"] = hadith_item["title"]
                            print(f"✅ تم توليد السيناريو بنجاح عبر {model}")
                            return parsed
                    else:
                        print(f"⚠️ {model} محاولة {attempt+1} أعادت الحالة {resp.status_code}")
                except Exception as e:
                    print(f"⚠️ خطأ اتصال مع {model}: {e}")
                time.sleep(2 * (attempt + 1))

    # سيناريو احتياطي مصاغ بعناية إذا تعذر الاتصال
    print("ℹ️ استخدام السيناريو الاحتياطي المتقن للحديث المختار.")
    return {
        "hadith_id": hadith_item["id"],
        "title": hadith_item["title"],
        "caption": f"{hadith_item['title']}\n\n{hadith_item['hadith_text']}\n{hadith_item['source']}\n\n#مَنْسَى #حكمة_اليوم #حديث_شريف #تطوير_الذات",
        "scenes": [
            {
                "narration": "فِي زِحَامِ الحَيَاةِ اليَوْمِيَّةِ، نَبْحَثُ كَثِيرًا عَنْ مَعْنًى يُرَتِّبُ خُطُوَاتِنَا وَيَمْنَحُ قُلُوبَنَا الطُّمَأْنِينَةَ.",
                "mood": "thinking",
                "prop": "question",
            },
            {
                "narration": "وَقَدْ نَظُنُّ أَنَّ التَّغْيِيرَ الحَقِيقِيَّ يَحْتَاجُ إِلَى قَفَزَاتٍ كَبِيرَةٍ، بَيْنَمَا البِدَايَةُ الصَّادِقَةُ تَكُونُ فِي خُطْوَةٍ وَاحِدَةٍ ثَابِتَةٍ.",
                "mood": "focused",
                "prop": "steps",
            },
            {
                "narration": f"{hadith_item['hadith_text']} — {hadith_item['source']}",
                "mood": "calm_smile",
                "prop": "light",
            },
            {
                "narration": "اجْعَلْ هَذَا المَعْنَى رَفِيقَ يَوْمِكَ، فَالخَيْرُ الصَّغِيرُ حِينَ يَسْتَمِرُّ يَصْنَعُ أَثَرًا لا يَزُولُ.",
                "mood": "happy",
                "prop": "heart",
            },
        ],
    }


# ==============================================================================
# 5. توليد الصوت الواقعي، مؤثر الانتقال Whoosh، وهندسة FFmpeg (48kHz)
# ==============================================================================
async def _tts_edge_async(text: str, out_mp3: str):
    voice = "ar-SA-HamedNeural"
    communicate = edge_tts.Communicate(text=text, voice=voice, rate="-4%", pitch="-2Hz")
    await communicate.save(out_mp3)


def synthesize_speech(text: str, out_wav: str):
    clean_text = text.replace("ﷺ", "صلى الله عليه وسلم").strip()
    temp_mp3 = out_wav.replace(".wav", ".mp3")
    for attempt in range(3):
        try:
            asyncio.run(_tts_edge_async(clean_text, temp_mp3))
            if os.path.exists(temp_mp3) and os.path.getsize(temp_mp3) > 800:
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-loglevel", "error",
                        "-i", temp_mp3,
                        "-ar", "48000", "-ac", "1",
                        out_wav,
                    ],
                    check=True,
                )
                return
        except Exception as e:
            print(f"⚠️ محاولة الصوت {attempt+1} فشلت: {e}")
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("فشل توليد التعليق الصوتي بعد 3 محاولات.")


def generate_whoosh_wav(out_wav: str, duration: float = 0.38):
    sample_rate = 48000
    n_samples = int(sample_rate * duration)
    random.seed(42)
    prev = 0.0
    with wave.open(out_wav, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_samples):
            t = i / n_samples
            env = math.sin(math.pi * t) ** 2
            white = random.uniform(-1.0, 1.0)
            # مرشح تمرير منخفض ناعم لصوت هواء هادئ
            alpha = 0.08 + 0.18 * math.sin(math.pi * t)
            prev = prev + alpha * (white - prev)
            val = int(max(-32767, min(32767, prev * env * 9500)))
            frames.extend(struct.pack("<h", val))
        wf.writeframes(frames)


def generate_ambient_bed_wav(out_wav: str, total_duration: float):
    sample_rate = 48000
    n_samples = int(sample_rate * (total_duration + 1.0))
    # كورد هادئ دافئ بترددات منخفضة مريحة للأذن
    freqs = [146.83, 220.00, 293.66, 329.63]  # D3, A3, D4, E4
    with wave.open(out_wav, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_samples):
            t = i / sample_rate
            fade_in = min(1.0, t / 1.5)
            fade_out = min(1.0, max(0.0, (total_duration - t) / 1.5))
            lfo = 0.75 + 0.25 * math.sin(2 * math.pi * 0.18 * t)
            sig = 0.0
            for idx, f in enumerate(freqs):
                sig += math.sin(2 * math.pi * f * t + idx * 0.7) * (0.28 / (idx + 1))
            val = int(max(-32767, min(32767, sig * fade_in * fade_out * lfo * 1800)))
            frames.extend(struct.pack("<h", val))
        wf.writeframes(frames)


def get_wav_duration(wav_path: str) -> float:
    with wave.open(wav_path, "r") as wf:
        return wf.getnframes() / float(wf.getframerate())


def build_master_audio(scenes: list) -> tuple:
    whoosh_wav = os.path.join(BUILD_DIR, "whoosh.wav")
    generate_whoosh_wav(whoosh_wav)

    timeline = []
    current_time = 0.45  # صمت افتتاحي أنيق
    voice_segments = []

    for idx, sc in enumerate(scenes):
        raw_wav = os.path.join(BUILD_DIR, f"scene_{idx}.wav")
        synthesize_speech(sc["narration"], raw_wav)
        dur = get_wav_duration(raw_wav)

        # تقسيم نص المشهد إلى قطع كابشن (بحد أقصى 6 كلمات للقطعة بدون حذف أي كلمة)
        words = sc["narration"].split()
        chunks = [" ".join(words[i : i + 6]) for i in range(0, len(words), 6)]
        if not chunks:
            chunks = [sc["narration"]]

        chunk_dur = dur / len(chunks)
        chunk_spans = []
        for c_idx, c_text in enumerate(chunks):
            c_start = current_time + c_idx * chunk_dur
            c_end = c_start + chunk_dur
            chunk_spans.append((c_start, c_end, c_text))

        scene_start = current_time - (0.45 if idx == 0 else 0.25)
        scene_end = current_time + dur + 0.55

        timeline.append(
            {
                "index": idx,
                "start": max(0.0, scene_start),
                "end": scene_end,
                "mood": sc.get("mood", "calm_smile"),
                "prop": sc.get("prop", "light"),
                "chunks": chunk_spans,
            }
        )
        voice_segments.append((current_time, raw_wav))
        current_time = scene_end + 0.25

    total_duration = max(30.5, current_time + 0.6)
    timeline[-1]["end"] = total_duration

    ambient_wav = os.path.join(BUILD_DIR, "ambient.wav")
    generate_ambient_bed_wav(ambient_wav, total_duration)

    # دمج الصوتيات عبر FFmpeg مع الحفاظ على مستوى صوت المعلق (normalize=0)
    master_wav = os.path.join(BUILD_DIR, "master_audio.wav")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", ambient_wav]
    filter_parts = ["[0:a]volume=0.35[bg]"]
    mix_labels = ["[bg]"]
    input_idx = 1

    for start_t, seg_wav in voice_segments:
        cmd.extend(["-i", seg_wav])
        delay_ms = int(start_t * 1000)
        lbl = f"[v{input_idx}]"
        filter_parts.append(
            f"[{input_idx}:a]adelay={delay_ms}|{delay_ms},volume=1.25,"
            f"acompressor=threshold=-18dB:ratio=3:attack=10:release=120{lbl}"
        )
        mix_labels.append(lbl)
        input_idx += 1

    # إضافة مؤثر Whoosh عند بداية المشاهد 2، 3، 4
    for sc_info in timeline[1:]:
        cmd.extend(["-i", whoosh_wav])
        delay_ms = int(sc_info["start"] * 1000)
        lbl = f"[w{input_idx}]"
        filter_parts.append(f"[{input_idx}:a]adelay={delay_ms}|{delay_ms},volume=0.55{lbl}")
        mix_labels.append(lbl)
        input_idx += 1

    all_inputs = "".join(mix_labels)
    filter_parts.append(
        f"{all_inputs}amix=inputs={len(mix_labels)}:duration=first:dropout_transition=2:normalize=0,"
        f"alimiter=limit=0.95[out]"
    )

    cmd.extend(["-filter_complex", ";".join(filter_parts), "-map", "[out]", "-ar", "48000", "-ac", "2", master_wav])
    subprocess.run(cmd, check=True)
    return master_wav, total_duration, timeline


# ==============================================================================
# 6. محرك الرسم الفيكتور الاحترافي (PyCairo Flat-Vector Character + Scene)
# ==============================================================================
def rounded_rect(ctx, x, y, w, h, r):
    r = min(r, w / 2.0, h / 2.0)
    ctx.new_sub_path()
    ctx.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    ctx.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    ctx.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    ctx.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    ctx.close_path()


def draw_arabic_centered(ctx, text: str, cx: float, y: float, size: float, color: tuple, max_width: float = 920):
    shaped = shape_ar(text)
    ctx.select_font_face("Noto Sans Arabic", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    ctx.set_font_size(size)
    ext = ctx.text_extents(shaped)
    if ext.width > max_width and ext.width > 0:
        scale = max_width / ext.width
        ctx.set_font_size(size * scale)
        ext = ctx.text_extents(shaped)
    ctx.set_source_rgb(*color)
    ctx.move_to(cx - ext.width / 2.0 - ext.x_bearing, y)
    ctx.show_text(shaped)


def draw_flat_character(ctx, cx: float, base_y: float, t: float, mood: str):
    breath = math.sin(t * 2.6) * 6.0
    head_tilt = math.sin(t * 1.4) * 0.045
    blink = (int(t * 30) % 105) in (0, 1, 2)

    # ظل أرضي ناعم
    ctx.set_source_rgba(0, 0, 0, 0.14)
    ctx.save()
    ctx.translate(cx, base_y)
    ctx.scale(1.0, 0.22)
    ctx.arc(0, 0, 145, 0, 2 * math.pi)
    ctx.fill()
    ctx.restore()

    # الساقان (بنطال داكن أنيق)
    ctx.set_source_rgb(*PALETTE["ink"])
    rounded_rect(ctx, cx - 58, base_y - 210, 46, 205, 22)
    ctx.fill()
    rounded_rect(ctx, cx + 12, base_y - 210, 46, 205, 22)
    ctx.fill()

    # الحذاء
    ctx.set_source_rgb(*PALETTE["bone"])
    rounded_rect(ctx, cx - 72, base_y - 26, 66, 26, 13)
    ctx.fill()
    rounded_rect(ctx, cx + 6, base_y - 26, 66, 26, 13)
    ctx.fill()

    # الجذع (سترة بلون الصدأ المميز لهوية منسى)
    torso_y = base_y - 430 + breath
    ctx.set_source_rgb(*PALETTE["rust"])
    rounded_rect(ctx, cx - 95, torso_y, 190, 240, 56)
    ctx.fill()

    # ياقة داخلية عاجية
    ctx.set_source_rgb(*PALETTE["bone"])
    ctx.move_to(cx - 34, torso_y)
    ctx.line_to(cx + 34, torso_y)
    ctx.line_to(cx, torso_y + 44)
    ctx.close_path()
    ctx.fill()

    # الذراعان مع حركة إيماء طبيعية
    wave_l = math.sin(t * 2.2) * 0.12
    wave_r = -0.35 + math.cos(t * 2.5) * 0.15 if mood in ("thinking", "happy") else -math.sin(t * 2.2) * 0.12

    # ذراع يسرى
    ctx.save()
    ctx.translate(cx - 95, torso_y + 45)
    ctx.rotate(0.22 + wave_l)
    ctx.set_source_rgb(*PALETTE["rust"])
    rounded_rect(ctx, -20, 0, 40, 165, 20)
    ctx.fill()
    ctx.set_source_rgb(*PALETTE["skin"])
    ctx.arc(0, 172, 21, 0, 2 * math.pi)
    ctx.fill()
    ctx.restore()

    # ذراع يمنى
    ctx.save()
    ctx.translate(cx + 95, torso_y + 45)
    ctx.rotate(-0.22 + wave_r)
    ctx.set_source_rgb(*PALETTE["rust"])
    rounded_rect(ctx, -20, 0, 40, 165, 20)
    ctx.fill()
    ctx.set_source_rgb(*PALETTE["skin"])
    ctx.arc(0, 172, 21, 0, 2 * math.pi)
    ctx.fill()
    ctx.restore()

    # الرقبة
    ctx.set_source_rgb(*PALETTE["skin_shadow"])
    rounded_rect(ctx, cx - 22, torso_y - 36, 44, 48, 16)
    ctx.fill()

    # الرأس والملامح
    ctx.save()
    ctx.translate(cx, torso_y - 108)
    ctx.rotate(head_tilt)

    # الشعر الخلفي
    ctx.set_source_rgb(*PALETTE["ink"])
    ctx.arc(0, -14, 92, math.pi * 0.9, math.pi * 2.1)
    ctx.fill()

    # الوجه
    ctx.set_source_rgb(*PALETTE["skin"])
    rounded_rect(ctx, -78, -82, 156, 172, 74)
    ctx.fill()

    # خصلة شعر عصرية أمامية
    ctx.set_source_rgb(*PALETTE["ink"])
    ctx.move_to(-78, -28)
    ctx.curve_to(-65, -108, 65, -112, 80, -34)
    ctx.curve_to(42, -62, -25, -58, -78, -28)
    ctx.fill()

    # العيون
    ctx.set_source_rgb(*PALETTE["ink"])
    if blink:
        ctx.set_line_width(5)
        ctx.move_to(-40, -6)
        ctx.line_to(-18, -6)
        ctx.move_to(18, -6)
        ctx.line_to(40, -6)
        ctx.stroke()
    else:
        ctx.arc(-29, -6, 8.5, 0, 2 * math.pi)
        ctx.fill()
        ctx.arc(29, -6, 8.5, 0, 2 * math.pi)
        ctx.fill()
        # لمعة العين
        ctx.set_source_rgb(1, 1, 1)
        ctx.arc(-32, -9, 2.8, 0, 2 * math.pi)
        ctx.fill()
        ctx.arc(26, -9, 2.8, 0, 2 * math.pi)
        ctx.fill()

    # الحواجب المعبرة حسب الحالة
    ctx.set_source_rgb(*PALETTE["ink"])
    ctx.set_line_width(6.5)
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    if mood == "thinking":
        ctx.move_to(-44, -26)
        ctx.line_to(-16, -32)
        ctx.move_to(16, -24)
        ctx.line_to(44, -24)
    elif mood == "focused":
        ctx.move_to(-44, -28)
        ctx.line_to(-16, -22)
        ctx.move_to(16, -22)
        ctx.line_to(44, -28)
    else:
        ctx.move_to(-44, -26)
        ctx.line_to(-16, -28)
        ctx.move_to(16, -28)
        ctx.line_to(44, -26)
    ctx.stroke()

    # الفم
    ctx.set_source_rgb(*PALETTE["ink"])
    ctx.set_line_width(6)
    if mood in ("happy", "calm_smile"):
        ctx.arc(0, 24, 22, 0.15 * math.pi, 0.85 * math.pi)
        ctx.stroke()
    else:
        ctx.move_to(-14, 34)
        ctx.line_to(14, 34)
        ctx.stroke()

    ctx.restore()


def draw_scene_prop(ctx, prop: str, px: float, py: float, t: float):
    bob = math.sin(t * 3.0) * 12.0
    ctx.save()
    ctx.translate(px, py + bob)

    # دائرة خلفية مضيئة للأيقونة
    ctx.set_source_rgba(*PALETTE["lime"], 0.22)
    ctx.arc(0, 0, 76, 0, 2 * math.pi)
    ctx.fill()
    ctx.set_source_rgb(*PALETTE["lime"])
    ctx.arc(0, 0, 56, 0, 2 * math.pi)
    ctx.fill()

    ctx.set_source_rgb(*PALETTE["ink"])
    if prop == "question":
        draw_arabic_centered(ctx, "؟", 0, 22, 64, PALETTE["ink"], 100)
    elif prop == "steps":
        for i in range(3):
            rounded_rect(ctx, -30 + i * 20, 12 - i * 16, 16, 18 + i * 16, 4)
            ctx.fill()
    elif prop == "light":
        ctx.arc(0, -6, 22, 0, 2 * math.pi)
        ctx.fill()
        rounded_rect(ctx, -10, 18, 20, 12, 4)
        ctx.fill()
    else:  # heart
        ctx.arc(-11, -6, 13, 0, 2 * math.pi)
        ctx.arc(11, -6, 13, 0, 2 * math.pi)
        ctx.fill()
        ctx.move_to(-23, -2)
        ctx.line_to(23, -2)
        ctx.line_to(0, 24)
        ctx.close_path()
        ctx.fill()

    ctx.restore()


def render_video_pipe(story: dict, master_wav: str, total_duration: float, timeline: list, out_mp4: str):
    total_frames = int(math.ceil(total_duration * FPS))
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, WIDTH, HEIGHT)
    ctx = cairo.Context(surface)

    ffmpeg_cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-pix_fmt", "bgra",
        "-s", f"{WIDTH}x{HEIGHT}",
        "-r", str(FPS),
        "-i", "-",
        "-i", master_wav,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        out_mp4,
    ]
    proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE)

    for frame_idx in range(total_frames):
        t = frame_idx / float(FPS)

        # تحديد المشهد الحالي
        active_sc = timeline[-1]
        for sc in timeline:
            if sc["start"] <= t <= sc["end"]:
                active_sc = sc
                break

        sc_elapsed = max(0.0, t - active_sc["start"])
        sc_dur = max(0.1, active_sc["end"] - active_sc["start"])
        zoom = 1.0 + 0.045 * (sc_elapsed / sc_dur)

        # 1. خلفية عصرية بهوية منسى (Bone + عناصر هندسية)
        ctx.set_source_rgb(*PALETTE["bone"])
        ctx.paint()

        ctx.save()
        ctx.translate(WIDTH / 2.0, HEIGHT / 2.0)
        ctx.scale(zoom, zoom)
        ctx.translate(-WIDTH / 2.0, -HEIGHT / 2.0)

        # قوس هندسي علوي بلون الحبر الداكن
        ctx.set_source_rgb(*PALETTE["ink"])
        rounded_rect(ctx, 48, 88, WIDTH - 96, 230, 44)
        ctx.fill()

        # لمسة ركنية مميزة لهوية منسى (Quarter-circle cutout accent)
        ctx.set_source_rgb(*PALETTE["lime"])
        ctx.move_to(WIDTH - 48, 88)
        ctx.arc(WIDTH - 48, 88, 62, math.pi / 2, math.pi)
        ctx.close_path()
        ctx.fill()

        # دائرة خلفية ناعمة وراء الشخصية
        ctx.set_source_rgba(*PALETTE["lichen"], 0.45)
        ctx.arc(WIDTH / 2.0, 910, 330, 0, 2 * math.pi)
        ctx.fill()

        # خط الأرضية
        ctx.set_source_rgba(*PALETTE["ink"], 0.25)
        ctx.set_line_width(4)
        ctx.move_to(140, 1210)
        ctx.line_to(WIDTH - 140, 1210)
        ctx.stroke()

        # رسم الشخصية الكرتونية والأيقونة التفاعلية
        draw_flat_character(ctx, WIDTH / 2.0 - 40, 1210, t, active_sc["mood"])
        draw_scene_prop(ctx, WIDTH / 2.0 + 230, 740, t, active_sc["prop"])
        ctx.restore()

        # 2. ترويسة الصفحة والعنوان
        draw_arabic_centered(ctx, "مَنْسَى  •  MANSA", WIDTH / 2.0, 152, 34, PALETTE["lime"], 700)
        draw_arabic_centered(ctx, story["title"], WIDTH / 2.0, 245, 50, PALETTE["bone"], 880)

        # 3. بطاقة الكابشن السفلية الواضحة
        active_chunk = active_sc["chunks"][-1][2]
        for c_start, c_end, c_text in active_sc["chunks"]:
            if c_start <= t <= c_end:
                active_chunk = c_text
                break

        card_y = 1320
        card_h = 260
        ctx.set_source_rgb(*PALETTE["ink"])
        rounded_rect(ctx, 64, card_y, WIDTH - 128, card_h, 42)
        ctx.fill()

        # شريط جانبي برتقالي داخل البطاقة
        ctx.set_source_rgb(*PALETTE["rust"])
        rounded_rect(ctx, WIDTH - 94, card_y + 42, 12, card_h - 84, 6)
        ctx.fill()

        # تقسيم الكابشن لسطرين متوازنين إذا زاد عن 3 كلمات
        c_words = active_chunk.split()
        if len(c_words) > 3:
            mid = (len(c_words) + 1) // 2
            line1 = " ".join(c_words[:mid])
            line2 = " ".join(c_words[mid:])
            draw_arabic_centered(ctx, line1, WIDTH / 2.0, card_y + 108, 52, PALETTE["bone"], 860)
            draw_arabic_centered(ctx, line2, WIDTH / 2.0, card_y + 188, 52, PALETTE["lime"], 860)
        else:
            draw_arabic_centered(ctx, active_chunk, WIDTH / 2.0, card_y + 148, 56, PALETTE["bone"], 860)

        # 4. شريط التقدم العلوي
        progress = min(1.0, max(0.0, t / total_duration))
        ctx.set_source_rgba(*PALETTE["ink"], 0.18)
        ctx.rectangle(0, 0, WIDTH, 14)
        ctx.fill()
        ctx.set_source_rgb(*PALETTE["rust"])
        ctx.rectangle(0, 0, WIDTH * progress, 14)
        ctx.fill()

        proc.stdin.write(surface.get_data())

    proc.stdin.close()
    ret = proc.wait()
    if ret != 0:
        raise RuntimeError(f"فشل ترميز الفيديو عبر FFmpeg برمز خروج {ret}")


# ==============================================================================
# 7. فحص الجودة (QA Gate) والنشر على فيسبوك مع Polling
# ==============================================================================
def run_qa_gate(mp4_path: str, duration: float, story: dict) -> bool:
    os.makedirs("stickman_build", exist_ok=True)
    report_path = os.path.join("stickman_build", "qa-report.md")

    size_mb = os.path.getsize(mp4_path) / (1024 * 1024) if os.path.exists(mp4_path) else 0
    dur_ok = 29.5 <= duration <= 90.0
    size_ok = size_mb > 0.4
    passed = dur_ok and size_ok

    report = [
        "# تقرير فحص الجودة — Mansa v4.0",
        f"- **التاريخ (توقيت العراق):** {datetime.now(IRAQ_TZ).strftime('%Y-%m-%d %H:%M')}",
        f"- **العنوان:** {story['title']}",
        f"- **المدة:** {duration:.2f} ثانية ({'ناجح' if dur_ok else 'فشل: خارج النطاق 29.5-90 ثانية'})",
        f"- **الحجم:** {size_mb:.2f} MB ({'ناجح' if size_ok else 'فشل: حجم غير صالح'})",
        f"- **النتيجة النهائية:** {'✅ معتمد للنشر' if passed else '❌ مرفوض'}",
    ]
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report) + "\n")

    print("\n".join(report))
    return passed


def publish_to_facebook_reels(mp4_path: str, caption: str) -> str:
    page_id = os.getenv("FB_PAGE_ID", "").strip()
    token = os.getenv("FB_ACCESS_TOKEN", "").strip()
    if not page_id or not token:
        raise RuntimeError("متغيرات FB_PAGE_ID أو FB_ACCESS_TOKEN غير موجودة.")

    base = f"https://graph.facebook.com/{FB_GRAPH_VERSION}/{page_id}/video_reels"

    # المرحلة 1: بدء جلسة الرفع
    r1 = requests.post(
        base,
        data={"upload_phase": "start", "access_token": token},
        timeout=30,
    )
    r1.raise_for_status()
    j1 = r1.json()
    video_id = j1["video_id"]
    upload_url = j1["upload_url"]

    # المرحلة 2: رفع ملف الفيديو
    file_size = os.path.getsize(mp4_path)
    with open(mp4_path, "rb") as vf:
        r2 = requests.post(
            upload_url,
            headers={
                "Authorization": f"OAuth {token}",
                "offset": "0",
                "file_size": str(file_size),
            },
            data=vf,
            timeout=180,
        )
    r2.raise_for_status()

    # المرحلة 3: إنهاء ونشر الريل
    r3 = requests.post(
        base,
        data={
            "upload_phase": "finish",
            "video_id": video_id,
            "video_state": "PUBLISHED",
            "description": caption,
            "access_token": token,
        },
        timeout=45,
    )
    r3.raise_for_status()

    # المرحلة 4: Polling للتأكد من اكتمال معالجة الفيديو على سيرفرات Meta
    status_url = f"https://graph.facebook.com/{FB_GRAPH_VERSION}/{video_id}"
    for _ in range(12):
        time.sleep(5)
        rs = requests.get(
            status_url,
            params={"fields": "status", "access_token": token},
            timeout=20,
        )
        if rs.status_code == 200:
            v_status = rs.json().get("status", {}).get("video_status", "")
            print(f"📡 حالة معالجة الريل على فيسبوك: {v_status}")
            if v_status == "ready":
                break

    print(f"🚀 تم نشر الريل بنجاح! معرف الفيديو: {video_id}")
    return video_id


# ==============================================================================
# 8. نقطة التشغيل الرئيسية
# ==============================================================================
def main():
    local_only = "--local" in sys.argv
    ensure_arabic_libs()

    if os.path.exists(BUILD_DIR):
        shutil.rmtree(BUILD_DIR)
    os.makedirs(BUILD_DIR, exist_ok=True)
    os.makedirs(EXPORT_DIR, exist_ok=True)

    hadith_item = pick_next_hadith()
    story = generate_story_with_gemini(hadith_item)

    master_wav, total_duration, timeline = build_master_audio(story["scenes"])
    final_mp4 = os.path.join(EXPORT_DIR, "final.mp4")
    render_video_pipe(story, master_wav, total_duration, timeline, final_mp4)

    if not run_qa_gate(final_mp4, total_duration, story):
        sys.exit(1)

    if local_only:
        print("🧪 وضع المعاينة (--local): تم حفظ الفيديو في exports/final.mp4 بدون نشر.")
        return

    video_id = publish_to_facebook_reels(final_mp4, story["caption"])
    save_history(story, video_id)


if __name__ == "__main__":
    main()
