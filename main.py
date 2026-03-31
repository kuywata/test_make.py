import requests
import os
import json
import re
from datetime import datetime
import pytz
from google import genai
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# 1. ตั้งค่าพื้นฐาน
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") 
MAKE_WEBHOOK_URL = os.environ.get("MAKE_WEBHOOK_URL")
client = genai.Client(api_key=GEMINI_API_KEY)

# ตั้งเวลาไทย (Asia/Bangkok)
tz = pytz.timezone('Asia/Bangkok')
now = datetime.now(tz)
date_str = now.strftime("%d %B 2569")
time_str = now.strftime("%H:%M น.")

def get_real_data() -> dict:
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'}
    results = {"wl": "รออัปเดต", "bank": None, "discharge": "รออัปเดต", "temp": "N/A", "pm25": "N/A"}

    # ใช้ Playwright ทำหน้าที่เปิดเว็บแทน requests_html ของ ChatGPT
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # --- 1. ดึงระดับน้ำ C.3 อินทร์บุรี (แบบแผนหลักและสำรองของ ChatGPT) ---
        level_parsed = False
        try:
            page.goto("https://www.thaiwater.net/water/wl", timeout=60000)
            page.wait_for_timeout(10000) # รอสคริปต์โหลด
            soup = BeautifulSoup(page.content(), "html.parser")
            tables = soup.find_all("table")
            for table_el in tables:
                rows = table_el.find_all("tr")
                for row in rows:
                    cols = [col.get_text(strip=True) for col in row.find_all(["th", "td"])]
                    if cols and ("อินทร์บุรี" in cols[0] or "C.3" in cols[0]):
                        if len(cols) >= 4:
                            wl_str = re.sub(r"[^0-9\.]+", "", cols[2])
                            bank_str = re.sub(r"[^0-9\.]+", "", cols[3])
                            if wl_str:
                                results["wl"] = float(wl_str)
                                level_parsed = True
                            if bank_str:
                                results["bank"] = float(bank_str)
                        break
                if level_parsed:
                    break
        except Exception:
            pass

        # แผนสำรอง C.3 (ดึงจากเว็บย่อยสิงห์บุรี ตามโค้ด ChatGPT)
        if not level_parsed:
            try:
                res = requests.get("https://singburi.thaiwater.net/wl", headers=headers, timeout=20)
                if res.status_code == 200:
                    soup = BeautifulSoup(res.text, "html.parser")
                    table = soup.find("table")
                    if table:
                        rows = table.find_all("tr")
                        for row in rows:
                            cols = [col.get_text(strip=True) for col in row.find_all(["th", "td"])]
                            if cols and ("อินทร์บุรี" in cols[0] or "C.3" in cols[0]):
                                if len(cols) >= 5:
                                    wl_str = re.sub(r"[^0-9\.]+", "", cols[3])
                                    bank_str = re.sub(r"[^0-9\.]+", "", cols[4])
                                elif len(cols) >= 4:
                                    wl_str = re.sub(r"[^0-9\.]+", "", cols[3])
                                    bank_str = None
                                if wl_str: results["wl"] = float(wl_str)
                                if bank_str: results["bank"] = float(bank_str)
                                break
            except Exception:
                pass

        # --- 2. ดึงการระบายน้ำ C.13 เขื่อนเจ้าพระยา (แบบแผนหลักและสำรองของ ChatGPT) ---
        discharge_value = None
        # แผน 1: ดึงจาก API ของกรมชลประทานโดยตรง
        try:
            today = datetime.now(pytz.timezone('Asia/Bangkok')).strftime("%m/%d/%Y")
            payload = {"hydro": {"StationGroupID": "214", "TimeCurrent": today}}
            resp = requests.post(
                "https://hyd-app-db.rid.go.th/webservice/getGroupHourlyWaterLevelReportHL.ashx",
                headers={**headers, 'Content-Type': 'application/json'}, json=payload, timeout=30)
            if resp.status_code == 200:
                data = resp.json().get('rows', [])
                col_names = resp.json().get('colNames', [])
                c13_discharge_col = next((name for name in col_names if isinstance(name, str) and ('C.13' in name and ('ปริมาณ' in name or 'Q' in name))), None)
                if c13_discharge_col and data:
                    value_str = data[-1].get(c13_discharge_col)
                    if value_str: discharge_value = float(str(value_str).replace(",", ""))
        except Exception:
            pass
        
        # แผนสำรอง 2: ถ้า API กรมชลฯล่ม ให้ใช้ Playwright เปิดตารางแล้วให้ Pandas แกะ
        if discharge_value is None:
            try:
                page.goto("https://hyd-app-db.rid.go.th/hydro5h.html", timeout=60000)
                page.wait_for_timeout(10000)
                soup = BeautifulSoup(page.content(), "html.parser")
                table_el = soup.find("table")
                if table_el:
                    import pandas as pd
                    dfs = pd.read_html(str(table_el))
                    for df in dfs:
                        flat_cols = [" ".join([str(x) for x in col if str(x) != 'nan']).strip() if isinstance(col, tuple) else str(col).strip() for col in df.columns]
                        df.columns = flat_cols
                        for col in df.columns:
                            if 'C.13' in col and (('ปริมาณ' in col) or ('Q' in col)):
                                target = df[col].dropna()
                                if not target.empty:
                                    discharge_value = float(str(target.iloc[-1]).replace(",", ""))
                                    break
                        if discharge_value is not None: break
            except Exception:
                pass
        
        browser.close()

    if discharge_value is not None:
        results["discharge"] = discharge_value
        # ระบบจำค่าเก่าของ ChatGPT เพื่อเปรียบเทียบความเปลี่ยนแปลง
        history_file = 'last_discharge.json'
        try:
            if os.path.isfile(history_file):
                with open(history_file, 'r', encoding='utf-8') as f:
                    history = json.load(f)
                last_value = history.get('value')
                if isinstance(last_value, (int, float)):
                    results["discharge_diff"] = round(discharge_value - float(last_value), 2)
            with open(history_file, 'w', encoding='utf-8') as f:
                json.dump({'date': datetime.now(pytz.timezone('Asia/Bangkok')).strftime('%Y-%m-%d'), 'value': discharge_value}, f)
        except Exception:
            pass

    # --- 3. ดึงสภาพอากาศ ---
    try:
        w = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={'latitude': 14.9961, 'longitude': 100.3253, 'current': 'temperature_2m,pm2_5', 'timezone': 'Asia/Bangkok'},
            timeout=20).json()
        results["temp"] = w.get('current', {}).get('temperature_2m', 'N/A')
        results["pm25"] = w.get('current', {}).get('pm2_5', 'N/A')
    except Exception:
        pass

    return results

