import os
import hashlib
import requests
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder="static")

# ==================== 配置区 ====================
TRACKING_API_URL = os.getenv("TRACKING_API_URL", "")
TRACKING_API_KEY = os.getenv("TRACKING_API_KEY", "")
USE_MOCK = not (TRACKING_API_URL and TRACKING_API_KEY)
MAX_BATCH = 20

OWNER_CODE_MAP = {
    "MSCU": "MSC", "MEDU": "MSC", "TGHU": "MSC",
    "MSKU": "MAERSK", "MAEU": "MAERSK", "MRKU": "MAERSK",
    "PONU": "MAERSK", "POCU": "MAERSK",
    "CMAU": "CMA_CGM", "CGMU": "CMA_CGM",
    "HLXU": "HAPAG_LLOYD", "HLCU": "HAPAG_LLOYD",
    "TRLU": "HAPAG_LLOYD", "TEMU": "HAPAG_LLOYD",
    "EISU": "EVERGREEN", "EGHU": "EVERGREEN", "EMCU": "EVERGREEN",
    "EGSU": "EVERGREEN", "EITU": "EVERGREEN",
}


def detect_carrier(query: str):
    q = query.strip().upper()
    if len(q) == 11 and q[:4].isalpha() and q[4:].isdigit():
        return OWNER_CODE_MAP.get(q[:4])
    if q.startswith("MEDU"):
        return "MSC"
    return None


def extract_shipped_date(events):
    if not events:
        return None
    for ev in events:
        desc = (ev.get("description") or "").lower()
        if any(k in desc for k in ["装船", "loaded", "shipped", "on board"]):
            return ev.get("timestamp")
    return events[0].get("timestamp")


def call_real_api(query: str, carrier: str) -> dict:
    payload = {"containerNumber": query, "carrier": carrier}
    headers = {"Authorization": f"Bearer {TRACKING_API_KEY}"}
    resp = requests.post(TRACKING_API_URL, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def mock_data(query: str, carrier: str) -> dict:
    h = int(hashlib.md5(query.encode()).hexdigest(), 16)
    ships = ["MSC ISABELLA", "MAERSK EMDEN", "CMA CGM MARCO POLO",
             "HAPAG BERLIN", "EVER GIVEN", "MSC GULSUN"]
    ports = ["上海港", "宁波港", "深圳盐田港", "青岛港", "新加坡港"]
    vessel = ships[h % len(ships)]
    load_port = ports[h % len(ports)]

    lat = round(15 + (h % 150) / 10, 4)
    lon = round(108 + ((h // 150) % 150) / 10, 4)
    speed = round(12 + (h % 90) / 10, 1)
    conf = round(0.70 + (h % 25) / 100, 2)
    day = 1 + h % 7
    shipped = f"2026-10-{day:02d}T14:20:00"

    return {
        "carrier": carrier or "MSC",
        "vessel_name": vessel,
        "vessel_imo": str(9000000 + h % 999999),
        "position": {"latitude": lat, "longitude": lon,
                     "speed_knots": speed, "heading": h % 360},
        "eta": "2026-10-15T08:00:00",
        "eta_confidence": conf,
        "current_milestone": {"description": "海上运输中", "location": "南海"},
        "shipped_date": shipped,
        "events": [
            {"description": "已装船", "location": load_port, "timestamp": shipped},
            {"description": "已离港", "location": load_port, "timestamp": f"2026-10-{day:02d}T20:10:00"},
            {"description": "海上运输中", "location": "南海", "timestamp": "2026-10-09T18:00:00"},
        ],
    }


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/track/batch", methods=["POST"])
def track_batch():
    body = request.get_json() or {}
    queries = body.get("queries", [])
    carrier = body.get("carrier", "AUTO")

    if not queries or not isinstance(queries, list):
        return jsonify({"error": "请输入至少一个柜号"}), 400
    if len(queries) > MAX_BATCH:
        return jsonify({"error": f"单次最多查询 {MAX_BATCH} 个柜号"}), 400

    def track_one(q):
        c = carrier
        if c == "AUTO":
            c = detect_carrier(q) or ""
        try:
            if USE_MOCK:
                raw = mock_data(q, c)
            else:
                raw = call_real_api(q, c)
            raw["query"] = q
            raw["carrier"] = raw.get("carrier") or c or "未知"
            raw["success"] = True
            if not raw.get("shipped_date"):
                raw["shipped_date"] = extract_shipped_date(raw.get("events", []))
            return raw
        except requests.exceptions.Timeout:
            return {"query": q, "success": False, "error": "查询超时"}
        except requests.exceptions.HTTPError as e:
            return {"query": q, "success": False, "error": f"API 错误 {e.response.status_code}"}
        except Exception as e:
            return {"query": q, "success": False, "error": str(e)}

    with ThreadPoolExecutor(max_workers=min(10, len(queries))) as ex:
        results = list(ex.map(track_one, queries))

    return jsonify({"results": results})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
