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
TMD_API_KEY = os.environ.get("TMD_API_KEY")

client = genai.Client(api_key=GEMINI_API_KEY)

tz = pytz.timezone('Asia/Bangkok')
now = datetime.now(tz)
THAI_MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
               "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]
THAI_DAYS   = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"]
thai_month       = THAI_MONTHS[now.month - 1]
thai_year        = now.year + 543
thai_day_of_week = THAI_DAYS[now.weekday()]   # 0=จันทร์ … 6=อาทิตย์
date_str         = f"{now.day} {thai_month} {thai_year}"
time_str         = now.strftime("%H:%M น.")

INBURI_LAT = 14.9961
INBURI_LON = 100.3253

# ─────────────────────────────────────────────
# State
# ─────────────────────────────────────────────
STATE_FILE = "state.json"

def load_state() -> dict:
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(state: dict):
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ บันทึก state ไม่ได้: {e}")

def build_compare_text(current, previous, unit: str, label: str) -> str:
    if previous is None or current is None:
        return ""
    try:
        delta = round(float(current) - float(previous), 2)
        prev_str = f"{float(previous):.2f}".rstrip('0').rstrip('.')
        if delta > 0:
            return f"⬆️ เพิ่มขึ้น {abs(delta):.2f} {unit} จากเมื่อวาน ({prev_str} {unit})"
        elif delta < 0:
            return f"⬇️ ลดลง {abs(delta):.2f} {unit} จากเมื่อวาน ({prev_str} {unit})"
        else:
            return f"➡️ ทรงตัว เท่ากับเมื่อวาน ({prev_str} {unit})"
    except Exception:
        return ""

