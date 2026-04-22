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


# ── 할인권 적정성 분석 ───────────────────────────────────────
def analyze_tickets(tickets):
    """
    500m 이내 파트너 주차장 할인권 기준으로 권종별 적정성 분석
    - 당일권 / 3시간권 / 기타 로 분류
    - 평균가 대비 ±20% 기준으로 저렴/적정/비쌈 판단
    """
    PARTNER_RADIUS = 500

    def categorize(name):
        if "당일" in name: return "당일권"
        if "3시간" in name: return "3시간권"
        return "기타"

    def judge(price, avg):
        if avg == 0: return None
        ratio = price / avg
        if ratio < 0.8:   return ("저렴", "#10B981", f"주변 평균({avg:,}원) 대비 {(1-ratio)*100:.0f}% 저렴 ✅ 경쟁력 있음")
        if ratio <= 1.2:  return ("적정", "#3B82F6", f"주변 평균({avg:,}원) 대비 적정 수준 👍")
        return           ("비쌈", "#EF4444", f"주변 평균({avg:,}원) 대비 {(ratio-1)*100:.0f}% 높음 ⚠️ 조정 검토 필요")

    # 500m 이내 파트너 주차장 할인권만 수집 (판매중 + 비품절)
    partner_tickets = [t for t in tickets
                       if t["partner"] and t["dist"] <= PARTNER_RADIUS
                       and t["open"] and not t["soldout"] and t["price"] > 0]

    # 권종별 가격 그룹핑
    by_cat = {"당일권": [], "3시간권": [], "기타": []}
    for t in partner_tickets:
        by_cat[categorize(t["name"])].append(t["price"])

    # 평균가 계산
    avgs = {}
    for cat, prices in by_cat.items():
        avgs[cat] = int(sum(prices) / len(prices)) if prices else 0

    # CPBC(평화빌딩) 할인권 분석
    cpbc_tickets = [t for t in tickets if "평화빌딩" in t["lot"] and t["open"] and not t["soldout"]]

    analysis = []
    for t in cpbc_tickets:
        cat   = categorize(t["name"])
        avg   = avgs.get(cat, 0)
        if avg == 0: continue
        result = judge(t["price"], avg)
        if not result: continue
        label, color, comment = result
        analysis.append({
            "name":    t["name"],
            "price":   t["price"],
            "cat":     cat,
            "avg":     avg,
            "label":   label,
            "color":   color,
            "comment": comment,
            "count":   len(by_cat.get(cat, [])),
        })

    # 전체 파트너 권종별 요약도 반환
    summary = []
    for cat in ["당일권", "3시간권", "기타"]:
        prices = by_cat[cat]
        if not prices: continue
        summary.append({
            "cat":   cat,
            "avg":   avgs[cat],
            "min":   min(prices),
            "max":   max(prices),
            "count": len(prices),
        })

    return analysis, summary, avgs


