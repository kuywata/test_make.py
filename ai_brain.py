"""
ai_brain.py — สมองของเพจ "อินทร์บุรีรอดมั้ย"
- ค้นหารายชื่อโมเดลที่เปิดใช้งานจริงจาก Groq / OpenRouter อัตโนมัติ (แก้ปัญหา 404 model_not_found)
- ปรับเปลี่ยนถ้อยคำสรุปตามแนวโน้มน้ำจริง (ขาขึ้น/ขาลง) เพื่อไม่ให้ชาวบ้านตื่นตระหนก
"""
import os
import re
import csv
import json
import time
import random
import requests
from datetime import datetime, timedelta

import openpyxl
from google.genai import types

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
MODELS = [m.strip() for m in os.environ.get("GEMINI_MODELS", f"{MODEL},gemini-3.5-flash-lite").split(",") if m.strip()]
ENABLE_RESEARCH = os.environ.get("ENABLE_RESEARCH", "0") == "1"

# ───────── ค่าที่ปรับได้ ─────────
BANK_LEVEL = {"อินทร์บุรี": float(os.environ.get("BANK_LEVEL_INBURI", "13.00")),
              "โพนางดำ": float(os.environ.get("BANK_LEVEL_PHONANGDAM", "13.87"))}
GAP_HIGH = 0.50
GAP_WATCH = 1.50
RISE_FAST_PER_DAY = 0.15
DISCHARGE_JUMP = 150

LEVELS = ["normal", "watch", "high", "overbank"]
LEVEL_INFO = {
    "normal":   {"label": "ปกติ",
                 "tone": "พูดผ่อนคลายได้ แต่ยังต้องบอกระยะห่างตลิ่งจริงและแนวโน้ม"},
    "watch":    {"label": "เฝ้าระวัง",
                 "tone": "บอกตรงๆ ด้วยน้ำเสียงเป็นกันเอง ไม่ทำให้ชาวบ้านตกใจ แนะนำให้ติดตามระดับน้ำต่อเนื่อง ห้ามใช้คำปลอบใจเกินจริง"},
    "high":     {"label": "เฝ้าระวังใกล้ชิด/เสี่ยงสูง",
                 "tone": "จริงจังแต่ไม่ตื่นตระหนก ให้เตรียมดูแลของในที่ลุ่มต่ำ และติดตามประกาศทางการ ห้ามใช้คำปลอบใจ"},
    "overbank": {"label": "ล้นตลิ่ง",
                 "tone": "เตือนชัดเจนและจริงจัง บอกสิ่งที่ต้องทำทันที ห้ามใช้คำปลอบใจ"},
}

BANNED_REASSURE = ["สบายใจ", "วางใจ", "ไม่ต้องห่วง", "ไม่ต้องกังวล", "ไม่น่ากังวล",
                   "ปกติดี", "ปลอดภัยดี", "หายห่วง", "ไม่มีอะไรน่าห่วง", "รอดแน่", "ชิลๆ"]
NEG_OK = re.compile(r"(อย่า(เพิ่ง)?|ไม่ควร|ยัง)(วางใจ|สบายใจ)|(สบายใจ|วางใจ)ไม่ได้")


# ═════════════════════════════════════════════
# วันที่แบบไทย: วัน เดือน ปี(พ.ศ.)
# ═════════════════════════════════════════════
TH_MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
             "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]
_ISO_DT = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::\d{2})?)?(?!\d)")


def th_date_text(text):
    def _f(m):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (1 <= mo <= 12) or y < 1900 or y > 2400:
            return m.group(0)
        out = f"{d} {TH_MONTHS[mo-1]} {y+543}"
        if m.group(4):
            out += f" เวลา {m.group(4)}:{m.group(5)} น."
        return out
    return _ISO_DT.sub(_f, text)


