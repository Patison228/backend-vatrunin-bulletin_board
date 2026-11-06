import time
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from schemas import AdResponse, AdCreate, AdUpdate, ResponseCreate, ResponseOut

app = FastAPI(title="Доска объявлений")

ads_db = []
responses_db = []
ad_next_id = 1
resp_next_id = 1

def get_next_ad_id() -> int:
    global ad_next_id
    current = ad_next_id
    ad_next_id += 1
    return current

def get_next_resp_id() -> int:
    global resp_next_id
    current = resp_next_id
    resp_next_id += 1
    return current


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
def create_ad(data: AdCreate):
    ad = {
        "id": get_next_ad_id(),
        "title": data.title,
        "price": data.price,
        "status": "active" 
    }
    ads_db.append(ad)
    return ad


@app.get("/api/ads", response_model=list[AdResponse])
def get_ads(
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: str | None = None,
):
    items = ads_db
    if status is not None:
        items = [a for a in items if a["status"] == status]

    return items[offset: offset + limit]


@app.get("/api/ads/{ad_id}", response_model=AdResponse)
def get_ad(ad_id: int):
    for a in ads_db:
        if a["id"] == ad_id:
            return a
        
    raise HTTPException(status_code=404, detail="Ad not found")


@app.patch("/api/ads/{ad_id}", response_model=AdResponse)
def update_ad(ad_id: int, data: AdUpdate):
    for a in ads_db:
        if a["id"] == ad_id:
            update_data = data.model_dump(exclude_unset=True)
            a.update(update_data)
            return a
        
    raise HTTPException(status_code=404, detail="Ad not found")


@app.delete("/api/ads/{ad_id}", status_code=204)
def delete_ad(ad_id: int):
    global responses_db
    for i, a in enumerate(ads_db):
        if a["id"] == ad_id:
            ads_db.pop(i)
            responses_db = [r for r in responses_db if r["ad_id"] != ad_id]
            return None
        
    raise HTTPException(status_code=404, detail="Ad not found")


@app.post("/api/ads/{ad_id}/responses", status_code=201, response_model=ResponseOut)
def create_response(ad_id: int, data: ResponseCreate):
    target_ad = None
    for a in ads_db:
        if a["id"] == ad_id:
            target_ad = a
            break
            
    if not target_ad:
        raise HTTPException(status_code=404, detail="Ad not found")
        
    if target_ad["status"] == "sold":
        raise HTTPException(status_code=409, detail="Cannot respond to a sold ad")
        
    resp = {
        "id": get_next_resp_id(),
        "ad_id": ad_id,
        "name": data.name,
        "phone": data.phone,
        "message": data.message
    }
    responses_db.append(resp)
    return resp


@app.get("/api/ads/{ad_id}/responses", response_model=list[ResponseOut])
def get_responses(ad_id: int):

    if not any(a["id"] == ad_id for a in ads_db):
        raise HTTPException(status_code=404, detail="Ad not found")
        
    return [r for r in responses_db if r["ad_id"] == ad_id]


@app.get("/api/stats")
def get_stats():
    stats = []

    for ad in ads_db:
        count = sum(1 for r in responses_db if r["ad_id"] == ad["id"])
        stats.append({"ad_id": ad["id"], "responses_count": count})

    return stats