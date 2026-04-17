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

        # 1. กำหนดจุดศูนย์กลางอินทร์บุรี และปรับรัศมี (Buffer) ที่ 12,000 เมตร
        inburi_area = ee.Geometry.Point([100.3273, 15.0076]).buffer(12000)
        
        # 2. ดึงข้อมูล FIRMS ย้อนหลัง 24 ชั่วโมง
        end_date = ee.Date(datetime.now(tz))
        start_date = end_date.advance(-24, 'hour')
        
        dataset = ee.ImageCollection("FIRMS") \
            .filterBounds(inburi_area) \
            .filterDate(start_date, end_date)

        # หากไม่มีดาวเทียมบินผ่านเลย ให้ถือว่าไม่พบข้อมูล
        if dataset.size().getInfo() == 0:
            return 0

        # 3. รวบรวมภาพทั้งหมดใน 24 ชม. และเลือกแบนด์ T21 (อุณหภูมิความร้อน)
        fires = dataset.select('T21').max()

        # สร้าง Mask กรองเฉพาะจุดที่มีการเผาไหม้จริงๆ
        fire_mask = fires.gt(0)
        fires_masked = fires.updateMask(fire_mask)

        # 4. นับจำนวนพิกเซล (จุดความร้อน) ภายในพื้นที่อินทร์บุรี
        stats = fires_masked.reduceRegion(
            reducer=ee.Reducer.count(),
            geometry=inburi_area,
            scale=1000,  # ความละเอียดดาวเทียมอยู่ที่ประมาณ 1 กม. ต่อพิกเซล
            maxPixels=1e9
        )

        count = stats.get('T21').getInfo()
        
        # คืนค่าจำนวนจุด (ถ้าเป็น None แปลว่าไม่พบการเผา ให้คืนค่า 0)
        return int(count) if count is not None else 0

    except Exception as e:
        print(f"⚠️ ระบบดาวเทียมขัดข้อง: {e}")
        return "N/A"

def _safe_float(v):
    try:
        return float(str(v).replace(',', '').strip())
    except:
        return None

def _parse_air4thai_age_seconds(st):
    patterns = [
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y %H:%M:%S",
    ]
    try:
        lu = st.get('LastUpdate', {}) if isinstance(st, dict) else {}
        date_text = str(lu.get('date') or st.get('date') or '').strip()
        time_text = str(lu.get('time') or st.get('time') or '').strip()
        if not date_text or not time_text:
            return None

        raw = f"{date_text} {time_text}"
        dt = None
        for fmt in patterns:
            try:
                dt = datetime.strptime(raw, fmt)
                break
            except:
                pass

        if dt is None:
            return None

        if dt.tzinfo is None:
            dt = tz.localize(dt)

        return max(0, int((datetime.now(tz) - dt).total_seconds()))
    except:
        return None

def _weighted_pm25(rows):
    total_w = 0.0
    total_v = 0.0

    for r in rows:
        # ใกล้กว่า = น้ำหนักมากกว่า
        dist_w = 1 / ((r['distance'] + 1.0) ** 1.6)

        # ใหม่กว่า = น้ำหนักมากกว่า
        age_w = max(0.15, 1 - (r['age'] / r['max_age']))

        # ground station ให้เครดิตสูงกว่า model
        source_w = 1.0 if r['source'] == 'air4thai' else 0.85

        w = dist_w * age_w * source_w
        total_w += w
        total_v += r['pm25'] * w

    if total_w == 0:
        return None
    return total_v / total_w