def thai_dates(obj):
    if isinstance(obj, str):
        return th_date_text(obj)
    if isinstance(obj, dict):
        return {k: thai_dates(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [thai_dates(v) for v in obj]
    return obj


# ═════════════════════════════════════════════
# 1) ประวัติ + บริบทน้ำ
# ═════════════════════════════════════════════
def _parse_dt(raw):
    if raw is None:
        return None
    dt = None
    if isinstance(raw, datetime):
        dt = raw
    else:
        rd = str(raw).strip()
        for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                dt = datetime.strptime(rd, fmt)
                break
            except Exception:
                pass
    if dt is None:
        return None
    if dt.year > 2400:
        dt = dt.replace(year=dt.year - 543)
    return dt.replace(tzinfo=None)


def _to_float(v):
    try:
        s = str(v).split("/")[0].replace(",", "").strip()
        return None if s in ("", "-", "None", "nan") else float(s)
    except Exception:
        return None


def load_series(xlsx_files=("ข้อมูลน้ำอินทร์บุรี2568.xlsx", "โพนางดำ.xlsx"),
                csv_file="history_water.csv"):
    pool = {"อินทร์บุรี": {}, "โพนางดำ": {}}

    for fp in xlsx_files:
        if not os.path.exists(fp):
            continue
        st = "อินทร์บุรี" if "อินทร์" in fp else "โพนางดำ"
        try:
            wb = openpyxl.load_workbook(fp, data_only=True, read_only=True)
        except Exception as e:
            print(f"⚠️ เปิด {fp} ไม่ได้: {e}")
            continue
        for sh in wb.worksheets:
            rows = sh.iter_rows(values_only=True)
            try:
                headers = next(rows)
            except StopIteration:
                continue
            di = wi = pi = None
            for i, h in enumerate(headers):
                if not h:
                    continue
                nh = str(h).replace(" ", "").replace("\n", "")
                if ("วันที่" in nh or "วัน" in nh) and di is None:
                    di = i
                elif nh.startswith("ระดับน้ำ") and wi is None:
                    wi = i
                elif ("ปริมาณน้ำปล่อย" in nh or "เขื่อน" in nh) and pi is None:
                    pi = i
            if di is None or wi is None:
                continue
            for row in rows:
                if len(row) <= max(di, wi):
                    continue
                dt = _parse_dt(row[di])
                wl = _to_float(row[wi])
                if dt is None or wl is None:
                    continue
                dis = _to_float(row[pi]) if pi is not None and len(row) > pi else None
                pool[st][dt] = {"dt": dt, "wl": wl, "dis": dis}

    if os.path.exists(csv_file):
        try:
            with open(csv_file, encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    st = (r.get("Station") or "").strip()
                    if st not in pool:
                        continue
                    dt = _parse_dt(f"{r.get('Date','').strip()} {r.get('Time','').strip()}") \
                        or _parse_dt(r.get("Date", "").strip())
                    wl = _to_float(r.get("WaterLevel"))
                    if dt is None or wl is None:
                        continue
                    pool[st][dt] = {"dt": dt, "wl": wl, "dis": _to_float(r.get("Discharge"))}
        except Exception as e:
            print(f"⚠️ อ่าน {csv_file} ไม่ได้: {e}")

    return {st: sorted(d.values(), key=lambda x: x["dt"]) for st, d in pool.items()}


def _nearest(rows, target, max_hours):
    best, bd = None, None
    for r in rows:
        d = abs((r["dt"] - target).total_seconds())
        if bd is None or d < bd:
            best, bd = r, d
    return best if best is not None and bd <= max_hours * 3600 else None


def _fmt_dt(dt):
    return dt.strftime("%Y-%m-%d %H:%M")


def build_water_context(now, wl, discharge, series, bank=BANK_LEVEL["อินทร์บุรี"], pho_wl=None):
    now = now.replace(tzinfo=None)
    ins, pho = series.get("อินทร์บุรี", []), series.get("โพนางดำ", [])
    ctx = {
        "wl": wl, "bank_level": bank,
        "gap_to_bank": round(bank - wl, 2) if wl is not None else None,
        "discharge": discharge,
    }

    this_year = [r for r in ins if r["dt"].year == now.year and r["dt"] < now - timedelta(minutes=30)]
    if wl is not None and this_year:
        for key, hrs, tol in (("24h", 24, 8), ("3d", 72, 12), ("7d", 168, 24)):
            ref = _nearest(this_year, now - timedelta(hours=hrs), tol)
            if ref:
                ctx[f"change_{key}"] = round(wl - ref["wl"], 2)
                ctx[f"ref_{key}_time"] = _fmt_dt(ref["dt"])
                if key == "24h":
                    ctx["wl_24h_ago"] = ref["wl"]
                    if discharge is not None and ref.get("dis") is not None:
                        ctx["discharge_change_24h"] = round(discharge - ref["dis"], 1)
        if ctx.get("change_3d") is not None:
            ctx["rate_per_day_3d"] = round(ctx["change_3d"] / 3, 3)
        pk = max(this_year, key=lambda r: r["wl"])
        ctx["this_year_peak_in_data"] = {"wl": pk["wl"], "date": _fmt_dt(pk["dt"])}

    try:
        ly_target = now.replace(year=now.year - 1)
    except ValueError:
        ly_target = now.replace(year=now.year - 1, day=28)
    ly_rows = [r for r in ins if r["dt"].year == ly_target.year]
    near = _nearest(ly_rows, ly_target, 36)
    if near:
        ly = {"date": _fmt_dt(near["dt"]), "wl": near["wl"],
              "gap_to_bank": round(bank - near["wl"], 2), "discharge": near["dis"]}
        if wl is not None:
            ly["this_year_minus_last_year"] = round(wl - near["wl"], 2)
        b3 = _nearest(ly_rows, ly_target - timedelta(days=3), 24)
        if b3:
            ly["change_in_3d_before"] = round(near["wl"] - b3["wl"], 2)
        after = [r for r in ly_rows if ly_target <= r["dt"] <= ly_target + timedelta(days=30)]
        if after:
            pk = max(after, key=lambda r: r["wl"])
            ly["next_30d_peak"] = {"wl": pk["wl"], "date": _fmt_dt(pk["dt"]),
                                   "gap_to_bank": round(bank - pk["wl"], 2)}
        ctx["last_year"] = ly

    pho_ly = [r for r in pho if r["dt"].year == ly_target.year]
    pn = _nearest(pho_ly, ly_target, 36)
    if pn:
        b = BANK_LEVEL["โพนางดำ"]
        ctx["phonangdam_last_year"] = {"date": _fmt_dt(pn["dt"]), "wl": pn["wl"],
                                       "gap_to_bank": round(b - pn["wl"], 2),
                                       "note": "เป็นข้อมูลปีที่แล้วเท่านั้น ไม่มีค่าปัจจุบัน"}

    if pho_wl is not None:
        pb = BANK_LEVEL["โพนางดำ"]
        pnow = {"wl": pho_wl, "bank_level": pb, "gap_to_bank": round(pb - pho_wl, 2)}
        pho_this = [r for r in pho if r["dt"].year == now.year and r["dt"] < now - timedelta(minutes=30)]
        for key, hrs, tol in (("24h", 24, 8), ("3d", 72, 12), ("7d", 168, 24)):
            ref = _nearest(pho_this, now - timedelta(hours=hrs), tol)
            if ref:
                pnow[f"change_{key}"] = round(pho_wl - ref["wl"], 2)
        pl = _nearest(pho_ly, ly_target, 36)
        if pl:
            pnow["vs_last_year"] = {"date": _fmt_dt(pl["dt"]), "wl": pl["wl"],
                                    "gap_to_bank": round(pb - pl["wl"], 2),
                                    "this_year_minus_last_year": round(pho_wl - pl["wl"], 2)}
            aft = [r for r in pho_ly if ly_target <= r["dt"] <= ly_target + timedelta(days=30)]
            if aft:
                pk2 = max(aft, key=lambda r: r["wl"])
                pnow["vs_last_year"]["next_30d_peak"] = {"wl": pk2["wl"], "date": _fmt_dt(pk2["dt"]),
                                                          "gap_to_bank": round(pb - pk2["wl"], 2)}
        ctx["phonangdam_now"] = pnow
    return ctx


# ═════════════════════════════════════════════
# 2) ระดับความเสี่ยง (กฎแข็ง)
# ═════════════════════════════════════════════
def assess_water_risk(ctx):
    wl, gap = ctx.get("wl"), ctx.get("gap_to_bank")
    if wl is None or gap is None:
        return {"level": "watch", "idx": 1, "reasons": ["ไม่มีค่าระดับน้ำปัจจุบัน"],
                **LEVEL_INFO["watch"]}
    reasons = []
    if gap <= 0:
        idx = 3; reasons.append(f"ล้นตลิ่ง {abs(gap):.2f} ม.")
    elif gap <= GAP_HIGH:
        idx = 2; reasons.append(f"ห่างตลิ่งเพียง {gap:.2f} ม.")
    elif gap <= GAP_WATCH:
        idx = 1; reasons.append(f"ห่างตลิ่ง {gap:.2f} ม. (อยู่ในเกณฑ์เฝ้าระวัง ≤{GAP_WATCH} ม.)")
    else:
        idx = 0

    rate = ctx.get("rate_per_day_3d")
    if rate is not None and rate >= RISE_FAST_PER_DAY and idx < 3:
        idx += 1; reasons.append(f"น้ำขึ้นเฉลี่ย {rate:.2f} ม./วัน ใน 3 วัน")
    dj = ctx.get("discharge_change_24h")
    if dj is not None and dj >= DISCHARGE_JUMP:
        reasons.append(f"เขื่อนเพิ่มการระบาย {dj:.0f} ลบ.ม./วิ ใน 24 ชม.")

    ly = ctx.get("last_year") or {}
    pk = ly.get("next_30d_peak")
    if pk and pk["gap_to_bank"] <= GAP_HIGH:
        reasons.append(f"ปีที่แล้วหลังวันนี้ น้ำขึ้นไปสูงสุด {pk['wl']} ม. ({pk['date']})")

    level = LEVELS[idx]
    return {"level": level, "idx": idx, "reasons": reasons, **LEVEL_INFO[level]}


# ═════════════════════════════════════════════
# ระบบเรียก AI (ค้นหาโมเดลที่เปิดใช้งานจริงอัตโนมัติ)
# ═════════════════════════════════════════════
_CLIENTS = {}
_COOLDOWN = {}
_PROVIDER_MODELS_CACHE = {}
_BREAKER_TRIPPED = False


class CircuitBreakerError(RuntimeError):
    """ทุก key/โมเดลโควตาหมด → หยุดเรียก AI ทั้งรอบ แล้วสลับไปใช้เทมเพลตทันที"""


def _load_keys():
    raw_env = ",".join([
        os.environ.get("GROQ_API_KEY", ""),
        os.environ.get("OPENAI_API_KEY", ""),
        os.environ.get("OPENROUTER_API_KEY", ""),
        os.environ.get("GEMINI_API_KEYS", ""),
        os.environ.get("GEMINI_API_KEY", ""),
    ])
    seen = []
    for k in re.split(r"[,\s]+", raw_env):
        k = k.strip()
        if k and k not in seen:
            seen.append(k)
    return seen


def _discover_groq_models(key: str) -> list:
    """ถาม API ของ Groq โดยตรงว่าคีย์นี้เปิดสิทธิ์ให้ใช้โมเดลชื่ออะไรบ้าง (แก้ปัญหา 404 model_not_found ถาวร)"""
    cache_key = f"groq_{key[-6:]}"
    if cache_key in _PROVIDER_MODELS_CACHE:
        return _PROVIDER_MODELS_CACHE[cache_key]
    try:
        r = requests.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=10,
        )
        if r.status_code == 200:
            raw_models = [m.get("id", "") for m in r.json().get("data", []) if m.get("id")]
            # กรองโมเดลเสียง/ระบบตรวจสอบความปลอดภัยออก เหลือเฉพาะโมเดลแชตข้อความ
            chat_models = [
                m for m in raw_models
                if not any(skip in m.lower() for skip in ("whisper", "tts", "guard", "vision", "embed", "audio", "playai"))
            ]
            # จัดลำดับโมเดลที่เก่งภาษาไทยขึ้นก่อน
            priority_keywords = ("llama-4", "llama-3.3", "qwen", "deepseek", "gemma", "llama-3.1", "llama3", "mixtral", "compound")
            preferred = []
            for kw in priority_keywords:
                for m in chat_models:
                    if kw in m.lower() and m not in preferred:
                        preferred.append(m)
            others = [m for m in chat_models if m not in preferred]
            final_list = (preferred + others)[:4]
            if final_list:
                print(f"🔎 พบโมเดลที่เปิดใช้งานในคีย์ Groq (…{key[-4:]}): {final_list}")
                _PROVIDER_MODELS_CACHE[cache_key] = final_list
                return final_list
        else:
            print(f"⚠️ ตรวจสอบรายชื่อโมเดล Groq HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"⚠️ ตรวจสอบรายชื่อโมเดล Groq ไม่สำเร็จ: {e}")
    return ["llama-3.3-70b-versatile", "llama3-70b-8192", "gemma2-9b-it"]


def _detect_provider(key: str):
    if key.startswith("gsk_"):
        return "groq", "https://api.groq.com/openai/v1/chat/completions", _discover_groq_models(key)
    if key.startswith("sk-or-"):
        return "openrouter", "https://openrouter.ai/api/v1/chat/completions", ["meta-llama/llama-3.3-70b-instruct:free", "google/gemma-3-27b-it:free"]
    if key.startswith("sk-"):
        return "openai", "https://api.openai.com/v1/chat/completions", ["gpt-4o-mini", "gpt-4o"]
    return "gemini", None, MODELS


def _call_openai_compat(provider, url, model, key, prompt, json_mode, temperature):
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
    }
    if json_mode and provider in ("groq", "openai"):
        payload["response_format"] = {"type": "json_object"}

    res = requests.post(url, headers=headers, json=payload, timeout=25)
    if res.status_code == 200:
        data = res.json()
        txt = (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
        if txt:
            return txt
        raise RuntimeError("ข้อความตอบกลับว่างเปล่า")
    # กรณีโมเดลใดไม่รองรับ json_object ให้ลองส่งใหม่แบบปกติแล้วให้ _json_loads แกะ JSON เอง
    if json_mode and res.status_code == 400 and "response_format" in res.text:
        payload.pop("response_format", None)
        res2 = requests.post(url, headers=headers, json=payload, timeout=25)
        if res2.status_code == 200:
            txt = (res2.json().get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
            if txt:
                return txt
    raise RuntimeError(f"HTTP {res.status_code}: {res.text[:300]}")


def _trip_breaker(reason, last=None):
    global _BREAKER_TRIPPED
    _BREAKER_TRIPPED = True
    print(f"🚨 Circuit Breaker: {reason} → หยุดเรียก AI ทั้งรอบทันที")
    raise CircuitBreakerError(f"429 RESOURCE_EXHAUSTED - All keys exhausted: {last}")


def _gen(client, prompt, *, json_mode=False, search=False, temperature=0.7, retries=2):
    global _BREAKER_TRIPPED
    if _BREAKER_TRIPPED:
        raise CircuitBreakerError("429 RESOURCE_EXHAUSTED - Circuit Breaker ทำงานแล้วในรอบนี้")

    keys = _load_keys()
    if not keys:
        _trip_breaker("ไม่พบ API Key ในระบบ")

    last_err = None
    # 1) ลองคีย์ตระกูล Groq / ChatGPT (OpenAI) / OpenRouter ก่อน
    ext_keys = [k for k in keys if _detect_provider(k)[0] != "gemini"]
    for k in ext_keys:
        provider, url, p_models = _detect_provider(k)
        tail = f"…{k[-4:]}"
        for m in p_models:
            cid = (provider, m, tail)
            if _COOLDOWN.get(cid, 0) > time.time():
                continue
            try:
                txt = _call_openai_compat(provider, url, m, k, prompt, json_mode, temperature)
                print(f"✅ AI ตอบกลับสำเร็จ [{provider}/{m} key {tail}]")
                return txt, None
            except Exception as e:
                last_err = e
                print(f"⚠️ [{provider}/{m} key {tail}] ล้มเหลว: {e}")
                _COOLDOWN[cid] = time.time() + 300

    # 2) ลองคีย์ Gemini (ถ้ามี)
    gem_keys = [k for k in keys if _detect_provider(k)[0] == "gemini"]
    if gem_keys:
        from google import genai
        cfg = {"temperature": temperature}
        if json_mode:
            cfg["response_mime_type"] = "application/json"
        if search:
            cfg["tools"] = [types.Tool(google_search=types.GoogleSearch())]

        plan_exhausted_hits = 0
        for m in MODELS:
            for k in gem_keys:
                tail = f"…{k[-4:]}/gemini"
                cid = (m, tail)
                if _COOLDOWN.get(cid, 0) > time.time():
                    continue
                if k not in _CLIENTS:
                    try:
                        _CLIENTS[k] = genai.Client(api_key=k, http_options=types.HttpOptions(timeout=15000))
                    except Exception:
                        _CLIENTS[k] = genai.Client(api_key=k)
                try:
                    resp = _CLIENTS[k].models.generate_content(
                        model=m, contents=prompt, config=types.GenerateContentConfig(**cfg))
                    txt = (resp.text or "").strip()
                    if txt:
                        return txt, resp
                except Exception as e:
                    last_err = e
                    low = str(e).lower().replace(" ", "")
                    is_plan_limit = ("exceededyourcurrentquota" in low or "checkyourplan" in low or "limit:0" in low)
                    _COOLDOWN[cid] = time.time() + 3600
                    print(f"⏭️ ข้าม [{m} / key {tail}]: {str(e)[:220]}")
                    if is_plan_limit:
                        plan_exhausted_hits += 1
                        if plan_exhausted_hits >= 2:
                            _trip_breaker("โควตา Gemini ติด Prepayment/Plan Limit (Fast-Fail)", last_err)

    _trip_breaker("ทุกคีย์/ทุกค่ายไม่สามารถใช้งานได้ในขณะนี้", last_err)


def _json_loads(txt):
    txt = re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip()
    try:
        return json.loads(txt)
    except Exception:
        m = re.search(r"\{.*\}", txt, flags=re.S)
        return json.loads(m.group(0)) if m else None


def period_of_day(hour):
    if hour < 5:  return "ดึก"
    if hour < 10: return "เช้า"
    if hour < 13: return "สาย-เที่ยง"
    if hour < 17: return "บ่าย"
    if hour < 20: return "เย็น"
    return "ค่ำ"


# ═════════════════════════════════════════════
# 3) ไปหาข้อมูลล่าสุดก่อนเขียน
# ═════════════════════════════════════════════
def research_latest(client, date_text, period, water_ctx, risk):
    if not ENABLE_RESEARCH:
        return "", []
    gap = water_ctx.get("gap_to_bank")
    prompt = f"""วันนี้คือ {date_text} ช่วง{period} คุณเป็นผู้ช่วยหาข่าวให้เพจเตือนภัยน้ำ/อากาศของ อ.อินทร์บุรี จ.สิงห์บุรี (แม่น้ำเจ้าพระยา)
สถานะตอนนี้จากเซ็นเซอร์: ระดับน้ำอินทร์บุรี {water_ctx.get('wl')} ม. (ห่างตลิ่ง {gap} ม.), ระบายเขื่อนเจ้าพระยา {water_ctx.get('discharge')} ลบ.ม./วิ, ระดับความเสี่ยงเบื้องต้น: {risk['label']}

ให้ค้นหา "ข้อมูลล่าสุด" (ไม่เกิน 3 วัน) ในหัวข้อเหล่านี้:
1. กรมชลประทาน/สทนช.: แผนการระบายน้ำเขื่อนเจ้าพระยา
2. กรมอุตุนิยมวิทยา: พยากรณ์ฝน พายุ ภาคกลางตอนบน/สิงห์บุรี
3. ข่าวท้องถิ่น/ทางการ: น้ำท่วมหรือน้ำล้นตลิ่งที่ สิงห์บุรี ชัยนาท อ่างทอง ล่าสุด"""
    try:
        txt, resp = _gen(client, prompt, search=True, temperature=0.2, retries=1)
    except CircuitBreakerError:
        raise
    except Exception as e:
        print(f"⚠️ ค้นข่าวไม่สำเร็จ ข้ามขั้นตอนนี้: {e}")
        return "", []
    sources = []
    try:
        if resp:
            for ch in resp.candidates[0].grounding_metadata.grounding_chunks or []:
                if ch.web:
                    sources.append({"title": ch.web.title, "uri": ch.web.uri})
    except Exception:
        pass
    return txt, sources


# ═════════════════════════════════════════════
# 4) วิเคราะห์ (JSON)
# ═════════════════════════════════════════════
def analyze(client, facts, risk, research_text, prev_post):
    prompt = f"""คุณคือนักวิเคราะห์สถานการณ์น้ำของ อ.อินทร์บุรี หน้าที่: อ่านข้อมูลทั้งหมดแล้วสรุปเป็น JSON สำหรับให้แอดมินเพจเขียนโพสต์

ข้อมูลจริง (ตัวเลขทุกตัวต้องมาจากตรงนี้เท่านั้น):
{json.dumps(facts, ensure_ascii=False, indent=1)}

ระดับความเสี่ยงที่กฎของระบบกำหนดแล้ว: {risk['level']} ({risk['label']}) เหตุผล: {'; '.join(risk['reasons']) or '-'}
คุณ "เพิ่มระดับ" ได้ถ้าข่าว/ประกาศล่าสุดบ่งชี้ว่าแย่กว่า แต่ "ลดระดับ" ไม่ได้

โพสต์รอบก่อนหน้า: {json.dumps(prev_post, ensure_ascii=False) if prev_post else '(ไม่มี)'}

ตอบเป็น JSON เท่านั้น:
{{
 "level": "normal|watch|high|overbank",
 "focus": "water|storm|dust|fire|calm",
 "so_what": "ตัวเลขน้ำวันนี้แปลว่าอะไรกับชาวบ้าน 1-2 ประโยค (พูดตามจริงแต่ไม่ทำให้ชาวบ้านตื่นตระหนก)",
 "trend": "แนวโน้มน้ำ/เขื่อนตอนนี้",
 "vs_last_year": "เทียบปีที่แล้ว 3 มิติ พร้อมส่วนต่างเป็นเมตร",
 "changed_since_last_post": "อะไรเปลี่ยนไปจากโพสต์ก่อน (หรือ null)",
 "news_points": [],
 "advice": ["คำแนะนำที่เหมาะสมกับแนวโน้มน้ำจริง 1-2 ข้อ"],
 "unsure": []
}}"""
    try:
        txt, _ = _gen(client, prompt, json_mode=True, temperature=0.3)
        data = _json_loads(txt) or {}
    except CircuitBreakerError:
        raise
    except Exception as e:
        print(f"⚠️ analyze ล้มเหลว ใช้ค่ากฎแข็งแทน: {e}")
        data = {}
    ai_idx = LEVELS.index(data["level"]) if data.get("level") in LEVELS else 0
    idx = max(ai_idx, risk["idx"])
    data["level"] = LEVELS[idx]
    data["level_label"] = LEVEL_INFO[LEVELS[idx]]["label"]
    return data


# ═════════════════════════════════════════════
# 5) เขียนโพสต์
# ═════════════════════════════════════════════
def write_post(client, facts, analysis, research_text, prev_post, header, has_fire, feedback=None, suppress_news=False):
    lvl = analysis.get("level", "watch")
    lvl_label = analysis.get("level_label") or LEVEL_INFO.get(lvl, LEVEL_INFO["watch"])["label"]
    fire_line = ("🔥 **เฝ้าระวังความร้อน:** (พูดว่า 'ควันจากการเผาไร่/นา' ห้ามพูดว่าไฟป่า)\n" if has_fire else "")
    fb = ""
    if feedback:
        fb = "\n⚠️ ร่างก่อนหน้าไม่ผ่านการตรวจ ต้องแก้ให้ได้ดังนี้:\n- " + "\n- ".join(feedback) + "\n"
    prompt = f"""คุณคือแอดมินเพจ "อินทร์บุรีรอดมั้ย" เขียนโพสต์ให้ชาวบ้านอ่านแบบ "คุยกัน" เหมือนเพื่อนบ้านที่ตามน้ำมาตลอด
และอธิบายให้ฟังว่า "ตัวเลขนี้แปลว่าอะไรกับเรา" ด้วยน้ำเสียงที่อบอุ่น เป็นกันเอง ไม่ทำให้ชาวบ้านตื่นตระหนกตกใจ

ข้อมูลจริง (ใช้ตัวเลขจากนี้เท่านั้น):
{json.dumps(facts, ensure_ascii=False, indent=1)}

ผลวิเคราะห์ (ยึดตามนี้):
{json.dumps(analysis, ensure_ascii=False, indent=1)}

โพสต์รอบก่อน (ต้องเปลี่ยนสำนวนและคำพูดใหม่ ไม่ให้ซ้ำกับรอบก่อน): {json.dumps(prev_post, ensure_ascii=False) if prev_post else '(ไม่มี)'}

ระดับความเสี่ยงที่ยืนยันแล้ว: {lvl_label} → น้ำเสียง: {LEVEL_INFO[lvl]['tone']}
{fb}
กฎเหล็ก:
1. ภาษาพูดง่ายๆ เป็นกันเอง เหมือนเพื่อนบ้านคุยกัน ห้ามเป็นทางการ ห้ามลงท้าย "ครับ/ค่ะ" ตัดศัพท์วิชาการ (ม.รทก. → เมตร) ตัวเลขต่างเล็กๆ (เช่นเทียบเมื่อวาน/ระยะห่างตลิ่ง) บอกเป็นเซนติเมตรอย่างเดียว
2. เน้นเฉพาะที่สำคัญ: ตัวเลขแต่ละตัวพูดครั้งเดียว ห้ามซ้ำ อธิบาย "แปลว่าอะไรกับเรา" รวมเป็น 1 ประโยคต่อหัวข้อ
3. เทียบปีที่แล้ว: 1-2 ประโยค บอกส่วนต่างวันนี้ + ปีที่แล้วหลังช่วงนี้น้ำขึ้นไปสูงสุดเท่าไหร่ (ถ้ามีข้อมูล) ห้ามใช้ "ต่ำกว่าปีที่แล้ว" เป็นเหตุผลว่าปลอดภัย
4. เทียบ "เมื่อวาน" ได้เฉพาะถ้ามี change_24h ในข้อมูล ถ้าไม่มีให้ไม่พูดถึง
5. ห้ามบอกว่าฝนกำลังตก/ฝนปรอย ถ้า rain_now_confirmed เป็น false ให้พูดเป็น "โอกาสฝน" และใช้ตัวเลขเปอร์เซ็นต์โอกาสฝนตามที่ให้มา (max_rain_prob_24h_pct) เท่านั้น
6. ระบายน้ำเขื่อน: ใช้ตัวเลขของเขื่อนเจ้าพระยาเท่านั้น ห้ามใส่ตัวเลขระบายน้ำของโพนางดำ
   โพนางดำ: ต้องมีหัวข้อนี้เสมอ! ถ้ามี phonangdam_now ให้บอกระดับน้ำ แต่ถ้าไม่มี ให้เขียนว่า "วันนี้ยังไม่มีค่าล่าสุด" พร้อมตัวเลขปีที่แล้ว ห้ามข้ามหัวข้อโพนางดำเด็ดขาด
7. ฝุ่น: ใช้ถ้อยคำให้ตรงกับระดับ (pm25.instruction)
8. ไม่ต้องใส่หัวข้อข่าว 📰 ให้สรุปจากข้อมูลจริงล้วนๆ
9. ทักทายตามวัน{facts['now']['weekday']} และช่วง{facts['now']['period']}จริงๆ
10. อย่าเรียกสถานการณ์ว่า "ล้นตลิ่งแล้ว" ถ้า gap_to_bank ยังเป็นบวก ให้พูดว่า "ห่างตลิ่งอีก X เซนติเมตร" แทน
11. ปิดด้วย 📌 สรุป 1 ประโยค + คำแนะนำที่เหมาะกับแนวโน้มน้ำ (ถ้าน้ำกำลังลดลง อย่าสั่งให้ย้ายของจนชาวบ้านตกใจ ให้แนะนำการติดตามข้อมูลข่าวสารแทน) ความยาวรวมไม่เกิน 850 ตัวอักษร ห้ามใส่ hashtag
12. วันที่ทุกที่ต้องเขียนเป็น วัน เดือน ปี แบบไทย เช่น 14 ตุลาคม 2569 (พ.ศ.) ห้ามใช้รูปแบบ 2026-10-14 เด็ดขาด

โครงสร้าง:
{header}

🌡️ **สภาพอากาศและฝุ่น:** …
{fire_line}🌧️ **เฝ้าระวังฝนและพายุ:** …
🌊 **ระดับน้ำอินทร์บุรี:** …
🛑 **ระบายน้ำเขื่อนเจ้าพระยา:** …
🏞️ **ระดับน้ำโพนางดำ:** …
📌 **สรุป:** …"""
    txt, _ = _gen(client, prompt, temperature=0.7)
    return txt


# ═════════════════════════════════════════════
# 6) ตรวจ + แก้
# ═════════════════════════════════════════════
def rule_check(post, analysis, facts):
    problems = []
    lvl = analysis.get("level", "watch")
    lvl_label = analysis.get("level_label") or LEVEL_INFO.get(lvl, LEVEL_INFO["watch"])["label"]
    lvl_idx = LEVELS.index(lvl) if lvl in LEVELS else 1
    if lvl_idx >= 1:
        cleaned = NEG_OK.sub("", post)
        hit = [w for w in BANNED_REASSURE if w in cleaned]
        if hit:
            problems.append(f"ระดับความเสี่ยงคือ '{lvl_label}' แต่ใช้คำปลอบใจ: {', '.join(hit)}")
    if re.search(r"ครับ|ค่ะ", post):
        problems.append("มีคำลงท้าย ครับ/ค่ะ")
    if re.search(r"\d{4}-\d{2}-\d{2}", post):
        problems.append("ใช้วันที่แบบ 2025-09-29 ต้องเขียนเป็น วัน เดือน ปี แบบไทย (พ.ศ.)")
    if len(post) > 1300:
        problems.append(f"ยาวเกินไป ({len(post)} ตัวอักษร) ตัดให้เหลือไม่เกิน ~900")
    if "โพนางดำ" not in post:
        problems.append("ไม่มีหัวข้อโพนางดำ ต้องเล่าให้ครบ")
    wl = facts.get("water", {}).get("wl")
    if wl is not None and f"{wl:g}" not in post and f"{wl:.2f}" not in post:
        problems.append(f"ไม่ได้ระบุระดับน้ำปัจจุบัน {wl:.2f} ม.")
    if not facts.get("rain_storm", {}).get("rain_now_confirmed") and \
            re.search(r"ฝนกำลังตก|ฝนปรอย|ฝนตกอยู่|ฝนลงมาแล้ว", post):
        problems.append("บอกว่าฝนกำลังตก ทั้งที่ข้อมูลไม่ยืนยัน")
    return problems


def llm_review(client, post, facts, analysis):
    lvl = analysis.get("level", "watch")
    lvl_label = analysis.get("level_label") or LEVEL_INFO.get(lvl, LEVEL_INFO["watch"])["label"]
    prompt = f"""ตรวจโพสต์เพจน้ำท่วมนี้เทียบกับข้อมูลจริง ตอบ JSON เท่านั้น: {{"ok": true, "problems": []}} หรือ {{"ok": false, "problems": ["..."]}}
ตรวจ: (1) ตัวเลขทุกตัวตรงกับข้อมูลจริง (2) น้ำเสียงสอดคล้องระดับ '{lvl_label}' (3) ไม่ใช้ "ต่ำกว่าปีที่แล้ว" เป็นเหตุผลว่าปลอดภัย (4) ไม่มี ครับ/ค่ะ (5) วันที่เป็นแบบไทย
ตอบ ok=true ถ้าไม่มีปัญหาที่ร้ายแรง อย่าจู้จี้เรื่องสำนวน

ข้อมูลจริง: {json.dumps(facts, ensure_ascii=False)}
โพสต์:
{post}"""
    try:
        txt, _ = _gen(client, prompt, json_mode=True, temperature=0.0, retries=1)
        d = _json_loads(txt) or {}
        return bool(d.get("ok", True)), list(d.get("problems") or [])
    except Exception as e:
        print(f"⚠️ reviewer ใช้ไม่ได้ ข้าม: {e}")
        return True, []


def strip_unverified_news(post, research_text):
    if "📰" not in post:
        return post, []
    cleaned = re.sub(r"\n*📰[^\n]*\n(?=\n|$)", "\n", post)
    return cleaned, []


def _m(v):
    return f"{v:.2f}".rstrip("0").rstrip(".") if isinstance(v, (int, float)) else str(v)


def _cm(d):
    return f"{abs(d) * 100:.0f} เซนติเมตร"


def _no_time(t):
    return re.sub(r" เวลา \d{2}:\d{2} น\.", "", t or "")


def _short_date(t):
    return re.sub(r" \d{4}$", "", _no_time(t))


def _station_line(icon, name, st, last_year=None):
    gap = st.get("gap_to_bank")
    s = f"{icon} {name}ตอนนี้ **{st['wl']:.2f} เมตร**"
    if gap is not None:
        if gap > 0:
            s += f" ห่างตลิ่งอีก {_cm(gap)}"
        elif gap < 0:
            s += f" ล้นตลิ่งแล้ว {abs(gap):.2f} เมตร"
        else:
            s += " ถึงตลิ่งพอดี"
    c24, c3 = st.get("change_24h"), st.get("change_3d")
    if c24 is not None:
        s += " เท่าเมื่อวาน" if abs(c24) < 0.005 else f" {'ขึ้น' if c24 > 0 else 'ลง'}จากเมื่อวาน {_cm(c24)}"
        if c3 is not None and abs(c3) >= 0.05:
            s += f" (3 วันที่ผ่านมา{'ขึ้น' if c3 > 0 else 'ลง'}รวม {_cm(c3)})"
    return s


def _last_year_line(now_wl, ly, hot):
    s = f"📅 ปีที่แล้ววันเดียวกันน้ำ {_m(ly['wl'])} เมตร"
    if ly.get("gap_to_bank", 1) < 0:
        s += " (ล้นตลิ่งแล้ว)"
        
    diff = ly.get("this_year_minus_last_year")
    if diff is None and now_wl is not None:
        diff = round(now_wl - ly["wl"], 2)
    if diff is not None and abs(diff) >= 0.005:
        s += f" ปีนี้{'สูงกว่า' if diff > 0 else 'ต่ำกว่า'} {_cm(diff)}"
        
    pk = ly.get("next_30d_peak")
    if pk and now_wl is not None:
        pk_wl = pk['wl']
        if now_wl >= pk_wl:
            s += f" 🚨 และตอนนี้ระดับน้ำ **ทำลายสถิติสูงสุดของช่วงนี้เมื่อปีที่แล้ว** ({_m(pk_wl)} เมตร ช่วง {_short_date(pk['date'])}) ไปแล้ว สถานการณ์น่าเป็นห่วงอย่างยิ่ง!"
        else:
            s += f" และหลังจากนั้นปีที่แล้วขึ้นไปสูงสุด {_m(pk_wl)} เมตร ช่วง {_short_date(pk['date'])}"
            if hot:
                s += " จึงยังต้องติดตามต่อเนื่อง"
    return s


def _choose_dynamic_tip(lvl, w):
    """เลือกข้อความสรุปท้ายโพสต์แบบสุ่มสลับสำนวนตามแนวโน้มน้ำจริง เพื่อไม่ให้ซ้ำและไม่ทำให้ชาวบ้านตกใจ"""
    c24 = w.get("change_24h") or 0
    c3 = w.get("change_3d") or 0
    is_falling = (c24 <= 0 and c3 < -0.05) or (c24 < -0.03)

    if lvl == "normal":
        tips = [
            "ระดับน้ำยังอยู่ในเกณฑ์ปกติ ติดตามอัปเดตหน้าเพจได้ต่อเนื่องทุกวัน",
            "สถานการณ์น้ำยังปกติ ใช้ชีวิตได้ตามปกติและคอยเช็กข้อมูลรายวันนะ",
            "ระดับน้ำยังห่างตลิ่งพอสมควร แวะมาเช็กอัปเดตด้วยกันได้ทุกวัน",
        ]
    elif lvl == "watch" and is_falling:
        # กรณีเฝ้าระวัง แต่น้ำกำลังลดลง -> ใช้ถ้อยคำที่ผ่อนคลาย ไม่สั่งให้ย้ายของจนชาวบ้านตกใจ
        tips = [
            "แนวโน้มน้ำทยอยลดลงต่อเนื่อง แต่ระดับน้ำยังอยู่ใกล้ตลิ่ง แวะเช็กอัปเดตหน้าเพจทุกวันนะ",
            "ทิศทางน้ำค่อยๆ ลดลงแล้ว แต่ยังอยู่ในระยะเฝ้าระวัง คอยติดตามข้อมูลจากกรมชลประทานต่อเนื่อง",
            "ระดับน้ำขยับลดลงแต่ยังสูงใกล้ตลิ่ง เช็กค่าระดับน้ำรายวันไว้เป็นข้อมูลวางแผนล่วงหน้าได้",
            "น้ำเริ่มลดระดับลงอย่างต่อเนื่อง ยังไม่ต้องตื่นตระหนก แต่คอยเช็กอัปเดตหน้าเพจไว้ทุกวันนะ",
        ]
    elif lvl == "watch":
        # กรณีเฝ้าระวัง และน้ำทรงตัวหรือขยับขึ้น
        tips = [
            "ระดับน้ำยังอยู่ใกล้ตลิ่ง คอยเช็กระดับน้ำทุกวันและติดตามประกาศการระบายน้ำจากกรมชลประทาน",
            "ช่วงนี้น้ำยังทรงตัวใกล้ตลิ่ง แวะเช็กข้อมูลทุกวันและวางแผนดูแลของใช้ในที่ลุ่มต่ำไว้ล่วงหน้า",
            "ยังอยู่ในระยะเฝ้าระวัง แนะนำให้เช็กระดับน้ำรายวันและติดตามข่าวสารจากทางราชการต่อเนื่อง",
        ]
    else:
        # กรณีเสี่ยงสูง (high) หรือล้นตลิ่ง (overbank)
        tips = [
            "น้ำอยู่ใกล้ตลิ่งมาก เช็กระดับน้ำทุกวัน เตรียมดูแลของจำเป็นในพื้นที่ลุ่มต่ำ และติดตามประกาศกรมชลประทาน/ปภ.",
            "เฝ้าระวังใกล้ชิด บ้านที่อยู่ริมน้ำหรือที่ลุ่มต่ำควรเตรียมยกของขึ้นที่สูง และติดตามประกาศแจ้งเตือนต่อเนื่อง",
        ]
    return random.choice(tips)


def template_post(facts, analysis, header):
    """เทมเพลตสำรอง 100% จากโค้ด พร้อมระบบสุ่มสลับข้อความสรุปตามแนวโน้มน้ำจริง"""
    lines = [header, ""]
    w = facts.get("water") or {}
    lvl = (analysis or {}).get("level") or w.get("risk_level") or "watch"
    lvl_label = (analysis or {}).get("level_label") or LEVEL_INFO.get(lvl, LEVEL_INFO["watch"])["label"]
    hot = lvl in ("watch", "high", "overbank")
    gap = w.get("gap_to_bank")

    if w.get("wl") is not None:
        lines.append(_station_line("🌊", "น้ำอินทร์บุรี", w))
        ly = w.get("last_year")
        if ly:
            lines.append(_last_year_line(w["wl"], ly, hot))
    else:
        lines.append("🌊 น้ำอินทร์บุรี: วันนี้ดึงค่าล่าสุดไม่ได้ ขอให้เช็กประกาศทางการ")

    if w.get("discharge") is not None:
        d = f"🛑 เขื่อนเจ้าพระยาระบายน้ำ {w['discharge']:,.0f} ลบ.ม./วินาที"
        dj = w.get("discharge_change_24h")
        if dj is not None:
            d += " เท่าเมื่อวาน" if abs(dj) < 0.5 else f" ({'เพิ่ม' if dj > 0 else 'ลด'}จากเมื่อวาน {abs(dj):,.0f})"
        lines.append(d)

    pn, pl = w.get("phonangdam_now"), w.get("phonangdam_last_year")
    if pn:
        s = _station_line("🏞️", "โพนางดำ", pn)
        vl = pn.get("vs_last_year")
        if vl:
            s += f" (ปีที่แล้ววันเดียวกัน {_m(vl['wl'])} เมตร)"
        lines.append(s)
    elif pl:
        lines.append(f"🏞️ โพนางดำ: วันนี้ยังไม่มีค่าล่าสุด (ปีที่แล้วช่วงนี้ {_m(pl['wl'])} เมตร)")

    w8, pm, rs = facts.get("weather") or {}, facts.get("pm25") or {}, facts.get("rain_storm") or {}
    seg = []
    if w8.get("temp_c") is not None:
        seg.append(f"{w8['temp_c']} องศา")
    if pm.get("value") is not None:
        seg.append(f"ฝุ่น PM2.5 ระดับ{pm.get('level', '-')} ({pm['value']})")
    if rs.get("max_rain_prob_24h_pct") is not None:
        seg.append(f"โอกาสฝนใน 24 ชม. {rs['max_rain_prob_24h_pct']:.0f}%")
    if seg:
        lines.append("🌡️ " + " · ".join(seg))
    hs = facts.get("hotspots") or {}
    if isinstance(hs.get("count"), int) and hs["count"] > 0:
        lines.append(f"🔥 ควันจากการเผา: ตรวจพบจุดความร้อน {hs['count']} จุด")

    if lvl == "overbank" and gap is not None and gap > 0:
        label = "เฝ้าระวังสูงสุด ยังไม่ล้นแต่เหลืออีกนิดเดียว"
    elif lvl == "overbank":
        label = "น้ำล้นตลิ่งแล้ว"
    else:
        label = lvl_label
    tip = _choose_dynamic_tip(lvl, w)
    lines += ["", f"📌 {label} — {tip}"]
    return "\n".join(lines)


def review_and_fix(client, post, facts, analysis, research_text, prev_post, header, has_fire, max_fix=1):
    problems = ["ยังไม่ได้ตรวจ"]
    for attempt in range(max_fix + 1):
        post, _ = strip_unverified_news(post, research_text)
        problems = rule_check(post, analysis, facts)
        if not problems:
            ok, llm_problems = llm_review(client, post, facts, analysis)
            if ok:
                return post, []
            problems = llm_problems
        print(f"🧪 review รอบ {attempt+1}: {problems}")
        if attempt == max_fix:
            break
        try:
            post = write_post(client, facts, analysis, research_text, prev_post, header,
                              has_fire, feedback=problems, suppress_news=True)
        except Exception as e:
            print(f"⚠️ เขียนใหม่ไม่สำเร็จ: {e}")
            break
    if problems:
        print(f"⚠️ ร่างสุดท้ายยังมีปัญหาที่แก้ไม่หมด ({problems}) → ใช้เทมเพลตข้อเท็จจริงแทน")
        return template_post(facts, analysis, header), problems
    return post, []


# ═════════════════════════════════════════════
# ความจำ (state.json)
# ═════════════════════════════════════════════
def previous_post_brief(state):
    posts = state.get("recent_posts") or []
    if not posts:
        return None
    p = posts[-1]
    return {"posted_at": p.get("at"), "level": p.get("level"), "wl": p.get("wl"),
            "discharge": p.get("discharge"), "text_excerpt": (p.get("text") or "")[:500]}


def remember_post(state, post, analysis, facts, now_str, keep=6):
    posts = list(state.get("recent_posts") or [])
    lvl = (analysis or {}).get("level", "watch")
    posts.append({"at": now_str, "level": lvl, "focus": (analysis or {}).get("focus"),
                  "wl": facts["water"].get("wl"), "discharge": facts["water"].get("discharge"),
                  "text": post[:900]})
    state["recent_posts"] = posts[-keep:]
    return state
