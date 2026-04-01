import os
import re
import json
import random
import requests
from datetime import datetime
import pytz
from google import genai
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# --- ตั้งค่าพื้นฐาน ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") 
MAKE_WEBHOOK_URL = os.environ.get("MAKE_WEBHOOK_URL")
client = genai.Client(api_key=GEMINI_API_KEY)

tz = pytz.timezone('Asia/Bangkok')
now = datetime.now(tz)
date_str = now.strftime("%d %B 2569")
time_str = now.strftime("%H:%M น.")

# --- 1. ดึงสภาพอากาศ (ลูกผสม: อุณหภูมิ/ฝุ่นจาก Open-Meteo, ฝน/ลม/ชื้นจาก Tomorrow.io) ---
def get_weather():
    TOMORROW_API_KEY = os.environ.get("TOMORROW_API_KEY")
    
    # URL 1: ดึงฝน ลม ความชื้น จาก Tomorrow.io (แม่นยำเรื่องฝนเฉพาะจุด)
    tmr_url = f"https://api.tomorrow.io/v4/weather/forecast?location=14.9961,100.3253&apikey={TOMORROW_API_KEY}"
    # URL 2: ดึงอุณหภูมิ และ PM 2.5 จาก Open-Meteo (อุณหภูมิตรงกับความรู้สึกและแอปทั่วไปมากกว่า)
    om_url = "https://api.open-meteo.com/v1/forecast?latitude=14.9961&longitude=100.3253&current=temperature_2m,pm2_5&timezone=Asia%2FBangkok"
    
    try:
        # 1.1 ดึงข้อมูลพายุ ฝนเฉพาะจุด ลม และความชื้น (Tomorrow.io)
        tmr_res = requests.get(tmr_url).json()
        current_data = tmr_res['timelines']['minutely'][0]['values']
        
        humidity = round(current_data['humidity'], 1)
        wind = round(current_data['windSpeed'], 1) # หน่วยเป็น m/s
        
        # หาโอกาสฝนตกสูงสุดใน 12 ชั่วโมงข้างหน้า
        hourly_data = tmr_res['timelines']['hourly'][:12]
        rain_probs = [hour['values']['precipitationProbability'] for hour in hourly_data]
        rain_prob = max(rain_probs)

        # 1.2 ดึงอุณหภูมิและฝุ่น (Open-Meteo)
        om_res = requests.get(om_url).json()
        temp = om_res['current']['temperature_2m']
        pm25 = om_res['current'].get('pm2_5', 'N/A')

        return temp, pm25, rain_prob, humidity, wind
    except Exception as e:
        print(f"เกิดข้อผิดพลาดในการดึงสภาพอากาศแบบลูกผสม: {e}")
        return "N/A", "N/A", "N/A", "N/A", "N/A"

# --- 2. ดึงระดับน้ำอินทร์บุรี (ท่าไม้ตายของพี่: เจาะเว็บสาขาสิงห์บุรี) ---
def get_inburi_data():
    url = f"https://singburi.thaiwater.net/wl?cb={random.randint(10000, 99999)}"
    water_level = None
    bank_level = 13.10 # ใช้ 13.10 ตามที่ตกลงกันไว้
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(url, timeout=60000)
            page.wait_for_selector("th[scope='row']", timeout=30000)
            html = page.content()
            
            soup = BeautifulSoup(html, "html.parser")
            for th in soup.select("th[scope='row']"):
                if "อินทร์บุรี" in th.get_text(strip=True):
                    tr = th.find_parent("tr")
                    cols = tr.find_all("td")
                    numeric_values = []
                    
                    for td in cols:
                        text = td.get_text(strip=True)
                        try:
                            cleaned = re.sub(r"[ ,]", "", text)
                            cleaned = re.sub(r"[^0-9\.\-]", "", cleaned)
                            if cleaned and cleaned != "-":
                                numeric_values.append(float(cleaned))
                        except:
                            continue
                            
                    if numeric_values:
                        water_level = numeric_values[0]
                        break
        except Exception as e:
            print(f"เกิดข้อผิดพลาดในการดึงข้อมูลสิงห์บุรี: {e}")
        finally:
            browser.close()
            
    return water_level, bank_level

# --- 3. ดึงเขื่อนเจ้าพระยา (ท่าไม้ตายของพี่: ล้วงตัวแปร json_data) ---
def fetch_chao_phraya_dam_discharge():
    url = f"https://tiwrm.hii.or.th/DATA/REPORT/php/chart/chaopraya/small/chaopraya.php?cb={random.randint(10000, 99999)}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/91.0.4472.124 Safari/537.36',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache'
    }
    try:
        response = requests.get(url, headers=headers, timeout=20)
        response.encoding = 'utf-8'
        
        match = re.search(r'var json_data = (\[.*\]);', response.text)
        if match:
            json_string = match.group(1)
            data = json.loads(json_string)
            water_storage = data[0]['itc_water']['C13']['storage']
            
            if water_storage is not None:
                if isinstance(water_storage, (int, float)):
                    return float(water_storage)
                else:
                    return float(str(water_storage).replace(',', ''))
    except Exception as e:
        print(f"เกิดข้อผิดพลาดในการดึงข้อมูลเขื่อน HII: {e}")
        
    return None

