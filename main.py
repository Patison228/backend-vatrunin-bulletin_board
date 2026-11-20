import time
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Query, Depends, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, ForeignKey, func, DateTime
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship
from dotenv import load_dotenv

from jose import jwt, JWTError
from passlib.context import CryptContext

from schemas import (
    UserCreate, UserOut, Token,
    AdResponse, AdCreate, AdUpdate,
    ResponseCreate, ResponseOut, AdStats,
)

load_dotenv()


DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL не задан в .env")

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin-test-pass")


engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    role = Column(String, default="user", nullable=False)


class Ad(Base):
    __tablename__ = "ads"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False, index=True)
    price = Column(Float, nullable=False)
    status = Column(String, default="active")
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    responses = relationship("Response", back_populates="ad", cascade="all, delete-orphan")


class Response(Base):
    __tablename__ = "responses"
    id = Column(Integer, primary_key=True, index=True)
    ad_id = Column(Integer, ForeignKey("ads.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    message = Column(String, default="")
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    ad = relationship("Ad", back_populates="responses")


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False, index=True)
    failed_count = Column(Integer, default=0, nullable=False)
    window_start = Column(DateTime(timezone=True), nullable=False)
    last_attempt = Column(DateTime(timezone=True), nullable=False)

Base.metadata.create_all(bind=engine)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(plain, hashed)
    except Exception:
        return False


def create_access_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "iat": now,
        "exp": now + timedelta(minutes=JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


app = FastAPI(title="Доска объявлений")

bearer_scheme = HTTPBearer(auto_error=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:

    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    token = credentials.credentials

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    
    user_id = payload.get("sub")

    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    user = db.query(User).filter(User.id == int(user_id)).first()

    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def require_owner_or_admin(obj, user: User):
    if user.role == "admin":
        return
    
    if obj.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Forbidden")


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


@app.on_event("startup")
def ensure_admin():
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == ADMIN_USERNAME).first()
        if not existing:
            admin = User(
                username=ADMIN_USERNAME,
                password_hash=hash_password(ADMIN_PASSWORD),
                role="admin",
            )
            db.add(admin)
            db.commit()
            print(f"[startup] admin '{ADMIN_USERNAME}' создан")
    finally:
        db.close()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/auth/register", status_code=201, response_model=UserOut)
def register(data: UserCreate, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.username == data.username).first()

    if existing:
        raise HTTPException(status_code=409, detail="Username already exists")
    
    user = User(
        username=data.username,
        password_hash=hash_password(data.password),
        role="user",
    )

    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.post("/auth/login", response_model=Token)
def login(data: UserCreate, db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    window = timedelta(minutes=1)

    attempt = (db.query(LoginAttempt).filter(LoginAttempt.username == data.username).with_for_update().first())

    if attempt is not None:

        if now - attempt.window_start > window:
            attempt.failed_count = 0
            attempt.window_start = now

        if attempt.failed_count >= 5:
            db.commit()
            raise HTTPException(status_code=429, detail="Too many login attempts")

    user = db.query(User).filter(User.username == data.username).first()
    password_ok = user is not None and verify_password(data.password, user.password_hash)

    if not password_ok:
        if attempt is None:
            attempt = LoginAttempt(
                username=data.username,
                failed_count=1,
                window_start=now,
                last_attempt=now,
            )
            db.add(attempt)
        else:
            attempt.failed_count += 1
            attempt.last_attempt = now
        db.commit()

        if attempt.failed_count >= 5:
            raise HTTPException(status_code=429, detail="Too many login attempts")
        raise HTTPException(status_code=401, detail="Invalid credentials")


    if attempt is not None:
        db.delete(attempt)
        db.commit()

    return Token(access_token=create_access_token(user))


@app.get("/auth/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user


@app.post("/api/ads", status_code=201, response_model=AdResponse)
def create_ad(
    data: AdCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ad = Ad(
        title=data.title,
        price=data.price,
        status="active",
        owner_id=current_user.id,  
    )
    db.add(ad)
    db.commit()
    db.refresh(ad)
    return ad


@app.get("/api/ads", response_model=list[AdResponse])
def get_ads(
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: Optional[str] = None,
    q: Optional[str] = None,
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
            func.coalesce(func.sum(Ad.price), 0).label("total_price"),
            func.coalesce(func.avg(Ad.price), 0).label("avg_price"),
        )
        .outerjoin(Response, Ad.id == Response.ad_id)
        .group_by(Ad.id, Ad.title)
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
def update_ad(
    ad_id: int,
    data: AdUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ad = db.query(Ad).filter(Ad.id == ad_id).first()
    if not ad:
        raise HTTPException(status_code=404, detail="Ad not found")
    
    require_owner_or_admin(ad, current_user)

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(ad, key, value)
    db.commit()
    db.refresh(ad)
    return ad


@app.delete("/api/ads/{ad_id}", status_code=204)
def delete_ad(
    ad_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ad = db.query(Ad).filter(Ad.id == ad_id).first()
    if not ad:
        raise HTTPException(status_code=404, detail="Ad not found")
    require_owner_or_admin(ad, current_user)
    db.delete(ad)
    db.commit()


@app.post("/api/ads/{ad_id}/responses", status_code=201, response_model=ResponseOut)
def create_response(
    ad_id: int,
    data: ResponseCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
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
            message=data.message,
            owner_id=current_user.id,  # ставит сервер
        )
        db.add(resp)
        db.commit()
        db.refresh(resp)
        return resp
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise e


@app.get("/api/ads/{ad_id}/responses", response_model=list[ResponseOut])
def get_responses(ad_id: int, db: Session = Depends(get_db)):
    ad = db.query(Ad).filter(Ad.id == ad_id).first()
    if not ad:
        raise HTTPException(status_code=404, detail="Ad not found")
    return db.query(Response).filter(Response.ad_id == ad_id).all()


@app.get("/api/stats", response_model=list[AdStats])
def legacy_stats(db: Session = Depends(get_db)):
    return get_stats(db)