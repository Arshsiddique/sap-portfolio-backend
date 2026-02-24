from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy import func
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from typing import Any, Dict, List

from pydantic import BaseModel, field_validator
import requests
import json

import models
import schemas
from database import get_db, engine

# Creates tables in MySQL automatically on startup
models.Base.metadata.create_all(bind=engine)


def _ensure_symbols_symbol_column():
    insp = inspect(engine)
    if "symbols" not in insp.get_table_names():
        return
    columns = {c["name"] for c in insp.get_columns("symbols")}
    if "symbol" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE symbols ADD COLUMN symbol VARCHAR(100) NULL"))


_ensure_symbols_symbol_column()

app = FastAPI(title="SAP Portfolio Backend", version="1.0.0")


# ─────────────────────────────────────────────
# SIMPLE, CONSISTENT RESPONSE FORMAT
# ─────────────────────────────────────────────
def ok(*, message: str = "Success", data: Any = None) -> Dict[str, Any]:
    return {"success": True, "message": message, "data": data, "errors": None}


def fail(*, message: str = "Request failed", errors: Any = None) -> Dict[str, Any]:
    return {"success": False, "message": message, "data": None, "errors": errors}


def algo_to_dict(a: models.Algo) -> Dict[str, Any]:
    return {"id": a.id, "algoid": a.algoid}


def symbol_to_dict(s: models.Symbol) -> Dict[str, Any]:
    return {
        "id": s.id,
        "algoid": s.algoid,
        "triggerType": s.triggerType,
        "symbolName": s.symbolName,
        "symbol": getattr(s, "symbol", None),
        "assetType": s.assetType,
        "weight": s.weight,
        "marketProtection": s.marketProtection,
    }


class SymbolCreateBody(BaseModel):
    triggerType: str
    symbolName: str
    symbol: str | None = None
    assetType: str
    weight: float
    marketProtection: str

    @field_validator("triggerType", "symbolName", "symbol", "assetType", "marketProtection")
    @classmethod
    def strip_strings(cls, value: str):
        if isinstance(value, str):
            return value.strip()
        return value

    # @field_validator("weight")
    # @classmethod
    # def weight_must_not_exceed_100(cls, value):
    #     if value <= 0:
    #         raise ValueError("Weight must be greater than 0")
    #     if value > 100:
    #         raise ValueError("Weight cannot be more than 100")
    #     return value


# ─────────────────────────────────────────────
# GLOBAL VALIDATION ERROR HANDLER
# ─────────────────────────────────────────────
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = []
    for error in exc.errors():
        loc = error["loc"]
        if loc == ("body",):
            errors.append({
                "field": "body",
                "message": "Request body is missing. Please send JSON data."
            })
        else:
            field = " -> ".join(str(l) for l in loc if l != "body")
            raw_msg = error["msg"].replace("Value error, ", "")
            message = f"'{field}' field is required" if error["type"] == "missing" else raw_msg
            errors.append({"field": field, "message": message})

    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "message": "Validation failed",
            "data": None,
            "errors": errors,
        }
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, (list, dict)):
        content = fail(message="Request failed", errors=detail)
    else:
        content = fail(message=str(detail), errors=None)
    return JSONResponse(status_code=exc.status_code, content=content)


# ─────────────────────────────────────────────
# 1) CREATE ALGO ID
# POST /algos   (alias: POST /algo/create)
# ─────────────────────────────────────────────
@app.get("/algos", status_code=200)
def list_algos(db: Session = Depends(get_db)):
    algos = db.query(models.Algo).order_by(models.Algo.id.asc()).all()
    return ok(
        message="Algos fetched successfully",
        data=[algo_to_dict(a) for a in algos],
    )


@app.post("/algos", status_code=201)
@app.post("/algo/create", status_code=201, include_in_schema=False)
def create_algo(data: schemas.AlgoCreate, db: Session = Depends(get_db)):
    existing = db.query(models.Algo).filter(models.Algo.algoid == data.algoid).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Algo ID '{data.algoid}' already exists")

    default_symbols = [
        {"symbolName": "NIFTY200 MOMENTUM 30", "symbol": "HDFCMOMENT", "assetType": "EQUITY"},
        {"symbolName": "NIFTY MIDSMALLCAP400 MOMENTUM QUALITY 100", "symbol": "MIDSMALL", "assetType": "EQUITY"},
        {"symbolName": "GOLDBEES", "symbol": "GOLDBEES", "assetType": "GOLD"},
        {"symbolName": "SILVERBEES", "symbol": "SILVERBEES", "assetType": "SILVER"},
        {"symbolName": "LIQUIDCASE", "symbol": "LIQUIDCASE", "assetType": "DEBT"},
    ]

    try:
        new_algo = models.Algo(algoid=data.algoid)
        db.add(new_algo)
        db.flush()  # keep everything in one transaction

        symbols = [
            models.Symbol(
                algoid=data.algoid,
                triggerType="LTP_UPDATE",
                symbolName=s["symbolName"],
                symbol=s.get("symbol") or s["symbolName"],
                assetType=s["assetType"],
                weight=10.0,
                marketProtection="0.2",
            )
            for s in default_symbols
        ]
        db.add_all(symbols)
        db.commit()
        db.refresh(new_algo)

        return ok(
            message="Algo ID created successfully",
            data={
                **algo_to_dict(new_algo),
                "default_symbols_added": [symbol_to_dict(s) for s in symbols],
            },
        )
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create algo with default symbols")




