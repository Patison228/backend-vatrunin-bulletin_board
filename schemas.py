from pydantic import BaseModel, Field
from typing import Optional, Literal

class AdCreate(BaseModel):
    title: str = Field(..., min_length=1)
    price: float = Field(..., gt=0)

class AdUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1)
    price: Optional[float] = Field(None, gt=0)
    status: Optional[Literal["active", "sold"]] = None 

class AdResponse(BaseModel):
    id: int
    title: str
    price: float
    status: str

    class Config:
        from_attributes = True 

class ResponseCreate(BaseModel):
    name: str = Field(..., min_length=1)
    phone: str = Field(..., min_length=1) 
    message: str = Field(default="")

class ResponseOut(BaseModel):
    id: int
    ad_id: int
    name: str
    phone: str
    message: str

    class Config:
        from_attributes = True

class AdStats(BaseModel):
    ad_id: int
    title: str
    responses_count: int
    total_price: float
    avg_price: float