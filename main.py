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

# --- 1. ดึงสภาพอากาศ ---
def get_weather():
    TOMORROW_API_KEY = os.environ.get("TOMORROW_API_KEY")
    
    tmr_url = f"https://api.tomorrow.io/v4/weather/forecast?location=14.9961,100.3253&apikey={TOMORROW_API_KEY}"
    om_weather_url = "https://api.open-meteo.com/v1/forecast?latitude=14.9961&longitude=100.3253&current=temperature_2m,uv_index&timezone=Asia%2FBangkok"
    om_aqi_url = "https://air-quality-api.open-meteo.com/v1/air-quality?latitude=14.9961&longitude=100.3253&current=pm2_5&timezone=Asia%2FBangkok"
    
    try:
        tmr_res = requests.get(tmr_url).json()
        current_data = tmr_res['timelines']['minutely'][0]['values']
        humidity = round(current_data['humidity'], 1)
        wind = round(current_data['windSpeed'], 1) 
        
        hourly_data = tmr_res['timelines']['hourly'][:12]
        rain_probs = [hour['values']['precipitationProbability'] for hour in hourly_data]
        rain_prob = max(rain_probs)

        w_res = requests.get(om_weather_url).json()
        temp = w_res['current']['temperature_2m']
        uv = w_res['current'].get('uv_index', 'N/A')

        aqi_res = requests.get(om_aqi_url).json()
        pm25 = aqi_res['current'].get('pm2_5', 'N/A')

        return temp, pm25, rain_prob, humidity, wind, uv
    except Exception as e:
        print(f"เกิดข้อผิดพลาดสภาพอากาศ: {e}")
        return "N/A", "N/A", "N/A", "N/A", "N/A", "N/A"

# --- 2. ดึงระดับน้ำอินทร์บุรี ---
def get_inburi_data():
    url = f"https://singburi.thaiwater.net/wl?cb={random.randint(10000, 99999)}"
    water_level = None
    bank_level = 13.10 
    
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
            print(f"เกิดข้อผิดพลาดข้อมูลสิงห์บุรี: {e}")
        finally:
            browser.close()
            
    return water_level, bank_level

# --- 3. ดึงระบายน้ำเขื่อนเจ้าพระยา (แบบ 2 ชั้น ป้องกันเซิร์ฟเวอร์บล็อก IP ต่างประเทศ) ---
def fetch_chao_phraya_dam_discharge():
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    # --- แผน A: ดึงจากเว็บกรมชลประทานโดยตรง ---
    print("▶️ กำลังพยายามดึงข้อมูลจากเว็บกรมชลประทาน...")
    url_rid = f"http://water.rid.go.th/flood/flood/chao.htm?cb={random.randint(10000, 99999)}"
    try:
        response = requests.get(url_rid, headers=headers, timeout=20)
        response.encoding = 'tis-620'
        soup = BeautifulSoup(response.text, "html.parser")
        
        for tr in soup.find_all("tr"):
            row_text = tr.get_text(strip=True)
            if "C.13" in row_text or "ท้ายเขื่อนเจ้าพระยา" in row_text:
                cols = tr.find_all(["td", "th"])
                numeric_values = []
                for td in cols:
                    text = td.get_text(strip=True)
                    cleaned = re.sub(r"[ ,]", "", text)
                    cleaned = re.sub(r"[^0-9\.]", "", cleaned)
                    if cleaned and cleaned != ".":
                        numeric_values.append(float(cleaned))
                        
                if numeric_values:
                    val = numeric_values[-1]
                    print(f"✅ สำเร็จ! ได้ข้อมูลจากกรมชลประทาน: {val}")
                    return val 
    except Exception as e:
        print(f"⚠️ กรมชลประทาน Error หรือไม่ตอบสนอง: {e}")

    # --- แผน B: ถ้าแผน A ล้มเหลว (โดนบล็อก IP) สลับมาดึงเว็บสำรอง EGA ทันที ---
    print("🔄 กรมชลประทานบล็อกการเข้าถึง! กำลังสลับไปใช้เว็บสำรอง EGA...")
    url_ega = f"http://tiwrm-ns1.ega.or.th/DATA/REPORT/php/chart/chaopraya/small/chaopraya.php?cb={random.randint(10000, 99999)}"
    try:
        response_ega = requests.get(url_ega, headers=headers, timeout=20)
        response_ega.encoding = 'utf-8'
        soup_ega = BeautifulSoup(response_ega.text, "html.parser")
        
        c13_box = soup_ega.find("div", id="C13")
        if c13_box:
            tds = c13_box.find_all("td")
            for i, td in enumerate(tds):
                if "ปริมาณน้ำ" in td.get_text(strip=True):
                    val_td = tds[i+1]
                    raw_text = val_td.get_text(strip=True).split('/')[0] 
                    cleaned = re.sub(r"[^0-9\.]", "", raw_text)
                    if cleaned:
                        val = float(cleaned)
                        print(f"✅ สำเร็จ! ดึงข้อมูลจากเว็บสำรอง EGA แทน: {val}")
                        return val
    except Exception as e:
        print(f"⚠️ เว็บสำรอง EGA ก็ดึงไม่ได้: {e}")
        
    return None

