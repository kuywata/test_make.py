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
    # ล็อกพิกัด "ตลาดอินทร์บุรี" (Lat 14.9961, Lon 100.3253) 
    weather_url = "https://api.open-meteo.com/v1/forecast?latitude=14.9961&longitude=100.3253&current=temperature_2m&timezone=Asia%2FBangkok"
    aqi_url = "https://air-quality-api.open-meteo.com/v1/air-quality?latitude=14.9961&longitude=100.3253&current=pm2_5&timezone=Asia%2FBangkok"
    try:
        temp = requests.get(weather_url).json()['current']['temperature_2m']
        pm25 = requests.get(aqi_url).json()['current']['pm2_5']
        return temp, pm25
    except:
        return "N/A", "N/A"

def get_raw_water_data():
    headers = {'User-Agent': 'Mozilla/5.0'}
    inburi_raw = ""
    chaophraya_raw = ""
    
    # 1. วิ่งเข้าฐานข้อมูลระดับน้ำ Thaiwater 
    try:
        wl_url = "https://www.thaiwater.net/api/v1/thaiwater30/public/waterlevel"
        wl_res = requests.get(wl_url, headers=headers, timeout=10).json()
        wl_list = wl_res.get('waterlevel_data', []) if isinstance(wl_res, dict) else wl_res
        # ค้นหาข้อมูลของ "อินทร์บุรี" เท่านั้น
        for item in wl_list:
            item_str = json.dumps(item, ensure_ascii=False)
            if 'อินทร์บุรี' in item_str:
                inburi_raw = item_str
                break
    except:
        pass

    # 2. วิ่งเข้าฐานข้อมูลเขื่อน Thaiwater
    try:
        dam_url = "https://www.thaiwater.net/api/v1/thaiwater30/public/dam"
        dam_res = requests.get(dam_url, headers=headers, timeout=10).json()
        dam_list = dam_res.get('dam_data', []) if isinstance(dam_res, dict) else dam_res
        # ค้นหาข้อมูลของเขื่อน "เจ้าพระยา" เท่านั้น
        for item in dam_list:
            item_str = json.dumps(item, ensure_ascii=False)
            if 'เจ้าพระยา' in item_str and 'เขื่อน' in item_str:
                chaophraya_raw = item_str
                break
    except:
        pass
        
    return inburi_raw, chaophraya_raw

def generate_local_update(temp, pm25, inburi_raw, chaophraya_raw):
    prompt = f"""
    คุณคือผู้ช่วยรายงานสถานการณ์ท้องถิ่น อ.อินทร์บุรี จ.สิงห์บุรี ที่มีความแม่นยำสูงที่สุด
    วันนี้อุณหภูมิเช้านี้คือ {temp}°C และ PM 2.5 คือ {pm25} μg/m³ (อ้างอิงพื้นที่ตลาดอินทร์บุรี)

    คำสั่ง:
    ด้านล่างนี้คือข้อมูลดึงตรงมาจากหน้าเว็บ 'คลังข้อมูลน้ำแห่งชาติ' แบบสดๆ 
    ให้คุณอ่านข้อมูลนี้แล้วดึงตัวเลขมาใส่ในแบบฟอร์ม:

    1. ข้อมูลระดับน้ำสถานีอินทร์บุรี (JSON 1):
       - หา "ระดับน้ำปัจจุบัน" (water_level)
       - หา "ระดับตลิ่ง" (bank_level)
       - คำนวณความห่าง: (ระดับตลิ่ง - ระดับน้ำปัจจุบัน) = ตัวเลขที่จะเอาไปแสดงผล

    2. ข้อมูลเขื่อนเจ้าพระยา (JSON 2):
       - หา "ปริมาณน้ำระบายวันนี้" (dam_released)
       - *หากในข้อมูลไม่มีตัวเลขเปรียบเทียบของเมื่อวาน ให้คุณค้นหา Google Search ด้วยคำว่า "เขื่อนเจ้าพระยาระบายน้ำล่าสุด เมื่อวาน" เพื่อหาข้อมูลมาเติมให้ครบ

    จัดทำโพสต์สรุป โดยใช้รูปแบบนี้เป๊ะๆ (ห้ามแต่งเลขเองเด็ดขาด ยึดตามฐานข้อมูลดิบที่ให้ไป):

    📍 **[อัปเดตพื้นที่อินทร์บุรี]**
    • สภาพอากาศ (ตลาดอินทร์บุรี): อุณหภูมิเช้านี้ {temp}°C
    • คุณภาพอากาศ (PM 2.5): {pm25} μg/m³
    • ระดับน้ำอินทร์บุรี: ความสูง [ระดับน้ำปัจจุบัน] ม.รทก. (ห่างจากตลิ่ง [ตัวเลขที่คำนวณ] เมตร)
    • เขื่อนเจ้าพระยาปล่อยน้ำ: วันนี้ระบายที่ [ปริมาณวันนี้] ลบ.ม./วินาที (เมื่อวาน [ปริมาณเมื่อวาน] ลบ.ม./วินาที)

    ฐานข้อมูลดิบ (JSON 1 - อินทร์บุรี):
    {inburi_raw if inburi_raw else 'ไม่พบข้อมูล'}

    ฐานข้อมูลดิบ (JSON 2 - เขื่อนเจ้าพระยา):
    {chaophraya_raw if chaophraya_raw else 'ไม่พบข้อมูล'}
    """
    
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            # เปิดระบบ Google Search ให้ AI ไปค้นหาข้อมูลเมื่อวานเพิ่มได้ ถ้าในระบบไม่มี
            tools=[types.Tool(google_search=types.GoogleSearch())]
        )
    )
    
    return response.text

if __name__ == "__main__":
    print("ดึงข้อมูลสภาพอากาศและฝุ่น ตลาดอินทร์บุรี...")
    temp, pm25 = get_inburi_weather()
    
    print("ล้วงข้อมูลตัวเลขระดับน้ำสดๆ จากคลังข้อมูลน้ำแห่งชาติ...")
    inburi_raw, chaophraya_raw = get_raw_water_data()
    
    print("กำลังจัดเรียงตัวเลขลงหน้าเพจ...")
    final_post = generate_local_update(temp, pm25, inburi_raw, chaophraya_raw)
    
    # เติม Tag ปิดท้าย
    final_message = final_post + "\n\n-----------------\n🤖 อัปเดตอัตโนมัติ by Alieninburi\n#อินทร์บุรีรอดมั้ย"
    
    payload = {"text_to_post": final_message}
    response = requests.post(MAKE_WEBHOOK_URL, json=payload)
    
    if response.status_code == 200:
        print("✅ ส่งโพสต์สำเร็จ!")
    else:
        print("❌ เกิดข้อผิดพลาด:", response.text)