# ── HTML 대시보드 생성 ───────────────────────────────────────
def build_html(lots, tickets, changes, snap_history, now_str, sheet_id, analysis=None, summary=None):
    lots_json    = json.dumps(lots,    ensure_ascii=False)
    tickets_json = json.dumps(tickets, ensure_ascii=False)
    sheets_url   = f"https://docs.google.com/spreadsheets/d/{sheet_id}"

    if not snap_history or len(snap_history) <= 1:
        change_html  = '<div class="empty"><span>💾</span><p>기준 데이터 저장됨. 다음 실행부터 변경사항을 감지합니다.</p></div>'
        change_count = "-"
        first_ts     = now_str
        snap_count   = 1
    else:
        snap_count = len(snap_history)
        first_ts   = snap_history[0].get("ts", now_str)
        if not changes:
            change_html  = '<div class="empty"><span>✓</span><p>변경사항이 없습니다</p></div>'
            change_count = "없음"
        else:
            change_count = f"{len(changes)}건"
            kc = {"요금변경":"#F59E0B","할인권신규":"#10B981","할인권삭제":"#EF4444",
                  "할인권상태변경":"#818CF8","할인권품절":"#94A3B8","삭제":"#EF4444"}
            change_html = "".join(f"""
            <div class="change-row">
              <span class="change-badge" style="background:{kc.get(c['kind'],'#94A3B8')}22;color:{kc.get(c['kind'],'#94A3B8')};border-color:{kc.get(c['kind'],'#94A3B8')}44">{c['kind']}</span>
              <div><div class="change-name">{c['name']}</div><div class="change-desc">{c['desc']}</div></div>
            </div>""" for c in changes)

    # 적정성 분석 HTML
    analysis = analysis or []
    summary  = summary  or []

    if summary:
        summary_html = "".join(
            f'<div class="ana-summary-item"><span class="ana-cat">{s["cat"]}</span>' +
            f'<span class="ana-range">{s["min"]:,}~{s["max"]:,}원</span>' +
            f'<span class="ana-avg">평균 {s["avg"]:,}원</span>' +
            f'<span class="ana-cnt">({s["count"]}개 주차장)</span></div>'
            for s in summary
        )
    else:
        summary_html = '<span style="color:var(--t3);font-size:11px">500m 이내 파트너 할인권 데이터 없음</span>'

    if analysis:
        analysis_rows = "".join(
            f'<div class="ana-row">' +
            f'<div class="ana-left"><span class="ana-badge" style="background:{a["color"]}22;color:{a["color"]};border:1px solid {a["color"]}44">{a["label"]}</span>' +
            f'<span class="ana-name">{a["name"]}</span><span class="ana-price">{a["price"]:,}원</span></div>' +
            f'<div class="ana-comment">{a["comment"]}</div></div>'
            for a in analysis
        )
    else:
        analysis_rows = '<div style="color:var(--t3);font-size:12px;padding:12px 16px">평화빌딩 주차장 판매중 할인권 없음</div>'

    partners  = sum(1 for l in lots if l["partner"])
    with_tick = len(set(t["lot"] for t in tickets))
    cheap     = [l for l in lots if l.get("p60") and l["p60"] > 0]
    min_p     = cheap[0] if cheap else None

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CPBC 주변 주차장 현황</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;700;900&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
:root{{--bg:#0A0E1A;--s1:#111827;--s2:#1C2333;--bd:#2A3550;--blue:#3B82F6;--green:#10B981;--yellow:#F59E0B;--red:#EF4444;--t1:#F1F5F9;--t2:#94A3B8;--t3:#64748B;--mono:'JetBrains Mono',monospace}}
body{{font-family:'Noto Sans KR',sans-serif;background:var(--bg);color:var(--t1);min-height:100vh}}
body::before{{content:'';position:fixed;inset:0;background-image:linear-gradient(rgba(59,130,246,.03) 1px,transparent 1px),linear-gradient(90deg,rgba(59,130,246,.03) 1px,transparent 1px);background-size:40px 40px;pointer-events:none}}
.wrap{{position:relative;z-index:1;max-width:1400px;margin:0 auto;padding:20px}}
.hdr{{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;padding-bottom:16px;border-bottom:1px solid var(--bd);flex-wrap:wrap;gap:10px}}
.hdr-l{{display:flex;align-items:center;gap:12px}}
.logo{{width:40px;height:40px;background:linear-gradient(135deg,var(--blue),#6366F1);border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:18px;box-shadow:0 0 20px rgba(59,130,246,.3);flex-shrink:0}}
.hdr h1{{font-size:16px;font-weight:700;letter-spacing:-.5px}}.hdr p{{font-size:11px;color:var(--t3);margin-top:2px}}
.hdr-r{{display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
.badge{{font-family:var(--mono);font-size:11px;padding:4px 10px;border-radius:20px;border:1px solid var(--bd);color:var(--t2);white-space:nowrap}}
.badge.ok{{border-color:var(--green);color:var(--green)}}.badge.ok::before{{content:'● ';}}
.badge.sheets{{border-color:#34A853;color:#34A853;text-decoration:none;display:inline-flex;align-items:center;gap:4px}}
.kpi{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:16px}}
.kcard{{background:var(--s1);border:1px solid var(--bd);border-radius:12px;padding:14px 16px;position:relative;overflow:hidden}}
.kcard::before{{content:'';position:absolute;top:0;left:0;right:0;height:2px}}
.kcard.b::before{{background:var(--blue)}}.kcard.g::before{{background:var(--green)}}.kcard.y::before{{background:var(--yellow)}}.kcard.r::before{{background:var(--red)}}.kcard.p::before{{background:#8B5CF6}}
.kl{{font-size:10px;color:var(--t3);font-weight:600;letter-spacing:.6px;text-transform:uppercase}}
.kv{{font-family:var(--mono);font-size:24px;font-weight:700;margin:4px 0 2px;letter-spacing:-1px}}
.kv.b{{color:var(--blue)}}.kv.g{{color:var(--green)}}.kv.y{{color:var(--yellow)}}.kv.r{{color:var(--red)}}.kv.p{{color:#8B5CF6}}
.ks{{font-size:10px;color:var(--t3)}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px}}
@media(max-width:700px){{.grid{{grid-template-columns:1fr}}}}
.full{{grid-column:1/-1}}
.panel{{background:var(--s1);border:1px solid var(--bd);border-radius:12px;overflow:hidden}}
.ph{{padding:12px 16px;border-bottom:1px solid var(--bd);display:flex;align-items:center;justify-content:space-between}}
.pt{{font-size:12px;font-weight:600;display:flex;align-items:center;gap:7px}}
.dot{{width:6px;height:6px;border-radius:50%;display:inline-block;flex-shrink:0}}
.dot.b{{background:var(--blue)}}.dot.g{{background:var(--green)}}.dot.y{{background:var(--yellow)}}
.pc{{font-family:var(--mono);font-size:11px;color:var(--t3)}}
.fb{{padding:8px 12px;border-bottom:1px solid var(--bd);display:flex;align-items:center;gap:4px;flex-wrap:wrap;background:var(--s2)}}
.fb-btn{{font-family:'Noto Sans KR',sans-serif;font-size:11px;padding:3px 8px;border-radius:20px;border:1px solid var(--bd);background:transparent;color:var(--t3);cursor:pointer;transition:all .15s;white-space:nowrap}}
.fb-btn:hover,.fb-btn.on{{border-color:var(--blue);color:var(--blue);background:rgba(59,130,246,.12);font-weight:600}}
.fb-search{{font-family:'Noto Sans KR',sans-serif;font-size:11px;padding:3px 8px;border-radius:6px;border:1px solid var(--bd);background:var(--bg);color:var(--t1);outline:none;margin-left:auto;width:110px}}
.fb-search:focus{{border-color:var(--blue)}}.fb-search::placeholder{{color:var(--t3)}}
.sw{{padding:6px 12px;border-bottom:1px solid var(--bd);display:flex;align-items:center;gap:8px;background:var(--s2)}}
.sl{{font-size:11px;color:var(--t3);white-space:nowrap}}
.sv{{font-family:var(--mono);font-size:11px;color:var(--blue);width:44px;text-align:right;white-space:nowrap}}
input[type=range]{{flex:1;-webkit-appearance:none;height:3px;border-radius:2px;background:var(--bd);outline:none;cursor:pointer}}
input[type=range]::-webkit-slider-thumb{{-webkit-appearance:none;width:12px;height:12px;border-radius:50%;background:var(--blue);cursor:pointer}}
.tw{{overflow:auto;max-height:320px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
thead th{{padding:8px 10px;text-align:left;font-size:10px;font-weight:600;color:var(--t3);letter-spacing:.6px;text-transform:uppercase;background:var(--s1);position:sticky;top:0;z-index:1;white-space:nowrap;cursor:pointer;user-select:none}}
thead th:hover{{color:var(--t1)}}
thead th.sa::after{{content:' ▲';color:var(--blue);font-size:9px}}
thead th.sd::after{{content:' ▼';color:var(--blue);font-size:9px}}
tbody tr{{border-bottom:1px solid rgba(42,53,80,.5);transition:background .12s}}
tbody tr:hover{{background:var(--s2)}}
tbody td{{padding:7px 10px;color:var(--t2);white-space:nowrap}}
.nc{{color:var(--t1);font-weight:500;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.dist{{font-family:var(--mono);font-size:10px;background:var(--s2);border:1px solid var(--bd);padding:2px 5px;border-radius:4px;color:var(--t3)}}
.p{{font-family:var(--mono);font-size:11px}}
.p.free{{color:var(--green);font-weight:700}}.p.low{{color:var(--green)}}.p.mid{{color:var(--t2)}}.p.high{{color:var(--red)}}
.tc{{font-size:11px;color:var(--t3);max-width:130px;overflow:hidden;text-overflow:ellipsis}}
.rdim td{{opacity:.42}}
.tag{{font-size:9px;padding:2px 5px;border-radius:3px;font-weight:600;display:inline-block}}
.tag.partner{{background:rgba(59,130,246,.15);color:var(--blue);border:1px solid rgba(59,130,246,.3)}}
.tag.open{{background:rgba(16,185,129,.15);color:var(--green);border:1px solid rgba(16,185,129,.3)}}
.tag.closed{{background:rgba(100,116,139,.15);color:var(--t3);border:1px solid var(--bd)}}
.tag.soldout{{background:rgba(245,158,11,.15);color:var(--yellow);border:1px solid rgba(245,158,11,.3)}}
.change-row{{padding:9px 16px;border-bottom:1px solid rgba(42,53,80,.4);display:flex;align-items:flex-start;gap:8px;font-size:12px}}
.change-badge{{font-size:9px;padding:2px 6px;border-radius:3px;font-weight:700;border:1px solid;white-space:nowrap;flex-shrink:0;margin-top:1px}}
.change-name{{color:var(--t1);font-weight:500}}.change-desc{{color:var(--t3);margin-top:2px;font-size:11px}}
.empty{{padding:36px;text-align:center;color:var(--t3)}}.empty span{{font-size:28px;display:block;margin-bottom:8px;opacity:.4}}.empty p{{font-size:12px}}
.sb{{padding:6px 12px;font-size:11px;color:var(--t3);border-top:1px solid var(--bd);display:flex;justify-content:space-between;align-items:center}}
.sb a{{color:#34A853;text-decoration:none;font-size:11px}}.sb a:hover{{text-decoration:underline}}
.ana-summary{{padding:10px 16px;border-bottom:1px solid var(--bd);display:flex;gap:12px;flex-wrap:wrap;background:var(--s2)}}
.ana-summary-item{{display:flex;align-items:center;gap:6px;font-size:11px}}
.ana-cat{{font-weight:700;color:var(--t1)}}
.ana-range{{color:var(--t3);font-family:var(--mono)}}
.ana-avg{{color:var(--blue);font-family:var(--mono);font-weight:600}}
.ana-cnt{{color:var(--t3)}}
.ana-row{{padding:10px 16px;border-bottom:1px solid rgba(42,53,80,.4);display:flex;flex-direction:column;gap:4px}}
.ana-left{{display:flex;align-items:center;gap:8px}}
.ana-badge{{font-size:9px;padding:2px 7px;border-radius:3px;font-weight:700;white-space:nowrap}}
.ana-name{{font-size:12px;color:var(--t1);font-weight:500}}
.ana-price{{font-family:var(--mono);font-size:12px;color:var(--t2)}}
.ana-comment{{font-size:11px;color:var(--t3);margin-left:4px}}
::-webkit-scrollbar{{width:4px;height:4px}}::-webkit-scrollbar-thumb{{background:var(--bd);border-radius:2px}}
</style>
</head>
<body>
<div class="wrap">
  <div class="hdr">
    <div class="hdr-l">
      <div class="logo">🅿</div>
      <div><h1>CPBC 주변 주차장 현황</h1><p>삼일대로 330 기준 반경 800m · 모두의주차 데이터</p></div>
    </div>
    <div class="hdr-r">
      <span class="badge">{now_str}</span>
      <a href="{sheets_url}" target="_blank" class="badge sheets">📊 Sheets 이력</a>
      <span class="badge ok">최신</span>
    </div>
  </div>
  <div class="kpi">
    <div class="kcard b"><div class="kl">주차장 수</div><div class="kv b">{len(lots)}</div><div class="ks">반경 800m 이내</div></div>
    <div class="kcard g"><div class="kl">파트너</div><div class="kv g">{partners}</div><div class="ks">앱 결제 가능</div></div>
    <div class="kcard y"><div class="kl">할인권 보유</div><div class="kv y">{with_tick}</div><div class="ks">개 주차장</div></div>
    <div class="kcard r"><div class="kl">최저 1시간</div><div class="kv r">{f"{min_p['p60']:,}원" if min_p else "-"}</div><div class="ks">{min_p['name'][:12] if min_p else "-"}</div></div>
    <div class="kcard p"><div class="kl">누적 기록</div><div class="kv p">{snap_count}</div><div class="ks">회 ({first_ts[:10]}~)</div></div>
  </div>
  <div class="grid">
    <div class="panel" id="pp">
      <div class="ph"><div class="pt"><span class="dot b"></span>요금 현황</div><span class="pc" id="p-cnt"></span></div>
      <div class="fb">
        <button class="fb-btn on" onclick="pF('all',this)">전체</button>
        <button class="fb-btn" onclick="pF('partner',this)">파트너</button>
        <button class="fb-btn" onclick="pF('cheap',this)">저렴 ≤4천</button>
        <input class="fb-search" id="ps" placeholder="🔍 검색..." oninput="rP()">
      </div>
      <div class="sw">
        <span class="sl">거리</span>
        <input type="range" id="pd" min="100" max="800" step="50" value="800" oninput="document.getElementById('pdv').textContent=this.value+'m';rP()">
        <span class="sv" id="pdv">800m</span>
      </div>
      <div class="tw"><table>
        <thead><tr>
          <th id="ph0" onclick="sP('name')">주차장명</th>
          <th id="ph1" onclick="sP('dist')">거리</th>
          <th id="ph2" onclick="sP('p30')">30분</th>
          <th id="ph3" onclick="sP('p60')">1시간</th>
          <th id="ph4" onclick="sP('p120')">2시간</th>
          <th id="ph5" onclick="sP('p180')">3시간</th>
        </tr></thead>
        <tbody id="pb"></tbody>
      </table></div>
      <div class="sb"><span id="p-st"></span><a href="{sheets_url}" target="_blank">📊 전체 이력 →</a></div>
    </div>
    <div class="panel" id="tp">
      <div class="ph"><div class="pt"><span class="dot g"></span>할인권·선불권</div><span class="pc" id="t-cnt"></span></div>
      <div class="fb">
        <button class="fb-btn on" onclick="tTglOpen(this)">판매중만</button>
        <div style="width:1px;height:12px;background:var(--bd);margin:0 4px;"></div>
        <button class="fb-btn type-btn on" onclick="tF('all',this)">전체</button>
        <button class="fb-btn type-btn" onclick="tF('night',this)">심야</button>
        <button class="fb-btn type-btn" onclick="tF('day',this)">당일</button>
        <button class="fb-btn type-btn" onclick="tF('hour',this)">시간권</button>
        <button class="fb-btn type-btn" onclick="tF('month',this)">월정기</button>
        <input class="fb-search" id="ts" placeholder="🔍 검색..." oninput="rT()">
      </div>
      <div class="sw">
        <span class="sl">거리</span>
        <input type="range" id="td" min="100" max="800" step="50" value="800" oninput="document.getElementById('tdv').textContent=this.value+'m';rT()">
        <span class="sv" id="tdv">800m</span>
      </div>
      <div class="tw"><table>
        <thead><tr>
          <th id="th0" onclick="sT('lot')">주차장명</th>
          <th id="th1" onclick="sT('dist')">거리</th>
          <th id="th2" onclick="sT('name')">권종명</th>
          <th id="th3" onclick="sT('price')">가격</th>
          <th>이용시간대</th>
          <th>상태</th>
        </tr></thead>
        <tbody id="tb"></tbody>
      </table></div>
      <div class="sb"><span id="t-st"></span><a href="{sheets_url}" target="_blank">📊 이력 →</a></div>
    </div>
    <div class="panel full">
      <div class="ph"><div class="pt"><span class="dot y"></span>변경사항 감지</div><span class="pc">{{change_count}}</span></div>
      {{change_html}}
      <div class="sb"><span>누적 {{snap_count}}회 기록 ({{first_ts[:10]}}~)</span><a href="{{sheets_url}}" target="_blank">📊 변경이력 전체 →</a></div>
    </div>
    <div class="panel full">
      <div class="ph">
        <div class="pt"><span class="dot" style="background:#8B5CF6"></span>할인권 적정성 분석</div>
        <span class="pc" style="color:#A78BFA">500m 이내 파트너 기준</span>
      </div>
      <div class="ana-summary">
        <span style="font-size:11px;color:var(--t3);margin-right:4px">주변 평균가</span>
        {{summary_html}}
      </div>
      <div>{{analysis_rows}}</div>
      <div class="sb"><span>평화빌딩 주차장 할인권 적정성 검토</span><span style="color:var(--t3)">±20% 기준 판단</span></div>
    </div>
  </div>
</div>
<script>
const LOTS={lots_json};
const TICKETS={tickets_json};
let pFlt='all',pSK='p60',pSA=true;
let tFlt='all',tOpenOnly=true,tSK='dist',tSA=true;
function fp(v){{if(v==null)return'-';if(v===0)return'무료';return v.toLocaleString()+'원';}}
function pc(v){{if(v==null)return'';if(v===0)return'free';if(v<=3000)return'low';if(v<=6000)return'mid';return'high';}}
function pF(f,el){{pFlt=f;document.querySelectorAll('#pp .fb-btn').forEach(b=>b.classList.remove('on'));el.classList.add('on');rP();}}
function sP(k){{if(pSK===k)pSA=!pSA;else{{pSK=k;pSA=true;}}rP();}}
function rP(){{
  const s=document.getElementById('ps').value.toLowerCase();
  const md=+document.getElementById('pd').value;
  let d=LOTS.filter(l=>{{if(l.dist>md)return false;if(pFlt==='partner'&&!l.partner)return false;if(pFlt==='cheap'&&(l.p60==null||l.p60>4000))return false;if(s&&!l.name.toLowerCase().includes(s))return false;return true;}});
  d.sort((a,b)=>{{let av=a[pSK],bv=b[pSK];if(av==null)av=99999;if(bv==null)bv=99999;if(typeof av==='string')return pSA?av.localeCompare(bv):bv.localeCompare(av);return pSA?av-bv:bv-av;}});
  const km={{name:0,dist:1,p30:2,p60:3,p120:4,p180:5}};
  [0,1,2,3,4,5].forEach(i=>{{const th=document.getElementById('ph'+i);th.className='';if(km[pSK]===i)th.className=pSA?'sa':'sd';}});
  document.getElementById('p-cnt').textContent=d.length+'개소';
  document.getElementById('p-st').textContent=`전체 ${{LOTS.length}}개 중 ${{d.length}}개`;
  const tb=document.getElementById('pb');
  if(!d.length){{tb.innerHTML='<tr><td colspan="6" style="text-align:center;padding:24px;color:var(--t3)">없음</td></tr>';return;}}
  tb.innerHTML=d.map(l=>`<tr><td class="nc">${{l.name}}${{l.partner?' <span class="tag partner">파트너</span>':''}}</td><td><span class="dist">${{l.dist}}m</span></td><td class="p ${{pc(l.p30)}}">${{fp(l.p30)}}</td><td class="p ${{pc(l.p60)}}">${{fp(l.p60)}}</td><td class="p ${{pc(l.p120)}}">${{fp(l.p120)}}</td><td class="p ${{pc(l.p180)}}">${{fp(l.p180)}}</td></tr>`).join('');
}}
function tTglOpen(el){{tOpenOnly=!tOpenOnly;if(tOpenOnly)el.classList.add('on');else el.classList.remove('on');rT();}}
function tF(f,el){{tFlt=f;document.querySelectorAll('#tp .type-btn').forEach(b=>b.classList.remove('on'));el.classList.add('on');rT();}}
function sT(k){{if(tSK===k)tSA=!tSA;else{{tSK=k;tSA=true;}}rT();}}
function rT(){{
  const s=document.getElementById('ts').value.toLowerCase();
  const md=+document.getElementById('td').value;
  let d=TICKETS.filter(t=>{{if(t.dist>md)return false;if(tOpenOnly&&(!t.open||t.soldout))return false;if(tFlt==='night'&&!t.name.includes('심야'))return false;if(tFlt==='day'&&!t.name.includes('당일'))return false;if(tFlt==='hour'&&!/(시간|h)/i.test(t.name))return false;if(tFlt==='month'&&!t.name.includes('월'))return false;if(s&&!t.lot.toLowerCase().includes(s)&&!t.name.toLowerCase().includes(s))return false;return true;}});
  d.sort((a,b)=>{{let av=a[tSK],bv=b[tSK];if(typeof av==='string')return tSA?av.localeCompare(bv):bv.localeCompare(av);return tSA?av-bv:bv-av;}});
  const km={{lot:0,dist:1,name:2,price:3}};
  [0,1,2,3].forEach(i=>{{const th=document.getElementById('th'+i);th.className='';if(km[tSK]===i)th.className=tSA?'sa':'sd';}});
  document.getElementById('t-cnt').textContent=d.length+'개';
  document.getElementById('t-st').textContent=`전체 ${{TICKETS.length}}개 중 ${{d.length}}개`;
  const tb=document.getElementById('tb');
  if(!d.length){{tb.innerHTML='<tr><td colspan="6" style="text-align:center;padding:24px;color:var(--t3)">없음</td></tr>';return;}}
  tb.innerHTML=d.map(t=>{{const st=t.soldout?'<span class="tag soldout">품절</span>':t.open?'<span class="tag open">판매중</span>':'<span class="tag closed">비판매</span>';return`<tr class="${{(!t.open||t.soldout)?'rdim':''}}"><td class="nc">${{t.lot}}${{t.partner?' <span class="tag partner">파트너</span>':''}}</td><td><span class="dist">${{t.dist}}m</span></td><td>${{t.name}}</td><td class="p mid">${{t.price.toLocaleString()}}원</td><td class="tc">${{t.time}}</td><td>${{st}}</td></tr>`;}}).join('');
}}
rP();rT();
</script>
</body>
</html>"""


# ── 메인 ────────────────────────────────────────────────────
if __name__ == "__main__":
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_str}] 모두의주차 API 수집 중...")

    raw = fetch_api()
    lots, tickets = parse(raw)
    print(f"  주차장 {len(lots)}개 / 할인권 {len(tickets)}개 수집")

    old_snap = load_snap()
    changes  = compare(old_snap, lots, tickets)

    # 스냅샷 이력 관리 (최대 365개)
    hist_file = os.path.join(BASE_DIR, "history.json")
    if os.path.exists(hist_file):
        with open(hist_file, "r", encoding="utf-8") as f:
            snap_history = json.load(f)
    else:
        snap_history = []

    snap_history.append({"ts": now_str})
    if len(snap_history) > 365:
        snap_history = snap_history[-365:]
    with open(hist_file, "w", encoding="utf-8") as f:
        json.dump(snap_history, f, ensure_ascii=False)

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

    print("할인권 적정성 분석 중...")
    analysis, summary, avgs = analyze_tickets(tickets)
    for a in analysis:
        print(f"  [{a['label']}] {a['name']} {a['price']:,}원 - {a['comment']}")

    print("대시보드 HTML 생성 중...")
    html = build_html(lots, tickets, changes, snap_history, now_str, SHEET_ID, analysis, summary)
    html_path = os.path.join(BASE_DIR, "modu_dashboard.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  HTML 생성 완료: {html_path}")
    print("완료!")