if __name__ == "__main__":
    print("=== เริ่มใช้ตรรกะเจาะข้อมูลระดับเทพ ===")
    
    # 1. รวบรวมข้อมูล
    temp, pm25, rain_prob, humidity, wind, uv = get_weather()
    wl, bank_level = get_inburi_data()
    discharge = fetch_chao_phraya_dam_discharge()
    
    # 2. จัดการคำ
    if wl is not None:
        dist = round(bank_level - wl, 2)
        wl_text = f"ความสูง {wl} ม.รทก. (ห่างจากตลิ่ง {dist} เมตร)"
    else: 
        wl_text = "รออัปเดตข้อมูล ม.รทก."
        
    discharge_text = f"{discharge} ลบ.ม./วินาที" if discharge is not None else "รออัปเดต"

    # 3. เตรียมโพสต์
    prompt = f"""
    คุณคือแอดมินเพจ "อินทร์บุรีรอดมั้ย" ที่คอยอัปเดตข่าวสารให้ชาวบ้านอินทร์บุรีแบบเป็นกันเอง ภาษาอ่านง่าย ไม่เป็นทางการเกินไป และไม่จำเจ
    
    ข้อมูลดิบวันนี้:
    - วันที่: {date_str} เวลา {time_str}
    - อุณหภูมิ: {temp}°C, ความชื้น: {humidity}%, ลม: {wind} m/s
    - ดัชนี UV (ความแรงแดด): {uv}
    - โอกาสฝนตก: {rain_prob}%
    - ฝุ่น PM 2.5: {pm25} μg/m³ (ห้ามพิมพ์ตัวเลขนี้ลงในโพสต์เด็ดขาด)
    - ระดับน้ำอินทร์บุรี: {wl_text}
    - ระบายน้ำเขื่อนเจ้าพระยา: {discharge_text}

    กฎการเขียนโพสต์ (สำคัญมาก):
    1. นำข้อมูลดิบมาเรียบเรียงใหม่ให้เป็นธรรมชาติ เช่น:
       - อากาศและฝน: ถ้าร้อน+ชื้นบอก "ร้อนอบอ้าว", มีลมบอก "ลมพัดเย็นๆ"
       - เรื่องฝุ่น PM 2.5: **ห้ามระบุตัวเลขค่าฝุ่นเด็ดขาด** ให้อธิบายเป็นความรู้สึกสั้นๆ เช่น "อากาศโปร่งหายใจโล่ง" หรือ "วันนี้ฝุ่นเริ่มเยอะ" 
       - UV: ถ้า UV สูง (เกิน 8) ให้เตือนว่าแดดแรงแสบผิว
       - ระดับน้ำ: ถ้ายังห่างตลิ่งเยอะ ให้เสริมว่า "น้ำยังอยู่ในระดับต่ำ ปลอดภัย"
       - เขื่อนเจ้าพระยา: ถ้าระบายน้ำน้อย ให้บอกว่า "เป็นระดับปกติ"
    2. ห้ามใช้คำลงท้ายว่า "ครับ", "ค่ะ" แบบหุ่นยนต์เด็ดขาด ให้ใช้ภาษาเล่าเรื่องแบบธรรมชาติ
    3. ให้ผลลัพธ์ออกมาตามโครงสร้างนี้เป๊ะๆ (ห้ามปรับเปลี่ยนรูปแบบหัวข้อเด็ดขาด):

    **สถานการณ์อินทร์บุรี** (ข้อมูล ณ {date_str} เวลา {time_str})
    
    🌡️ **สภาพอากาศ:** [สรุปอุณหภูมิ UV ฝน ลม และอธิบายฝุ่นโดยห้ามใส่ตัวเลข แบบสั้น กระชับ]
    🌊 **ระดับน้ำอินทร์บุรี:** [บอกตัวเลข พร้อมประโยคเสริมความอุ่นใจ]
    🛑 **ระบายน้ำเขื่อนเจ้าพระยา:** [บอกตัวเลข และวิเคราะห์ว่าเป็นระดับปกติหรือไม่]

    📌 **สรุป:** [สรุปภาพรวมสั้นๆ 1-2 บรรทัด แบบเป็นกันเอง]
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
