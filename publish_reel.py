import os
import requests
import asyncio
import json
import random
import re
from PIL import Image, ImageDraw, ImageFont
import numpy as np
import google.generativeai as genai
import edge_tts
import subprocess

# 1. إعداد المفاتيح والاتصالات
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN")
FB_PAGE_ID = os.getenv("FB_PAGE_ID")

genai.configure(api_key=GEMINI_API_KEY)

# 2. توليد المحتوى بالذكاء الاصطناعي
async def generate_script():
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = """
    اكتب سيناريو فيديو ريلز (Reel) قصير وشيق جداً باللهجة العراقية المبسطة أو العربية الفصحى الخفيفة عن (معلومة غريبة، حقيقة تكنولوجية، أو نصيحة ذهبية سريعة).
    يجب أن يكون النص مقسماً إلى 5 أو 6 جمل قصيرة جداً (كل جملة لا تتعدى 5 كلمات) لتناسب الموشن جرافيك السريع.
    حدد لكل جملة واحدة من هذه الحركات الستة المناسبة لشخصية الستيك مان:
    [WELCOME (ترحيب), THINKING (تفكير), EXPLAINING (شرح), IDEA (فكرة وإلهام), PUZZLED (حيرة), SUCCESS (إنجاز وتأكيد)].
    
    أرجع النتيجة بصيغة JSON فقط كالتالي:
    {
      "title": "عنوان مشوق وقصير جداً",
      "caption": "وصف الريلز مع هاشتاغات مناسبة",
      "scenes": [
        {"text": "الجملة الأولى هنا", "pose": "WELCOME"},
        {"text": "الجملة الثانية هنا", "pose": "THINKING"},
        {"text": "الجملة الثالثة هنا", "pose": "EXPLAINING"},
        {"text": "الجملة الرابعة هنا", "pose": "PUZZLED"},
        {"text": "الجملة الخامسة هنا", "pose": "IDEA"},
        {"text": "الجملة السادسة هنا", "pose": "SUCCESS"}
      ]
    }
    """
    response = model.generate_content(prompt)
    try:
        # تنظيف النص واستخراج الـ JSON
        text = response.text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        return json.loads(text)
    except Exception as e:
        print("خطأ في قراءة الـ JSON:", e)
        # سيناريو احتياطي في حال حدوث خطأ
        return {
            "title": "معلومة سريعة!",
            "caption": "هل كنت تعرف هذه المعلومة؟ 🤔💬 #معلومات #تكنولوجيا #ستيك_مان",
            "scenes": [
                {"text": "هلو عيوني! اليوم جبتلكم معلومة سريعة", "pose": "WELCOME"},
                {"text": "عمرك فكرت شلون يشتغل الذكاء الاصطناعي؟", "pose": "THINKING"},
                {"text": "هو مثل عقلك بس يقرأ ملايين البيانات بثانية", "pose": "EXPLAINING"},
                {"text": "يعني مو سحر، السالفة كلها رياضيات!", "pose": "PUZZLED"},
                {"text": "وتكدر تستفاد منه اليوم لتسهيل شغلك!", "pose": "IDEA"},
                {"text": "جربوه وشاركوني رأيكم بالتعليقات!", "pose": "SUCCESS"}
            ]
        }