# ─────────────────────────────────────────────
# ฟังก์ชันระยะทาง (เดิม)
# ─────────────────────────────────────────────
def get_dist(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(float(lat2) - float(lat1))
    dlon = math.radians(float(lon2) - float(lon1))
    a = (math.sin(dlat/2)**2
         + math.cos(math.radians(float(lat1)))
         * math.cos(math.radians(float(lat2)))
         * math.sin(dlon/2)**2)
    return R * 2 * math.asin(math.sqrt(a))

# ─────────────────────────────────────────────
# TMD ชุดที่ 1: ผลตรวจวัดและพยากรณ์อากาศ (Observation)
# endpoint: https://data.tmd.go.th/api/Weather3Hours/v2/
# ข้อมูลจริงจากสถานีตรวจอากาศทุก 3 ชั่วโมง
# ─────────────────────────────────────────────
def get_tmd_observation() -> dict:
    result = {'available': False}
    if not TMD_API_KEY:
        print("⚠️ TMD_API_KEY ไม่พบ")
        return result
    try:
        # แก้จุดที่ 1: ลบ domain=string ออก
        url = (
            "https://data.tmd.go.th/api/Weather3Hours/v2/"
            f"?APIkey={TMD_API_KEY}&station_type=ตรวจอากาศผิวพื้น"
        )
        # เพิ่ม headers บังคับขอรับข้อมูลเป็น JSON
        headers = {'Accept': 'application/json'}
        res = requests.get(url, headers=headers, timeout=15)
        print(f"TMD Observation HTTP: {res.status_code}")
        if res.status_code != 200:
            return result

        raw = res.text.strip()
        if not raw:
            print("⚠️ TMD Obs: response body ว่างเปล่า (ยังไม่มีข้อมูลในรอบนี้)")
            return result
        if raw.startswith('<'):
            print("⚠️ TMD Obs: ได้ HTML กลับมาแทน JSON")
            return result
        try:
            data = res.json()
        except Exception as je:
            print(f"⚠️ TMD Obs: JSON parse ไม่ได้ ({je})")
            return result

        stations = data.get('Stations', {}).get('Station', [])

        def _parse_cands(max_dist_km):
            cands = []
            for st in stations:
                try:
                    lat  = float(st.get('Latitude', 0))
                    lon  = float(st.get('Longitude', 0))
                    dist = get_dist(INBURI_LAT, INBURI_LON, lat, lon)
                    if dist > max_dist_km:
                        continue
                    obs      = st.get('Observation', {})
                    rain_raw = obs.get('Rainfall', {})
                    rain     = float(rain_raw.get('Value') or 0) if isinstance(rain_raw, dict) else float(rain_raw or 0)
                    temp     = float((obs.get('AirTemperature', {}) or {}).get('Value') or 0)
                    hum      = float((obs.get('RelativeHumidity', {}) or {}).get('Value') or 0)
                    wind     = float((obs.get('WindSpeed', {}) or {}).get('Value') or 0)
                    cands.append({
                        'name': st.get('StationNameThai', st.get('StationNameEng', '?')),
                        'dist': dist, 'rain_3h': rain,
                        'temp': temp, 'humidity': hum, 'wind_speed': wind,
                    })
                except Exception:
                    continue
            return cands

        candidates = _parse_cands(35)
        if not candidates:
            candidates = _parse_cands(80)
            print("⚠️ TMD Obs: ไม่พบสถานีใน 35 กม. ขยายเป็น 80 กม.")
        if not candidates:
            print("⚠️ TMD Obs: ไม่พบสถานีเลย")
            return result

        candidates.sort(key=lambda x: x['dist'])
        top3    = candidates[:3]
        total_w = sum(1.0 / max(c['dist'], 0.1) ** 2 for c in top3)
        w_rain  = sum(c['rain_3h']    / max(c['dist'], 0.1) ** 2 for c in top3) / total_w
        w_temp  = sum(c['temp']       / max(c['dist'], 0.1) ** 2 for c in top3) / total_w
        w_hum   = sum(c['humidity']   / max(c['dist'], 0.1) ** 2 for c in top3) / total_w
        w_wind  = sum(c['wind_speed'] / max(c['dist'], 0.1) ** 2 for c in top3) / total_w
        best    = candidates[0]
        rain_report = max(w_rain, best['rain_3h'])

        result.update({
            'available': True,
            'rain_3h': round(rain_report, 2),
            'temp': round(w_temp, 1),
            'humidity': round(w_hum, 1),
            'wind_speed': round(w_wind, 1),
            'station_name': best['name'],
            'dist_km': round(best['dist'], 1),
        })
        print(f"✅ TMD Obs ({len(top3)} สถานี): {best['name']} {best['dist']:.1f}กม. ฝน={rain_report:.2f}มม.")
    except Exception as e:
        print(f"⚠️ TMD Observation error: {e}")
    return result


# ─────────────────────────────────────────────
# TMD ชุดที่ 2: พยากรณ์จากกรมอุตุฯ (WeatherForecast)
# endpoint: https://data.tmd.go.th/api/WeatherForecast/v2/
# ─────────────────────────────────────────────
def get_tmd_nwp_forecast() -> dict:
    result = {'available': False}
    if not TMD_API_KEY:
        return result

    # แก้จุดที่ 2: ลบ domain=string ออกจากทั้ง 2 endpoint
    endpoints = [
        (
            "https://data.tmd.go.th/api/WeatherForecast/v2/"
            f"?APIkey={TMD_API_KEY}"
            f"&lat={INBURI_LAT}&lon={INBURI_LON}"
        ),
        (
            "https://data.tmd.go.th/api/NowcastForecast/v2/"
            f"?APIkey={TMD_API_KEY}"
            f"&lat={INBURI_LAT}&lon={INBURI_LON}"
            f"&fields=rain,tc,rh,ws,thunderstorm"
        ),
    ]

    data = None
    headers = {'Accept': 'application/json'} # เพิ่ม headers ตรงนี้
    for ep_url in endpoints:
        try:
            res = requests.get(ep_url, headers=headers, timeout=15)
            ep_name = "WeatherForecast" if "WeatherForecast" in ep_url else "NowcastForecast"
            print(f"TMD NWP ({ep_name}) HTTP: {res.status_code}")
            raw = res.text.strip()
            if not raw:
                print(f"⚠️ TMD NWP ({ep_name}): body ว่างเปล่า")
                continue
            if raw.startswith('<'):
                print(f"⚠️ TMD NWP ({ep_name}): ได้ HTML กลับมา (endpoint ไม่รองรับ)")
                continue
            if res.status_code == 200:
                data = res.json()
                print(f"✅ TMD NWP: ใช้ {ep_name}")
                break
        except Exception as ep_e:
            print(f"⚠️ TMD NWP endpoint error: {ep_e}")
            continue

    if data is None:
        print("⚠️ TMD NWP: ทุก endpoint ล้มเหลว")
        return result

    try:
        wf_list   = data.get('WeatherForecasts', [])
        forecasts = (
            (wf_list[0].get('forecasts', []) if isinstance(wf_list, list) and wf_list else [])
            or data.get('forecasts', [])
            or data.get('Forecasts', [])
            or (wf_list if isinstance(wf_list, list) else [])
        )

        next_6h = forecasts[:6]
        if not next_6h:
            print("⚠️ TMD NWP: ไม่พบข้อมูลพยากรณ์ (keys:", list(data.keys()), ")")
            return result

        rain_vals, thunder_vals, wind_vals, temp_vals = [], [], [], []
        for fc in next_6h:
            d = fc.get('data', fc)
            try: rain_vals.append(float(d.get('rain', 0) or 0))
            except: pass
            try: thunder_vals.append(float(d.get('thunderstorm', 0) or 0))
            except: pass
            try: wind_vals.append(float(d.get('ws', 0) or 0))
            except: pass
            try: temp_vals.append(float(d.get('tc', 0) or 0))
            except: pass

        max_rain    = max(rain_vals,    default=0)
        max_thunder = max(thunder_vals, default=0)
        max_wind    = max(wind_vals,    default=0)
        avg_temp    = sum(temp_vals) / len(temp_vals) if temp_vals else 0

        if max_rain >= 35 or (max_rain >= 20 and max_thunder >= 50):
            desc = f"🚨 NWP กรมอุตุฯ: ฝนหนักมาก {max_rain:.1f} มม./ชม. ฟ้าคะนอง {max_thunder:.0f}%"
        elif max_rain >= 10 or (max_rain >= 5 and max_thunder >= 30):
            desc = f"⚠️ NWP กรมอุตุฯ: ฝนหนัก {max_rain:.1f} มม./ชม. ฟ้าคะนอง {max_thunder:.0f}%"
        elif max_rain >= 1:
            desc = f"🌧️ NWP กรมอุตุฯ: มีฝน {max_rain:.1f} มม./ชม. ใน 6 ชม. ข้างหน้า"
        else:
            desc = f"☀️ NWP กรมอุตุฯ: ไม่มีฝนใน 6 ชม. ข้างหน้า"

        result.update({
            'available': True,
            'rain_1h_max': max_rain,
            'thunder_max': max_thunder,
            'wind_max': max_wind,
            'temp_avg': round(avg_temp, 1),
            'description': desc,
        })
        print(f"✅ TMD NWP: ฝน={max_rain:.1f} ฟ้าคะนอง={max_thunder:.0f}% ลม={max_wind:.1f}")
    except Exception as e:
        print(f"⚠️ TMD NWP parse error: {e}")
    return result


# ─────────────────────────────────────────────
# Open-Meteo: ฝนที่ตกจริงย้อนหลัง (backup)
# ─────────────────────────────────────────────
def get_actual_rain_last_hour() -> dict:
    result = {'rain_1h': None, 'rain_3h': None}
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={INBURI_LAT}&longitude={INBURI_LON}"
            f"&hourly=precipitation&past_hours=3&forecast_hours=1"
            f"&timezone=Asia%2FBangkok"
        )
        # แก้จุดที่ 3: เปลี่ยน timeout เป็น 20
        res    = requests.get(url, timeout=20).json()
        times  = res['hourly']['time']
        precip = res['hourly']['precipitation']

        now_str = datetime.now(tz).strftime("%Y-%m-%dT%H:00")
        current_idx = next((i for i, t in enumerate(times) if t == now_str), None)
        if current_idx is None:
            past = [i for i, t in enumerate(times) if t <= now_str]
            current_idx = max(past) if past else 0

        if current_idx >= 1:
            result['rain_1h'] = round(float(precip[current_idx - 1] or 0), 2)
        if current_idx >= 3:
            result['rain_3h'] = round(
                sum(float(p or 0) for p in precip[max(0, current_idx-3):current_idx]), 2
            )
    except Exception as e:
        print(f"⚠️ Open-Meteo actual rain error: {e}")
    return result

