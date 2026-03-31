import requests
import os
import json
from datetime import datetime
import pytz
from google import genai

# 1. ตั้งค่าพื้นฐาน
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") 
MAKE_WEBHOOK_URL = os.environ.get("MAKE_WEBHOOK_URL")
client = genai.Client(api_key=GEMINI_API_KEY)

# ตั้งเวลาไทย
tz = pytz.timezone('Asia/Bangkok')
now = datetime.now(tz)
date_str = now.strftime("%d %B 2569")
time_str = now.strftime("%H:%M น.")

def get_real_data():
    # ใช้ User-Agent ปลอมตัวเป็น Browser เพื่อไม่ให้เว็บรัฐบาลบล็อก
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0'}
    results = {"wl": "รออัปเดต", "discharge": "รออัปเดต", "temp": "N/A", "pm25": "N/A"}

    # ดึงระดับน้ำ (C.3 อินทร์บุรี) และ การระบายน้ำ (C.13 เขื่อนเจ้าพระยา)
    try:
        res = requests.get("https://www.thaiwater.net/api/v1/thaiwater30/public/waterlevel", headers=headers, timeout=20)
        if res.status_code == 200:
            data = res.json().get('waterlevel_data', [])
            for s in data:
                code = s.get('station', {}).get('station_old_code', '')
                # เจาะจงรหัสสถานี C.3 (อินทร์บุรี)
                if code == 'C.3':
                    results["wl"] = float(s.get('water_level', 0))
                # เจาะจงรหัสสถานี C.13 (เขื่อนเจ้าพระยา)
                if code == 'C.13':
                    results["discharge"] = float(s.get('discharge', 0))
    except: pass

    # ดึงสภาพอากาศตลาดอินทร์บุรี
    try:
        w = requests.get("https://api.open-meteo.com/v1/forecast?latitude=14.9961&longitude=100.3253&current=temperature_2m,pm2_5&timezone=Asia%2FBangkok").json()
        results["temp"] = w['current']['temperature_2m']
        results["pm25"] = w['current'].get('pm2_5', 'N/A')
    except: pass

    return results

if __name__ == "__main__":
    d = get_real_data()
    
    # คำนวณความห่างตลิ่ง (เกณฑ์ 13.10 เมตร ตามที่พี่สั่ง)
    bank = 13.10
    if isinstance(d["wl"], float):
        dist = round(bank - d["wl"], 2)
        wl_text = f"ความสูง {d['wl']} ม.รทก. (ห่างจากตลิ่ง {dist} เมตร)"
    else:
        wl_text = "รออัปเดตข้อมูล ม.รทก."

    # ใช้ AI จัดรูปแบบโพสต์เท่านั้น (ห้าม AI หาเลขเอง)
    prompt = f"""
    สรุปสถานการณ์วันที่ {date_str} เวลา {time_str}
    - อุณหภูมิ: {d['temp']}°C
    - ฝุ่น PM 2.5: {d['pm25']} μg/m³
    - ระดับน้ำอินทร์บุรี: {wl_text}
    - ระบายน้ำเขื่อนเจ้าพระยา: {d['discharge']} ลบ.ม./วินาที

    เขียนโพสต์ให้ชาวบ้านอ่านง่าย สั้นๆ เริ่มที่หัวข้อ **สถานการณ์อินทร์บุรี** ทันที ไม่ต้องมีคำเกริ่น
    """

    response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
    
    # ส่งโพสต์
    requests.post(MAKE_WEBHOOK_URL, json={"text_to_post": response.text + "\n\n#อินทร์บุรีรอดมั้ย"})
