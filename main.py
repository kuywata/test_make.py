import requests
import os
import json
from datetime import datetime
import pytz
from google import genai
from google.genai import types

# 1. ตั้งค่ากุญแจความลับ
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") 
MAKE_WEBHOOK_URL = os.environ.get("MAKE_WEBHOOK_URL")
client = genai.Client(api_key=GEMINI_API_KEY)

# ตั้งเวลาไทย
tz = pytz.timezone('Asia/Bangkok')
now = datetime.now(tz)
date_str = now.strftime("%d %B 2569")
time_str = now.strftime("%H:%M น.")

def get_weather_data():
    try:
        w = requests.get("https://api.open-meteo.com/v1/forecast?latitude=14.9961&longitude=100.3253&current=temperature_2m,pm2_5&timezone=Asia%2FBangkok").json()
        return w['current']['temperature_2m'], w['current'].get('pm2_5', 'N/A')
    except: return "N/A", "N/A"

def get_water_api():
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0'}
    try:
        res = requests.get("https://www.thaiwater.net/api/v1/thaiwater30/public/waterlevel", headers=headers, timeout=15)
        return res.json().get('waterlevel_data', []) if res.status_code == 200 else []
    except: return []

if __name__ == "__main__":
    temp, pm = get_weather_data()
    water_list = get_water_api()
    
    # เจาะจงสถานี C.3 และ C.13 จากฐานข้อมูล
    inburi_raw = next((json.dumps(s, ensure_ascii=False) for s in water_list if s.get('station', {}).get('station_old_code') == 'C.3'), "NO_DATA")
    c13_raw = next((json.dumps(s, ensure_ascii=False) for s in water_list if s.get('station', {}).get('station_old_code') == 'C.13'), "NO_DATA")

    # ใช้ Gemini 3 Flash (อัปเกรดล่าสุด) ทำหน้าที่ Search Grounding เจาะหน้าเว็บที่พี่ให้มา
    prompt = f"""
    วันนี้วันที่ {date_str} เวลา {time_str}
    คุณคือ AI อัจฉริยะที่ทำหน้าที่รายงานข้อมูลน้ำที่แม่นยำที่สุด 100% จากแหล่งข้อมูลจริง

    คำสั่งเด็ดขาด:
    1. ถ้าข้อมูลจาก API (C.3 หรือ C.13) เป็น NO_DATA ให้คุณใช้เครื่องมือ 'Google Search' 
       ดึงข้อมูลสดๆ จากเว็บ: www.thaiwater.net/water/wl และ tiwrm.hii.or.th (สถานี C.13) ของวันนี้เท่านั้น
    2. ใช้เกณฑ์ตลิ่งอินทร์บุรีใหม่ที่ **13.10 เมตร** (คำนวณ: 13.10 - ระดับน้ำปัจจุบัน)
    3. ห้ามมโนตัวเลขเอง ถ้าไม่มีข้อมูลของวันนี้จริงๆ ให้เขียนว่า "รอประกาศอย่างเป็นทางการ"

    รูปแบบโพสต์:
    📍 **สถานการณ์อินทร์บุรี ({time_str} / {date_str})**
    * อุณหภูมิ: {temp}°C
    * ฝุ่น PM 2.5: {pm} μg/m³
    * ระดับน้ำอินทร์บุรี: ความสูง [เลข] ม.รทก. (ห่างจากตลิ่ง [เลข] เมตร)
    * ระบายน้ำเขื่อนเจ้าพระยา: [เลข] ลบ.ม./วินาที

    ข้อมูล API ปัจจุบัน:
    C.3: {inburi_raw}
    C.13: {c13_raw}
    """

    response = client.models.generate_content(
        model='gemini-2.5-flash', # ใช้โมเดลตัวท็อปที่สุด
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0 # ป้องกันการมโน
        )
    )

    # ส่งเข้า Make.com โดยไม่มีเครดิตบอทตามสั่ง
    requests.post(MAKE_WEBHOOK_URL, json={"text_to_post": response.text.strip() + "\n\n#อินทร์บุรีรอดมั้ย"})
