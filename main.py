import time
import os
from fastapi import FastAPI, HTTPException, Request, Query, Depends
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey, func
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship
from dotenv import load_dotenv

from schemas import AdResponse, AdCreate, AdUpdate, ResponseCreate, ResponseOut, AdStats

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL не задан в .env")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Ad(Base):
    __tablename__ = "ads"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False, index=True) 
    price = Column(Float, nullable=False)
    status = Column(String, default="active")
    responses = relationship("Response", back_populates="ad", cascade="all, delete-orphan")

class Response(Base):
    __tablename__ = "responses"
    id = Column(Integer, primary_key=True, index=True)
    ad_id = Column(Integer, ForeignKey("ads.id", ondelete="CASCADE"), nullable=False, index=True) 
    name = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    message = Column(String, default="")
    ad = relationship("Ad", back_populates="responses")


Base.metadata.create_all(bind=engine)

app = FastAPI(title="Доска объявлений")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    elapsed_ms = (time.time() - start) * 1000
    print(f"{request.method} {request.url.path} {response.status_code} {elapsed_ms:.1f}ms")
    return response

@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    err = exc.errors()[0]
    loc = err["loc"]
    field = str(loc[-1]) if loc else "unknown"
    return JSONResponse(status_code=400, content={"error": err["msg"], "field": field})

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/ads", status_code=201, response_model=AdResponse)
def create_ad(data: AdCreate, db: Session = Depends(get_db)):
    ad = Ad(title=data.title, price=data.price, status="active")
    db.add(ad)
    db.commit()
    db.refresh(ad)
    return ad

@app.get("/api/ads", response_model=list[AdResponse])
def get_ads(
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: str | None = None,
    q: str | None = None, 
    db: Session = Depends(get_db),
):
    query = db.query(Ad)
    if status is not None:
        query = query.filter(Ad.status == status)
    if q:
        query = query.filter(Ad.title.ilike(f"%{q}%"))
    
    return query.offset(offset).limit(limit).all()

@app.get("/api/ads/stats", response_model=list[AdStats])
def get_stats(db: Session = Depends(get_db)):
    stats = (
        db.query(
            Ad.id.label("ad_id"),
            Ad.title,
            func.count(Response.id).label("responses_count"),
            Ad.price.label("total_price"),
            Ad.price.label("avg_price")
        )
        .outerjoin(Response, Ad.id == Response.ad_id)
        .group_by(Ad.id, Ad.title, Ad.price)
        .all()
    )
    return stats

@app.get("/api/ads/{ad_id}", response_model=AdResponse)
def get_ad(ad_id: int, db: Session = Depends(get_db)):
    ad = db.query(Ad).filter(Ad.id == ad_id).first()
    if not ad:
        raise HTTPException(status_code=404, detail="Ad not found")
    return ad

@app.patch("/api/ads/{ad_id}", response_model=AdResponse)
def update_ad(ad_id: int, data: AdUpdate, db: Session = Depends(get_db)):
    ad = db.query(Ad).filter(Ad.id == ad_id).first()
    if not ad:
        raise HTTPException(status_code=404, detail="Ad not found")
    
    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(ad, key, value)
    
    db.commit()
    db.refresh(ad)
    return ad

@app.delete("/api/ads/{ad_id}", status_code=204)
def delete_ad(ad_id: int, db: Session = Depends(get_db)):
    ad = db.query(Ad).filter(Ad.id == ad_id).first()
    if not ad:
        raise HTTPException(status_code=404, detail="Ad not found")
    db.delete(ad)
    db.commit()

@app.post("/api/ads/{ad_id}/responses", status_code=201, response_model=ResponseOut)
def create_response(ad_id: int, data: ResponseCreate, db: Session = Depends(get_db)):
    try:
        ad = db.query(Ad).filter(Ad.id == ad_id).with_for_update().first()
        if not ad:
            raise HTTPException(status_code=404, detail="Ad not found")
        
        if ad.status == "sold":
            raise HTTPException(status_code=409, detail="Cannot respond to a sold ad")
            
        resp = Response(
            ad_id=ad_id,
            name=data.name,
            phone=data.phone,
            message=data.message
        )
        db.add(resp)
        db.commit()
        db.refresh(resp)
        return resp
    except Exception as e:
        db.rollback()
        raise e

@app.get("/api/ads/{ad_id}/responses", response_model=list[ResponseOut])
def get_responses(ad_id: int, db: Session = Depends(get_db)):
    ad = db.query(Ad).filter(Ad.id == ad_id).first()
    if not ad:
        raise HTTPException(status_code=404, detail="Ad not found")
    return db.query(Response).filter(Response.ad_id == ad_id).all()

@app.get("/api/ads/stats", response_model=list[AdStats])
def get_stats(db: Session = Depends(get_db)):
    stats = (
        db.query(
            Ad.id.label("ad_id"),
            Ad.title,
            func.count(Response.id).label("responses_count"),
            Ad.price.label("total_price"), 
            Ad.price.label("avg_price")
        )
        .outerjoin(Response, Ad.id == Response.ad_id)
        .group_by(Ad.id, Ad.title, Ad.price)
        .all()
    )
    return stats