if __name__ == "__main__":
    d = get_real_data()
    
    # คำนวณความห่างตลิ่ง (ถ้าหน้าเว็บมีเลขตลิ่งใช้เลขเว็บ ถ้าไม่มีใช้ 13.10)
    bank_level = d.get("bank") or 13.10
    if isinstance(d["wl"], float):
        diff_from_bank = round(bank_level - d["wl"], 2)
        wl_text = f"ความสูง {d['wl']} ม.รทก. (ห่างจากตลิ่ง {diff_from_bank} เมตร)"
    else:
        wl_text = "รออัปเดตข้อมูล ม.รทก."

    # คำนวณตัวเลขเปรียบเทียบกับเมื่อวาน (ฟีเจอร์เด็ดของ ChatGPT)
    discharge_diff_str = ""
    diff_val = d.get('discharge_diff')
    if isinstance(diff_val, (int, float)):
        sign = "+" if diff_val >= 0 else ""
        discharge_diff_str = f" ({sign}{diff_val} ลบ.ม./วินาที เทียบกับเมื่อวาน)"
        
    prompt = f"""
    สรุปสถานการณ์วันที่ {date_str} เวลา {time_str}
    - อุณหภูมิ: {d['temp']}°C
    - ฝุ่น PM 2.5: {d['pm25']} μg/m³
    - ระดับน้ำอินทร์บุรี: {wl_text}
    - ระบายน้ำเขื่อนเจ้าพระยา: {d['discharge']} ลบ.ม./วินาที{discharge_diff_str}

    เขียนโพสต์ให้ชาวบ้านอ่านง่าย สั้นๆ ไม่ต้องมีคำเกริ่นนำ เริ่มที่หัวข้อ **สถานการณ์อินทร์บุรี** ทันที
    """

    try:
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        content = response.text.strip()
    except Exception:
        content = prompt

    if MAKE_WEBHOOK_URL:
        requests.post(MAKE_WEBHOOK_URL, json={"text_to_post": content + "\n\n#อินทร์บุรีรอดมั้ย"})
