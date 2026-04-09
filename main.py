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

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") 
MAKE_WEBHOOK_URL = os.environ.get("MAKE_WEBHOOK_URL")
client = genai.Client(api_key=GEMINI_API_KEY)

tz = pytz.timezone('Asia/Bangkok')
now = datetime.now(tz)
THAI_MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]
thai_month = THAI_MONTHS[now.month - 1]
thai_year = now.year + 543
date_str = f"{now.day} {thai_month} {thai_year}"
time_str = now.strftime("%H:%M น.")

def get_dist(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(float(lat2) - float(lat1))
    dlon = math.radians(float(lon2) - float(lon1))
    a = math.sin(dlat/2)**2 + math.cos(math.radians(float(lat1))) * math.cos(math.radians(float(lat2))) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    return R * c

def get_hotspots():
    EE_JSON_KEY = os.environ.get("EE_JSON_KEY")
    if not EE_JSON_KEY:
        return "N/A"
    try:
        json_key = json.loads(EE_JSON_KEY)
        credentials = ee.ServiceAccountCredentials(json_key['client_email'], key_data=EE_JSON_KEY)
        ee.Initialize(credentials)

        inburi_area = ee.Geometry.Point([100.3273, 15.0076]).buffer(10000)
        end_date = ee.Date(datetime.now())
        start_date = end_date.advance(-24, 'hour')
        fire_col = ee.ImageCollection("FIRMS").filterBounds(inburi_area).filterDate(start_date, end_date)
        
        return fire_col.size().getInfo()
    except Exception as e:
        print(f"⚠️ ระบบดาวเทียมขัดข้อง: {e}")
        return "N/A"

def get_accurate_pm25():
    INBURI_LAT, INBURI_LON = 15.0076, 100.3273
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
    except: pass

    try:
        res = requests.get(f"http://air4thai.pcd.go.th/services/getNewAQI_JSON.php?t={int(time.time())}", headers=headers, timeout=15, verify=False)
        if res.status_code == 200:
            for st in res.json().get('stations', []):
                pm25_val = st.get('LastUpdate', {}).get('PM25', {}).get('value')
                if not pm25_val or pm25_val == "-": continue
                dist = get_dist(INBURI_LAT, INBURI_LON, st.get('lat'), st.get('long'))
                if dist <= MAX_DISTANCE_KM:
                    all_sources.append({'pm25': float(pm25_val), 'distance': dist, 'age': 0, 'priority': 1})
    except: pass

    try:
        url = f"https://air-quality-api.open-meteo.com/v1/air-quality?latitude={INBURI_LAT}&longitude={INBURI_LON}&current=pm2_5&timezone=Asia%2FBangkok"
        res = requests.get(url, headers=headers, timeout=10)
        if 'current' in res.json():
            all_sources.append({'pm25': float(res.json()['current']['pm2_5']), 'distance': 0, 'age': 0, 'priority': 3})
    except: pass

    if not all_sources: return "N/A"
    all_sources.sort(key=lambda x: (x['priority'], x['distance']))
    return f"{all_sources[0]['pm25']:.1f}"

def get_weather():
    TOMORROW_API_KEY = os.environ.get("TOMORROW_API_KEY")
    temp, pm25, rain_prob, humidity, wind, uv = "N/A", "N/A", "N/A", "N/A", "N/A", "N/A"
    if TOMORROW_API_KEY:
        try:
            tmr_url = f"https://api.tomorrow.io/v4/weather/forecast?location=14.9961,100.3253&apikey={TOMORROW_API_KEY}"
            res = requests.get(tmr_url, timeout=10)
            if res.status_code == 200:
                tmr_res = res.json()
                current_data = tmr_res['timelines']['minutely'][0]['values']
                humidity = round(current_data['humidity'], 1)
                wind = round(current_data['windSpeed'], 1) 
                rain_prob = max([h['values']['precipitationProbability'] for h in tmr_res['timelines']['hourly'][:12]])
        except: pass
    try:
        om_weather_url = "https://api.open-meteo.com/v1/forecast?latitude=14.9961&longitude=100.3253&current=temperature_2m,uv_index&timezone=Asia%2FBangkok"
        res = requests.get(om_weather_url, timeout=10).json()
        temp = res['current']['temperature_2m']
        uv = res['current'].get('uv_index', 'N/A')
    except: pass
    return temp, pm25, rain_prob, humidity, wind, uv

def get_inburi_data():
    url = f"https://singburi.thaiwater.net/wl?cb={random.randint(10000, 99999)}"
    water_level, bank_level = None, 13.10 
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(url, timeout=60000)
            page.wait_for_selector("th[scope='row']", timeout=30000)
            soup = BeautifulSoup(page.content(), "html.parser")
            for th in soup.select("th[scope='row']"):
                if "อินทร์บุรี" in th.get_text(strip=True):
                    cols = th.find_parent("tr").find_all("td")
                    nums = [float(re.sub(r"[^0-9\.\-]", "", td.get_text(strip=True))) for td in cols if re.sub(r"[^0-9\.\-]", "", td.get_text(strip=True))]
                    if nums: water_level = nums[0]; break
        except: pass
        finally: browser.close()
    return water_level, bank_level

def fetch_chao_phraya_dam_discharge():
    url = f"https://tiwrm.hii.or.th/DATA/REPORT/php/chart/chaopraya/small/chaopraya.php?cb={random.randint(10000, 99999)}"
    try:
        res = requests.get(url, timeout=20)
        match = re.search(r'var json_data = (\[.*\]);', res.text)
        if match:
            data = json.loads(match.group(1))
            val = data[0]['itc_water']['C13']['storage']
            return float(val) if isinstance(val, (int, float)) else float(str(val).replace(',', ''))
    except: pass
    return None

if __name__ == "__main__":
    print("=== เริ่มรวบรวมข้อมูลอินทร์บุรี + พลังดาวเทียม ===")
    
    temp, _, rain_prob, humidity, wind, uv = get_weather() 
    pm25 = get_accurate_pm25()
    wl, bank_level = get_inburi_data()
    discharge = fetch_chao_phraya_dam_discharge()
    hotspots = get_hotspots()
    
    wl_text = f"ความสูง {wl} ม.รทก. (ห่างจากตลิ่ง {round(bank_level - wl, 2)} เมตร)" if wl else "รออัปเดต"
    discharge_text = f"{discharge} ลบ.ม./วินาที" if discharge else "รออัปเดต"
    
    # ✅ ปรับแก้ตรรกะใหม่ ให้ AI ไม่งงเวลาดาวเทียมพัง
    if hotspots == "N/A":
        hotspot_text = "ระบบตรวจจับขัดข้องชั่วคราว ไม่สามารถระบุได้"
    elif hotspots == 0:
        hotspot_text = "0 จุด (ไม่พบการเผาไหม้ในพื้นที่ ปลอดภัย)"
    else:
        hotspot_text = f"ตรวจพบ {hotspots} จุด (เฝ้าระวังการเผาไหม้)"

    prompt = f"""
    คุณคือแอดมินเพจ "อินทร์บุรีรอดมั้ย" อัปเดตข่าวสารให้ชาวบ้านแบบเป็นกันเอง ไม่จำเจ
    ข้อมูลดิบ: {date_str} {time_str}
    - อากาศ: {temp}°C, แดด(UV): {uv}, ฝน: {rain_prob}%, ลม: {wind} m/s
    - ฝุ่น PM 2.5: {pm25} (ห้ามพิมพ์ตัวเลข ให้บอกความรู้สึก)
    - ดาวเทียม VIIRS (รัศมี 10 กม. รอบอินทร์บุรี): {hotspot_text}
    - ระดับน้ำ: {wl_text}, ระบายเขื่อน: {discharge_text}

    กฎ:
    1. วิเคราะห์เรื่องจุดความร้อนให้สอดคล้องกับข้อความที่ให้ไป ห้ามพูดขัดแย้งกันเอง
    2. ห้ามใช้คำลงท้าย "ครับ/ค่ะ" เด็ดขาด
    3. ผลลัพธ์ต้องออกมาตามโครงสร้างนี้เป๊ะๆ:

    **สถานการณ์อินทร์บุรี** (ข้อมูล ณ {date_str} เวลา {time_str})
    
    🌡️ **สภาพอากาศและฝุ่น:** [สรุปอากาศ+ความรู้สึกเรื่องฝุ่น]
    🔥 **เฝ้าระวังความร้อน (ดาวเทียม):** [สรุปเรื่องจุดความร้อนตามข้อมูล]
    🌊 **ระดับน้ำอินทร์บุรี:** [สรุปตัวเลขน้ำและความอุ่นใจ]
    🛑 **ระบายน้ำเขื่อนเจ้าพระยา:** [สรุปการระบายน้ำ]

    📌 **สรุป:** [สรุปภาพรวมสั้นๆ 1-2 บรรทัด]
    """
    
    max_retries = 3
    final_post = ""
    for attempt in range(max_retries):
        try:
            print(f"กำลังร่างโพสต์ (รอบที่ {attempt+1})...")
            response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
            final_post = response.text.strip() + "\n\n#อินทร์บุรีรอดมั้ย #VIIRS #GEE"
            break
        except Exception as e:
            if attempt < max_retries - 1: time.sleep(5)
            else: final_post = f"**สถานการณ์อินทร์บุรี**... (ระบบ AI ขัดข้องชั่วคราว)"

    print("\nข้อความที่จะโพสต์:\n", final_post)
    
    if MAKE_WEBHOOK_URL and final_post:
        res = requests.post(MAKE_WEBHOOK_URL, json={"text_to_post": final_post})
        if res.status_code == 200: print("\n✅ ส่ง Webhook สำเร็จ!")
