# -*- coding: utf-8 -*-
"""
CPBC 인근 주차장 모니터링 - GitHub Actions용
매일 자동 실행 → Google Sheets에 이력 누적 저장
"""

import json, math, os, urllib.request
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

# ── 설정 ────────────────────────────────────────────────────
CPBC_LAT = 37.5643171
CPBC_LNG = 126.9881729
RADIUS   = 800
API_URL  = (
    "https://api.modu.cloud/poi/pins?"
    "geohash=wydmc2,wydmc8,wydm9x,wydm9w,wydm9q,wydm9n,wydm9p,"
    "wydmc0,wydm9r,wydmc3,wydmc9,wydmc1,wydm9m,wydm9t,wydm9j"
    "&shareMode=true&partnerMode=true"
)
BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
CRED_FILE = os.path.join(BASE_DIR, "credentials.json")
SNAP_FILE = os.path.join(BASE_DIR, "snapshot.json")
SHEET_ID  = "1E0llbaOGSsHWO1DPVRy14uUTSvPOhqJyk6OcyVvzG7w"
SCOPES    = ["https://www.googleapis.com/auth/spreadsheets",
             "https://www.googleapis.com/auth/drive"]

# ── 유틸 ────────────────────────────────────────────────────
def calc_dist(lat1, lng1, lat2, lng2):
    R = 6371000; r = math.pi/180
    a = (math.sin((lat2-lat1)*r/2)**2
         + math.cos(lat1*r)*math.cos(lat2*r)*math.sin((lng2-lng1)*r/2)**2)
    return R*2*math.atan2(math.sqrt(a), math.sqrt(1-a))

def fp(v):
    if v is None: return "-"
    if v == 0:   return "무료"
    return f"{v:,}원"

# ── API 호출 ─────────────────────────────────────────────────
def fetch_api():
    req = urllib.request.Request(API_URL, headers={
        "accept":"application/json", "origin":"https://app.modu.kr",
        "referer":"https://app.modu.kr/", "user-agent":"Mozilla/5.0"
    })
    with urllib.request.urlopen(req, timeout=15) as res:
        return json.loads(res.read().decode("utf-8"))

# ── 파싱 ────────────────────────────────────────────────────
def parse(raw):
    lots, tickets = [], []
    for group in raw.get("data", []):
        for lot in group.get("parkinglots", []):
            d = calc_dist(CPBC_LAT, CPBC_LNG,
                          lot.get("latitude", 0), lot.get("longitude", 0))
            if d > RADIUS: continue
            cp = lot.get("calcPrice") or {}
            if cp.get("60") is None and not lot.get("tickets"): continue
            item = {
                "seq": lot["parkinglotSeq"], "name": lot["name"],
                "dist": int(d), "partner": lot.get("isPartner", False),
                "p30": cp.get("30"), "p60": cp.get("60"),
                "p120": cp.get("120"), "p180": cp.get("180"),
            }
            lots.append(item)
            for t in lot.get("tickets", []):
                tickets.append({
                    "lot": lot["name"], "dist": int(d),
                    "partner": lot.get("isPartner", False),
                    "name": t.get("couponName", ""),
                    "price": t.get("price", 0),
                    "time": t.get("usingTimeLabel", ""),
                    "open": t.get("isOpen", False),
                    "soldout": t.get("isSoldOut", False),
                })
    lots.sort(key=lambda x: (x["p60"] if x["p60"] is not None else 99999, x["dist"]))
    tickets.sort(key=lambda x: (x["dist"], x["price"]))
    return lots, tickets

