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
    
    # ตั้งเงื่อนไข: ถ้าโอกาสฝนตกเกิน 70% ให้ส่งแจ้งเตือน
    if max_rain_prob >= 70:
        prompt = f"""
        คุณคือแอดมินเพจ "อินทร์บุรีรอดมั้ย" 
        ขณะนี้เวลา {time_str}
        ข้อมูลเรดาร์: ตรวจพบโอกาสฝนตกหนักสูงถึง {max_rain_prob}% ในพื้นที่อินทร์บุรี ภายใน 1-3 ชั่วโมงข้างหน้า
        
        ให้เขียนโพสต์เตือนภัยฉุกเฉินสั้นๆ 2-3 บรรทัด แจ้งเตือนชาวบ้านให้เตรียมรับมือ เก็บผ้า พกร่ม ขับขี่ระมัดระวัง 
        ภาษาต้องเป็นกันเอง ดูตื่นตัวแต่ไม่ตื่นตระหนก และห้ามใช้คำลงท้าย หุ่นยนต์เด็ดขาด
        """
        
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        alert_post = "🚨 **แจ้งเตือนสภาพอากาศฉุกเฉิน!** 🚨\n\n" + response.text.strip() + "\n\n#อินทร์บุรีรอดมั้ย #เตือนภัยพายุฝน"
        
        print("\nข้อความเตือนภัย:\n", alert_post)
        
        # ยิงเข้า Make.com ทะลุไป Facebook
        if MAKE_WEBHOOK_URL:
            res = requests.post(MAKE_WEBHOOK_URL, json={"text_to_post": alert_post})
            if res.status_code == 200:
                print("✅ ยิงโพสต์ฉุกเฉินเข้าเพจสำเร็จ!")
    else:
        print("🌤️ สภาพอากาศยังปกติ ไม่มีฝนหนัก ไม่ต้องโพสต์แจ้งเตือนใดๆ")