# ─────────────────────────────────────────────
# 2) DELETE ALGO ID
# DELETE /algos/{algoid}   (alias: DELETE /algo/{algoid}/delete)
# ─────────────────────────────────────────────
@app.delete("/algos/{algoid}", status_code=200)
@app.delete("/algo/{algoid}/delete", status_code=200, include_in_schema=False)
def delete_algo(algoid: str, db: Session = Depends(get_db)):
    algo = db.query(models.Algo).filter(models.Algo.algoid == algoid).first()
    if not algo:
        raise HTTPException(status_code=404, detail=f"Algo ID '{algoid}' not found")

    db.delete(algo)
    db.commit()

    return ok(message=f"Algo ID '{algoid}' deleted successfully", data=None)

# ─────────────────────────────────────────────
# 3) ADD SYMBOL
# POST /algos/{algoid}/symbols   (alias: POST /algo/symbol/add)
# ─────────────────────────────────────────────
@app.post("/algos/{algoid}/symbols", status_code=201)
def add_symbol(algoid: str, data: SymbolCreateBody, db: Session = Depends(get_db)):
    algo = db.query(models.Algo).filter(models.Algo.algoid == algoid).first()
    if not algo:
        raise HTTPException(status_code=404, detail=f"Algo ID '{algoid}' not found")

    payload = data.model_dump()
    payload["symbol"] = payload.get("symbol") or payload.get("symbolName")
    new_symbol = models.Symbol(algoid=algoid, **payload)
    db.add(new_symbol)
    db.commit()
    db.refresh(new_symbol)
    return ok(message="Symbol added successfully", data=symbol_to_dict(new_symbol))


@app.post("/algo/symbol/add", status_code=201, include_in_schema=False)
def add_symbol_legacy(data: schemas.SymbolCreate, db: Session = Depends(get_db)):
    algo = db.query(models.Algo).filter(models.Algo.algoid == data.algoid).first()
    if not algo:
        raise HTTPException(status_code=404, detail=f"Algo ID '{data.algoid}' not found")

    payload = data.model_dump()
    payload["symbol"] = payload.get("symbol") or payload.get("symbolName")
    new_symbol = models.Symbol(**payload)
    db.add(new_symbol)
    db.commit()
    db.refresh(new_symbol)
    return ok(message="Symbol added successfully", data=symbol_to_dict(new_symbol))


# ─────────────────────────────────────────────
# 4) LIST SYMBOLS
# GET /algo/{algoid}/symbols
# ─────────────────────────────────────────────
@app.get("/algos/{algoid}/symbols", status_code=200)
@app.get("/algo/{algoid}/symbols", status_code=200, include_in_schema=False)
def list_symbols(algoid: str, db: Session = Depends(get_db)):
    algo = db.query(models.Algo).filter(models.Algo.algoid == algoid).first()
    if not algo:
        raise HTTPException(status_code=404, detail=f"Algo ID '{algoid}' not found")
    symbols = db.query(models.Symbol).filter(models.Symbol.algoid == algoid).all()
    return ok(message="Symbols fetched successfully", data=[symbol_to_dict(s) for s in symbols])


# ─────────────────────────────────────────────
# 5) SEARCH ALGO
# GET /algos/search?q=abc   (alias: GET /algo/search?name=abc)
# ─────────────────────────────────────────────
@app.get("/algos/search", status_code=200)
def search_algo(q: str, db: Session = Depends(get_db)):
    algos = (
        db.query(models.Algo)
        .filter(models.Algo.algoid.like(f"%{q}%"))
        .all()
    )
    return ok(message="Algos fetched successfully", data=[algo_to_dict(a) for a in algos])