# ── 스냅샷 로드/저장 ─────────────────────────────────────────
def load_snap():
    if not os.path.exists(SNAP_FILE): return None
    with open(SNAP_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_snap(lots, tickets):
    data = {str(l["seq"]): {**l, "tickets": [t for t in tickets if t["lot"] == l["name"]]}
            for l in lots}
    with open(SNAP_FILE, "w", encoding="utf-8") as f:
        json.dump({"ts": datetime.now().isoformat(), "data": data}, f, ensure_ascii=False)

# ── 변경사항 비교 ────────────────────────────────────────────
def compare(old_snap, lots, tickets):
    if not old_snap: return []
    old = old_snap.get("data", {})
    changes = []
    new_map = {str(l["seq"]): l for l in lots}
    new_tk  = {}
    for t in tickets: new_tk.setdefault(t["lot"], []).append(t)

    for sid, o in old.items():
        n = new_map.get(sid)
        name = o["name"]
        if not n:
            changes.append({"kind": "삭제", "name": name, "desc": "주차장 목록에서 사라짐"})
            continue
        for k, label in [("p60","1시간"),("p30","30분"),("p120","2시간"),("p180","3시간")]:
            if o.get(k) != n.get(k):
                ov = f"{o[k]:,}원" if isinstance(o.get(k), int) else "-"
                nv = f"{n[k]:,}원" if isinstance(n.get(k), int) else "-"
                changes.append({"kind": "요금변경", "name": name, "desc": f"{label}: {ov} → {nv}"})
        ot = {t["name"]: t for t in o.get("tickets", [])}
        nt = {t["name"]: t for t in new_tk.get(name, [])}
        for tn in set(list(ot) + list(nt)):
            a, b = ot.get(tn), nt.get(tn)
            if a and not b:
                changes.append({"kind": "할인권삭제", "name": name, "desc": f"[{tn}] 삭제됨"})
            elif not a and b:
                changes.append({"kind": "할인권신규", "name": name, "desc": f"[{tn}] 신규 ({b['price']:,}원)"})
            elif a and b:
                if a["price"] != b["price"]:
                    changes.append({"kind": "요금변경", "name": name,
                                    "desc": f"[{tn}] {a['price']:,}원 → {b['price']:,}원"})
                if a["open"] != b["open"]:
                    changes.append({"kind": "할인권상태변경", "name": name,
                                    "desc": f"[{tn}] {'판매시작' if b['open'] else '판매중단'}"})
                if a["soldout"] != b["soldout"]:
                    changes.append({"kind": "할인권품절", "name": name,
                                    "desc": f"[{tn}] {'품절' if b['soldout'] else '재입고'}"})
    return changes

# ── Google Sheets 연동 ───────────────────────────────────────
def get_gc():
    creds = Credentials.from_service_account_file(CRED_FILE, scopes=SCOPES)
    return gspread.authorize(creds)

def setup_sheets(sh):
    existing = [ws.title for ws in sh.worksheets()]

    def make_sheet(title, rows, cols, headers):
        if title not in existing:
            ws = sh.add_worksheet(title=title, rows=rows, cols=cols)
            ws.append_row(headers, value_input_option="RAW")
            ws.format(f"A1:{chr(64+len(headers))}1", {
                "backgroundColor": {"red": 0.12, "green": 0.31, "blue": 0.47},
                "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True}
            })
            return ws
        return sh.worksheet(title)

    ws_p = make_sheet("요금이력", 9999, 8,
                      ["기록일시","주차장명","거리(m)","파트너","30분","1시간","2시간","3시간"])
    ws_t = make_sheet("할인권이력", 9999, 9,
                      ["기록일시","주차장명","거리(m)","파트너","권종명","가격","이용시간대","판매여부","품절여부"])
    ws_c = make_sheet("변경이력", 9999, 4,
                      ["기록일시","구분","주차장명","변경내용"])

    if "Sheet1" in existing:
        try: sh.del_worksheet(sh.worksheet("Sheet1"))
        except: pass

    return ws_p, ws_t, ws_c

def write_sheets(gc, lots, tickets, changes, now_str):
    sh = gc.open_by_key(SHEET_ID)
    ws_p, ws_t, ws_c = setup_sheets(sh)

    # 요금 이력
    price_rows = [
        [now_str, l["name"], l["dist"], "O" if l["partner"] else "",
         fp(l["p30"]), fp(l["p60"]), fp(l["p120"]), fp(l["p180"])]
        for l in lots
    ]
    if price_rows:
        ws_p.append_rows(price_rows, value_input_option="RAW")

    # 할인권 이력
    ticket_rows = [
        [now_str, t["lot"], t["dist"], "O" if t["partner"] else "",
         t["name"], t["price"], t["time"],
         "판매중" if t["open"] else "비판매",
         "품절" if t["soldout"] else ""]
        for t in tickets
    ]
    if ticket_rows:
        ws_t.append_rows(ticket_rows, value_input_option="RAW")

    # 변경 이력
    if changes:
        change_rows = [[now_str, c["kind"], c["name"], c["desc"]] for c in changes]
        ws_c.append_rows(change_rows, value_input_option="RAW")

    print(f"  요금 {len(price_rows)}행 / 할인권 {len(ticket_rows)}행 / 변경 {len(changes)}건")

# ── 메인 ────────────────────────────────────────────────────
if __name__ == "__main__":
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_str}] 모두의주차 API 수집 중...")

    raw = fetch_api()
    lots, tickets = parse(raw)
    print(f"  주차장 {len(lots)}개 / 할인권 {len(tickets)}개 수집")

    old_snap = load_snap()
    changes  = compare(old_snap, lots, tickets)
    save_snap(lots, tickets)

    if changes:
        print(f"  🔔 변경사항 {len(changes)}건:")
        for c in changes:
            print(f"    [{c['kind']}] {c['name']} - {c['desc']}")
    else:
        print("  변경사항 없음")

    print("Google Sheets 기록 중...")
    gc = get_gc()
    write_sheets(gc, lots, tickets, changes, now_str)
    print("완료!")
