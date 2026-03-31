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

        # 1. ดึงระดับน้ำอินทร์บุรี (thaiwater.net)
        try:
            print("กำลังเข้าเว็บ Thaiwater...")
            page.goto("https://www.thaiwater.net/water/wl", timeout=60000)
            # แก้ปัญหา Timeout: บังคับให้รอ 15 วินาที เพื่อให้เว็บโหลดตาราง Javascript จนเสร็จชัวร์ๆ
            page.wait_for_timeout(15000) 
            
            # ดึงข้อความทั้งหน้ามาหาคำว่า สถานีอินทร์บุรี แทนการดักจับ Selector ที่อาจจะคลาดเคลื่อน
            body_text = page.locator("body").inner_text()
            
            # ตัดเอาเฉพาะบรรทัดที่มีคำว่า 'สถานีอินทร์บุรี' หรือ 'C.3'
            for line in body_text.split('\n'):
                if 'สถานีอินทร์บุรี' in line and 'แม่น้ำเจ้าพระยา' in line:
                    # หาตัวเลขทศนิยมในบรรทัดนั้น (มักจะเป็นระดับน้ำ)
                    nums = re.findall(r'\b\d+\.\d{2}\b', line)
                    if nums:
                        wl = float(nums[0])
                        break
        except Exception as e:
            print(f"เกิดข้อผิดพลาด Thaiwater: {e}")

        # 2. ดึงปริมาณการระบายน้ำ (HII)
        try:
            print("กำลังเข้าเว็บ HII...")
            page.goto("https://tiwrm.hii.or.th/DATA/REPORT/php/chart/chaopraya/small/chaopraya.php", timeout=60000)
            # บังคับรอ 10 วินาทีให้โหลดภาพและข้อความเสร็จ
            page.wait_for_timeout(10000)
            body_text = page.locator("body").inner_text()
            match = re.search(r'ท้ายเขื่อนเจ้าพระยา.*?ปริมาณน้ำ\s*([\d\.]+)', body_text, re.DOTALL | re.IGNORECASE)
            if match:
                discharge = float(match.group(1))
        except Exception as e:
            print(f"เกิดข้อผิดพลาด HII: {e}")

        browser.close()
        
    return wl, discharge

if __name__ == "__main__":
    print("กำลังเปิดเบราว์เซอร์จำลองเพื่อดึงข้อมูล...")
    wl, discharge = get_water_data()
    temp, pm25 = get_weather()
    
    # --------- STATE MANAGEMENT ---------
    # สร้างไฟล์ state.json หลอกไว้ก่อนถ้ายาวไม่มี เพื่อป้องกัน GitHub พังตอน Git add
    if not os.path.exists(STATE_FILE):
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"wl": "รออัปเดต", "discharge": "รออัปเดต"}, f)
            
    current_state = {"wl": wl, "discharge": discharge}
    
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        old_state = json.load(f)
            
    # ถ้าค่าไม่ได้มา (รออัปเดต) หรือค่าเท่าเดิมเป๊ะๆ ให้ข้ามการโพสต์
    if current_state["wl"] == old_state.get("wl") and current_state["discharge"] == old_state.get("discharge"):
        print("⚠️ ข้อมูลน้ำไม่มีการเปลี่ยนแปลงจากรอบที่แล้ว หรือดึงข้อมูลไม่ได้ ระบบจะข้ามการโพสต์เพื่อไม่ให้รกเพจ")
        exit(0)
    else:
        print("✅ พบการเปลี่ยนแปลงของข้อมูล! กำลังบันทึกค่าใหม่ลง State และส่งโพสต์...")
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