# ─────────────────────────────────────────────
# Tomorrow.io: minutely + 6h forecast
# ─────────────────────────────────────────────
def get_rain_storm_forecast() -> dict:
    TOMORROW_API_KEY = os.environ.get("TOMORROW_API_KEY")
    if not TOMORROW_API_KEY:
        return {}
    try:
        res = requests.get(
            f"https://api.tomorrow.io/v4/weather/forecast"
            f"?location={INBURI_LAT},{INBURI_LON}&apikey={TOMORROW_API_KEY}",
            timeout=10)
        if res.status_code != 200:
            return {}
        data = res.json()

        hourly_6h   = data['timelines']['hourly'][:6]
        hourly_24h  = data['timelines']['hourly'][:24]
        minutely_30 = data['timelines'].get('minutely', [])[:30]

        rain_now_prob, rain_now_intensity = 0, 0
        if minutely_30:
            rain_now_prob = max(
                float(m['values'].get('precipitationProbability', 0) or 0) for m in minutely_30)
            rain_now_intensity = max(
                float(m['values'].get('precipitationIntensity', 0) or 0) for m in minutely_30)

        danger_slots, warn_slots, watch_slots, heavy_slots = [], [], [], []
        for h in hourly_6h:
            v         = h.get('values', {})
            rain_prob = float(v.get('precipitationProbability', 0) or 0)
            thunder   = float(v.get('thunderstormProbability',  0) or 0)
            wind_spd  = float(v.get('windSpeed', 0) or 0)
            wind_gust = float(v.get('windGust',  0) or 0)
            intensity = float(v.get('precipitationIntensity', 0) or 0)
            try:
                hour_label = datetime.fromisoformat(
                    h['time'].replace('Z', '+00:00')).astimezone(tz).strftime("%H:%M")
            except Exception:
                hour_label = "?"

            slot     = {'time': hour_label, 'rain_prob': rain_prob, 'thunder': thunder,
                        'wind': wind_spd, 'gust': wind_gust, 'intensity': intensity}
            eff_wind = max(wind_spd, wind_gust * 0.7)

            if   rain_prob >= 80 and (thunder >= 50 or eff_wind >= 14): danger_slots.append(slot)
            elif rain_prob >= 60 and (thunder >= 30 or eff_wind >= 10): warn_slots.append(slot)
            elif rain_prob >= 40 and (thunder >= 20 or eff_wind >= 7):  watch_slots.append(slot)
            elif rain_prob >= 60:                                         heavy_slots.append(slot)

        now_summary = ""
        if rain_now_prob >= 70 or rain_now_intensity >= 2.0:
            now_summary = f"🌧️ ฝนกำลังตกอยู่ขณะนี้ ({rain_now_intensity:.1f} มม./ชม.)"
        elif rain_now_prob >= 40:
            now_summary = f"🌦️ มีโอกาสฝนตกใน 30 นาทีนี้ ({rain_now_prob:.0f}%)"

        max_rain_24h = max(
            (float(h['values'].get('precipitationProbability', 0) or 0) for h in hourly_24h),
            default=0)

        return {
            'danger_slots': danger_slots, 'warn_slots': warn_slots,
            'watch_slots': watch_slots,   'heavy_slots': heavy_slots,
            'now_summary': now_summary,
            'rain_now_prob': rain_now_prob, 'rain_now_intensity': rain_now_intensity,
            'max_rain_24h': max_rain_24h,
        }
    except Exception as e:
        print(f"⚠️ Tomorrow.io error: {e}")
        return {}


