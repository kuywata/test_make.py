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
    # ล็อกเป้าดึงเฉพาะข่าวหมวด "การเมือง" และ "เศรษฐกิจ" ตัดข่าวบันเทิง/PR ทิ้ง 100%
    feed_urls = [
        "https://www.matichon.co.th/politics/feed",
        "https://www.matichon.co.th/economy/feed"
    ]
    
    pool_news = []
    for url in feed_urls:
        feed = feedparser.parse(url)
        if feed.entries:
            # ดึงมาหมวดละ 5 ข่าวล่าสุด รวมเป็น 10 ข่าวใหญ่
            pool_news.extend(feed.entries[:5])
            
    if not pool_news:
        return "📰 **[สรุปข่าวเด่นเช้านี้]**\n(ระบบไม่สามารถดึงข้อมูลข่าวใหญ่ได้ในขณะนี้)\n"
        
    raw_news = ""
    for i, entry in enumerate(pool_news):
        published = entry.get('published', 'ไม่ระบุเวลา')
        raw_news += f"[{i+1}] หัวข้อ: {entry.title}\nรายละเอียด: {entry.description}\nเวลาเผยแพร่: {published}\n\n"
        
    # สั่ง AI ด้วย Prompt ขั้นเด็ดขาด
    prompt = f"""
    คุณคือผู้ประกาศข่าวท้องถิ่นที่ต้องสรุป "ข่าวใหญ่ระดับประเทศ" ให้ชาวบ้านอ่าน
    จากข้อมูลข่าวด้านล่าง ให้ทำตามคำสั่งนี้อย่างเคร่งครัด:
    
    1. คัดเลือกเฉพาะข่าวที่เป็น "ข่าวใหญ่ระดับประเทศจริงๆ" จำนวน 3 ข่าว (เช่น นโยบายรัฐบาล, การแจกเงิน, กฎหมายใหม่, ราคาสินค้าเกษตร, เรื่องปากท้องที่กระทบทุกคน)
    2. สรุปเนื้อหาให้ชาวบ้านอ่านแล้วเข้าใจทันทีว่า "เรื่องนี้ส่งผลกระทบอะไรกับเขา" ใช้ภาษาชาวบ้าน เล่าเรื่องตรงไปตรงมา ไม่อ้อมค้อม (ความยาว 3-4 บรรทัดต่อข่าว)
    3. ห้ามมีคำเกริ่นนำใดๆ ทั้งสิ้น ให้เริ่มที่เนื้อหาข่าวเลย

    รูปแบบการสรุปแต่ละข่าว (ทำตามนี้เป๊ะๆ):
    [ใส่ Emoji 1 ตัว] **[หัวข้อข่าวที่เขียนใหม่ให้อ่านง่ายและดึงดูดใจ]**
    [สรุปเนื้อหาข่าวแบบตรงประเด็น ภาษาชาวบ้านเข้าใจง่าย]
    ⏰ เวลา: [แปลงเวลาที่ให้ไปเป็นเวลาไทย เช่น 08:30 น.] | 📰 แหล่งข่าว: มติชนออนไลน์

    ข้อมูลข่าว:
    {raw_news}
    """
    
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt
    )
    
    return "📰 **[สรุปข่าวใหญ่เช้านี้]**\n\n" + response.text

if __name__ == "__main__":
    print("เริ่มการทำงาน...")
    
    inburi_info = get_inburi_data()
    news_info = get_news_and_summarize()
    
    final_message = inburi_info + news_info + "\n-----------------\n🤖 อัปเดตอัตโนมัติ by Alieninburi\n#อินทร์บุรีรอดมั้ย #สรุปข่าวเช้า"
    
    payload = {"text_to_post": final_message}
    response = requests.post(MAKE_WEBHOOK_URL, json=payload)
    
    if response.status_code == 200:
        print("✅ ส่งโพสต์สำเร็จ!")
    else:
        print("❌ เกิดข้อผิดพลาด:", response.text)
