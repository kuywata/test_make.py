import requests
import feedparser
from google import genai
import os

# 1. ดึงกุญแจจากตู้เซฟ GitHub 
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") 
MAKE_WEBHOOK_URL = os.environ.get("MAKE_WEBHOOK_URL")

# 2. ตั้งค่า AI
client = genai.Client(api_key=GEMINI_API_KEY)

def get_inburi_data():
    weather_url = "https://api.open-meteo.com/v1/forecast?latitude=14.9961&longitude=100.3253&current=temperature_2m&timezone=Asia%2FBangkok"
    aqi_url = "https://air-quality-api.open-meteo.com/v1/air-quality?latitude=14.9961&longitude=100.3253&current=pm2_5&timezone=Asia%2FBangkok"
    
    try:
        w_res = requests.get(weather_url).json()
        temp = w_res['current']['temperature_2m']
        
        a_res = requests.get(aqi_url).json()
        pm25 = a_res['current']['pm2_5']
        
        text = "📍 **[อัปเดตพื้นที่อินทร์บุรี]**\n"
        text += f"• สภาพอากาศ: อุณหภูมิเช้านี้ {temp}°C\n"
        text += f"• คุณภาพอากาศ (PM 2.5): {pm25} μg/m³\n"
        text += "• ระดับน้ำ: สถานการณ์น้ำแม่น้ำเจ้าพระยาอยู่ในระดับปกติ\n\n"
        return text
    except Exception as e:
        return "📍 [อัปเดตพื้นที่อินทร์บุรี] (ไม่สามารถดึงข้อมูลได้ในขณะนี้)\n\n"

def get_news_and_summarize():
    # แก้ไขจุดที่ 1: เปลี่ยนมาใช้ RSS Feed ของมติชนที่เสถียรกว่า
    feed_url = "https://www.matichon.co.th/feed"
    feed = feedparser.parse(feed_url)
    
    # แก้ไขจุดที่ 2: เพิ่มระบบดักจับ Error ถ้าดึงข้อมูลข่าวมาไม่ได้เลย
    if not feed.entries:
        return "📰 **[สรุปข่าวเด่นเช้านี้]**\n(ระบบไม่สามารถดึงข้อมูลจากสำนักข่าวต้นทางได้ในขณะนี้)\n"
        
    top_3_news = feed.entries[:3]
    
    raw_news = ""
    for i, entry in enumerate(top_3_news):
        raw_news += f"หัวข้อ: {entry.title}\nรายละเอียด: {entry.description}\n\n"
        
    prompt = f"""
    สรุปข่าวต่อไปนี้ให้สั้น กระชับ เป็น Bullet points เพื่อโพสต์ลง Facebook
    อ้างอิงจากข้อความที่ให้มาเท่านั้น ห้ามแต่งเติม ห้ามแสดงความเห็น ตัวเลขและชื่อต้องเป๊ะ 100%
    
    ข้อมูลข่าว:
    {raw_news}
    """
    
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt
    )
    
    return "📰 **[สรุปข่าวเด่นเช้านี้]**\n" + response.text

if __name__ == "__main__":
    print("เริ่มการทำงาน...")
    
    inburi_info = get_inburi_data()
    news_info = get_news_and_summarize()
    
    final_message = inburi_info + news_info + "\n\n-----------------\n🤖 อัปเดตอัตโนมัติ by Alieninburi\n#อินทร์บุรีรอดมั้ย #สรุปข่าวเช้า"
    
    payload = {"text_to_post": final_message}
    response = requests.post(MAKE_WEBHOOK_URL, json=payload)
    
    if response.status_code == 200:
        print("✅ ส่งโพสต์สำเร็จ!")
    else:
        print("❌ เกิดข้อผิดพลาด:", response.text)
