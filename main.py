import requests
from google import genai
from google.genai import types
import os

# 1. ดึงกุญแจจากตู้เซฟ GitHub 
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") 
MAKE_WEBHOOK_URL = os.environ.get("MAKE_WEBHOOK_URL")

# 2. ตั้งค่า AI
client = genai.Client(api_key=GEMINI_API_KEY)

def get_inburi_weather():
    # ล็อกพิกัดไว้ที่ "ตลาดอินทร์บุรี" (Lat 14.9961, Lon 100.3253) 
    # เพื่อให้ได้ข้อมูลฝุ่นและอากาศที่แม่นยำระดับตำบล
    weather_url = "https://api.open-meteo.com/v1/forecast?latitude=14.9961&longitude=100.3253&current=temperature_2m&timezone=Asia%2FBangkok"
    aqi_url = "https://air-quality-api.open-meteo.com/v1/air-quality?latitude=14.9961&longitude=100.3253&current=pm2_5&timezone=Asia%2FBangkok"
    
    try:
        w_res = requests.get(weather_url).json()
        temp = w_res['current']['temperature_2m']
        
        a_res = requests.get(aqi_url).json()
        pm25 = a_res['current']['pm2_5']
        return temp, pm25
    except Exception:
        return "N/A", "N/A"

def generate_local_update(temp, pm25):
    prompt = f"""
    คุณคือผู้ช่วยรายงานสถานการณ์ท้องถิ่น อ.อินทร์บุรี จ.สิงห์บุรี ที่มีความแม่นยำสูงที่สุด
    วันนี้อุณหภูมิเช้านี้คือ {temp}°C และ PM 2.5 คือ {pm25} μg/m³ (อ้างอิงพื้นที่ตลาดอินทร์บุรี)

    คำสั่ง:
    ให้คุณค้นหาข้อมูลผ่าน Google Search ทันที เพื่อหา "รายงานสถานการณ์น้ำประจำวัน กรมชลประทาน ล่าสุด" หรือข่าวสถานการณ์น้ำล่าสุดเกี่ยวกับ:
    1. ระดับน้ำแม่น้ำเจ้าพระยาที่ ต.อินทร์บุรี (หรือ จ.สิงห์บุรี สถานี C.3) ปัจจุบันระดับน้ำสูงเท่าไหร่ และ "ห่างจากระดับตลิ่งเท่าไหร่"
    2. ปริมาณการระบายน้ำของ "เขื่อนเจ้าพระยา" ล่าสุด ว่าปล่อยน้ำกี่ ลบ.ม./วินาที และเปรียบเทียบกับ "เมื่อวานว่าปล่อยที่กี่ ลบ.ม./วินาที"

    จากนั้นให้จัดทำโพสต์สรุป โดยใช้รูปแบบด้านล่างนี้เป๊ะๆ (ห้ามมีคำเกริ่นนำ ห้ามพิมพ์อธิบายเพิ่ม ห้ามมีข่าวอื่นปนเด็ดขาด):

    📍 **[อัปเดตพื้นที่อินทร์บุรี]**
    • สภาพอากาศ: อุณหภูมิเช้านี้ {temp}°C
    • คุณภาพอากาศ (PM 2.5): {pm25} μg/m³
    • ระดับน้ำอินทร์บุรี: [ใส่ตัวเลขระดับน้ำปัจจุบัน] (ห่างจากตลิ่ง [ใส่ตัวเลข] เมตร)
    • เขื่อนเจ้าพระยาปล่อยน้ำ: วันนี้ [ใส่ปริมาณวันนี้] ลบ.ม./วินาที (เมื่อวาน [ใส่ปริมาณเมื่อวาน] ลบ.ม./วินาที)

    ⚠️ กฎเหล็ก: 
    1. ข้อมูลตัวเลขน้ำต้องมาจากความจริงที่ค้นพบจากแหล่งข่าว/กรมชลประทานเท่านั้น ห้ามเดาหรือแต่งเลขเองเด็ดขาด 
    2. หากระบบค้นหาไม่พบข้อมูลระดับน้ำของเช้าวันนี้จริงๆ ให้เขียนในช่องนั้นว่า "รออัปเดตข้อมูลจากกรมชลประทาน" 
    """
    
    # สั่ง AI พร้อมเปิดฟีเจอร์ค้นหา Google (Grounding) เพื่อไปดึงเลขน้ำล่าสุด
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())]
        )
    )
    
    return response.text

if __name__ == "__main__":
    print("ดึงข้อมูลสภาพอากาศและฝุ่น ตลาดอินทร์บุรี...")
    temp, pm25 = get_inburi_weather()
    
    print("กำลังให้ AI ค้นหาตัวเลขระดับน้ำปัจจุบันและสร้างโพสต์...")
    final_post = generate_local_update(temp, pm25)
    
    # เติม Tag ท้ายโพสต์
    final_message = final_post + "\n\n-----------------\n🤖 อัปเดตอัตโนมัติ by Alieninburi\n#อินทร์บุรีรอดมั้ย"
    
    # ส่งเข้า Make.com
    payload = {"text_to_post": final_message}
    response = requests.post(MAKE_WEBHOOK_URL, json=payload)
    
    if response.status_code == 200:
        print("✅ ส่งโพสต์สำเร็จ!")
    else:
        print("❌ เกิดข้อผิดพลาด:", response.text)
