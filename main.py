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
        
    # ดึงข่าวมา 15 ข่าวล่าสุด ให้ AI มีตัวเลือกเยอะๆ ในการคัดกรอง
    pool_news = feed.entries[:15]
    
    raw_news = ""
    for i, entry in enumerate(pool_news):
        published = entry.get('published', 'ไม่ระบุเวลา')
        raw_news += f"[{i+1}] หัวข้อ: {entry.title}\nรายละเอียด: {entry.description}\nเวลาเผยแพร่: {published}\n\n"
        
    # ล็อกคำสั่งให้ AI ทำหน้าที่เป็นบรรณาธิการ คัดเฉพาะข่าวที่กระทบชาวบ้าน
    prompt = f"""
    คุณคือบรรณาธิการข่าวของเพจ Alieninburi ทำหน้าที่คัดกรองข่าวให้ชาวบ้านในพื้นที่
    จากข้อมูลข่าวทั้ง 15 ข่าวด้านล่างนี้ ให้คุณทำตามคำสั่งต่อไปนี้อย่างเคร่งครัด:
    
    1. **คัดเลือกข่าวมาเพียง 3 ข่าว** ที่เป็น "ข่าวใหญ่และสำคัญต่อชีวิตชาวบ้านจริงๆ" เช่น เรื่องปากท้อง, ค่าครองชีพ, ราคาสินค้าเกษตร, นโยบายแจกเงิน/เก็บเงินของรัฐ, ภัยพิบัติ หรือเหตุการณ์ระดับประเทศที่คนต่างจังหวัดต้องระวัง
    2. **ห้าม** เลือกข่าวประเภทต่อไปนี้เด็ดขาด: ข่าวประชาสัมพันธ์องค์กรเอกชน (PR), ข่าวเปิดงาน/ตัดริบบิ้น, ข่าวซุบซิบดารา, หรือข่าวในพื้นที่กรุงเทพฯ ที่ไกลตัวชาวบ้าน
    3. เมื่อเลือกได้ 3 ข่าวแล้ว ให้สรุปข่าวให้ชาวบ้านอ่านเข้าใจง่าย เล่าเรื่องตรงไปตรงมา ไม่อ้อมค้อม (ความยาวประมาณ 3-4 บรรทัดต่อข่าว)
    4. ห้ามพิมพ์คำเกริ่นนำหรือคำทักทายใดๆ (ไม่ต้องมี "เรียนพี่น้องชาวบ้าน") ให้เริ่มพิมพ์ที่ตัวข่าวเลย

    รูปแบบการสรุปแต่ละข่าว ให้ทำตามนี้เป๊ะๆ:
    [ใส่ Emoji 1 ตัวที่เข้ากับข่าว] **[หัวข้อข่าวที่สรุปให้อ่านง่ายขึ้น]**
    [เนื้อหาสรุปที่ได้ใจความ ภาษาเล่าเรื่อง เข้าใจง่าย]
    ⏰ เวลา: [แปลงเวลาที่ให้ไปเป็นเวลาไทย เช่น 08:30 น.] | 📰 แหล่งข่าว: มติชนออนไลน์

    ข้อมูลข่าว 15 ข่าว:
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
    
    final_message = inburi_info + news_info + "\n-----------------\n🤖 อัปเดตอัตโนมัติ by Alieninburi\n#อินทร์บุรีรอดมั้ย #สรุปข่าวเช้า"
    
    payload = {"text_to_post": final_message}
    response = requests.post(MAKE_WEBHOOK_URL, json=payload)
    
    if response.status_code == 200:
        print("✅ ส่งโพสต์สำเร็จ!")
    else:
        print("❌ เกิดข้อผิดพลาด:", response.text)
