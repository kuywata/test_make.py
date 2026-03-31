import requests
from google import genai
from google.genai import types
import os
import json

# 1. ดึงกุญแจจากตู้เซฟ GitHub 
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") 
MAKE_WEBHOOK_URL = os.environ.get("MAKE_WEBHOOK_URL")

client = genai.Client(api_key=GEMINI_API_KEY)

def get_inburi_weather():
    weather_url = "https://api.open-meteo.com/v1/forecast?latitude=14.9961&longitude=100.3253&current=temperature_2m&timezone=Asia%2FBangkok"
    aqi_url = "https://air-quality-api.open-meteo.com/v1/air-quality?latitude=14.9961&longitude=100.3253&current=pm2_5&timezone=Asia%2FBangkok"
    try:
        temp = requests.get(weather_url).json()['current']['temperature_2m']
        pm25 = requests.get(aqi_url).json()['current']['pm2_5']
        return temp, pm25
    except:
        return "N/A", "N/A"

def get_raw_water_data():
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
    }
    inburi_raw = ""
    chaophraya_raw = ""
    
    try:
        # เจาะเข้าฐานข้อมูลสถานีวัดน้ำ (Waterlevel API)
        wl_url = "https://www.thaiwater.net/api/v1/thaiwater30/public/waterlevel"
        wl_res = requests.get(wl_url, headers=headers, timeout=15)
        if wl_res.status_code == 200:
            wl_list = wl_res.json().get('waterlevel_data', [])
            for item in wl_list:
                item_str = json.dumps(item, ensure_ascii=False)
                
                # หาตัวเลขของ "สถานีอินทร์บุรี"
                if 'สถานีอินทร์บุรี' in item_str or ('อินทร์บุรี' in item_str and 'ระดับน้ำ' in item_str):
                    inburi_raw = item_str
                
                # หาตัวเลขการระบายน้ำของเขื่อนเจ้าพระยา (ซึ่งอยู่ในรหัสสถานี C.13)
                if 'C.13' in item_str or 'ท้ายเขื่อนเจ้าพระยา' in item_str:
                    chaophraya_raw = item_str
    except Exception as e:
        pass

    # หากยังไม่ได้ข้อมูลเขื่อน ค่อยลองไปเจาะหาใน API เขื่อนใหญ่สำรอง
    if not chaophraya_raw:
        try:
            dam_url = "https://www.thaiwater.net/api/v1/thaiwater30/public/dam"
            dam_res = requests.get(dam_url, headers=headers, timeout=15)
            if dam_res.status_code == 200:
                dam_list = dam_res.json().get('dam_data', [])
                for item in dam_list:
                    item_str = json.dumps(item, ensure_ascii=False)
                    if 'เจ้าพระยา' in item_str:
                        chaophraya_raw = item_str
                        break
        except Exception as e:
            pass
            
    return inburi_raw, chaophraya_raw

def generate_local_update(temp, pm25, inburi_raw, chaophraya_raw):
    prompt = f"""
    คุณคือผู้ช่วยรายงานสถานการณ์ท้องถิ่น อ.อินทร์บุรี จ.สิงห์บุรี 
    
    คำสั่งสำคัญ (ทำตามอย่างเคร่งครัดเพื่อความถูกต้อง 100%):
    1. ห้ามเดาหรือแต่งตัวเลขเด็ดขาด! ให้ดึงตัวเลขจาก 'ข้อมูลดิบ JSON' ด้านล่างนี้เท่านั้น
    2. สำหรับระดับน้ำอินทร์บุรี: ให้หาค่าระดับน้ำปัจจุบัน (water_level) จาก JSON 1 
       - ระดับตลิ่งของอินทร์บุรีคือ 15.10 เมตร ให้คำนวณ: 15.10 - ระดับน้ำปัจจุบัน = ค่าห่างจากตลิ่ง
    3. สำหรับเขื่อนเจ้าพระยา: ให้หาค่าการระบายน้ำ (discharge, water_flow, หรือ dam_released) จาก JSON 2
    4. หากใน JSON ไม่มีข้อมูลให้ระบุ หรือคุณหาตัวเลขไม่พบ ให้พิมพ์คำว่า 'รออัปเดตจากกรมชลฯ' ลงไปแทนตัวเลขนั้นทันที 

    รูปแบบโพสต์ที่ต้องการ (พิมพ์ตามนี้เป๊ะๆ ห้ามเพิ่มคำอธิบาย):
    📍 **[อัปเดตพื้นที่อินทร์บุรี]**
    • สภาพอากาศ (ตลาดอินทร์บุรี): อุณหภูมิเช้านี้ {temp}°C
    • คุณภาพอากาศ (PM 2.5): {pm25} μg/m³
    • ระดับน้ำอินทร์บุรี: ความสูง [ระดับน้ำ] ม.รทก. (ห่างจากตลิ่ง [ผลคำนวณ] เมตร)
    • เขื่อนเจ้าพระยาปล่อยน้ำ: [ปริมาณระบายน้ำที่หาได้] ลบ.ม./วินาที

    ข้อมูลดิบ JSON 1 (อินทร์บุรี): {inburi_raw if inburi_raw else 'ไม่มีข้อมูล'}
    ข้อมูลดิบ JSON 2 (เขื่อนเจ้าพระยา / C.13): {chaophraya_raw if chaophraya_raw else 'ไม่มีข้อมูล'}
    """
    
    # ถอดเครื่องมือค้นหา Google ออก และล็อกค่า AI ไม่ให้แต่งเรื่องเอง (temperature=0.0)
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(temperature=0.0)
    )
    
    return response.text

if __name__ == "__main__":
    temp, pm25 = get_inburi_weather()
    inburi_raw, chaophraya_raw = get_raw_water_data()
    final_post = generate_local_update(temp, pm25, inburi_raw, chaophraya_raw)
    
    final_message = final_post + "\n\n-----------------\n🤖 อัปเดตอัตโนมัติ by Alieninburi\n#อินทร์บุรีรอดมั้ย"
    
    payload = {"text_to_post": final_message}
    response = requests.post(MAKE_WEBHOOK_URL, json=payload)
    
    if response.status_code == 200:
        print("✅ ส่งโพสต์สำเร็จ!")
    else:
        print("❌ เกิดข้อผิดพลาด:", response.text)
