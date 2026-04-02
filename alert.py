import os
import requests
from datetime import datetime
import pytz
from google import genai

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
MAKE_WEBHOOK_URL = os.environ.get("MAKE_WEBHOOK_URL")
TOMORROW_API_KEY = os.environ.get("TOMORROW_API_KEY")

client = genai.Client(api_key=GEMINI_API_KEY)
tz = pytz.timezone('Asia/Bangkok')
now = datetime.now(tz)
time_str = now.strftime("%H:%M น.")

def check_weather_alert():
    # ดึงข้อมูลพยากรณ์จาก Tomorrow.io
    tmr_url = f"https://api.tomorrow.io/v4/weather/forecast?location=14.9961,100.3253&apikey={TOMORROW_API_KEY}"
    try:
        tmr_res = requests.get(tmr_url).json()
        # เช็กล่วงหน้าแค่ 3 ชั่วโมง
        hourly_data = tmr_res['timelines']['hourly'][:3] 
        rain_probs = [hour['values']['precipitationProbability'] for hour in hourly_data]
        max_rain_prob = max(rain_probs)
        return max_rain_prob
    except Exception as e:
        print(f"เกิดข้อผิดพลาดในการดึงข้อมูล: {e}")
        return 0

if __name__ == "__main__":
    print("=== 🛰️ เริ่มเดินเครื่องเรดาร์ตรวจจับฝนอินทร์บุรี ===")
    
    max_rain_prob = check_weather_alert()
    print(f"โอกาสฝนตกสูงสุดใน 3 ชั่วโมงข้างหน้า: {max_rain_prob}%")
    
    # ตั้งเงื่อนไข: ถ้าโอกาสฝนตกเกิน 80% ให้ส่งแจ้งเตือนแบบเฝ้าระวัง
    if max_rain_prob >= 80:
        prompt = f"""
        คุณคือแอดมินเพจ "อินทร์บุรีรอดมั้ย" 
        ขณะนี้เวลา {time_str}
        ข้อมูลเรดาร์: พบกลุ่มฝนหรือความเสี่ยงฝนตกหนักสูงถึง {max_rain_prob}% ในพื้นที่อินทร์บุรี ภายใน 1-3 ชั่วโมงข้างหน้า
        
        ให้เขียนโพสต์เพื่อ "เฝ้าระวัง" ความยาว 2-3 บรรทัด โดยมีเงื่อนไขดังนี้:
        1. แจ้งให้ลูกเพจเตรียมตัว เช่น เก็บผ้าที่ตากไว้ พกร่ม หรือระวังถนนลื่น
        2. **สำคัญมาก:** ต้องมีประโยคออกตัวทำนองว่า "กลุ่มฝนอาจมีการเปลี่ยนทิศทางตามกระแสลม แต่แอดมินขอมาเตือนให้เตรียมตัวกันไว้ก่อน" เพื่อไม่ให้ชาวบ้านตระหนก และป้องกันการเสียหน้าหากฝนไม่ตกจริง
        3. ภาษาต้องเป็นกันเอง ห่วงใย เหมือนเพื่อนเตือนเพื่อน ห้ามตื่นตระหนกเกินเหตุ และห้ามใช้คำลงท้ายว่า ครับ, ค่ะ, ครับ/ค่ะ แบบหุ่นยนต์เด็ดขาด
        """
        
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        alert_post = "☁️ **เรดาร์จับตาสภาพอากาศ (เตือนล่วงหน้า)** 🌧️\n\n" + response.text.strip() + "\n\n#อินทร์บุรีรอดมั้ย #เฝ้าระวังฝน"
        
        print("\nข้อความเตือนภัย:\n", alert_post)
        
        # ยิงเข้า Make.com ทะลุไป Facebook
        if MAKE_WEBHOOK_URL:
            res = requests.post(MAKE_WEBHOOK_URL, json={"text_to_post": alert_post})
            if res.status_code == 200:
                print("✅ ยิงโพสต์แจ้งเตือนเข้าเพจสำเร็จ!")
    else:
        print("🌤️ สภาพอากาศยังปกติ ไม่มีกลุ่มฝนเสี่ยงสูง ไม่ต้องโพสต์แจ้งเตือนใดๆ")
