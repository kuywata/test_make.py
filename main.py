import requests
import os
import json
from datetime import datetime
import pytz
from google import genai

# 1. ตั้งค่า API และดึงกุญแจความลับ
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") 
MAKE_WEBHOOK_URL = os.environ.get("MAKE_WEBHOOK_URL")
client = genai.Client(api_key=GEMINI_API_KEY)

# ตั้งค่าเวลาไทย
tz = pytz.timezone('Asia/Bangkok')
now = datetime.now(tz)
date_str = now.strftime("%d %B 2569")

def get_hard_data():
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/123.0.0.0'}
    results = {
        "inburi_level": "รออัปเดต",
        "chaophraya_discharge": "รออัปเดต",
        "temp": "N/A",
        "pm25": "N/A"
    }

    # --- ส่วนที่ 1: ดึงระดับน้ำอินทร์บุรีจาก Thaiwater (ตรงตามหน้าเว็บ www.thaiwater.net/water/wl) ---
    try:
        # ดึงจาก API หลักที่แสดงผลหน้าตาราง
        res = requests.get("https://www.thaiwater.net/api/v1/thaiwater30/public/waterlevel", headers=headers, timeout=15)
        if res.status_code == 200:
            data = res.json().get('waterlevel_data', [])
            for item in data:
                # เจาะจงสถานีอินทร์บุรี (C.3)
                if 'อินทร์บุรี' in item.get('station', {}).get('station_name', {}).get('th', ''):
                    results["inburi_level"] = float(item.get('water_level', 0))
                    break
    except: pass

    # --- ส่วนที่ 2: ดึงการระบายน้ำเขื่อนเจ้าพระยาจาก HII (ตรงตามแผนภาพ tiwrm.hii.or.th) ---
    try:
        # ใช้ API เดียวกันแต่หาค่าระบายน้ำ (Discharge) ของสถานี C.13 ท้ายเขื่อน
        res = requests.get("https://www.thaiwater.net/api/v1/thaiwater30/public/waterlevel", headers=headers, timeout=15)
        if res.status_code == 200:
            data = res.json().get('waterlevel_data', [])
            for item in data:
                if 'C.13' in item.get('station', {}).get('station_old_code', ''):
                    results["chaophraya_discharge"] = float(item.get('discharge', 0))
                    break
    except: pass

    # --- ส่วนที่ 3: ดึงอากาศและฝุ่นตลาดอินทร์บุรี ---
    try:
        weather = requests.get("https://api.open-meteo.com/v1/forecast?latitude=14.9961&longitude=100.3253&current=temperature_2m,pm2_5&timezone=Asia%2FBangkok").json()
        results["temp"] = weather['current']['temperature_2m']
        results["pm25"] = weather['current'].get('pm2_5', 'N/A')
    except: pass

    return results

if __name__ == "__main__":
    # ดึงข้อมูลดิบ
    data = get_hard_data()
    
    # คำนวณความห่างตลิ่ง (ใช้เกณฑ์ 13.10 เมตรตามที่สั่ง)
    bank_level = 13.10
    try:
        distance_from_bank = round(bank_level - data["inburi_level"], 2)
        wl_display = f"{data['inburi_level']} ม.รทก. (ห่างจากตลิ่ง {distance_from_bank} เมตร)"
    except:
        wl_display = "รออัปเดตข้อมูล ม.รทก."

    # ใช้ AI แค่จัดรูปแบบข้อความจากตัวเลขที่เราดึงมาให้ (ห้ามให้ AI ไปหาเอง)
    prompt = f"""
    วันนี้วันที่ {date_str} เวลา {now.strftime('%H:%M น.')}
    รายงานข้อมูลต่อไปนี้ให้ชาวบ้านอินทร์บุรี (ห้ามมโนตัวเลขเองเด็ดขาด):
    - อุณหภูมิ: {data['temp']}°C
    - ฝุ่น PM 2.5: {data['pm25']} μg/m³
    - ระดับน้ำอินทร์บุรี: {wl_display}
    - การระบายน้ำเขื่อนเจ้าพระยา: {data['chaophraya_discharge']} ลบ.ม./วินาที

    จัดรูปแบบโพสต์ให้สั้น กระชับ แข็งแรง ตามสไตล์เพจอินทร์บุรีรอดมั้ย โดยเริ่มที่หัวข้อทันที
    """

    response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
    
    # ส่งเข้า Make.com
    requests.post(MAKE_WEBHOOK_URL, json={"text_to_post": response.text + "\n\n#อินทร์บุรีรอดมั้ย"})
