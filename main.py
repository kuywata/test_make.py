import requests
import os
import json
import re
from datetime import datetime
import pytz
from google import genai
from playwright.sync_api import sync_playwright

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") 
MAKE_WEBHOOK_URL = os.environ.get("MAKE_WEBHOOK_URL")
client = genai.Client(api_key=GEMINI_API_KEY)

tz = pytz.timezone('Asia/Bangkok')
now = datetime.now(tz)
date_str = now.strftime("%d %B 2569")
time_str = now.strftime("%H:%M น.")

STATE_FILE = "state.json"

def get_weather():
    try:
        w = requests.get("https://api.open-meteo.com/v1/forecast?latitude=14.9961&longitude=100.3253&current=temperature_2m,pm2_5&timezone=Asia%2FBangkok").json()
        return w['current']['temperature_2m'], w['current'].get('pm2_5', 'N/A')
    except:
        return "N/A", "N/A"

def get_water_data():
    wl = "รออัปเดต"
    discharge = "รออัปเดต"
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # 1. ดึงข้อมูล Thaiwater (แก้บั๊ก String Object)
        try:
            print("กำลังเข้าเว็บ Thaiwater และดักจับ API...")
            with page.expect_response(re.compile(r".*/public/waterlevel"), timeout=30000) as response_info:
                page.goto("https://www.thaiwater.net/water/wl", timeout=60000)
            
            # อ่านค่าดิบมาปอกเปลือก
            raw_text = response_info.value.text()
            data = json.loads(raw_text)
            
            # ป้องกันกรณีที่ API ห่อข้อมูลมาเป็น String ซ้อน String
            if isinstance(data, str):
                data = json.loads(data)
                
            wl_list = data.get('waterlevel_data', []) if isinstance(data, dict) else data
            for s in wl_list:
                code = s.get('station', {}).get('station_old_code', '')
                if code == 'C.3':
                    wl = float(s.get('water_level', 0))
                if code == 'C.13':
                    discharge = float(s.get('discharge', 0))
        except Exception as e:
            print(f"เกิดข้อผิดพลาดในการดักจับข้อมูล Thaiwater: {e}")

        # 2. ดึงข้อมูล HII (เปลี่ยนไปอ่านจากโค้ด HTML โดยตรง)
        if discharge == "รออัปเดต":
            try:
                print("กำลังเข้าเว็บ HII สำรอง...")
                page.goto("https://tiwrm.hii.or.th/DATA/REPORT/php/chart/chaopraya/small/chaopraya.php", timeout=60000)
                page.wait_for_timeout(10000)
                
                # ดึง HTML โค้ดทั้งหมดของหน้าเว็บมาค้นหา
                html_content = page.content()
                
                # ทะลวงหาตัวเลขที่อยู่หลังคำว่า 'ปริมาณน้ำ' โดยข้ามโค้ด HTML รูปภาพออกไป
                match = re.search(r'ท้ายเขื่อนเจ้าพระยา.*?ปริมาณน้ำ[^\d]+([\d\.]+)', html_content, re.DOTALL | re.IGNORECASE)
                if match:
                    discharge = float(match.group(1))
            except Exception as e:
                print(f"เกิดข้อผิดพลาด HII: {e}")

        browser.close()
        
    return wl, discharge

if __name__ == "__main__":
    print("เริ่มการทำงาน...")
    wl, discharge = get_water_data()
    temp, pm25 = get_weather()
    
    # --------- STATE MANAGEMENT ---------
    if not os.path.exists(STATE_FILE):
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"wl": "รออัปเดต", "discharge": "รออัปเดต"}, f)
            
    current_state = {"wl": wl, "discharge": discharge}
    
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        old_state = json.load(f)
            
    if current_state["wl"] == old_state.get("wl") and current_state["discharge"] == old_state.get("discharge"):
        print(f"⚠️ ข้อมูลไม่มีการเปลี่ยนแปลง (น้ำ: {wl}, ระบาย: {discharge}) ระบบข้ามการโพสต์")
        exit(0)
    else:
        print(f"✅ พบตัวเลขใหม่! (น้ำ: {wl}, ระบาย: {discharge}) กำลังส่งโพสต์...")
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(current_state, f)
    # ------------------------------------

    bank_level = 13.10
    if isinstance(wl, float):
        dist = round(bank_level - wl, 2)
        wl_text = f"ความสูง {wl} ม.รทก. (ห่างจากตลิ่ง {dist} เมตร)"
    else: 
        wl_text = "รออัปเดตข้อมูล ม.รทก."

    prompt = f"""
    สรุปสถานการณ์วันที่ {date_str} เวลา {time_str}
    - อุณหภูมิ: {temp}°C
    - ฝุ่น PM 2.5: {pm25} μg/m³
    - ระดับน้ำอินทร์บุรี: {wl_text}
    - ระบายน้ำเขื่อนเจ้าพระยา: {discharge} ลบ.ม./วินาที
    เขียนโพสต์ให้ชาวบ้านอ่านง่าย สั้นๆ เริ่มที่หัวข้อ **สถานการณ์อินทร์บุรี** """
    
    response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
    
    res = requests.post(MAKE_WEBHOOK_URL, json={"text_to_post": response.text.strip() + "\n\n#อินทร์บุรีรอดมั้ย"})
    if res.status_code == 200:
        print("✅ ส่ง Webhook สำเร็จ!")