@app.get("/algo/search", status_code=200, include_in_schema=False)
def search_algo_legacy(name: str, db: Session = Depends(get_db)):
    algos = (
        db.query(models.Algo)
        .filter(models.Algo.algoid.like(f"%{name}%"))
        .all()
    )
    return ok(message="Algos fetched successfully", data=[algo_to_dict(a) for a in algos])


@app.post("/api/portfolio/submit", status_code=200)
def submit_portfolio(data: Dict[str, Any], db: Session = Depends(get_db)):
    endpoint = "https://sap-portfolioapi.samco.app/api/model-portfolio"

    sap_algo_id = data.get("sapAlgoId") or data.get("algoid") or None

    # try:
    #     # resp = requests.post(endpoint, json=data, timeout=15)
    #     resp = {
    #             "cd": 0,
    #             "msg": "Success",
    #             "res": "Algo ID pushed: ABC123. Stored [2] symbols into Model Portfolio",
    #             "errors": {},
    #             "st": "1770616372596",
    #             "mid": "58e92ede-8579-4df9-a426-ba71d348efde"
    #             }
    #     try:
    #         resp_content = resp.json()
    #     except ValueError:
    #         resp_content = {"text": resp.text}
    #     status = resp.status_code
    # except requests.RequestException as exc:
    #     # save failed request
    #     try:
    #         history = models.PortfolioHistory(
    #             sapAlgoId=str(sap_algo_id) if sap_algo_id else None,
    #             request_body=json.dumps(data),
    #             response_body=str(exc),
    #             status_code=0,
    #         )
    #         db.add(history)
    #         db.commit()
    #     except SQLAlchemyError:
    #         db.rollback()

    #     raise HTTPException(status_code=502, detail=f"Failed to call external API: {exc}")
    
    try:
        # Simulated success response (dict)
        # resp = {
        #     "cd": 0,
        #     "msg": "Success",
        #     "res": "Algo ID pushed: ABC123. Stored [2] symbols into Model Portfolio",
        #     "errors": {},
        #     "st": "1770616372596",
        #     "mid": "58e92ede-8579-4df9-a426-ba71d348efde"
        # }
        
        resp = {
            "cd": -1,
            "msg": "Failure",
            "res": "Invalid symbols: GOLDBEES1",
            "errors": {
            "Error": "Internal Server Error"
            },
            "st": "1770616515109",
            "mid": "e918cc9f-6f81-4163-ab7a-45058390c118"
        }

        # Since resp is already dict
        resp_content = resp

        # Manually set HTTP status
        status = 200

    except Exception as exc:
        # save failed request
        try:
            history = models.PortfolioHistory(
                sapAlgoId=str(sap_algo_id) if sap_algo_id else None,
                request_body=json.dumps(data),
                response_body=str(exc),
                status_code=0,
            )
            db.add(history)
            db.commit()
        except SQLAlchemyError:
            db.rollback()

        raise HTTPException(status_code=502, detail=f"Failed to call external API: {exc}")


    # save successful request/response
    try:
        history = models.PortfolioHistory(
            sapAlgoId=str(sap_algo_id) if sap_algo_id else None,
            request_body=json.dumps(data),
            response_body=json.dumps(resp_content),
            status_code=status,
        )
        db.add(history)
        db.commit()
        db.refresh(history)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to save portfolio history")

    return ok(message="Portfolio submitted successfully", data={"external_status": status, "external_response": resp_content, "history_id": history.id})


@app.get("/algo/search", status_code=200, include_in_schema=False)
def search_algo_legacy(name: str, db: Session = Depends(get_db)):
    algos = (
        db.query(models.Algo)
        .filter(models.Algo.algoid.like(f"%{name}%"))
        .all()
    )
    return ok(message="Algos fetched successfully", data=[algo_to_dict(a) for a in algos])


def history_to_dict(h: models.PortfolioHistory) -> Dict[str, Any]:
    return {
        "id": h.id,
        "sapAlgoId": h.sapAlgoId,
        "request_body": h.request_body,
        "response_body": h.response_body,
        "status_code": h.status_code,
        "created_at": h.created_at.isoformat() if getattr(h, "created_at", None) is not None else None,
    }


@app.get("/algos/{algoid}/portfolio-history", status_code=200)
def get_portfolio_history(algoid: str, db: Session = Depends(get_db)):
    algoid = (algoid or "").strip()
    if not algoid:
        raise HTTPException(status_code=400, detail="algoid is required")

    entries = (
        db.query(models.PortfolioHistory)
        .filter(models.PortfolioHistory.sapAlgoId == algoid)
        .order_by(models.PortfolioHistory.created_at.desc())
        .all()
    )

    return ok(message="Portfolio history fetched successfully", data=[history_to_dict(h) for h in entries])