# ─────────────────────────────────────────────
# รวมข้อมูลฝนทั้ง 4 แหล่ง → สรุปเดียว
# ─────────────────────────────────────────────
def get_comprehensive_rain_info() -> dict:
    print("กำลังดึงข้อมูลฝน/พายุจาก 4 แหล่ง...")

    tmd_obs = get_tmd_observation()        # แหล่ง 1: ตรวจวัดจริง
    tmd_nwp = get_tmd_nwp_forecast()       # แหล่ง 2: NWP กรมอุตุฯ
    tmr     = get_rain_storm_forecast()    # แหล่ง 3: Tomorrow.io minutely+6h
    actual  = get_actual_rain_last_hour()  # แหล่ง 4: Open-Meteo ย้อนหลัง

    sources_used = []
    if tmd_obs.get('available'):            sources_used.append('TMD สถานี')
    if tmd_nwp.get('available'):            sources_used.append('TMD NWP')
    if tmr:                                 sources_used.append('Tomorrow.io')
    if actual.get('rain_1h') is not None:   sources_used.append('Open-Meteo')

    # ── ฝนจริงที่ตกแล้ว ──────────────────────
    rain_1h     = actual.get('rain_1h', 0) or 0
    rain_3h     = actual.get('rain_3h', 0) or 0
    tmd_rain_3h = tmd_obs.get('rain_3h', 0) or 0

    # TMD สถานีน่าเชื่อถือกว่า Open-Meteo สำหรับฝนจริง
    actual_rain_best = tmd_rain_3h if (tmd_obs.get('available') and tmd_rain_3h > 0) else rain_3h

    actual_text = ""
    if tmd_obs.get('available') and tmd_rain_3h >= 1:
        actual_text = (
            f"🌧️ สถานีอุตุฯ {tmd_obs['station_name']} "
            f"(ห่าง {tmd_obs['dist_km']} กม.): ฝน 3 ชม. = {tmd_rain_3h:.1f} มม."
        )
    elif rain_1h >= 2:
        actual_text = f"🌧️ มีฝนตกในชั่วโมงที่ผ่านมา {rain_1h:.1f} มม."

    # ── NWP กรมอุตุฯ ─────────────────────────
    nwp_text = tmd_nwp.get('description', '') if tmd_nwp.get('available') else ''

    # ── Tomorrow.io ──────────────────────────
    now_text      = tmr.get('now_summary', '') if tmr else ''
    tmr_slot_text = ''
    if tmr:
        if tmr.get('danger_slots'):
            s = tmr['danger_slots'][0]
            times = ", ".join(x['time'] for x in tmr['danger_slots'][:3])
            tmr_slot_text = (f"🚨 Tomorrow.io: เสี่ยงพายุรุนแรงช่วง {times} น. "
                             f"โอกาสฝน {s['rain_prob']:.0f}% ลม {s['wind']:.1f} m/s")
        elif tmr.get('warn_slots'):
            s = tmr['warn_slots'][0]
            times = ", ".join(x['time'] for x in tmr['warn_slots'][:3])
            tmr_slot_text = (f"⚠️ Tomorrow.io: เสี่ยงพายุช่วง {times} น. "
                             f"โอกาสฝน {s['rain_prob']:.0f}%")
        elif tmr.get('watch_slots'):
            s = tmr['watch_slots'][0]
            times = ", ".join(x['time'] for x in tmr['watch_slots'][:3])
            tmr_slot_text = f"👀 Tomorrow.io: เฝ้าระวังช่วง {times} น. โอกาสฝน {s['rain_prob']:.0f}%"

    # ── รวม ──────────────────────────────────
    parts = [p for p in [actual_text, nwp_text, now_text, tmr_slot_text] if p]
    if not parts:
        max_r = tmr.get('max_rain_24h', 0) if tmr else 0
        parts = ([f"อาจมีฝนประปราย โอกาสสูงสุดวันนี้ {max_r:.0f}%"]
                 if max_r >= 30 else
                 ["ท้องฟ้าโปร่งดี ไม่มีพายุในพื้นที่อินทร์บุรี"])

    combined   = " | ".join(parts)
    confidence = f"(ข้อมูลจาก: {', '.join(sources_used)})" if sources_used else ""

    # ── ระดับความเสี่ยงรวม ────────────────────
    nwp_rain    = tmd_nwp.get('rain_1h_max', 0) or 0
    nwp_thunder = tmd_nwp.get('thunder_max', 0) or 0
    has_danger  = (bool(tmr.get('danger_slots')) or nwp_rain >= 35
                   or (nwp_rain >= 20 and nwp_thunder >= 50) or actual_rain_best >= 15)
    has_warn    = (bool(tmr.get('warn_slots'))   or nwp_rain >= 10 or actual_rain_best >= 5)
    has_watch   = (bool(tmr.get('watch_slots'))  or nwp_rain >= 1  or actual_rain_best >= 1)

    risk_level = ('danger' if has_danger else
                  'warn'   if has_warn   else
                  'watch'  if has_watch  else 'none')

    return {
        'summary': combined, 'confidence': confidence,
        'risk_level': risk_level, 'sources': sources_used,
        'tmd_obs': tmd_obs, 'tmd_nwp': tmd_nwp,
        'forecast': tmr,    'actual_rain_1h': rain_1h,
    }


# ─────────────────────────────────────────────
# ฟังก์ชันเดิม ไม่แตะ
# ─────────────────────────────────────────────
def get_hotspots():
    EE_JSON_KEY = os.environ.get("EE_JSON_KEY")
    if not EE_JSON_KEY:
        return "N/A"
    try:
        json_key    = json.loads(EE_JSON_KEY)
        credentials = ee.ServiceAccountCredentials(json_key['client_email'], key_data=EE_JSON_KEY)
        ee.Initialize(credentials)
        inburi_area = ee.Geometry.Point([100.3273, 15.0076]).buffer(12000)
        end_date    = ee.Date(datetime.now(tz))
        start_date  = end_date.advance(-24, 'hour')
        dataset = (ee.ImageCollection("FIRMS")
                   .filterBounds(inburi_area)
                   .filterDate(start_date, end_date))
        if dataset.size().getInfo() == 0:
            return 0
        fires        = dataset.select('T21').max()
        fires_masked = fires.updateMask(fires.gt(0))
        stats = fires_masked.reduceRegion(
            reducer=ee.Reducer.count(), geometry=inburi_area,
            scale=1000, maxPixels=1e9)
        count = stats.get('T21').getInfo()
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
    patterns = ["%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S",
                "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S"]
    try:
        lu        = st.get('LastUpdate', {}) if isinstance(st, dict) else {}
        date_text = str(lu.get('date') or st.get('date') or '').strip()
        time_text = str(lu.get('time') or st.get('time') or '').strip()
        if not date_text or not time_text:
            return None
        raw = f"{date_text} {time_text}"
        dt  = None
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
    total_w, total_v = 0.0, 0.0
    for r in rows:
        w = (1 / ((r['distance'] + 1.0) ** 1.6)
             * max(0.15, 1 - (r['age'] / max(r['max_age'], 1)))
             * (1.0 if r['source'] == 'air4thai' else 0.85))
        total_w += w
        total_v += r['pm25'] * w
    return total_v / total_w if total_w else None


