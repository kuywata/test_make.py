import requests
import os
import json
import re
from datetime import datetime
import pytz
from google import genai
from bs4 import BeautifulSoup

# We'll attempt to render pages with JavaScript when necessary using requests_html.
# This library allows a headless browser to execute scripts and return the final HTML.
try:
    from requests_html import HTMLSession
except ImportError:
    # If requests_html isn't available, we'll handle gracefully in get_discharge
    HTMLSession = None

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
    """
    Collect real-time data for weather, PM2.5, water level at Inburi (C.3) and
    discharge at the Chao Phraya Dam (C.13). If any source is unavailable,
    returns placeholder strings. The water level is parsed from the Singburi
    provincial water-level page, while the discharge is obtained from the RID
    hydro data report using a rendered page or fallback JSON endpoint.

    Returns
    -------
    dict
        A dictionary with keys: 'wl', 'discharge', 'temp', 'pm25'.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0'
    }
    results = {"wl": "รออัปเดต", "bank": None, "discharge": "รออัปเดต", "temp": "N/A", "pm25": "N/A"}

    # --- Fetch water level (C.3 Inburi) and bank level from Thaiwater provincial page ---
    # We attempt multiple sources: first, the dynamic Thaiwater page that lists stations across Thailand.
    # If requests_html is available, we render the page to execute JavaScript and then scrape the table.
    level_parsed = False
    # Attempt 1: use the full national page that includes all stations. This page contains dynamic content populated by JS.
    if HTMLSession is not None:
        try:
            session = HTMLSession()
            # Use the national water level page which lists all stations.
            r = session.get("https://www.thaiwater.net/water/wl", headers=headers)
            r.html.render(timeout=60)
            # After rendering, find the data table.
            tables = r.html.find("table")
            for table_el in tables:
                # Use BeautifulSoup to parse each table's HTML for easier processing.
                soup = BeautifulSoup(table_el.html, "html.parser")
                rows = soup.find_all("tr")
                for row in rows:
                    cols = [col.get_text(strip=True) for col in row.find_all(["th", "td"])]
                    # Identify the row for Inburi station (C.3).
                    if cols and ("อินทร์บุรี" in cols[0] or "C.3" in cols[0]):
                        # Many Thaiwater tables separate values into columns: [Station, Basin, Water level, Bank level, Situation, Trend]
                        # We attempt to parse water level and bank level from positions 2 and 3 (0-indexed). Adjust if necessary.
                        # Remove any non-numeric characters such as commas or units.
                        try:
                            if len(cols) >= 4:
                                wl_str = re.sub(r"[^0-9\.]+", "", cols[2])  # e.g., "5.73" from "5.73"
                                bank_str = re.sub(r"[^0-9\.]+", "", cols[3])  # e.g., "15.10" from "15.10"
                                if wl_str:
                                    results["wl"] = float(wl_str)
                                    level_parsed = True
                                if bank_str:
                                    results["bank"] = float(bank_str)
                        except Exception:
                            pass
                        break
                if level_parsed:
                    break
            session.close()
        except Exception:
            pass

    # Attempt 2: fallback to provincial subdomain (singburi.thaiwater.net/wl) which may serve static HTML
    if not level_parsed:
        try:
            url = "https://singburi.thaiwater.net/wl"
            res = requests.get(url, headers=headers, timeout=20)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "html.parser")
                table = soup.find("table")
                if table:
                    rows = table.find_all("tr")
                    for row in rows:
                        cols = [col.get_text(strip=True) for col in row.find_all(["th", "td"])]
                        if cols and ("อินทร์บุรี" in cols[0] or "C.3" in cols[0]):
                            # Some provincial pages may provide water level and bank level in adjacent columns (e.g., idx 3 and 4).
                            try:
                                if len(cols) >= 5:
                                    wl_str = re.sub(r"[^0-9\.]+", "", cols[3])
                                    bank_str = re.sub(r"[^0-9\.]+", "", cols[4])
                                elif len(cols) >= 4:
                                    wl_str = re.sub(r"[^0-9\.]+", "", cols[3])
                                    bank_str = None
                                else:
                                    wl_str = bank_str = None
                                if wl_str:
                                    results["wl"] = float(wl_str)
                                    level_parsed = True
                                if bank_str:
                                    results["bank"] = float(bank_str)
                            except Exception:
                                pass
                            break
        except Exception:
            pass

    # --- Fetch discharge (C.13 Chao Phraya Dam) ---
    discharge_value = None
    # Attempt 1: use RID JSON endpoint if accessible
    try:
        # This endpoint is used by the hydro data page. We choose StationGroupID '214'
        # which corresponds to the Chao Phraya River stations. TimeCurrent uses mm/dd/yyyy.
        today = datetime.now(pytz.timezone('Asia/Bangkok')).strftime("%m/%d/%Y")
        payload = {"hydro": {"StationGroupID": "214", "TimeCurrent": today}}
        resp = requests.post(
            "https://hyd-app-db.rid.go.th/webservice/getGroupHourlyWaterLevelReportHL.ashx",
            headers={**headers, 'Content-Type': 'application/json'},
            json=payload,
            timeout=30
        )
        # The API may respond with JSON that contains rows and colNames
        if resp.status_code == 200:
            data = resp.json().get('rows', [])
            # Each row in data is a dict of column values; column names may include station code and parameter
            if data:
                # Find the column representing discharge for C.13.
                # Column names may look like 'C.13_Q' or include a Thai word.
                col_names = resp.json().get('colNames', [])
                c13_discharge_col = None
                for name in col_names:
                    # Look for numeric discharge values in the column name
                    # we assume the word 'ปริมาณ' or 'Q' indicates discharge
                    if isinstance(name, str) and ('C.13' in name and ('ปริมาณ' in name or 'Q' in name)):
                        c13_discharge_col = name
                        break
                if c13_discharge_col:
                    # Extract the most recent discharge value from the last row
                    last_row = data[-1]
                    value_str = last_row.get(c13_discharge_col)
                    if value_str:
                        # convert to float after removing commas
                        discharge_value = float(str(value_str).replace(",", ""))
    except Exception:
        pass
    
    # Attempt 2: if API failed, render the hydro page and parse the table
    if discharge_value is None and HTMLSession is not None:
        try:
            session = HTMLSession()
            r = session.get("https://hyd-app-db.rid.go.th/hydro5h.html", headers=headers)
            # Render JavaScript to produce the final table; increase timeout for slower connection
            r.html.render(timeout=60)
            # After rendering, parse the table text
            # Find the table that contains hourly values
            table_el = r.html.find("table", first=True)
            if table_el:
                # Use pandas to read the HTML table if available
                try:
                    import pandas as pd
                    dfs = pd.read_html(table_el.html)
                except Exception:
                    dfs = []
                target = None
                for df in dfs:
                    # Flatten multi-level columns into single strings
                    flat_cols = []
                    for col in df.columns:
                        if isinstance(col, tuple):
                            flat_cols.append(" ".join([str(x) for x in col if str(x) != 'nan']).strip())
                        else:
                            flat_cols.append(str(col).strip())
                    df.columns = flat_cols
                    # Identify the discharge column for C.13
                    for col in df.columns:
                        if 'C.13' in col and (('ปริมาณ' in col) or ('Q' in col)):
                            target = df[col]
                            break
                    if target is not None:
                        break
                if target is not None:
                    # Drop NaNs and pick the last value as the most recent discharge
                    non_na = target.dropna()
                    if not non_na.empty:
                        val = str(non_na.iloc[-1])
                        discharge_value = float(val.replace(",", ""))
            session.close()
        except Exception:
            pass

    if discharge_value is not None:
        results["discharge"] = discharge_value

        # Compute difference from the last recorded discharge
        history_file = os.path.join(os.path.dirname(__file__), 'last_discharge.json')
        try:
            # Read previous record if available
            if os.path.isfile(history_file):
                with open(history_file, 'r', encoding='utf-8') as f:
                    history = json.load(f)
                last_value = history.get('value')
                last_date = history.get('date')
                if isinstance(last_value, (int, float)):
                    results["discharge_diff"] = round(discharge_value - float(last_value), 2)
            # Write current record for next run
            with open(history_file, 'w', encoding='utf-8') as f:
                json.dump({'date': datetime.now(pytz.timezone('Asia/Bangkok')).strftime('%Y-%m-%d'), 'value': discharge_value}, f)
        except Exception:
            # If any error occurs while reading or writing, ignore silently
            pass

    # --- Fetch weather and PM2.5 from Open‑Meteo ---
    try:
        w = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                'latitude': 14.9961,
                'longitude': 100.3253,
                'current': ','.join(['temperature_2m', 'pm2_5']),
                'timezone': 'Asia/Bangkok'
            },
            timeout=20
        ).json()
        results["temp"] = w.get('current', {}).get('temperature_2m', 'N/A')
        results["pm25"] = w.get('current', {}).get('pm2_5', 'N/A')
    except Exception:
        pass

    return results

if __name__ == "__main__":
    d = get_real_data()
    
    # คำนวณความห่างตลิ่ง ใช้ระดับตลิ่งจากข้อมูลหากมี (ค่า bank จาก Thaiwater) มิฉะนั้นใช้ค่ามาตรฐาน 13.10 ม.รทก.
    bank_level = d.get("bank", None)
    if bank_level is None:
        bank_level = 13.10
    # หากระดับน้ำเป็นตัวเลข ให้คำนวณส่วนต่างจากตลิ่ง
    if isinstance(d["wl"], float):
        diff_from_bank = round(bank_level - d["wl"], 2)
        wl_text = f"ความสูง {d['wl']} ม.รทก. (ห่างจากตลิ่ง {diff_from_bank} เมตร)"
    else:
        wl_text = "รออัปเดตข้อมูล ม.รทก."

    # จัดเตรียมข้อความสำหรับโมเดล AI โดยระบุข้อมูลตัวเลขที่ดึงได้
    # If we have a discharge difference, format it for display
    discharge_diff_str = ""
    diff_val = d.get('discharge_diff')
    if isinstance(diff_val, (int, float)):
        # Prefix a plus sign for increases
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
        # If the AI call fails, fallback to raw message
        content = prompt

    # ส่งโพสต์เข้าเพจผ่าน Make webhook
    if MAKE_WEBHOOK_URL:
        try:
            requests.post(MAKE_WEBHOOK_URL, json={"text_to_post": content + "\n\n#อินทร์บุรีรอดมั้ย"})
        except Exception:
            pass
