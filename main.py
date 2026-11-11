import os

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

# 로컬에선 .env를 읽고, Railway에선 Variables가 이미 들어 있음
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SECRET_KEY = os.environ["SUPABASE_SECRET_KEY"]
FRONT_ORIGINS = [
    o.strip() for o in os.getenv("FRONT_ORIGINS", "").split(",") if o.strip()
]

sb = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)

app = FastAPI(title="my-api")

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONT_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


def get_session_id(x_session_id: str = Header(...)) -> str:
    """브라우저가 만든 익명 장바구니 ID (로그인 없이 장바구니 구분용)"""
    if not 8 <= len(x_session_id) <= 64:
        raise HTTPException(status_code=400, detail="invalid session id")
    return x_session_id


def db_call(fn):
    try:
        return fn()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"database error: {type(e).__name__}")


def cart_response(session_id: str):
    res = db_call(
        lambda: sb.table("cart_items")
        .select("id, quantity, product:products(id, name, price, emoji)")
        .eq("session_id", session_id)
        .order("id")
        .execute()
    )
    items = res.data
    total = sum(i["quantity"] * i["product"]["price"] for i in items)
    return {"items": items, "total": total, "count": sum(i["quantity"] for i in items)}


class AddItem(BaseModel):
    product_id: int
    quantity: int = Field(default=1, ge=1, le=99)


class SetQuantity(BaseModel):
    quantity: int = Field(ge=0, le=99)


@app.get("/products")
def list_products():
    res = db_call(lambda: sb.table("products").select("*").order("id").execute())
    return res.data


@app.get("/cart")
def get_cart(session_id: str = Depends(get_session_id)):
    return cart_response(session_id)


@app.post("/cart")
def add_to_cart(body: AddItem, session_id: str = Depends(get_session_id)):
    exist = db_call(
        lambda: sb.table("cart_items").select("id, quantity")
        .eq("session_id", session_id).eq("product_id", body.product_id).execute()
    ).data
    if exist:
        qty = min(99, exist[0]["quantity"] + body.quantity)
        db_call(lambda: sb.table("cart_items").update({"quantity": qty}).eq("id", exist[0]["id"]).execute())
    else:
        prod = db_call(lambda: sb.table("products").select("id").eq("id", body.product_id).execute()).data
        if not prod:
            raise HTTPException(status_code=404, detail="product not found")
        db_call(
            lambda: sb.table("cart_items")
            .insert({"session_id": session_id, "product_id": body.product_id, "quantity": body.quantity})
            .execute()
        )
    return cart_response(session_id)


@app.patch("/cart/{product_id}")
def set_quantity(product_id: int, body: SetQuantity, session_id: str = Depends(get_session_id)):
    q = sb.table("cart_items")
    if body.quantity == 0:
        db_call(lambda: q.delete().eq("session_id", session_id).eq("product_id", product_id).execute())
    else:
        db_call(
            lambda: q.update({"quantity": body.quantity})
            .eq("session_id", session_id).eq("product_id", product_id).execute()
        )
    return cart_response(session_id)


@app.delete("/cart/{product_id}")
def remove_item(product_id: int, session_id: str = Depends(get_session_id)):
    db_call(
        lambda: sb.table("cart_items").delete()
        .eq("session_id", session_id).eq("product_id", product_id).execute()
    )
    return cart_response(session_id)


@app.delete("/cart")
def clear_cart(session_id: str = Depends(get_session_id)):
    db_call(lambda: sb.table("cart_items").delete().eq("session_id", session_id).execute())
    return cart_response(session_id)


@app.get("/supabase/health")
def supabase_health():
    """Supabase 연결/키 유효성 확인 (데이터는 노출하지 않음)"""
    try:
        sb.auth.admin.list_users(page=1, per_page=1)
        return {"supabase": "connected"}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"supabase error: {type(e).__name__}: {str(e)[:200]}")


# ---------- 한국환경공단 에어코리아 대기오염정보 (공공데이터포털) ----------
import httpx

AIR_URL = "http://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getCtprvnRltmMesureDnsty"


def grade_text(v):
    return {"1": "좋음", "2": "보통", "3": "나쁨", "4": "매우나쁨"}.get(str(v), "정보없음")


def fetch_air(sido: str):
    """에어코리아 원본 응답(items) 조회 (키는 서버에서만 사용)"""
    key = os.getenv("DATA_GO_KR_KEY", "")
    if not key:
        raise HTTPException(status_code=500, detail="DATA_GO_KR_KEY not set")
    params = {
        "serviceKey": key, "returnType": "json", "numOfRows": 100,
        "pageNo": 1, "sidoName": sido, "ver": "1.0",
    }
    try:
        r = httpx.get(AIR_URL, params=params, timeout=10)
        r.raise_for_status()
        body = r.json()["response"]
        header = body["header"]
        if header.get("resultCode") != "00":
            raise HTTPException(status_code=502, detail=f"api error: {header.get('resultMsg')}")
        items = body["body"].get("items", [])
    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"air api http {e.response.status_code}: {e.response.text[:150]}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"air api error: {type(e).__name__}")
    return items


@app.get("/air")
def air(sido: str = "제주"):
    """시도별 실시간 측정소 대기오염 정보"""
    items = fetch_air(sido)
    return [
        {
            "station": i.get("stationName"), "time": i.get("dataTime"),
            "pm10": i.get("pm10Value"), "pm25": i.get("pm25Value"),
            "o3": i.get("o3Value"), "no2": i.get("no2Value"),
            "pm10_grade": grade_text(i.get("pm10Grade")),
            "pm25_grade": grade_text(i.get("pm25Grade")),
        }
        for i in items
    ]


