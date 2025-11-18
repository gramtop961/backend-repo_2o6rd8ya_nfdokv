"""
Database Schemas

Define your MongoDB collection schemas here using Pydantic models.
Each Pydantic model represents a collection in your database.
Model name is converted to lowercase for the collection name:
- User -> "user" collection
- Product -> "product" collection
- Lead -> "lead" collection
- Order -> "order" collection
"""

from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List
from datetime import datetime

class Lead(BaseModel):
    """
    Leads captured from the perfume site
    Collection: lead
    """
    name: str = Field(..., description="Full name")
    email: EmailStr = Field(..., description="Email address")
    phone: Optional[str] = Field(None, description="Phone number")
    interest: Optional[str] = Field(None, description="What they're looking for")
    source: Optional[str] = Field("website", description="Lead source")
    notes: Optional[str] = Field(None, description="Additional notes")

class Product(BaseModel):
    """
    Perfume products for sale
    Collection: product
    """
    title: str = Field(..., description="Product title")
    description: Optional[str] = Field(None, description="Product description")
    price_cents: int = Field(..., ge=0, description="Price in cents")
    currency: str = Field("usd", description="ISO currency code")
    image: Optional[str] = Field(None, description="Product image URL")
    in_stock: bool = Field(True, description="In stock flag")
    tags: Optional[List[str]] = Field(default=None, description="Product tags")

class Order(BaseModel):
    """
    Orders initiated via checkout
    Collection: order
    """
    product_id: str = Field(..., description="Product ObjectId as string")
    quantity: int = Field(1, ge=1, description="Quantity")
    amount_total_cents: int = Field(..., ge=0, description="Total in cents")
    currency: str = Field("usd")
    status: str = Field("created", description="created|paid|canceled|failed")
    customer_email: Optional[EmailStr] = None
    checkout_session_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
