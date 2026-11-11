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
