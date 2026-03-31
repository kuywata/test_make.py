import requests

# เอา URL ของ Webhook ที่ก๊อปจาก Make.com มาใส่ตรงนี้
WEBHOOK_URL = "https://hook.eu2.make.com/5gteqe4dxdmgmu759o3mfyigslh8vsm4"

data = {
    "text_to_post": "🚨 [ทดสอบระบบจาก GitHub] สวัสดีครับ! นี่คือการทดสอบยิงข้อมูลจาก GitHub Actions วิ่งผ่าน Make.com เพื่อมาลงเพจอินทร์บุรีรอดมั้ยครับ"
}

print("กำลังส่งข้อมูลทดสอบ...")
response = requests.post(WEBHOOK_URL, json=data)

if response.status_code == 200:
    print("✅ ส่งข้อมูลไป Make.com สำเร็จ!")
else:
    print("❌ เกิดข้อผิดพลาด:", response.text)
