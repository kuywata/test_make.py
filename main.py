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
    feed_url = "https://www.matichon.co.th/feed"
    feed = feedparser.parse(feed_url)
    
    if not feed.entries:
        return "📰 **[สรุปข่าวเด่นเช้านี้]**\n(ระบบไม่สามารถดึงข้อมูลจากสำนักข่าวต้นทางได้ในขณะนี้)\n"
        
    top_3_news = feed.entries[:3]
    
    raw_news = ""
    for i, entry in enumerate(top_3_news):
        # ดึงเวลาเผยแพร่จากระบบ RSS มาด้วย
        published = entry.get('published', 'ไม่ระบุเวลา')
        raw_news += f"ข่าวที่ {i+1}\nหัวข้อ: {entry.title}\nรายละเอียด: {entry.description}\nเวลาเผยแพร่: {published}\n\n"
        
    # ปรับ Prompt ใหม่ให้ภาษาชาวบ้าน อ่านง่าย ได้ใจความ มีเวลาและแหล่งข่าว
    prompt = f"""
    คุณคือผู้ประกาศข่าวท้องถิ่นที่ใกล้ชิดชาวบ้าน โปรดสรุปข่าวต่อไปนี้ให้ชาวบ้านอ่านเข้าใจง่าย ได้ใจความครบถ้วน รู้เรื่องตั้งแต่ต้นจนจบ (ความยาวประมาณ 3-5 บรรทัดต่อข่าว)
    อ้างอิงจากข้อความที่ให้มาเท่านั้น ห้ามแต่งเติมข้อมูลเอง ห้ามแสดงความเห็นส่วนตัว ข้อมูลตัวเลข สถานที่ และชื่อคนต้องถูกต้อง 100%

    รูปแบบการสรุปแต่ละข่าว ให้ทำตามนี้เป๊ะๆ:
    [ใส่ Emoji 1 ตัวที่เข้ากับข่าว] **[หัวข้อข่าวที่สรุปให้อ่านง่ายขึ้น]**
    [เนื้อหาสรุปที่ได้ใจความ ภาษาเล่าเรื่อง เข้าใจง่าย]
    ⏰ เวลา: [แปลงเวลาที่ให้ไปเป็นเวลาไทยที่อ่านง่าย เช่น 08:30 น.] | 📰 แหล่งข่าว: มติชนออนไลน์

    ข้อมูลข่าว:
    {raw_news}
    """
    
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt
    )
    
    return "📰 **[สรุปข่าวเด่นเช้านี้]**\n\n" + response.text

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