# 3. رسم شخصية الستيك مان B حركياً (وضعيات 2D)
def draw_stickman(pose, frame_num, size=(1080, 1920)):
    # إنشاء خلفية رقمية متحركة
    img = Image.new("RGBA", size, "#0B0F19")
    draw = ImageDraw.Draw(img)
    
    # رسم خطوط رقمية أو شبكة خفيفة في الخلفية
    for i in range(0, size[0], 80):
        draw.line([(i, 0), (i, size[1])], fill="#141B2D", width=1)
    for j in range(0, size[1], 80):
        draw.line([(0, j), (size[0], j)], fill="#141B2D", width=1)
        
    # نبضات رقمية متحركة بالخلفية بناءً على رقم الإطار
    offset = (frame_num * 3) % 80
    for y in range(offset, size[1], 160):
        draw.line([(0, y), (size[0], y)], fill="#1E2942", width=2)

    # إعدادات الشخصية B (اللون الكحلي الغامق والأزرق المتوهج)
    primary_color = "#1A365D" # كحلي
    glow_color = "#00F0FF" # أزرق متوهج (سماوي)
    line_width = 16
    
    # مركز الشخصية في الشاشة
    cx, cy = 540, 960
    
    # حركة تنفس خفيفة (بوبينغ) للشخصية بناءً على الإطار
    bob = int(np.sin(frame_num * 0.15) * 8)
    cy += bob
    
    # رسم الجسم (الخط العمودي)
    draw.line([(cx, cy), (cx, cy + 250)], fill=primary_color, width=line_width)
    
    # رسم الأرجل (ثابتة في الأسفل)
    draw.line([(cx, cy + 250), (cx - 80, cy + 450)], fill=primary_color, width=line_width)
    draw.line([(cx, cy + 250), (cx + 80, cy + 450)], fill=primary_color, width=line_width)
    
    # رسم الرأس (دائرة فارغة مع سماعات)
    draw.ellipse([(cx - 100, cy - 200), (cx + 100, cy)], outline=primary_color, width=line_width)
    
    # رسم سماعات الرأس B المميزة الرقمية (Cyan headsets)
    draw.ellipse([(cx - 120, cy - 130), (cx - 95, cy - 70)], fill=glow_color, outline=primary_color, width=6) # اليسرى
    draw.ellipse([(cx + 95, cy - 130), (cx + 120, cy - 70)], fill=glow_color, outline=primary_color, width=6) # اليمنى
    draw.arc([(cx - 105, cy - 205), (cx + 105, cy - 90)], start=180, end=360, fill=primary_color, width=12) # القوس العلوي للسماعة
    
    # مايك السماعة الصغير
    draw.line([(cx - 110, cy - 80), (cx - 60, cy - 50)], fill=primary_color, width=6)
    draw.ellipse([(cx - 62, cy - 52), (cx - 52, cy - 42)], fill=glow_color)

    # رسم اليدين والملامح حسب الوضعية (Pose)
    if pose == "WELCOME": # ترحيب
        draw.line([(cx, cy + 50), (cx - 100, cy + 180)], fill=primary_color, width=line_width) # اليد اليسرى لأسفل
        # اليد اليمنى تلوح بالأعلى (تتحرك مع الإطارات)
        wave = int(np.sin(frame_num * 0.3) * 25)
        draw.line([(cx, cy + 50), (cx + 100, cy - 20), (cx + 140 + wave, cy - 100)], fill=primary_color, width=line_width)
        # ملامح سعيدة
        draw.ellipse([(cx - 45, cy - 125), (cx - 25, cy - 105)], fill=primary_color) # عين يسرى
        draw.ellipse([(cx + 25, cy - 125), (cx + 45, cy - 105)], fill=primary_color) # عين يمنى
        draw.arc([(cx - 40, cy - 100), (cx + 40, cy - 40)], start=10, end=170, fill=primary_color, width=8) # فم مبتسم
        
    elif pose == "THINKING": # تفكير
        # اليد اليسرى متكئة أو لأسفل
        draw.line([(cx, cy + 50), (cx - 100, cy + 160)], fill=primary_color, width=line_width)
        # اليد اليمنى تفكر وتلمس الذقن
        draw.line([(cx, cy + 50), (cx + 60, cy + 40), (cx + 20, cy - 40)], fill=primary_color, width=line_width)
        # ملامح متسائلة
        draw.ellipse([(cx - 40, cy - 125), (cx - 20, cy - 105)], fill=primary_color)
        draw.ellipse([(cx + 20, cy - 125), (cx + 40, cy - 105)], fill=primary_color)
        draw.line([(cx - 25, cy - 70), (cx + 25, cy - 70)], fill=primary_color, width=6) # فم مستقيم للتفكير
        # علامات تساؤل (?) متحركة رقمية بالأعلى
        q_y = cy - 260 + int(np.sin(frame_num * 0.1) * 5)
        draw.text((cx + 110, q_y), "?", fill=glow_color, font=None, font_size=80)
        
    elif pose == "EXPLAINING": # شرح
        # اليد اليسرى تشرح لليسار
        explain_l = int(np.sin(frame_num * 0.2) * 15)
        draw.line([(cx, cy + 50), (cx - 110 - explain_l, cy - 20), (cx - 160 - explain_l, cy - 30)], fill=primary_color, width=line_width)
        # اليد اليمنى تشرح لليمين
        draw.line([(cx, cy + 50), (cx + 110, cy + 30), (cx + 170, cy + 60)], fill=primary_color, width=line_width)
        # ملامح مندمجة بالشرح
        draw.ellipse([(cx - 40, cy - 125), (cx - 20, cy - 105)], fill=primary_color)
        draw.ellipse([(cx + 20, cy - 125), (cx + 40, cy - 105)], fill=primary_color)
        draw.arc([(cx - 30, cy - 90), (cx + 30, cy - 50)], start=0, end=180, fill=primary_color, width=6) # فم مفتوح للشرح
        
    elif pose == "IDEA": # فكرة وإلهام
        # اليد اليسرى على الخصر
        draw.line([(cx, cy + 50), (cx - 80, cy + 100), (cx - 40, cy + 160)], fill=primary_color, width=line_width)
        # اليد اليمنى تشير للأعلى باتجاه المصباح (الرمز)
        draw.line([(cx, cy + 50), (cx + 90, cy + 20), (cx + 110, cy - 100)], fill=primary_color, width=line_width)
        # مصباح فكرة متوهج (Idea Bulb)
        bulb_y = cy - 260 + int(np.cos(frame_num * 0.2) * 4)
        draw.ellipse([(cx + 90, bulb_y), (cx + 130, bulb_y + 40)], fill=glow_color)
        draw.line([(cx + 110, bulb_y + 40), (cx + 110, bulb_y + 55)], fill="#FFA500", width=8)
        # ملامح منبهرة / سعيدة
        draw.ellipse([(cx - 45, cy - 130), (cx - 25, cy - 110)], fill=primary_color)
        draw.ellipse([(cx + 25, cy - 130), (cx + 45, cy - 110)], fill=primary_color)
        draw.arc([(cx - 40, cy - 95), (cx + 40, cy - 45)], start=10, end=170, fill=primary_color, width=8)
        
    elif pose == "PUZZLED": # حيرة
        # اليد اليسرى تلمس الرأس من الحيرة
        draw.line([(cx, cy + 50), (cx - 90, cy - 30), (cx - 50, cy - 150)], fill=primary_color, width=line_width)
        # اليد اليمنى لأسفل
        draw.line([(cx, cy + 50), (cx + 90, cy + 170)], fill=primary_color, width=line_width)
        # ملامح مرتبكة / حائرة
        draw.ellipse([(cx - 40, cy - 120), (cx - 25, cy - 105)], fill=primary_color)
        draw.ellipse([(cx + 25, cy - 120), (cx + 40, cy - 105)], fill=primary_color)
        # حواجب مائلة للحيرة
        draw.line([(cx - 45, cy - 135), (cx - 20, cy - 130)], fill=primary_color, width=6)
        draw.line([(cx + 20, cy - 130), (cx + 45, cy - 135)], fill=primary_color, width=6)
        draw.arc([(cx - 25, cy - 80), (cx + 25, cy - 50)], start=180, end=360, fill=primary_color, width=6) # فم متموج/قلق
        
    else: # SUCCESS (إنجاز وتأكيد)
        # اليد اليسرى على الخصر
        draw.line([(cx, cy + 50), (cx - 80, cy + 100), (cx - 40, cy + 160)], fill=primary_color, width=line_width)
        # اليد اليمنى ترفع الإبهام (Thumbs up) بحماس
        draw.line([(cx, cy + 50), (cx + 100, cy + 20), (cx + 140, cy - 30)], fill=primary_color, width=line_width)
        draw.ellipse([(cx + 135, cy - 45), (cx + 155, cy - 25)], fill=glow_color) # إشارة Thumbs-up رقمية
        # ملامح سعيدة جداً وفخورة
        draw.ellipse([(cx - 45, cy - 125), (cx - 25, cy - 105)], fill=primary_color)
        draw.ellipse([(cx + 25, cy - 125), (cx + 45, cy - 105)], fill=primary_color)
        draw.arc([(cx - 45, cy - 100), (cx + 45, cy - 40)], start=10, end=170, fill=primary_color, width=10) # ابتسامة عريضة
        # نجوم رقمية متوهجة متطايرة
        for i in range(3):
            sx = cx + 180 + int(np.sin(frame_num*0.2 + i)*15)
            sy = cy - 100 - i*60
            draw.text((sx, sy), "✦", fill=glow_color, font_size=40)

    return img

