import requests
import json

def test_thaiwater_api(station_names):
    # Endpoint หลักแบบ Public ของระบบ ThaiWater v3 
    url = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/waterlevel"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json"
    }
    
    print(f"🔌 กำลังเชื่อมต่อ: {url}")
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        # ดึงข้อมูล JSON
        payload = response.json()
        
        # ข้อมูลสถานีมักจะซ้อนอยู่ใน key 'data' หรือ 'waterlevel_data'
        water_data = payload.get('waterlevel_data', payload.get('data', []))
        
        if not water_data:
            print("⚠️ ดึงข้อมูลสำเร็จ แต่ไม่พบ Array ข้อมูลระดับน้ำใน JSON")
            return
            
        print(f"📦 ดึงข้อมูลสำเร็จ จำนวน {len(water_data)} สถานีทั่วประเทศ\n")
        
        found_data = {name: None for name in station_names}
        all_station_names = [] # เอาไว้ดูเผื่อสะกดไม่ตรงกัน
        
        for item in water_data:
            # โครงสร้าง JSON ของ ThaiWater มักเอาชื่อไปซ่อนไว้ใน dict 'station' 
            station = item.get('station', {})
            
            # พยายามดึงชื่อภาษาไทยออกมา (ดักไว้หลายๆ รูปแบบที่ ThaiWater ชอบใช้)
            name_th = (
                station.get('tele_station_name', {}).get('th', '') or 
                station.get('station_name', {}).get('th', '') or
                item.get('station_name_th', '') or
                station.get('tele_station_name', '')
            )
            
            if not isinstance(name_th, str):
                name_th = str(name_th)
                
            all_station_names.append(name_th)
            
            # ระดับน้ำ
            water_level = item.get('water_level') or item.get('wl_value')
            update_time = item.get('water_level_datetime') or item.get('datetime')
            
            # ค้นหาคำที่ต้องการ
            for target in station_names:
                if target in name_th and found_data[target] is None:
                    found_data[target] = water_level
                    print(f"✅ พบสถานี '{name_th}' (ตรงกับคีย์เวิร์ด: {target})")
                    print(f"   -> ระดับน้ำ: {water_level} เมตร")
                    print(f"   -> เวลาอัปเดต: {update_time}")
                    print(f"   -> โครงสร้างดิบ: {json.dumps(item, ensure_ascii=False)[:200]}...\n")
        
        # สรุปผล
        print("=== สรุปผลการค้นหา ===")
        for target, val in found_data.items():
            if val is not None:
                print(f"🟢 {target}: {val}")
            else:
                print(f"🔴 {target}: ไม่พบข้อมูล")
                
        # ถ้าหาไม่เจอเลย ลองโชว์ชื่อสถานีแถวๆ สิงห์บุรี / ชัยนาท มาดูเป็นตัวอย่าง
        missing = [k for k, v in found_data.items() if v is None]
        if missing:
            print("\n💡 ตัวอย่างรายชื่อสถานีอื่นๆ เผื่อชื่อในระบบเปลี่ยนไป:")
            samples = [name for name in all_station_names if "สิงห์บุรี" in name or "ชัยนาท" in name or "เจ้าพระยา" in name]
            print(samples[:15])

    except Exception as e:
        print(f"❌ เกิดข้อผิดพลาดในการดึง API: {e}")

if __name__ == "__main__":
    # คีย์เวิร์ดที่เราต้องการหา
    targets = ["อินทร์บุรี", "โพนางดำ"]
    test_thaiwater_api(targets)