def classify_pm25_th(pm25_value):
    """
    อ้างอิงเกณฑ์ AQI ไทยปี 2566 ของ คพ.
    PM2.5:
      0-15      = ดีมาก
      15.1-25   = ดี
      25.1-37.5 = ปานกลาง/เริ่มกระทบกลุ่มเสี่ยง
      37.6-75   = เริ่มมีผลกระทบต่อสุขภาพ
      >75       = มีผลกระทบต่อสุขภาพมาก
    """
    pm = _safe_float(pm25_value)
    if pm is None:
        return {
            'label': 'ไม่ทราบ',
            'brief': 'เซ็นเซอร์ขัดข้อง',
            'warning': 'ให้แจ้งตามตรงว่าระบบดึงค่าฝุ่นไม่ได้ และเตือนให้ป้องกันตัวไว้ก่อน',
        }

    if pm <= 15:
        return {
            'label': 'ดีมาก',
            'brief': 'อากาศดีมาก',
            'warning': 'พูดได้ว่าอากาศดีมาก',
        }
    if pm <= 25:
        return {
            'label': 'ดี',
            'brief': 'อากาศค่อนข้างดี',
            'warning': 'พูดได้ว่าอากาศดี แต่ไม่ต้องเว่อร์',
        }
    if pm <= 37.5:
        return {
            'label': 'ปานกลาง',
            'brief': 'เริ่มมีฝุ่นและกลุ่มเสี่ยงควรระวัง',
            'warning': 'ห้ามเขียนว่าอากาศดี ให้สื่อว่าเริ่มมีฝุ่น/กลุ่มเสี่ยงควรระวัง',
        }
    if pm <= 75:
        return {
            'label': 'เริ่มมีผลกระทบต่อสุขภาพ',
            'brief': 'ฝุ่นเยอะ มีผลต่อสุขภาพ',
            'warning': 'ห้ามเขียนว่าอากาศดี ให้เตือนเรื่องหน้ากากและลดกิจกรรมกลางแจ้ง',
        }
    return {
        'label': 'มีผลกระทบต่อสุขภาพมาก',
        'brief': 'ฝุ่นหนักมาก อันตราย',
        'warning': 'ห้ามเขียนว่าอากาศดีเด็ดขาด ให้เตือนจริงจังให้อยู่ในอาคารและใส่หน้ากาก',
    }


def build_pm25_instruction(pm25_value, source_label='ระบบคัดกรองฝุ่น'):
    info = classify_pm25_th(pm25_value)
    pm = _safe_float(pm25_value)
    if pm is None:
        return ("เซ็นเซอร์ขัดข้อง ดึงค่าไม่ได้ ให้เขียนบอกลูกเพจไปตามตรงว่าระบบขัดข้อง "
                "แต่ให้เตือนว่าควรใส่หน้ากากอนามัยป้องกันไว้ก่อนเพื่อความปลอดภัย")

    return (
        f"ค่าฝุ่นอยู่ที่ {pm:.1f} µg/m³ จาก {source_label} "
        f"จัดอยู่ระดับ '{info['label']}' ({info['brief']}) "
        f"{info['warning']} และให้ใช้ถ้อยคำสอดคล้องกับระดับนี้เท่านั้น"
    )


