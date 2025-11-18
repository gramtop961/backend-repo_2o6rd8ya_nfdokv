import os
import json
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr

from database import create_document, get_documents, db
from schemas import Lead as LeadSchema, Product as ProductSchema, Order as OrderSchema

app = FastAPI(title="Perfume AI Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Utility: Google Sheets Integration ----------

def append_lead_to_google_sheets(lead: LeadSchema) -> Optional[str]:
    spreadsheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")
    service_account_json = os.getenv("GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON")
    sheet_range = os.getenv("GOOGLE_SHEETS_LEADS_RANGE", "Leads!A:F")

    if not spreadsheet_id or not service_account_json:
        return None  # Not configured

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except Exception:
        # Google libs not installed
        return None

    try:
        # Service account JSON can be JSON string or base64 encoded string
        try:
            # Try direct JSON first
            info = json.loads(service_account_json)
        except json.JSONDecodeError:
            import base64
            decoded = base64.b64decode(service_account_json).decode("utf-8")
            info = json.loads(decoded)

        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        credentials = service_account.Credentials.from_service_account_info(info, scopes=scopes)
        service = build("sheets", "v4", credentials=credentials)

        values = [[
            lead.name,
            lead.email,
            lead.phone or "",
            lead.interest or "",
            lead.source or "website",
            lead.notes or "",
        ]]
        body = {"values": values}
        result = service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=sheet_range,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": values}
        ).execute()
        updates = result.get("updates", {})
        return str(updates.get("updatedRange"))
    except Exception:
        return None

# ---------- Models for requests ----------

class LeadIn(BaseModel):
    name: str
    email: EmailStr
    phone: Optional[str] = None
    interest: Optional[str] = None
    source: Optional[str] = "website"
    notes: Optional[str] = None

class CheckoutItem(BaseModel):
    product_id: str
    quantity: int = 1

class CheckoutRequest(BaseModel):
    items: List[CheckoutItem]
    customer_email: Optional[EmailStr] = None

# ---------- Seed Data ----------

def ensure_sample_products():
    try:
        existing = get_documents("product", {}, limit=1)
        if existing:
            return
    except Exception:
        return

    samples = [
        ProductSchema(title="Citrus Bloom", description="Fresh citrus with floral heart.", price_cents=5900, currency="usd", image="https://images.unsplash.com/photo-1608571424053-c7c3a1e3a1cc?q=80&w=1600&auto=format&fit=crop", tags=["citrus","daytime"]),
        ProductSchema(title="Amber Dusk", description="Warm amber and vanilla.", price_cents=7200, currency="usd", image="https://images.unsplash.com/photo-1541643600914-78b084683601?q=80&w=1600&auto=format&fit=crop", tags=["amber","evening"]),
        ProductSchema(title="Verdant Mist", description="Green notes with a dewy finish.", price_cents=6400, currency="usd", image="https://images.unsplash.com/photo-1600180758890-6b94519a8ba6?q=80&w=1600&auto=format&fit=crop", tags=["green","unisex"]),
    ]
    for s in samples:
        try:
            create_document("product", s)
        except Exception:
            pass

# ---------- Routes ----------

@app.get("/")
def root():
    return {"message": "Perfume AI Agent Backend Running"}

@app.get("/api/products")
def list_products():
    ensure_sample_products()
    prods = get_documents("product")
    # Convert ObjectId to string if present
    for p in prods:
        if "_id" in p:
            p["id"] = str(p.pop("_id"))
    return {"products": prods}

@app.post("/api/leads")
def create_lead(lead: LeadIn):
    lead_doc = LeadSchema(**lead.model_dump())
    try:
        inserted_id = create_document("lead", lead_doc)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

    # Try to sync to Google Sheets (best-effort)
    _range = append_lead_to_google_sheets(lead_doc)

    return {"status": "ok", "id": inserted_id, "google_range": _range}

@app.post("/api/checkout/create-session")
def create_checkout_session(payload: CheckoutRequest):
    # Fetch products and compute total
    if not payload.items:
        raise HTTPException(status_code=400, detail="No items provided")

    # Stripe integration if available
    stripe_key = os.getenv("STRIPE_SECRET_KEY")

    # Build line items for Stripe and compute order total
    line_items = []
    amount_total = 0

    # Build a map of product by id
    products = get_documents("product")
    by_id = {}
    for p in products:
        pid = str(p.get("_id"))
        by_id[pid] = p

    for it in payload.items:
        prod = by_id.get(it.product_id)
        if not prod:
            raise HTTPException(status_code=404, detail=f"Product not found: {it.product_id}")
        qty = max(1, it.quantity)
        price_cents = int(prod.get("price_cents") or 0)
        amount_total += price_cents * qty
        line_items.append({
            "name": prod.get("title"),
            "amount": price_cents,
            "currency": prod.get("currency", "usd"),
            "quantity": qty,
        })

    # Create order record
    order = OrderSchema(
        product_id=payload.items[0].product_id,
        quantity=sum([it.quantity for it in payload.items]),
        amount_total_cents=amount_total,
        customer_email=payload.customer_email,
        status="created",
    )
    order_id = create_document("order", order)

    # If Stripe is configured, create a Checkout Session using legacy/simple params
    if stripe_key:
        try:
            import stripe
            stripe.api_key = stripe_key
            success_url = os.getenv("CHECKOUT_SUCCESS_URL", "https://example.com/success")
            cancel_url = os.getenv("CHECKOUT_CANCEL_URL", "https://example.com/cancel")

            # Convert to Stripe line_items format using Prices creation-in-line (for quick demo)
            stripe_line_items = []
            for it in payload.items:
                prod = by_id[it.product_id]
                stripe_line_items.append({
                    "price_data": {
                        "currency": prod.get("currency", "usd"),
                        "product_data": {"name": prod.get("title")},
                        "unit_amount": int(prod.get("price_cents") or 0)
                    },
                    "quantity": max(1, it.quantity)
                })

            session = stripe.checkout.Session.create(
                mode="payment",
                line_items=stripe_line_items,
                success_url=success_url + f"?order_id={order_id}",
                cancel_url=cancel_url + f"?order_id={order_id}",
                customer_email=payload.customer_email,
            )
            return {"checkout_url": session.url, "order_id": order_id}
        except Exception as e:
            # Fallback to mock URL if Stripe fails
            pass

    # Fallback: return a mock checkout URL (client can handle as test mode)
    return {
        "checkout_url": f"https://checkout.mock/{order_id}",
        "order_id": order_id,
        "note": "Stripe not configured; using mock checkout URL. Set STRIPE_SECRET_KEY to enable real payments."
    }

@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = getattr(db, 'name', None) or "Unknown"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️ Connected but Error: {str(e)[:80]}"
        else:
            response["database"] = "⚠️ Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:80]}"

    return response


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
