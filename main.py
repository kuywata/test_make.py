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
    # ใส่หน้ากากหลอกเว็บรัฐบาลไทยว่าเป็นคนใช้เบราว์เซอร์ Chrome เพื่อป้องกันการโดนบล็อก IP
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Referer': 'https://www.thaiwater.net/water/wl'
    }
    inburi_raw = ""
    chaophraya_raw = ""
    
    try:
        print("กำลังเจาะทะลุดึงข้อมูลระดับน้ำ...")
        wl_url = "https://www.thaiwater.net/api/v1/thaiwater30/public/waterlevel"
        wl_res = requests.get(wl_url, headers=headers, timeout=15)
        print(f"สถานะการดึงข้อมูลน้ำ: {wl_res.status_code}") # ถ้าขึ้น 200 คือทะลุสำเร็จ
        
        if wl_res.status_code == 200:
            wl_data = wl_res.json()
            wl_list = wl_data.get('waterlevel_data', wl_data)
            for item in wl_list:
                item_str = json.dumps(item, ensure_ascii=False)
                if 'อินทร์บุรี' in item_str:
                    inburi_raw = item_str
                    break
    except Exception as e:
        print(f"เกิดข้อผิดพลาดการดึงข้อมูลน้ำ: {e}")

    try:
        print("กำลังเจาะทะลุดึงข้อมูลเขื่อน...")
        dam_url = "https://www.thaiwater.net/api/v1/thaiwater30/public/dam"
        dam_res = requests.get(dam_url, headers=headers, timeout=15)
        print(f"สถานะการดึงข้อมูลเขื่อน: {dam_res.status_code}")
        
        if dam_res.status_code == 200:
            dam_data = dam_res.json()
            dam_list = dam_data.get('dam_data', dam_data)
            for item in dam_list:
                item_str = json.dumps(item, ensure_ascii=False)
                if 'เจ้าพระยา' in item_str and 'เขื่อน' in item_str:
                    chaophraya_raw = item_str
                    break
    except Exception as e:
        print(f"เกิดข้อผิดพลาดการดึงข้อมูลเขื่อน: {e}")
        
    return inburi_raw, chaophraya_raw

def generate_local_update(temp, pm25, inburi_raw, chaophraya_raw):
    prompt = f"""
    คุณคือผู้ช่วยรายงานสถานการณ์ท้องถิ่น อ.อินทร์บุรี จ.สิงห์บุรี ที่รายงานตัวเลขแม่นยำที่สุด
    วันนี้อุณหภูมิเช้านี้คือ {temp}°C และ PM 2.5 คือ {pm25} μg/m³ 

    ข้อมูลดิบจาก API คลังข้อมูลน้ำแห่งชาติ:
    - อินทร์บุรี: {inburi_raw if inburi_raw else 'ไม่มีข้อมูลจาก API'}
    - เขื่อนเจ้าพระยา: {chaophraya_raw if chaophraya_raw else 'ไม่มีข้อมูลจาก API'}

    คำสั่งสำคัญ (ทำตามอย่างเคร่งครัด):
    1. หากมี "ข้อมูลดิบจาก API" ให้ดึงตัวเลขจากข้อมูลดิบมาใช้ (หา water_level ของอินทร์บุรี และ dam_released ของเขื่อนเจ้าพระยา)
    2. **สำคัญมาก:** หากข้อมูลดิบขึ้นว่า "ไม่มีข้อมูลจาก API" ให้คุณเปิดใช้เครื่องมือ Google Search ค้นหาข้อมูลล่าสุดของวันนี้เกี่ยวกับ "ระดับน้ำ สถานี C.3 อินทร์บุรี" และ "ปริมาณน้ำระบาย เขื่อนเจ้าพระยา ล่าสุด" จากข่าวเพื่อนำตัวเลขมาเติมให้ครบ ห้ามเว้นว่างเด็ดขาด
    3. ระดับตลิ่งของสถานีอินทร์บุรีคือ 15.10 เมตรเสมอ ให้คุณนำ 15.10 ลบด้วยระดับน้ำปัจจุบัน เพื่อหาค่า "ห่างจากตลิ่ง"

    รูปแบบโพสต์ที่ต้องการ (ห้ามมีคำเกริ่นนำ ห้ามพิมพ์อธิบายเพิ่ม):
    📍 **[อัปเดตพื้นที่อินทร์บุรี]**
    • สภาพอากาศ (ตลาดอินทร์บุรี): อุณหภูมิเช้านี้ {temp}°C
    • คุณภาพอากาศ (PM 2.5): {pm25} μg/m³
    • ระดับน้ำอินทร์บุรี: ความสูง [ระดับน้ำที่หาได้] ม.รทก. (ห่างจากตลิ่ง [ตัวเลขหลังคำนวณ 15.10 - ระดับน้ำ] เมตร)
    • เขื่อนเจ้าพระยาปล่อยน้ำ: วันนี้ระบายที่ [ปริมาณวันนี้] ลบ.ม./วินาที (เมื่อวาน [ปริมาณเมื่อวาน] ลบ.ม./วินาที)
    """
    
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.1 # บังคับ AI ไม่ให้แต่งเรื่องเอง ให้ยึดตัวเลขเป๊ะๆ
        )
    )
    
    return response.text

if __name__ == "__main__":
    print("1. ดึงข้อมูลสภาพอากาศ...")
    temp, pm25 = get_inburi_weather()
    
    print("2. ล้วงข้อมูลตัวเลขระดับน้ำ...")
    inburi_raw, chaophraya_raw = get_raw_water_data()
    
    print("3. กำลังให้ AI จัดเรียงตัวเลข (หากเว็บล่ม AI จะค้น Google แทน)...")
    final_post = generate_local_update(temp, pm25, inburi_raw, chaophraya_raw)
    
    final_message = final_post + "\n\n-----------------\n🤖 อัปเดตอัตโนมัติ by Alieninburi\n#อินทร์บุรีรอดมั้ย"
    
    payload = {"text_to_post": final_message}
    response = requests.post(MAKE_WEBHOOK_URL, json=payload)
    
    if response.status_code == 200:
        print("✅ ส่งโพสต์สำเร็จ!")
    else:
        print("❌ เกิดข้อผิดพลาด:", response.text)