def get_accurate_pm25(return_meta=False):
    """
    ดึงค่า PM2.5 โดยกัน under-report ให้มากที่สุดเท่าที่ทำได้จากแหล่งข้อมูลภายนอก
    โดยแตะเฉพาะระบบฝุ่นเท่านั้น

    ลำดับใหม่:
      [1] Air4Thai ≤20 กม. อายุ ≤1 ชม.      (สถานีจริง ใกล้สุด)
      [2] Air4Thai ≤50 กม. อายุ ≤3 ชม.      (restore พฤติกรรมเดิมที่แม่นกว่าโค้ดใหม่)
      [3] WAQI สถานีสิงห์บุรี/ใกล้เคียง      (ground station fallback)
      [4] max(GISTDA, Open-Meteo)            (protective ceiling กันบอกว่าอากาศดีทั้งที่ฝุ่นเริ่มขึ้น)
      [5] OWM                                 (fallback สุดท้าย)
      [6] Air4Thai stale ≤50 กม.             (ดีกว่า N/A)
    """
    PM_LAT, PM_LON = 15.0076, 100.3273  # ใช้พิกัดชุดเก่าที่ผู้ใช้บอกว่าแม่นกว่า เฉพาะ PM เท่านั้น
    STRICT_KM = 20
    WIDE_KM = 50
    STRICT_AGE = 3600
    WIDE_AGE = 10800
    headers = {'User-Agent': 'Mozilla/5.0'}

    air4thai_rows = []
    gistda_value = waqi_value = owm_value = openmeteo_value = None
    selected_source = None

    # ── 1. GISTDA ────────────────────────────────────────
    try:
        res = requests.get(
            f"https://pm25.gistda.or.th/rest/getPM25byLocation"
            f"?lat={PM_LAT}&lng={PM_LON}&t={int(time.time())}",
            headers=headers, timeout=15, verify=False)
        print(f"GISTDA HTTP: {res.status_code}")
        if res.status_code == 200:
            payload = res.json()
            pm = _safe_float((payload.get('data', payload)).get('pm25'))
            print(f"GISTDA PM2.5: {pm}")
            if pm is not None:
                gistda_value = pm
    except Exception as e:
        print(f"⚠️ GISTDA error: {e}")

    # ── 2. Air4Thai (PCD) — รัศมี 50 กม. ────────────────
    try:
        res = requests.get(
            f"http://air4thai.pcd.go.th/services/getNewAQI_JSON.php?t={int(time.time())}",
            headers=headers, timeout=15, verify=False)
        print(f"Air4Thai HTTP: {res.status_code}")
        if res.status_code == 200:
            nearby = []
            for st in res.json().get('stations', []):
                pm25_val = _safe_float(st.get('LastUpdate', {}).get('PM25', {}).get('value'))
                if pm25_val is None:
                    continue
                lat = _safe_float(st.get('lat'))
                lon = _safe_float(st.get('long'))
                if lat is None or lon is None:
                    continue
                dist = get_dist(PM_LAT, PM_LON, lat, lon)
                if dist > WIDE_KM:
                    continue
                age = _parse_air4thai_age_seconds(st)
                if age is None:
                    age = WIDE_AGE + 1
                name = st.get('nameTH') or st.get('nameEN') or '?'
                nearby.append((dist, age, name, pm25_val))
                air4thai_rows.append({
                    'source': 'air4thai',
                    'pm25': pm25_val,
                    'distance': dist,
                    'age': age,
                    'max_age': WIDE_AGE,
                    'station': name,
                })
            nearby.sort()
            print(
                f"Air4Thai สถานีใน {WIDE_KM} กม.: "
                f"{[(f'{d:.1f}km', f'{a//60}m', n, v) for d,a,n,v in nearby[:5]]}"
            )
    except Exception as e:
        print(f"⚠️ Air4Thai error: {e}")

    # ── 3. WAQI — สถานีสิงห์บุรีโดยตรง ──────────────────
    WAQI_TOKEN = os.environ.get("WAQI_TOKEN")
    if WAQI_TOKEN:
        for sid in ["419585", "419584"]:
            try:
                res = requests.get(
                    f"https://api.waqi.info/feed/@{sid}/?token={WAQI_TOKEN}",
                    timeout=15)
                print(f"WAQI @{sid} HTTP: {res.status_code}")
                if res.status_code == 200:
                    d = res.json()
                    if d.get('status') == 'ok':
                        iaqi = d['data'].get('iaqi', {})
                        pm = _safe_float((iaqi.get('pm25') or {}).get('v'))
                        sname = d['data'].get('city', {}).get('name', sid)
                        print(f"WAQI @{sid} ({sname}) PM2.5: {pm}")
                        if pm is not None:
                            waqi_value = pm
                            break
            except Exception as e:
                print(f"⚠️ WAQI @{sid} error: {e}")

        if waqi_value is None:
            try:
                res = requests.get(
                    f"https://api.waqi.info/feed/geo:{PM_LAT};{PM_LON}/?token={WAQI_TOKEN}",
                    timeout=15)
                print(f"WAQI geo HTTP: {res.status_code}")
                if res.status_code == 200:
                    d = res.json()
                    if d.get('status') == 'ok':
                        geo = d['data'].get('city', {}).get('geo', [])
                        sname = d['data'].get('city', {}).get('name', '?')
                        if len(geo) == 2:
                            dist = get_dist(PM_LAT, PM_LON, float(geo[0]), float(geo[1]))
                            print(f"WAQI geo: {sname} ห่าง {dist:.1f} กม.")
                            if dist <= 60:
                                iaqi = d['data'].get('iaqi', {})
                                pm = _safe_float((iaqi.get('pm25') or {}).get('v'))
                                print(f"WAQI geo PM2.5: {pm}")
                                if pm is not None:
                                    waqi_value = pm
                            else:
                                print(f"⚠️ WAQI geo: ไกลเกิน ({dist:.1f} กม.) ข้าม")
            except Exception as e:
                print(f"⚠️ WAQI geo error: {e}")
    else:
        print("⚠️ WAQI_TOKEN ไม่พบ")

    # ── 4. OpenWeatherMap Air Pollution API ───────────────
    OWM_API_KEY = os.environ.get("OWM_API_KEY")
    if OWM_API_KEY:
        try:
            res = requests.get(
                f"http://api.openweathermap.org/data/2.5/air_pollution"
                f"?lat={PM_LAT}&lon={PM_LON}&appid={OWM_API_KEY}",
                timeout=15)
            print(f"OWM HTTP: {res.status_code}")
            if res.status_code == 200:
                comp = res.json()['list'][0]['components']
                pm = _safe_float(comp.get('pm2_5'))
                print(f"OWM PM2.5: {pm}")
                if pm is not None:
                    owm_value = pm
        except Exception as e:
            print(f"⚠️ OWM error: {e}")
    else:
        print("⚠️ OWM_API_KEY ไม่พบ")

    # ── 5. Open-Meteo ─────────────────────────────────────
    try:
        res = requests.get(
            f"https://air-quality-api.open-meteo.com/v1/air-quality"
            f"?latitude={PM_LAT}&longitude={PM_LON}"
            f"&current=pm2_5&timezone=Asia%2FBangkok",
            headers=headers, timeout=20)
        res.raise_for_status()
        data = res.json()
        if 'current' in data:
            pm = _safe_float(data['current'].get('pm2_5'))
            print(f"Open-Meteo PM2.5: {pm}")
            if pm is not None:
                openmeteo_value = pm
    except Exception as e:
        print(f"⚠️ Open-Meteo error: {e}")

    # ── Decision Logic ────────────────────────────────────

    # [1] Air4Thai ใกล้มากและสดมาก
    strict = [r for r in air4thai_rows if r['distance'] <= STRICT_KM and r['age'] <= STRICT_AGE]
    if strict:
        strict.sort(key=lambda x: (x['distance'], x['age']))
        pm = _weighted_pm25(strict[:3])
        if pm is not None:
            selected_source = "Air4Thai ใกล้สุด ≤20 กม./≤1 ชม."
            print(f"✅ ใช้ {selected_source}: {pm:.1f}")
            return ({'pm25': f"{pm:.1f}", 'source': selected_source} if return_meta else f"{pm:.1f}")

    # [2] Air4Thai กว้างขึ้นแต่ยังสดพอ (restore แบบโค้ดเก่า)
    wide = [r for r in air4thai_rows if r['distance'] <= WIDE_KM and r['age'] <= WIDE_AGE]
    if wide:
        wide.sort(key=lambda x: (x['distance'], x['age']))
        pm = _weighted_pm25(wide[:3])
        if pm is not None:
            selected_source = "Air4Thai รอบกว้าง ≤50 กม./≤3 ชม."
            print(f"✅ ใช้ {selected_source}: {pm:.1f}")
            return ({'pm25': f"{pm:.1f}", 'source': selected_source} if return_meta else f"{pm:.1f}")

    # [3] WAQI เป็นสถานีภาคพื้น ให้มาก่อนดาวเทียม
    if waqi_value is not None:
        selected_source = "WAQI สถานีใกล้เคียง"
        print(f"✅ ใช้ {selected_source}: {waqi_value:.1f}")
        return ({'pm25': f"{waqi_value:.1f}", 'source': selected_source} if return_meta else f"{waqi_value:.1f}")

    # [4] ไม่มีสถานีภาคพื้น → ใช้ protective ceiling กัน false-good
    ceiling_candidates = [v for v in [gistda_value, openmeteo_value] if v is not None]
    if ceiling_candidates:
        pm = max(ceiling_candidates)
        selected_source = "เพดานคัดกรอง GISTDA/Open-Meteo"
        print(f"✅ ใช้ {selected_source}: {pm:.1f} จาก {ceiling_candidates}")
        return ({'pm25': f"{pm:.1f}", 'source': selected_source} if return_meta else f"{pm:.1f}")

    # [5] OWM fallback
    if owm_value is not None:
        selected_source = "OpenWeatherMap fallback"
        print(f"✅ ใช้ {selected_source}: {owm_value:.1f}")
        return ({'pm25': f"{owm_value:.1f}", 'source': selected_source} if return_meta else f"{owm_value:.1f}")

    # [6] Air4Thai stale ยังดีกว่า N/A
    stale = [r for r in air4thai_rows if r['distance'] <= WIDE_KM]
    if stale:
        stale.sort(key=lambda x: (x['age'], x['distance']))
        pm = _weighted_pm25(stale[:3])
        if pm is not None:
            selected_source = "Air4Thai stale ≤50 กม."
            print(f"⚠️ ใช้ {selected_source}: {pm:.1f}")
            return ({'pm25': f"{pm:.1f}", 'source': selected_source} if return_meta else f"{pm:.1f}")

    print("❌ ทุกแหล่งล้มเหลว → N/A")
    return ({'pm25': 'N/A', 'source': 'ทุกแหล่งล้มเหลว'} if return_meta else "N/A")

