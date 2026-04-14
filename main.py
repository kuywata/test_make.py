import os
import re
import json
import random
import requests
import math
import time
from datetime import datetime, timedelta, timezone
import pytz
import ee
from google import genai
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from requests.packages.urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

# ==========================================
# ⚙️ CONFIGURATION & CONSTANTS
# ==========================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") 
MAKE_WEBHOOK_URL = os.environ.get("MAKE_WEBHOOK_URL")
EE_JSON_KEY = os.environ.get("EE_JSON_KEY")
TOMORROW_API_KEY = os.environ.get("TOMORROW_API_KEY")

client = genai.Client(api_key=GEMINI_API_KEY)

# ตั้งค่าพิกัดอำเภออินทร์บุรี และข้อมูลพื้นฐาน
INBURI_LAT = 15.0076
INBURI_LON = 100.3273
BANK_LEVEL = 13.10

# ตั้งค่าเวลา
tz = pytz.timezone('Asia/Bangkok')
now = datetime.now(tz)
THAI_MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]
thai_month = THAI_MONTHS[now.month - 1]
thai_year = now.year + 543
date_str = f"{now.day} {thai_month} {thai_year}"
time_str = now.strftime("%H:%M น.")

# ==========================================
# 🛠️ HELPER FUNCTIONS
# ==========================================
def get_dist(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(float(lat2) - float(lat1))
    dlon = math.radians(float(lon2) - float(lon1))
    a = math.sin(dlat/2)**2 + math.cos(math.radians(float(lat1))) * math.cos(math.radians(float(lat2))) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    return R * c

# ==========================================
# 📡 DATA FETCHING FUNCTIONS
# ==========================================
def get_hotspots():
    if not EE_JSON_KEY:
        return "N/A"
    try:
        json_key = json.loads(EE_JSON_KEY)
        credentials = ee.ServiceAccountCredentials(json_key['client_email'], key_data=EE_JSON_KEY)
        ee.Initialize(credentials)

        inburi_area = ee.Geometry.Point([INBURI_LON, INBURI_LAT]).buffer(10000)
        end_date = ee.Date(datetime.now())
        start_date = end_date.advance(-24, 'hour')
        fire_col = ee.ImageCollection("FIRMS").filterBounds(inburi_area).filterDate(start_date, end_date)
        
        return fire_col.size().getInfo()
    except Exception as e:
        print(f"⚠️ [get_hotspots] ระบบดาวเทียมขัดข้อง: {e}")
        return "N/A"

def get_accurate_pm25():
    MAX_DATA_AGE_SECONDS = 10800 
    MAX_DISTANCE_KM = 50         
    headers = {'User-Agent': 'Mozilla/5.0'}
    all_sources = [] 
    
    try:
        current_ts = int(time.time())
        url_gistda = f"https://pm25.gistda.or.th/rest/getPM25byLocation?lat={INBURI_LAT}&lng={INBURI_LON}&t={current_ts}"
        res = requests.get(url_gistda, headers=headers, timeout=15, verify=False)
        if res.status_code == 200:
            data = res.json().get('data', res.json())
            if 'pm25' in data and data['pm25'] is not None:
                 all_sources.append({'pm25': float(data['pm25']), 'distance': 0, 'age': 0, 'priority': 0})
    except Exception as e: 
        print(f"⚠️ [PM2.5] GISTDA error: {e}")

    try:
        res = requests.get(f"http://air4thai.pcd.go.th/services/getNewAQI_JSON.php?t={int(time.time())}", headers=headers, timeout=15, verify=False)
        if res.status_code == 200:
            for st in res.json().get('stations', []):
                pm25_val = st.get('LastUpdate', {}).get('PM25', {}).get('value')
                if not pm25_val or pm25_val == "-": continue
                dist = get_dist(INBURI_LAT, INBURI_LON, st.get('lat'), st.get('long'))
                if dist <= MAX_DISTANCE_KM:
                    all_sources.append({'pm25': float(pm25_val), 'distance': dist, 'age': 0, 'priority': 1})
    except Exception as e:
        print(f"⚠️ [PM2.5] Air4Thai error: {e}")

    try:
        url = f"https://air-quality-api.open-meteo.com/v1/air-quality?latitude={INBURI_LAT}&longitude={INBURI_LON}&current=pm2_5&timezone=Asia%2FBangkok"
        res = requests.get(url, headers=headers, timeout=10)
        if 'current' in res.json():
            all_sources.append({'pm25': float(res.json()['current']['pm2_5']), 'distance': 0, 'age': 0, 'priority': 3})
    except Exception as e:
        print(f"⚠️ [PM2.5] Open-Meteo error: {e}")

    if not all_sources: return "N/A"
    all_sources.sort(key=lambda x: (x['priority'], x['distance']))
    return f"{all_sources[0]['pm25']:.1f}"

def get_weather():
    temp, rain_prob, humidity, wind, uv = "N/A", "N/A", "N/A", "N/A", "N/A"
    if TOMORROW_API_KEY:
        try:
            tmr_url = f"https://api.tomorrow.io/v4/weather/forecast?location={INBURI_LAT},{INBURI_LON}&apikey={TOMORROW_API_KEY}"
            res = requests.get(tmr_url, timeout=10)
            if res.status_code == 200:
                tmr_res = res.json()
                current_data = tmr_res['timelines']['minutely'][0]['values']
                humidity = round(current_data['humidity'], 1)
                wind = round(current_data['windSpeed'], 1) 
                rain_prob = max([h['values']['precipitationProbability'] for h in tmr_res['timelines']['hourly'][:12]])
        except Exception as e: 
            print(f"⚠️ [Weather] Tomorrow.io error: {e}")
            
    try:
        om_weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={INBURI_LAT}&longitude={INBURI_LON}&current=temperature_2m,uv_index&timezone=Asia%2FBangkok"
        res = requests.get(om_weather_url, timeout=10).json()
        temp = res['current']['temperature_2m']
        uv = res['current'].get('uv_index', 'N/A')
    except Exception as e: 
        print(f"⚠️ [Weather] Open-Meteo error: {e}")
        
    return temp, rain_prob, humidity, wind, uv

def get_inburi_data():
    url = f"https://singburi.thaiwater.net/wl?cb={random.randint(10000, 99999)}"
    water_level = None
    
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
                        except: continue
                    if numeric_values:
                        water_level = numeric_values[0]
                        break
        except Exception as e:
            print(f"⚠️ [get_inburi_data] เกิดข้อผิดพลาดในการดึงข้อมูลสิงห์บุรี: {e}")
        finally:
            browser.close()
            
    return water_level

def fetch_chao_phraya_dam_discharge():
    url = f"https://tiwrm.hii.or.th/DATA/REPORT/php/chart/chaopraya/small/chaopraya.php?cb={random.randint(10000, 99999)}"
    try:
        res = requests.get(url, timeout=20)
        match = re.search(r'var json_data = (\[.*\]);', res.text)
        if match:
            data = json.loads(match.group(1))
            # แก้ไขจาก 'storage' เป็น 'discharge' (หรือปรับตาม key จริงถ้าไม่ใช่ discharge)
            val = data[0]['itc_water']['C13'].get('discharge', data[0]['itc_water']['C13'].get('outflow')) 
            if val is not None:
                 return float(val) if isinstance(val, (int, float)) else float(str(val).replace(',', ''))
    except Exception as e: 
        print(f"⚠️ [fetch_chao_phraya] Error: {e}")
    return None

# ==========================================
# 🚀 MAIN EXECUTION
# ==========================================
if __name__ == "__main__":
    print("=== เริ่มรวบรวมข้อมูลอินทร์บุรี + พลังดาวเทียม ===")
    
    # 1. ดึงข้อมูลทั้งหมด
    temp, rain_prob, humidity, wind, uv = get_weather() 
    pm25 = get_accurate_pm25()
    wl = get_inburi_data()
    discharge = fetch_chao_phraya_dam_discharge()
    hotspots = get_hotspots()
    
    # 2. จัดรูปแบบข้อความ
    # แก้ไขบั๊กน้ำแห้ง (wl=0.0)
    wl_text = f"ระดับน้ำ {wl} เมตร (ห่างจากตลิ่ง {round(BANK_LEVEL - wl, 2)} เมตร)" if wl is not None else "รออัปเดต"
    discharge_text = f"{discharge} ลบ.ม./วินาที" if discharge else "รออัปเดต"
    
    if hotspots == "N/A":
        hotspot_text = "ระบบตรวจจับขัดข้องชั่วคราว"
    elif hotspots == 0:
        hotspot_text = "ไม่พบจุดเผาในพื้นที่ ปลอดภัยดี"
    else:
        hotspot_text = f"ตรวจพบ {hotspots} จุด (เฝ้าระวังควันจากการเผาไร่/นา)"

    # 3. เตรียม Prompt สำหรับ Gemini
    prompt = f"""
    คุณคือแอดมินเพจ "อินทร์บุรีรอดมั้ย" อัปเดตข่าวสารให้ชาวบ้านแบบเป็นกันเอง
    ข้อมูลดิบ: {date_str} {time_str}
    - อากาศ: {temp}°C, แดด(UV): {uv}, ฝน: {rain_prob}%, ลม: {wind} m/s
    - ฝุ่น PM 2.5: {pm25} (บอกความรู้สึกแบบบ้านๆ ไม่ต้องบอกตัวเลข)
    - จุดความร้อน (VIIRS): {hotspot_text}
    - ระดับน้ำ: {wl_text}
    - ระบายเขื่อน: {discharge_text}

    กฎการเขียน:
    1. จุดความร้อน: ถ้าพบ ให้เตือนเรื่อง 'ควันจากการเผาไร่/นา' ห้ามพูดว่าไฟป่าเด็ดขาด
    2. ภาษา: ใช้ภาษาพูดง่ายๆ ตัดศัพท์วิชาการทิ้ง (เช่น ม.รทก. ให้เปลี่ยนเป็น 'เมตร')
    3. ความไม่จำเจ: ให้ใส่ 'อารมณ์ขัน' หรือ 'การทักทายตามวัน' ลงไปในสรุป
    4. ห้ามใช้คำลงท้าย "ครับ/ค่ะ"

    โครงสร้างโพสต์:
    **สถานการณ์อินทร์บุรี** (ข้อมูล ณ {date_str} เวลา {time_str})
    
    🌡️ **สภาพอากาศและฝุ่น:** [สรุปอากาศ+ความรู้สึกเรื่องฝุ่น]
    🔥 **เฝ้าระวังความร้อน:** [สรุปเรื่องจุดเผาไร่นาตามข้อมูล]
    🌊 **ระดับน้ำอินทร์บุรี:** [บอกระดับน้ำเป็นเมตรแบบเข้าใจง่าย]
    🛑 **ระบายน้ำเขื่อนเจ้าพระยา:** [สรุปการระบายน้ำ]

    📌 **สรุป:** [ใส่ความจำเจ/อารมณ์ขัน/ทักทายตามวัน 1-2 บรรทัด]
    """
    
    # 4. เรียกใช้ Gemini พร้อม Retry + Exponential Backoff
    max_retries = 3
    final_post = ""
    for attempt in range(max_retries):
        try:
            print(f"กำลังร่างโพสต์ (รอบที่ {attempt+1})...")
            response = client.models.generate_content(
                model='gemini-2.5-flash-preview-04-17', # อัปเดตชื่อโมเดลให้ถูกต้อง
                contents=prompt,
                config={'temperature': 1.0}
            )
            final_post = response.text.strip() + "\n\n#อินทร์บุรีรอดมั้ย #VIIRS #GEE"
            break # สำเร็จแล้ว ออกจาก loop
        except Exception as e:
            print(f"❌ Error ในการเรียก Gemini (รอบ {attempt+1}): {e}")
            if attempt < max_retries - 1:
                # Exponential backoff พร้อม Jitter (กันชนกัน)
                wait = (5 * (3 ** attempt)) + random.uniform(0, 3) 
                print(f"⏳ รอ {wait:.2f} วินาทีก่อนลองใหม่...")
                time.sleep(wait)
            else:
                final_post = f"**สถานการณ์อินทร์บุรี** ข้อมูล ณ {date_str} เวลา {time_str}\n(ระบบ AI สรุปข่าวขัดข้องชั่วคราว โปรดติดตามอัปเดตภายหลัง)"

    print("\n📝 ข้อความที่จะโพสต์:\n", final_post)
    
    # 5. ส่ง Webhook ไปยัง Make.com
    if MAKE_WEBHOOK_URL and final_post:
        # เช็ค Guard ป้องกันขยะขึ้นเพจ
        if "ขัดข้องชั่วคราว" not in final_post:
            try:
                res = requests.post(MAKE_WEBHOOK_URL, json={"text_to_post": final_post}, timeout=15)
                if res.status_code == 200: 
                    print("\n✅ ส่ง Webhook สำเร็จ!")
                else:
                    print(f"\n⚠️ ส่ง Webhook ไม่สำเร็จ Status Code: {res.status_code}")
            except Exception as e:
                print(f"\n❌ Error ส่ง Webhook: {e}")
        else:
            print("\n⛔ ยกเลิกการส่งโพสต์ลงเพจ เนื่องจาก AI ขัดข้อง")