if __name__ == "__main__":
    print("=== เริ่มใช้ตรรกะเจาะข้อมูลระดับเทพ ===")
    
    # 1. รวบรวมข้อมูล (รับค่าสภาพอากาศมา 5 ตัว)
    temp, pm25, rain_prob, humidity, wind = get_weather()
    wl, bank_level = get_inburi_data()
    discharge = fetch_chao_phraya_dam_discharge()
    
    # 2. จัดการคำความห่างตลิ่ง
    if wl is not None:
        dist = round(bank_level - wl, 2)
        wl_text = f"ความสูง {wl} ม.รทก. (ห่างจากตลิ่ง {dist} เมตร)"
    else: 
        wl_text = "รออัปเดตข้อมูล ม.รทก."
        
    discharge_text = f"{discharge} ลบ.ม./วินาที" if discharge is not None else "รออัปเดต"

    # 3. เตรียมโพสต์ (รักษาโครงสร้างเดิม 100% แต่ปรับให้ AI สรุปอากาศสั้นๆ)
    prompt = f"""
    คุณคือแอดมินเพจ "อินทร์บุรีรอดมั้ย" ที่คอยอัปเดตข่าวสารให้ชาวบ้านอินทร์บุรีแบบเป็นกันเอง ภาษาอ่านง่าย ไม่เป็นทางการเกินไป และไม่จำเจ
    
    ข้อมูลดิบวันนี้:
    - วันที่: {date_str} เวลา {time_str}
    - อุณหภูมิ: {temp}°C, ความชื้น: {humidity}%, ลม: {wind} m/s
    - โอกาสฝนตก: {rain_prob}%
    - ฝุ่น PM 2.5: {pm25} μg/m³
    - ระดับน้ำอินทร์บุรี: {wl_text}
    - ระบายน้ำเขื่อนเจ้าพระยา: {discharge_text}

    กฎการเขียนโพสต์:
    1. นำข้อมูลดิบมาเรียบเรียงใหม่ให้เป็นธรรมชาติ เพิ่มคำขยายความให้เห็นภาพตามความเป็นจริง เช่น:
       - อากาศ/ฝน/ลม: ให้สรุปสั้นๆ กระชับที่สุด ไม่ต้องร่ายยาว ถ้าร้อน+ชื้นให้บอก "ร้อนอบอ้าว", มีลมบอก "ลมพัดเย็นๆ", ถ้าโอกาสฝนสูงให้เตือนพกร่มสั้นๆ, ถ้าฝุ่นน้อยให้บอกอากาศดี
       - ระดับน้ำ: ถ้ายังห่างตลิ่งเยอะ ให้เสริมว่า "น้ำยังอยู่ในระดับต่ำ ปลอดภัย" หรือ "ยังห่างตลิ่งอีกเยอะ สบายใจได้"
       - เขื่อนเจ้าพระยา: ถ้าระบายน้ำน้อย (เช่น ต่ำกว่า 700) ให้บอกว่า "เป็นระดับปกติ ไม่ได้มีการเร่งระบายน้ำแต่อย่างใด"
    2. ห้ามใช้คำลงท้ายว่า "ครับ", "ค่ะ", "ครับ/ค่ะ" แบบหุ่นยนต์เด็ดขาด ให้ใช้ภาษาเล่าเรื่องแบบเป็นธรรมชาติแทน
    3. พยายามสับเปลี่ยนคำศัพท์และรูปประโยคในแต่ละวันไม่ให้ซ้ำซากจำเจ
    4. ให้ผลลัพธ์ออกมาตามโครงสร้างนี้เป๊ะๆ (ห้ามปรับเปลี่ยนรูปแบบหัวข้อเด็ดขาด):

    **สถานการณ์อินทร์บุรี** (ข้อมูล ณ {date_str} เวลา {time_str})
    
    🌡️ **สภาพอากาศ:** [สรุปอุณหภูมิ ฝน ลม ฝุ่น แบบสั้น กระชับ เป็นธรรมชาติ]
    🌊 **ระดับน้ำอินทร์บุรี:** [บอกตัวเลข พร้อมประโยคเสริมความอุ่นใจหรือแจ้งเตือน]
    🛑 **ระบายน้ำเขื่อนเจ้าพระยา:** [บอกตัวเลข และวิเคราะห์ว่าเป็นระดับปกติหรือไม่]

    📌 **สรุป:** [สรุปภาพรวมสั้นๆ 1-2 บรรทัด แบบเป็นกันเองให้ชาวบ้านสบายใจ]
    """
    
    response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
    final_post = response.text.strip() + "\n\n#อินทร์บุรีรอดมั้ย"
    
    print("\nข้อความที่จะโพสต์:\n", final_post)
    
    # 4. ส่งเข้า Make.com
    if MAKE_WEBHOOK_URL:
        res = requests.post(MAKE_WEBHOOK_URL, json={"text_to_post": final_post})
        if res.status_code == 200:
            print("\n✅ ส่ง Webhook ไปยังหน้าเพจสำเร็จแล้ว!")
        else:
            print(f"\n❌ ส่ง Webhook ล้มเหลว: {res.text}")