def get_weather():
    TOMORROW_API_KEY = os.environ.get("TOMORROW_API_KEY")
    temp, pm25, rain_prob, humidity, wind, uv = "N/A", "N/A", "N/A", "N/A", "N/A", "N/A"
    if TOMORROW_API_KEY:
        try:
            res = requests.get(
                f"https://api.tomorrow.io/v4/weather/forecast"
                f"?location={INBURI_LAT},{INBURI_LON}&apikey={TOMORROW_API_KEY}",
                timeout=10)
            if res.status_code == 200:
                tmr_res      = res.json()
                current_data = tmr_res['timelines']['minutely'][0]['values']
                humidity     = round(current_data['humidity'], 1)
                wind         = round(current_data['windSpeed'], 1)
                rain_prob    = max(h['values']['precipitationProbability']
                                   for h in tmr_res['timelines']['hourly'][:12])
        except: pass
    try:
        res  = requests.get(
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={INBURI_LAT}&longitude={INBURI_LON}"
            f"&current=temperature_2m,uv_index&timezone=Asia%2FBangkok",
            timeout=10).json()
        temp = res['current']['temperature_2m']
        uv   = res['current'].get('uv_index', 'N/A')
    except: pass
    return temp, pm25, rain_prob, humidity, wind, uv

def get_inburi_data():
    url = f"https://singburi.thaiwater.net/wl?cb={random.randint(10000, 99999)}"
    water_level, bank_level = None, 13.10
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page    = browser.new_page()
        try:
            page.goto(url, timeout=60000)
            page.wait_for_selector("th[scope='row']", timeout=30000)
            soup = BeautifulSoup(page.content(), "html.parser")
            for th in soup.select("th[scope='row']"):
                if "อินทร์บุรี" in th.get_text(strip=True):
                    cols = th.find_parent("tr").find_all("td")
                    nums = []
                    for td in cols:
                        try:
                            c = re.sub(r"[^0-9.\-]", "",
                                       re.sub(r"[ ,]", "", td.get_text(strip=True)))
                            if c and c != "-": nums.append(float(c))
                        except: continue
                    if nums:
                        water_level = nums[0]
                        break
        except Exception as e:
            print(f"เกิดข้อผิดพลาดในการดึงข้อมูลสิงห์บุรี: {e}")
        finally:
            browser.close()
    return water_level, bank_level