# ---------- 미세먼지 이력 저장(Supabase) + 선형회귀 예측 ----------
import threading
import time as _time
from collections import defaultdict
from datetime import datetime, timedelta

COLLECT_SIDOS = [s.strip() for s in os.getenv("AIR_SIDOS", "제주").split(",") if s.strip()]
COLLECT_TOKEN = os.getenv("COLLECT_TOKEN", "")
TABLE_HINT = "air_readings 테이블이 없습니다. schema.sql의 미세먼지 부분을 Supabase SQL Editor에서 실행하세요."


def to_num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_dt(s):
    """'2026-11-11 24:00' 같은 24시 표기를 다음날 00:00으로 변환"""
    s = str(s)
    if s.endswith("24:00"):
        return (datetime.strptime(s[:10], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d 00:00")
    return s


def collect(sido: str) -> int:
    """현재 측정값을 DB에 저장 (같은 측정소·시각은 덮어써서 중복 없음)"""
    rows = [
        {
            "sido": sido, "station": i.get("stationName"), "data_time": parse_dt(i.get("dataTime")),
            "pm10": to_num(i.get("pm10Value")), "pm25": to_num(i.get("pm25Value")),
            "o3": to_num(i.get("o3Value")), "no2": to_num(i.get("no2Value")),
        }
        for i in fetch_air(sido) if i.get("stationName") and i.get("dataTime")
    ]
    if rows:
        sb.table("air_readings").upsert(rows, on_conflict="sido,station,data_time").execute()
    return len(rows)


def collector_loop():
    while True:
        for s in COLLECT_SIDOS:
            try:
                n = collect(s)
                print(f"[collector] {s}: {n}건 저장")
            except Exception as e:
                print(f"[collector] {s} 실패: {type(e).__name__}: {str(e)[:120]}")
        _time.sleep(1800)  # 30분마다 (데이터는 1시간 단위 갱신, 중복은 자동 제거)


@app.on_event("startup")
def start_collector():
    threading.Thread(target=collector_loop, daemon=True).start()


@app.post("/air/collect")
def air_collect(sido: str = "제주", x_collect_token: str = Header(default="")):
    """수동 저장 (COLLECT_TOKEN을 설정한 경우 헤더 X-Collect-Token 필요)"""
    if COLLECT_TOKEN and x_collect_token != COLLECT_TOKEN:
        raise HTTPException(status_code=401, detail="invalid token")
    try:
        return {"sido": sido, "saved": collect(sido)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=TABLE_HINT + f" ({type(e).__name__})")


def hourly_series(sido: str, hours: int):
    """DB 저장값을 시각별 시도 평균으로 집계 -> [(datetime, pm10, pm25)]"""
    try:
        data = (
            sb.table("air_readings").select("data_time,pm10,pm25").eq("sido", sido)
            .order("data_time", desc=True).limit(5000).execute().data
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=TABLE_HINT + f" ({type(e).__name__})")
    by = defaultdict(lambda: ([], []))
    for r in data:
        t = datetime.fromisoformat(r["data_time"])
        if r["pm10"] is not None:
            by[t][0].append(r["pm10"])
        if r["pm25"] is not None:
            by[t][1].append(r["pm25"])
    out = []
    for t in sorted(by):
        a, b = by[t]
        out.append((t, round(sum(a) / len(a), 1) if a else None, round(sum(b) / len(b), 1) if b else None))
    return out[-hours:]


def linreg(xs, ys):
    """최소제곱 선형회귀 y = a*x + b, 결정계수 r2"""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return 0.0, my, 0.0
    a = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    b = my - a * mx
    sst = sum((y - my) ** 2 for y in ys)
    sse = sum((y - (a * x + b)) ** 2 for x, y in zip(xs, ys))
    return a, b, (1 - sse / sst) if sst else 0.0


@app.get("/air/history")
def air_history(sido: str = "제주", hours: int = 72):
    return [
        {"time": t.strftime("%Y-%m-%d %H:%M"), "pm10": p10, "pm25": p25}
        for t, p10, p25 in hourly_series(sido, min(max(hours, 1), 720))
    ]


@app.get("/air/predict")
def air_predict(sido: str = "제주", hours: int = 72, ahead: int = 6):
    """저장된 시간별 평균에 선형회귀를 적용해 앞으로 ahead시간을 예측"""
    ahead = min(max(ahead, 1), 24)
    series = hourly_series(sido, min(max(hours, 3), 720))
    result = {"sido": sido, "points": len(series), "predictions": {}}
    for key, idx in (("pm10", 1), ("pm25", 2)):
        pts = [(t, row[idx]) for row in series for t in [row[0]] if row[idx] is not None]
        if len(pts) < 3:
            result["predictions"][key] = None
            continue
        t0 = pts[0][0]
        xs = [(t - t0).total_seconds() / 3600 for t, _ in pts]
        ys = [v for _, v in pts]
        a, b, r2 = linreg(xs, ys)
        last = pts[-1][0]
        preds = []
        for h in range(1, ahead + 1):
            tt = last + timedelta(hours=h)
            x = (tt - t0).total_seconds() / 3600
            preds.append({"time": tt.strftime("%Y-%m-%d %H:%M"), "value": round(max(0.0, a * x + b), 1)})
        result["predictions"][key] = {
            "slope_per_hour": round(a, 3), "r2": round(r2, 3), "forecast": preds,
        }
    if all(v is None for v in result["predictions"].values()):
        result["message"] = "예측에는 시간별 데이터가 3개 이상 필요합니다. 데이터가 쌓이면 자동으로 계산됩니다."
    return result
