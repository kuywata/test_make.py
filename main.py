import requests
from google import genai
from google.genai import types
import os
import json

# ดึงกุญแจความลับ
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") 
MAKE_WEBHOOK_URL = os.environ.get("MAKE_WEBHOOK_URL")

client = genai.Client(api_key=GEMINI_API_KEY)

def get_inburi_weather():
    weather_url = "https://api.open-meteo.com/v1/forecast?latitude=14.9961&longitude=100.3253&current=temperature_2m&timezone=Asia%2FBangkok"
    aqi_url = "https://air-quality-api.open-meteo.com/v1/air-quality?latitude=14.9961&longitude=100.3253&current=pm2_5&timezone=Asia%2FBangkok"
    try:
        temp = requests.get(weather_url).json()['current']['temperature_2m']
        pm25 = requests.get(aqi_url).json()['current']['pm2_5']
        return f"{temp}°C", f"{pm25} μg/m³"
    except:
        return "รออัปเดต", "รออัปเดต"

def get_raw_water_data():
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/123.0.0.0'}
    inburi_raw, c13_raw = "", ""
    try:
        res = requests.get("https://www.thaiwater.net/api/v1/thaiwater30/public/waterlevel", headers=headers, timeout=15)
        if res.status_code == 200:
            data = res.json().get('waterlevel_data', [])
            for item in data:
                code = item.get('station', {}).get('station_old_code', '')
                name = item.get('station', {}).get('station_name', {}).get('th', '')
                if 'C.3' in code or 'อินทร์บุรี' in name:
                    inburi_raw = json.dumps(item, ensure_ascii=False)
                if 'C.13' in code or 'ท้ายเขื่อนเจ้าพระยา' in name:
                    c13_raw = json.dumps(item, ensure_ascii=False)
    except:
        pass
    return inburi_raw, c13_raw

def generate_water_summary(inburi_raw, c13_raw):
    # ปรับเกณฑ์ตลิ่งใน Prompt เป็น 13.10 ม. เพื่อให้ AI คำนวณได้ถูกต้อง
    prompt = f"""
    รายงานสถานการณ์น้ำ อ.อินทร์บุรี วันนี้
    ยึดถือความถูกต้อง 100% จากข้อมูล JSON ด้านล่างนี้:
    - สถานี C.3 (อินทร์บุรี): {inburi_raw if inburi_raw else 'API_ERROR'}
    - สถานี C.13 (เขื่อนเจ้าพระยา): {c13_raw if c13_raw else 'API_ERROR'}

    คำสั่ง:
    1. ถ้า API_ERROR ให้คุณใช้ Google Search ค้นหา "รายงานสถานการณ์น้ำลุ่มเจ้าพระยา วันนี้"
    2. ต้องระบุ: ระดับน้ำปัจจุบัน ม.รทก. ของสถานี C.3 และ "ห่างจากตลิ่งกี่เมตร" โดยใช้เกณฑ์ตลิ่งอินทร์บุรีสูง 13.10 เมตร (คำนวณ: 13.10 - ระดับน้ำปัจจุบัน)
    3. ต้องระบุ: ปริมาณการระบายน้ำของเขื่อนเจ้าพระยา (C.13) วันนี้ และเปรียบเทียบกับเมื่อวาน
    
    รูปแบบที่ต้องแสดง (ห้ามมีคำอื่นปน):
    • ระดับน้ำอินทร์บุรี: ความสูง [เลข] ม.รทก. (ห่างจากตลิ่ง [เลข] เมตร)
    • เขื่อนเจ้าพระยาปล่อยน้ำ: วันนี้ [เลข] ลบ.ม./วินาที (เมื่อวาน [เลข] ลบ.ม./วินาที)
    """
    
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())])
    )
    return response.text

if __name__ == "__main__":
    temp, pm25 = get_inburi_weather()
    inburi_raw, c13_raw = get_raw_water_data()
    water_summary = generate_water_summary(inburi_raw, c13_raw)
    
    final_message = f"📍 **[อัปเดตพื้นที่อินทร์บุรี]**\n"
    final_message += f"• สภาพอากาศ (ตลาดอินทร์บุรี): อุณหภูมิเช้านี้ {temp}\n"
    final_message += f"• คุณภาพอากาศ (PM 2.5): {pm25}\n"
    final_message += f"{water_summary.strip()}\n"
    # ลบข้อความ "อัปเดตอัตโนมัติ by Alieninburi" ออกแล้ว เหลือเพียงแฮชแท็ก
    final_message += f"\n#อินทร์บุรีรอดมั้ย"
    
    requests.post(MAKE_WEBHOOK_URL, json={"text_to_post": final_message})