# ─────────────────────────────────────────────
# 6) DELETE SYMBOL
# DELETE /algos/{algoid}/symbols/{symbolName}   (alias: DELETE /algo/{algoid}/symbol/{symbolName})
# ─────────────────────────────────────────────
@app.delete("/algos/{algoid}/symbols/{symbolName}", status_code=200)
@app.delete("/algo/{algoid}/symbol/{symbolName}", status_code=200, include_in_schema=False)
def delete_symbol(algoid: str, symbolName: str, db: Session = Depends(get_db)):
    algoid = algoid.strip()
    symbolName = symbolName.strip()
    algoid_l = algoid.lower()
    symbolName_l = symbolName.lower()
    symbol = (
        db.query(models.Symbol)
        .filter(
            func.lower(func.trim(models.Symbol.algoid)) == algoid_l,
            func.lower(func.trim(models.Symbol.symbolName)) == symbolName_l,
        )
        .first()
    )
    if not symbol:
        raise HTTPException(status_code=404, detail="Symbol not found")

    db.delete(symbol)
    db.commit()
    return ok(message=f"Symbol '{symbolName}' deleted successfully", data=None)


# ─────────────────────────────────────────────
# 7) UPDATE SYMBOL
# PUT /algos/{algoid}/symbols   (symbolName in body)
# ─────────────────────────────────────────────
@app.put("/algos/{algoid}/symbols", status_code=200)
def update_symbol(algoid: str, data: SymbolCreateBody, db: Session = Depends(get_db)):
    algoid = algoid.strip()
    symbolName = data.symbolName.strip()
    algoid_l = algoid.lower()
    symbolName_l = symbolName.lower()
    symbol = (
        db.query(models.Symbol)
        .filter(
            func.lower(func.trim(models.Symbol.algoid)) == algoid_l,
            func.lower(func.trim(models.Symbol.symbolName)) == symbolName_l,
        )
        .first()
    )
    if not symbol:
        # Helpful hint: symbol might exist under a different algoid
        matches = (
            db.query(models.Symbol.algoid)
            .filter(func.lower(func.trim(models.Symbol.symbolName)) == symbolName_l)
            .all()
        )
        if matches:
            available = sorted({(m[0] or "").strip() for m in matches if (m[0] or "").strip()})
            raise HTTPException(
                status_code=404,
                detail=f"Symbol '{symbolName}' not found under Algo '{algoid}'. Available algoid(s) for this symbol: {available}",
            )
        raise HTTPException(status_code=404, detail="Symbol not found")

    # Update fields except identifier (symbolName)
    for key, value in data.model_dump(exclude={"symbolName"}).items():
        setattr(symbol, key, value)

    db.commit()
    db.refresh(symbol)
    return ok(message="Symbol updated successfully", data=symbol_to_dict(symbol))


# @app.put("/algos/{algoid}/symbols/{symbolName}", status_code=200, include_in_schema=False)
# def update_symbol_legacy_by_path(algoid: str, symbolName: str, data: SymbolCreateBody, db: Session = Depends(get_db)):
#     symbol = (
#         db.query(models.Symbol)
#         .filter(models.Symbol.algoid == algoid, models.Symbol.symbolName == symbolName)
#         .first()
#     )
#     if not symbol:
#         raise HTTPException(status_code=404, detail="Symbol not found")

#     for key, value in data.model_dump(exclude={"symbolName"}).items():
#         setattr(symbol, key, value)

#     db.commit()
#     db.refresh(symbol)
#     return ok(message="Symbol updated successfully", data=symbol_to_dict(symbol))


# @app.put("/algo/{algoid}/symbol/{symbol_id}/update", status_code=200, include_in_schema=False)
# @app.put("/algos/{algoid}/symbols/id/{symbol_id}", status_code=200, include_in_schema=False)
# def update_symbol_legacy_by_id(algoid: str, symbol_id: int, data: SymbolCreateBody, db: Session = Depends(get_db)):
#     symbol = (
#         db.query(models.Symbol)
#         .filter(models.Symbol.id == symbol_id, models.Symbol.algoid == algoid)
#         .first()
#     )
#     if not symbol:
#         raise HTTPException(status_code=404, detail="Symbol not found")

#     for key, value in data.model_dump(exclude={"symbolName"}).items():
#         setattr(symbol, key, value)

#     db.commit()
#     db.refresh(symbol)
#     return ok(message="Symbol updated successfully", data=symbol_to_dict(symbol))