# 4. تجميع الفيديو بالصوت والكتابة والتحريك
async def make_video(script_data):
    print("بدء صناعة ريلز الستيك مان...")
    scenes = script_data["scenes"]
    audio_paths = []
    
    # 1. توليد الأصوات لكل مشهد (عراقي أو فصيح ممتاز)
    # نستخدم صوت 'ar-EG-ShakirNeural' أو 'ar-IQ-BasselNeural' (عراقي)
    voice = "ar-IQ-BasselNeural"
    
    for idx, sc in enumerate(scenes):
        audio_fn = f"scene_{idx}.mp3"
        # تنظيف النص من أي رموز غريبة
        clean_text = re.sub(r'\[.*?\]', '', sc["text"]).strip()
        communicate = edge_tts.Communicate(clean_text, voice)
        await communicate.save(audio_fn)
        audio_paths.append(audio_fn)

    # 2. توليد إطارات الصور ودمج النصوص
    # تحميل الخط العربي (إذا لم يوجد نستخدم الافتراضي)
    font_path = "Cairo-Bold.ttf"
    if not os.path.exists(font_path):
        # تحميل خط Cairo-Bold مجاناً للاستخدام في الكتابة بجمالية عالية
        print("تحميل الخط العربي...")
        font_url = "[https://github.com/google/fonts/raw/main/ofl/cairo/Cairo%5Bwght%5D.ttf](https://github.com/google/fonts/raw/main/ofl/cairo/Cairo%5Bwght%5D.ttf)"
        r = requests.get(font_url)
        with open(font_path, "wb") as f:
            f.write(r.content)
            
    # توليد الفيديو لكل مشهد ودمجهم
    video_clips = []
    
    for idx, sc in enumerate(scenes):
        text = sc["text"]
        pose = sc["pose"]
        audio_file = f"scene_{idx}.mp3"
        
        # معرفة مدة الصوت باستخدام ffprobe
        duration_cmd = f"ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 {audio_file}"
        duration = float(subprocess.check_output(duration_cmd, shell=True).decode().strip())
        fps = 30
        total_frames = int(duration * fps)
        
        print(f"جاري معالجة المشهد {idx+1}/{len(scenes)} (الوضعية: {pose}, المدة: {duration} ثواني)")
        
        # إنشاء الصور للإطارات
        for f in range(total_frames):
            frame_img = draw_stickman(pose, f)
            draw = ImageDraw.Draw(frame_img)
            
            # رسم مربع النص السفلي الأنيق (Subtitles Panel)
            draw.rounded_rectangle([(100, 1500), (980, 1750)], radius=30, fill="#0F172AEE", outline="#00F0FF", width=4)
            
            # كتابة النص العربي بوضوح وبخط كبير في الأسفل
            font_title = ImageFont.truetype(font_path, 48)
            # توسيط النص في المربع
            left, top, right, bottom = draw.textbbox((0, 0), text, font=font_title)
            tw = right - left
            draw.text(((1080 - tw) // 2, 1600), text, fill="#FFFFFF", font=font_title)
            
            # حفظ الإطار مؤقتاً
            frame_img.save(f"frame_{idx:03d}_{f:03d}.png")
            
        # تحويل الإطارات إلى مقطع فيديو مؤقت مع الصوت
        # نستخدم ffmpeg لدمج الصور والصوت معاً لهذا المشهد
        output_scene = f"scene_{idx}.mp4"
        ffmpeg_cmd = f"ffmpeg -y -framerate {fps} -i frame_{idx:03d}_%03d.png -i {audio_file} -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest {output_scene}"
        subprocess.run(ffmpeg_cmd, shell=True, check=True)
        video_clips.append(output_scene)
        
        # تنظيف الإطارات المؤقتة لتوفير مساحة
        for f in range(total_frames):
            os.remove(f"frame_{idx:03d}_{f:03d}.png")

    # 3. دمج مشاهد الفيديو كلها في فيديو ريلز واحد نهائي
    with open("inputs.txt", "w") as file:
        for clip in video_clips:
            file.write(f"file '{clip}'\n")
            
    final_output = "final_reel.mp4"
    merge_cmd = f"ffmpeg -y -f concat -safe 0 -i inputs.txt -c copy {final_output}"
    subprocess.run(merge_cmd, shell=True, check=True)
    
    # تنظيف الفيديوهات الفردية المؤقتة
    for clip in video_clips:
        os.remove(clip)
    for audio in audio_paths:
        os.remove(audio)
    os.remove("inputs.txt")
    
    print(f"تم توليد ريلز الستيك مان بنجاح! {final_output}")
    return final_output

# 5. نشر الفيديو على بيج فيسبوك (تلقائياً)
def publish_to_facebook(video_path, title, caption):
    print("بدء عملية النشر على فيسبوك ريلز...")
    
    # الخطوة 1: بدء جلسة الرفع (Initialization)
    init_url = f"[https://graph.facebook.com/v18.0/](https://graph.facebook.com/v18.0/){FB_PAGE_ID}/video_reels"
    init_params = {
        "access_token": FB_ACCESS_TOKEN,
        "upload_phase": "start"
    }
    init_res = requests.post(init_url, params=init_params).json()
    print("الاستجابة الأولى:", init_res)
    
    video_id = init_res.get("video_id")
    upload_url = init_res.get("upload_url")
    
    if not video_id or not upload_url:
        print("خطأ في بدء جلسة الرفع بالفيسبوك!")
        return False
        
    # الخطوة 2: رفع ملف الفيديو (Upload)
    headers = {
        "Authorization": f"OAuth {FB_ACCESS_TOKEN}",
        "offset": "0",
        "file_size": str(os.path.getsize(video_path))
    }
    with open(video_path, "rb") as f:
        upload_res = requests.post(upload_url, headers=headers, data=f).json()
    print("استجابة الرفع:", upload_res)
    
    # الخطوة 3: نشر وتحديد العنوان والوصف (Publish)
    publish_url = f"[https://graph.facebook.com/v18.0/](https://graph.facebook.com/v18.0/){FB_PAGE_ID}/video_reels"
    publish_params = {
        "access_token": FB_ACCESS_TOKEN,
        "video_id": video_id,
        "upload_phase": "finish",
        "video_state": "PUBLISHED",
        "title": title,
        "description": caption
    }
    publish_res = requests.post(publish_url, params=publish_params).json()
    print("استجابة النشر النهائية:", publish_res)
    
    if publish_res.get("success") or "video_id" in publish_res:
        print("مبروك! تم نشر فيديو الستيك مان بنجاح على فيسبوك! 🎉🚀")
        return True
    else:
        print("حدث خطأ في عملية النشر!")
        return False

# تشغيل الأوتومايشن
async def main():
    script_data = await generate_script()
    print("السيناريو المولد:", json.dumps(script_data, ensure_ascii=False, indent=2))
    
    final_video = await make_video(script_data)
    
    # نشر
    publish_to_facebook(final_video, script_data["title"], script_data["caption"])

if __name__ == "__main__":
    asyncio.run(main())