def get_accurate_pm25():
    INBURI_LAT, INBURI_LON = 15.0076, 100.3273

    # ปรับให้เข้มงวดขึ้นเพื่อความ "ตรงพื้นที่"
    STRICT_RADIUS_KM = 20
    FALLBACK_RADIUS_KM = 35
    MAX_DATA_AGE_SECONDS = 7200   # 2 ชั่วโมง

    headers = {'User-Agent': 'Mozilla/5.0'}
    air4thai_rows = []
    gistda_value = None
    openmeteo_value = None

    # 1) GISTDA: ค่าตามพิกัดตรงจุด ใช้เป็น fallback ชั้นดี
    try:
        current_ts = int(time.time())
        url_gistda = (
            f"https://pm25.gistda.or.th/rest/getPM25byLocation"
            f"?lat={INBURI_LAT}&lng={INBURI_LON}&t={current_ts}"
        )
        res = requests.get(url_gistda, headers=headers, timeout=15, verify=False)
        if res.status_code == 200:
            payload = res.json()
            data = payload.get('data', payload)
            pm = _safe_float(data.get('pm25'))
            if pm is not None:
                gistda_value = pm
    except:
        pass

    # 2) Air4Thai: ใช้สถานีใกล้ + สด เป็นตัวหลัก
    try:
        url = f"http://air4thai.pcd.go.th/services/getNewAQI_JSON.php?t={int(time.time())}"
        res = requests.get(url, headers=headers, timeout=15, verify=False)
        if res.status_code == 200:
            for st in res.json().get('stations', []):
                pm25_val = _safe_float(st.get('LastUpdate', {}).get('PM25', {}).get('value'))
                if pm25_val is None:
                    continue

                lat = _safe_float(st.get('lat'))
                lon = _safe_float(st.get('long'))
                if lat is None or lon is None:
                    continue

                dist = get_dist(INBURI_LAT, INBURI_LON, lat, lon)
                if dist > FALLBACK_RADIUS_KM:
                    continue

                age = _parse_air4thai_age_seconds(st)
                if age is None:
                    age = MAX_DATA_AGE_SECONDS + 1  # ถ้าอ่านเวลาไม่ได้ ให้ถือว่าเก่าไว้ก่อน

                air4thai_rows.append({
                    'source': 'air4thai',
                    'pm25': pm25_val,
                    'distance': dist,
                    'age': age,
                    'max_age': MAX_DATA_AGE_SECONDS
                })
    except:
        pass

    # 3) Open-Meteo: สำรองสุดท้าย
    try:
        url = (
            "https://air-quality-api.open-meteo.com/v1/air-quality"
            f"?latitude={INBURI_LAT}&longitude={INBURI_LON}"
            "&current=pm2_5&timezone=Asia%2FBangkok"
        )
        res = requests.get(url, headers=headers, timeout=10)
        data = res.json()
        if 'current' in data:
            pm = _safe_float(data['current'].get('pm2_5'))
            if pm is not None:
                openmeteo_value = pm
    except:
        pass

    # A) ถ้ามีสถานีสดและใกล้จริง ใช้สถานีเป็นหลัก
    strict_rows = [
        r for r in air4thai_rows
        if r['distance'] <= STRICT_RADIUS_KM and r['age'] <= MAX_DATA_AGE_SECONDS
    ]
    if strict_rows:
        strict_rows.sort(key=lambda x: (x['distance'], x['age']))
        pm = _weighted_pm25(strict_rows[:3])   # เอา 3 สถานีใกล้สุด
        if pm is not None:
            return f"{pm:.1f}"

    # B) ถ้าไม่มีสถานีใกล้ที่สด ใช้ GISTDA ตามพิกัด
    if gistda_value is not None:
        return f"{gistda_value:.1f}"

    # C) ถ้ายังไม่ได้ ค่อยผ่อนเกณฑ์ Air4Thai
    loose_rows = [
        r for r in air4thai_rows
        if r['distance'] <= FALLBACK_RADIUS_KM and r['age'] <= MAX_DATA_AGE_SECONDS
    ]
    if loose_rows:
        loose_rows.sort(key=lambda x: (x['distance'], x['age']))
        pm = _weighted_pm25(loose_rows[:3])
        if pm is not None:
            return f"{pm:.1f}"

    # D) สำรองสุดท้าย
    if openmeteo_value is not None:
        return f"{openmeteo_value:.1f}"

    return "N/A"

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
                        except: continue
                    if numeric_values:
                        water_level = numeric_values[0]
                        break
        except Exception as e:
            print(f"เกิดข้อผิดพลาดในการดึงข้อมูลสิงห์บุรี: {e}")
        finally:
            browser.close()
            
    return water_level, bank_level

def fetch_chao_phraya_dam_discharge():
    url = f"https://tiwrm.hii.or.th/DATA/REPORT/php/chart/chaopraya/small/chaopraya.php?cb={random.randint(10000, 99999)}"
    try:
        res = requests.get(url, timeout=20)
        match = re.search(r'var json_data = (\[.*\]);', res.text)
        if match:
            data = json.loads(match.group(1))
            val = data[0]['itc_water']['C13']['storage']  # ← ถูกต้องแล้ว
            return float(val) if isinstance(val, (int, float)) else float(str(val).replace(',', ''))
    except Exception as e:
        print(f"⚠️ เขื่อน error: {e}")
    return None

if __name__ == "__main__":
    print("=== เริ่มรวบรวมข้อมูลอินทร์บุรี + พลังดาวเทียม ===")
    
    temp, _, rain_prob, humidity, wind, uv = get_weather() 
    pm25 = get_accurate_pm25()
    wl, bank_level = get_inburi_data()
    discharge = fetch_chao_phraya_dam_discharge()
    hotspots = get_hotspots()
    
    if wl is not None:
        diff = round(bank_level - wl, 2)
        if diff < 0:
            wl_text = f"ระดับน้ำ {wl} เมตร (⚠️ เกินตลิ่ง {abs(diff)} เมตร)"
        else:
            wl_text = f"ระดับน้ำ {wl} เมตร (ห่างจากตลิ่ง {diff} เมตร)"
    else:
        wl_text = "รออัปเดต"
    discharge_text = f"{discharge} ลบ.ม./วินาที" if discharge is not None else "รออัปเดต"
    
    if hotspots == "N/A":
        hotspot_text = "ระบบตรวจจับขัดข้องชั่วคราว"
    elif hotspots == 0:
        hotspot_text = "ไม่พบจุดเผาในพื้นที่ ปลอดภัยดี"
    else:
        hotspot_text = f"ตรวจพบ {hotspots} จุด (เฝ้าระวังควันจากการเผาไร่/นา)"

    # ล้างข้อความ cite ส่วนเกินและจัดย่อหน้าให้เป๊ะ
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
    
    max_retries = 5
    final_post = ""
    for attempt in range(max_retries):
        try:
            print(f"กำลังร่างโพสต์ (รอบที่ {attempt+1})...")
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config={'temperature': 1.0}
            )
            final_post = response.text.strip() + "\n\n#อินทร์บุรีรอดมั้ย #VIIRS #GEE"
            break
        except Exception as e:
            print(f"Error: {e}")
            if attempt < max_retries - 1:
                wait = 10 * (2 ** attempt)  # 10, 20, 40, 80 วินาที
                print(f"รอ {wait} วินาที...")
                time.sleep(wait)
            else:
                final_post = "**สถานการณ์อินทร์บุรี**... (ระบบ AI ขัดข้องชั่วคราว)"

    print("\nข้อความที่จะโพสต์:\n", final_post)
    
    if MAKE_WEBHOOK_URL and final_post and "ขัดข้องชั่วคราว" not in final_post:
        res = requests.post(MAKE_WEBHOOK_URL, json={"text_to_post": final_post})
        if res.status_code == 200: 
            print("\n✅ ส่ง Webhook สำเร็จ!")