def fetch_chao_phraya_dam_discharge():
    try:
        res = requests.get(
            f"https://tiwrm.hii.or.th/DATA/REPORT/php/chart/chaopraya/small/chaopraya.php"
            f"?cb={random.randint(10000, 99999)}",
            timeout=20)
        match = re.search(r'var json_data = (\[.*\]);', res.text)
        if match:
            val = json.loads(match.group(1))[0]['itc_water']['C13']['storage']
            return float(val) if isinstance(val, (int, float)) else float(str(val).replace(',', ''))
    except Exception as e:
        print(f"⚠️ เขื่อน error: {e}")
    return None


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=== เริ่มรวบรวมข้อมูลอินทร์บุรี + พลังดาวเทียม ===")

    state          = load_state()
    prev_wl        = state.get("last_water_level")
    prev_discharge = state.get("last_discharge")

    temp, _, rain_prob, humidity, wind, uv = get_weather()
    pm25_meta       = get_accurate_pm25(return_meta=True)
    pm25            = pm25_meta['pm25']
    pm25_instruction = build_pm25_instruction(pm25, pm25_meta.get('source', 'ระบบคัดกรองฝุ่น'))
    wl, bank_level = get_inburi_data()
    discharge      = fetch_chao_phraya_dam_discharge()
    hotspots       = get_hotspots()

    wl_compare_text        = build_compare_text(wl,       prev_wl,       "ม.", "ระดับน้ำ")
    discharge_compare_text = build_compare_text(discharge, prev_discharge, "ลบ.ม./วินาที", "การระบาย")

    new_state = dict(state)
    if wl is not None:        new_state["last_water_level"] = float(wl)
    if discharge is not None: new_state["last_discharge"]   = float(discharge)
    save_state(new_state)

    if wl is not None:
        diff    = round(bank_level - wl, 2)
        wl_text = (f"ระดับน้ำ {wl} เมตร (⚠️ เกินตลิ่ง {abs(diff)} เมตร)" if diff < 0
                   else f"ระดับน้ำ {wl} เมตร (ห่างจากตลิ่ง {diff} เมตร)")
    else:
        wl_text = "รออัปเดต"

    discharge_text = f"{discharge} ลบ.ม./วินาที" if discharge is not None else "รออัปเดต"

    if hotspots == "N/A":   hotspot_text = "ระบบตรวจจับขัดข้องชั่วคราว"
    elif hotspots == 0:     hotspot_text = "ไม่พบจุดเผาในพื้นที่ ปลอดภัยดี"
    else:                   hotspot_text = f"ตรวจพบ {hotspots} จุด (เฝ้าระวังควันจากการเผาไร่/นา)"

    # ── เลือกหัวข้อเฝ้าระวัง ─────────────────
    if hotspots not in (0, "N/A") and hotspots:
        watch_icon, watch_title = "🔥", "เฝ้าระวังความร้อน"
        watch_data = hotspot_text
        watch_rule = ("สรุปเรื่องจุดเผาไร่นาตามข้อมูล "
                      "ห้ามพูดว่าไฟป่าเด็ดขาด บอกเป็น 'ควันจากการเผาไร่/นา'")
    else:
        watch_icon, watch_title = "🌧️", "เฝ้าระวังฝนและพายุ"

        rain_info  = get_comprehensive_rain_info()   # ← รวม 4 แหล่ง
        watch_data = rain_info['summary']
        if rain_info.get('confidence'):
            watch_data += f"\n    {rain_info['confidence']}"

        risk = rain_info.get('risk_level', 'none')
        if risk == 'danger':
            watch_rule = ("มีความเสี่ยงพายุรุนแรงจากหลายแหล่งข้อมูล ให้เตือนชัดเจนจริงจัง "
                          "แนะนำให้ระวังตัวและอยู่ในที่ปลอดภัย")
        elif risk == 'warn':
            watch_rule = ("มีความเสี่ยงพายุ ให้เตือนอย่างจริงจัง "
                          "ไม่ตื่นตระหนกแต่ต้องระมัดระวัง")
        else:
            watch_rule = ("สรุปพยากรณ์ฝน/พายุตรงไปตรงมา "
                          "ถ้ามีความเสี่ยงให้เตือน ถ้าไม่มีให้บอกว่าสบายใจได้ "
                          "ห้ามกุเรื่องหรือเพิ่มความรุนแรงเกินจริง")

    wl_full_text = (wl_text
                    + (f"\n    (เทียบเมื่อวาน: {wl_compare_text})" if wl_compare_text else ""))
    discharge_full_text = (discharge_text
                           + (f"\n    (เทียบเมื่อวาน: {discharge_compare_text})"
                              if discharge_compare_text else ""))

    prompt = f"""
    คุณคือแอดมินเพจ "อินทร์บุรีรอดมั้ย" อัปเดตข่าวสารให้ชาวบ้านแบบเป็นกันเอง
    ข้อมูลดิบ: วัน{thai_day_of_week}ที่ {date_str} เวลา {time_str}
    - อากาศ: {temp}°C, แดด(UV): {uv}, ฝน: {rain_prob}%, ลม: {wind} m/s
    - ฝุ่น PM 2.5: {pm25_instruction}
    - {watch_title}: {watch_data}
    - ระดับน้ำ: {wl_full_text}
    - ระบายเขื่อน: {discharge_full_text}

    กฎการเขียน:
    1. {watch_title}: {watch_rule}
    2. ภาษา: ใช้ภาษาพูดง่ายๆ ตัดศัพท์วิชาการทิ้ง (เช่น ม.รทก. → 'เมตร')
    3. ความไม่จำเจ: ทักทายตามวัน{thai_day_of_week}จริงๆ ห้ามเดาวันเอง ใช้แค่ข้อมูลที่ให้มา
    4. ห้ามใช้คำลงท้าย "ครับ/ค่ะ"
    5. ระดับน้ำและเขื่อน: พูดแนวโน้มแบบธรรมชาติ ไม่ต้องพูดตัวเลขซ้ำ

    โครงสร้างโพสต์:
    **สถานการณ์อินทร์บุรี** (ข้อมูล ณ วัน{thai_day_of_week}ที่ {date_str} เวลา {time_str})

    🌡️ **สภาพอากาศและฝุ่น:** [สรุปอากาศ+ความรู้สึกเรื่องฝุ่น]
    {watch_icon} **{watch_title}:** [สรุปตาม {watch_rule}]
    🌊 **ระดับน้ำอินทร์บุรี:** [บอกระดับน้ำเป็นเมตร พร้อมแนวโน้มเทียบเมื่อวานถ้ามี]
    🛑 **ระบายน้ำเขื่อนเจ้าพระยา:** [สรุปการระบายน้ำ พร้อมแนวโน้มถ้ามี]

    📌 **สรุป:** [ทักทายตามวัน{thai_day_of_week} 1-2 บรรทัด]
    """

    max_retries = 5
    final_post  = ""
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
                wait = 10 * (2 ** attempt)
                print(f"รอ {wait} วินาที...")
                time.sleep(wait)
            else:
                final_post = "**สถานการณ์อินทร์บุรี**... (ระบบ AI ขัดข้องชั่วคราว)"

    print("\nข้อความที่จะโพสต์:\n", final_post)

    if MAKE_WEBHOOK_URL and final_post and "ขัดข้องชั่วคราว" not in final_post:
        res = requests.post(MAKE_WEBHOOK_URL, json={"text_to_post": final_post})
        if res.status_code == 200:
            print("\n✅ ส่ง Webhook สำเร็จ!")
