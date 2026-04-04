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
        rain_prob = max([hour['values']['precipitationProbability'] for hour in hourly_data])
        
        w_res = requests.get(om_weather_url).json()
        temp = w_res['current']['temperature_2m']
        uv = w_res['current'].get('uv_index', 'N/A')
        
        aqi_res = requests.get(om_aqi_url).json()
        pm25 = aqi_res['current'].get('pm2_5', 'N/A')
        
        return temp, pm25, rain_prob, humidity, wind, uv
    except Exception as e:
        print(f"เกิดข้อผิดพลาดสภาพอากาศ: {e}")
        return "N/A", "N/A", "N/A", "N/A", "N/A", "N/A"

# --- 2. ดึงระดับน้ำอินทร์บุรี (Playwright ตัวเก่ง) ---
def get_inburi_data():
    url = f"https://singburi.thaiwater.net/wl?cb={random.randint(10000, 99999)}"
    water_level = None
    bank_level = 13.10 
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "media", "font", "stylesheet"] else route.continue_())
        try:
            page.goto(url, timeout=60000, wait_until="domcontentloaded")
            page.wait_for_selector("th[scope='row']", timeout=30000)
            soup = BeautifulSoup(page.content(), "html.parser")
            
            for th in soup.select("th[scope='row']"):
                if "อินทร์บุรี" in th.get_text(strip=True):
                    cols = th.find_parent("tr").find_all("td")
                    numeric_values = []
                    for td in cols:
                        text = td.get_text(strip=True)
                        try:
                            cleaned = re.sub(r"[ ,]", "", text)
                            cleaned = re.sub(r"[^0-9\.\-]", "", cleaned)
                            if cleaned and cleaned not in ["-", ".", "..", "..."]:
                                numeric_values.append(float(cleaned))
                        except ValueError:
                            continue 
                    if numeric_values:
                        water_level = numeric_values[0]
                        print(f"✅ ได้ข้อมูลอินทร์บุรี: {water_level}")
                        break
        except Exception as e:
            print(f"เกิดข้อผิดพลาดข้อมูลสิงห์บุรี: {e}")
        finally:
            browser.close()
            
    return water_level, bank_level

# --- 3. ดึงระบายน้ำเขื่อนเจ้าพระยา (อัปเกรดใช้ Playwright ทะลวงกำแพงเว็บ!) ---
def fetch_chao_phraya_dam_discharge():
    print("▶️ กำลังใช้เบราว์เซอร์จำลอง (Playwright) ดึงข้อมูลจาก tiwrm.hii.or.th...")
    url = f"https://tiwrm.hii.or.th/DATA/REPORT/php/chart/chaopraya/small/chaopraya.php?cb={random.randint(10000, 99999)}"
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # ปิดโหลดรูปเพื่อความไว แต่ยอมให้โหลดสคริปต์เพื่อหลอกว่าเป็นคน
        page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "media", "font"] else route.continue_())
        
        try:
            page.goto(url, timeout=60000, wait_until="domcontentloaded")
            # บังคับรอให้กล่อง C13 โหลดขึ้นมาให้เห็นก่อน ค่อยดูดข้อมูล
            page.wait_for_selector("#C13", timeout=30000)
            
            html = page.content()
            soup = BeautifulSoup(html, "html.parser")
            
            # เจาะเข้า ID C13
            c13_box = soup.find("div", id="C13")
            if c13_box:
                tds = c13_box.find_all("td")
                for i, td in enumerate(tds):
                    if "ปริมาณน้ำ" in td.get_text(strip=True):
                        # ดึงข้อมูลจากช่องถัดไป (เช่น "300.00/ 2840 cms")
                        val_text = tds[i+1].get_text(strip=True).split('/')[0]
                        cleaned = re.sub(r"[^0-9\.]", "", val_text)
                        if cleaned:
                            val = float(cleaned)
                            print(f"✅ สำเร็จ! ดึงข้อมูลการปล่อยน้ำได้: {val}")
                            return val
            print("❌ เปิดเว็บได้แต่หาคำว่า ปริมาณน้ำ ไม่เจอ")
        except Exception as e:
            print(f"❌ เกิดข้อผิดพลาดในการใช้เบราว์เซอร์จำลองดึงเขื่อน: {e}")
        finally:
            browser.close()
            
    return None

if __name__ == "__main__":
    print("=== เริ่มใช้งานตรรกะระดับเทพ (Playwright Engine) ===")
    
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
