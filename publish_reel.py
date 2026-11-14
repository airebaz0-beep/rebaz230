import os
import sys
import re
import json
import asyncio
import subprocess
import requests
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import google.generativeai as genai
import edge_tts

# تثبيت مكتبات تعديل الحروف العربية تلقائياً إن لم تكن موجودة
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


def fix_arabic(text):
    if arabic_reshaper and get_display:
        reshaped = arabic_reshaper.reshape(text)
        return get_display(reshaped)
    return text


# 1. إعداد المفاتيح والاتصالات
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN")
FB_PAGE_ID = os.getenv("FB_PAGE_ID")

genai.configure(api_key=GEMINI_API_KEY, transport="rest")


# 2. توليد المحتوى بالذكاء الاصطناعي (مع مهلة سريعة تمنع التعليق)
async def generate_script():
    print("جاري توليد السيناريو...")
    prompt = """
    اكتب سيناريو فيديو ريلز (Reel) قصير وشيق جداً باللهجة العراقية المبسطة عن (معلومة غريبة، حقيقة تكنولوجية، أو نصيحة ذهبية للموبايل).
    يجب أن يكون النص مقسماً إلى 4 أو 5 جمل قصيرة جداً (كل جملة 3 إلى 5 كلمات فقط) لتناسب الموشن جرافيك السريع.
    حدد لكل جملة واحدة من هذه الحركات الستة المناسبة لشخصية الستيك مان:
    [WELCOME, THINKING, EXPLAINING, IDEA, PUZZLED, SUCCESS].
    
    أرجع النتيجة بصيغة JSON فقط بدون أي كلام إضافي:
    {
      "title": "عنوان مشوق وقصير",
      "caption": "وصف الريلز مع هاشتاغات مناسبة #تكنولوجيا #معلومات #العراق",
      "scenes": [
        {"text": "الجملة الأولى هنا", "pose": "WELCOME"},
        {"text": "الجملة الثانية هنا", "pose": "THINKING"},
        {"text": "الجملة الثالثة هنا", "pose": "EXPLAINING"},
        {"text": "الجملة الرابعة هنا", "pose": "IDEA"}
      ]
    }
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
            response = model.generate_content(prompt, request_options={"timeout": 20})
            text = response.text.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
            data = json.loads(text)
            if "scenes" in data and len(data["scenes"]) > 0:
                return data
    except Exception as e:
        print("تم استخدام السيناريو الاحتياطي بسبب:", e)

    return {
        "title": "سر بطارية موبايلك!",
        "caption": "ليش بطارية موبايلك تخلص بسرعة؟ معلومة تهمك! 🔋📱 #معلومات #تكنولوجيا #موبايل",
        "scenes": [
            {"text": "تدري ليش بطاريتك تخلص بسرعة؟", "pose": "WELCOME"},
            {"text": "أغلب التطبيقات تشتغل بالخلفية دائماً", "pose": "THINKING"},
            {"text": "وتسحب شحن وإنترنت بدون ما تدري", "pose": "EXPLAINING"},
            {"text": "طفي التحديث التلقائي وراح يفرق وياك!", "pose": "SUCCESS"}
        ]
    }


# 3. رسم شخصية الستيك مان حركياً
def draw_stickman(pose, frame_num, size=(1080, 1920)):
    img = Image.new("RGB", size, "#0B0F19")
    draw = ImageDraw.Draw(img)

    for i in range(0, size[0], 120):
        draw.line([(i, 0), (i, size[1])], fill="#141B2D", width=1)
    for j in range(0, size[1], 120):
        draw.line([(0, j), (size[0], j)], fill="#141B2D", width=1)

    primary_color = "#1A365D"
    glow_color = "#00F0FF"
    line_width = 16

    cx, cy = 540, 920
    bob = int(np.sin(frame_num * 0.25) * 8)
    cy += bob

    # الجسم والرأس والسماعات
    draw.line([(cx, cy), (cx, cy + 250)], fill=primary_color, width=line_width)
    draw.line([(cx, cy + 250), (cx - 80, cy + 450)], fill=primary_color, width=line_width)
    draw.line([(cx, cy + 250), (cx + 80, cy + 450)], fill=primary_color, width=line_width)
    draw.ellipse([(cx - 100, cy - 200), (cx + 100, cy)], outline=primary_color, width=line_width)

    draw.ellipse([(cx - 120, cy - 130), (cx - 95, cy - 70)], fill=glow_color, outline=primary_color, width=6)
    draw.ellipse([(cx + 95, cy - 130), (cx + 120, cy - 70)], fill=glow_color, outline=primary_color, width=6)
    draw.arc([(cx - 105, cy - 205), (cx + 105, cy - 90)], start=180, end=360, fill=primary_color, width=12)

    if pose == "WELCOME":
        draw.line([(cx, cy + 50), (cx - 100, cy + 180)], fill=primary_color, width=line_width)
        wave = int(np.sin(frame_num * 0.4) * 25)
        draw.line([(cx, cy + 50), (cx + 100, cy - 20), (cx + 140 + wave, cy - 100)], fill=primary_color, width=line_width)
        draw.ellipse([(cx - 45, cy - 125), (cx - 25, cy - 105)], fill=primary_color)
        draw.ellipse([(cx + 25, cy - 125), (cx + 45, cy - 105)], fill=primary_color)
        draw.arc([(cx - 40, cy - 100), (cx + 40, cy - 40)], start=10, end=170, fill=primary_color, width=8)

    elif pose == "THINKING":
        draw.line([(cx, cy + 50), (cx - 100, cy + 160)], fill=primary_color, width=line_width)
        draw.line([(cx, cy + 50), (cx + 60, cy + 40), (cx + 20, cy - 40)], fill=primary_color, width=line_width)
        draw.ellipse([(cx - 40, cy - 125), (cx - 20, cy - 105)], fill=primary_color)
        draw.ellipse([(cx + 20, cy - 125), (cx + 40, cy - 105)], fill=primary_color)
        draw.line([(cx - 25, cy - 70), (cx + 25, cy - 70)], fill=primary_color, width=6)

    elif pose == "EXPLAINING":
        explain_l = int(np.sin(frame_num * 0.3) * 15)
        draw.line([(cx, cy + 50), (cx - 110 - explain_l, cy - 20), (cx - 160 - explain_l, cy - 30)], fill=primary_color, width=line_width)
        draw.line([(cx, cy + 50), (cx + 110, cy + 30), (cx + 170, cy + 60)], fill=primary_color, width=line_width)
        draw.ellipse([(cx - 40, cy - 125), (cx - 20, cy - 105)], fill=primary_color)
        draw.ellipse([(cx + 20, cy - 125), (cx + 40, cy - 105)], fill=primary_color)

    elif pose == "IDEA":
        draw.line([(cx, cy + 50), (cx - 80, cy + 100), (cx - 40, cy + 160)], fill=primary_color, width=line_width)
        draw.line([(cx, cy + 50), (cx + 90, cy + 20), (cx + 110, cy - 100)], fill=primary_color, width=line_width)
        bulb_y = cy - 260 + int(np.cos(frame_num * 0.3) * 4)
        draw.ellipse([(cx + 90, bulb_y), (cx + 130, bulb_y + 40)], fill=glow_color)
        draw.ellipse([(cx - 45, cy - 130), (cx - 25, cy - 110)], fill=primary_color)
        draw.ellipse([(cx + 25, cy - 130), (cx + 45, cy - 110)], fill=primary_color)

    else:
        draw.line([(cx, cy + 50), (cx - 80, cy + 100), (cx - 40, cy + 160)], fill=primary_color, width=line_width)
        draw.line([(cx, cy + 50), (cx + 100, cy + 20), (cx + 140, cy - 30)], fill=primary_color, width=line_width)
        draw.ellipse([(cx - 45, cy - 125), (cx - 25, cy - 105)], fill=primary_color)
        draw.ellipse([(cx + 25, cy - 125), (cx + 45, cy - 105)], fill=primary_color)
        draw.arc([(cx - 40, cy - 100), (cx + 40, cy - 40)], start=10, end=170, fill=primary_color, width=8)

    return img


# 4. تجميع الفيديو بسرعة فائقة عبر الذاكرة (Pipe)
async def make_video(script_data):
    print("بدء صناعة ريلز الستيك مان...")
    scenes = script_data["scenes"]
    audio_paths = []
    voice = "ar-IQ-BasselNeural"

    for idx, sc in enumerate(scenes):
        audio_fn = f"scene_{idx}.mp3"
        clean_text = re.sub(r"\[.*?\]", "", sc["text"]).strip()
        communicate = edge_tts.Communicate(clean_text, voice)
        await communicate.save(audio_fn)
        audio_paths.append(audio_fn)

    font_path = "AQEEQ_SANS_PRO_ExtraLight.otf"
    if os.path.exists(font_path):
        print(f"تم تحميل الخط المحلي بنجاح: {font_path}")
        font_title = ImageFont.truetype(font_path, 54)
    else:
        print("تحذير: ملف الخط غير موجود، سيتم استخدام الخط الافتراضي.")
        font_title = ImageFont.load_default()

    video_clips = []
    fps = 15

    for idx, sc in enumerate(scenes):
        raw_text = sc["text"]
        display_text = fix_arabic(raw_text)
        pose = sc["pose"]
        audio_file = f"scene_{idx}.mp3"

        duration_cmd = f"ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 {audio_file}"
        duration = float(subprocess.check_output(duration_cmd, shell=True).decode().strip())
        total_frames = max(int(duration * fps), 15)

        print(f"ريندر سريع للمشهد {idx+1}/{len(scenes)} ({total_frames} إطار)...")
        output_scene = f"scene_{idx}.mp4"

        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-pix_fmt", "rgb24",
            "-s", "1080x1920",
            "-r", str(fps),
            "-i", "-",
            "-i", audio_file,
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-shortest",
            output_scene
        ]

        proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

        for f in range(total_frames):
            frame_img = draw_stickman(pose, f)
            draw = ImageDraw.Draw(frame_img)
            draw.rounded_rectangle([(80, 1480), (1000, 1730)], radius=30, fill="#0F172A", outline="#00F0FF", width=4)

            left, top, right, bottom = draw.textbbox((0, 0), display_text, font=font_title)
            tw = right - left
            th = bottom - top
            draw.text(((1080 - tw) // 2, 1605 - th // 2), display_text, fill="#FFFFFF", font=font_title)

            proc.stdin.write(frame_img.tobytes())

        proc.stdin.close()
        proc.wait()
        video_clips.append(output_scene)

    with open("inputs.txt", "w") as file:
        for clip in video_clips:
            file.write(f"file '{clip}'\n")

    final_output = "final_reel.mp4"
    merge_cmd = f"ffmpeg -y -f concat -safe 0 -i inputs.txt -c copy {final_output}"
    subprocess.run(merge_cmd, shell=True, check=True)

    for clip in video_clips:
        os.remove(clip)
    for audio in audio_paths:
        os.remove(audio)
    os.remove("inputs.txt")

    print(f"اكتمل بناء الفيديو بنجاح: {final_output}")
    return final_output


# 5. نشر الفيديو على بيج فيسبوك مع طباعة رد السيرفر بدقة
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
    script_data = await generate_script()
    final_video = await make_video(script_data)
    publish_to_facebook(final_video, script_data["title"], script_data["caption"])


if __name__ == "__main__":
    asyncio.run(main())
