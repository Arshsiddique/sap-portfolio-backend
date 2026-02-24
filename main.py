from fastapi import FastAPI, Depends, HTTPException, Request, Header
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy import func
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
from passlib.context import CryptContext
from jose import JWTError, jwt

from pydantic import BaseModel, field_validator
import requests
import json

import models
import schemas
from database import get_db, engine

# Creates tables in MySQL automatically on startup
models.Base.metadata.create_all(bind=engine)


# ─────────────────────────────────────────────
# AUTHENTICATION UTILITIES
# ─────────────────────────────────────────────
SECRET_KEY = "your-secret-key-change-this-in-production-123456789"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def get_token_from_header(authorization: Optional[str] = Header(None)) -> str:
    """Extract token from Authorization header"""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header missing")
    
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid authorization header format. Use 'Bearer <token>'")
    
    return parts[1]


def is_token_blacklisted(token: str, db: Session) -> bool:
    """Check if token is blacklisted"""
    blacklisted = db.query(models.TokenBlacklist).filter(models.TokenBlacklist.token == token).first()
    return blacklisted is not None


def get_current_user(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
) -> models.User:
    """Get current user from JWT token"""
    token = get_token_from_header(authorization)
    
    # Check if token is blacklisted
    if is_token_blacklisted(token, db):
        raise HTTPException(status_code=401, detail="Token has been revoked")
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    user = db.query(models.User).filter(models.User.email == email).first()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User account is inactive")
    
    return user


def _ensure_symbols_symbol_column():
    insp = inspect(engine)
    if "symbols" not in insp.get_table_names():
        return
    columns = {c["name"] for c in insp.get_columns("symbols")}
    if "symbol" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE symbols ADD COLUMN symbol VARCHAR(100) NULL"))


def _ensure_algos_trigger_type_column():
    insp = inspect(engine)
    if "algos" not in insp.get_table_names():
        return
    columns = {c["name"] for c in insp.get_columns("algos")}
    if "triggerType" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE algos ADD COLUMN triggerType VARCHAR(100) NOT NULL DEFAULT 'LTP_UPDATE'"))


_ensure_symbols_symbol_column()
_ensure_algos_trigger_type_column()


def _ensure_default_user():
    """Create a default user for testing if users table is empty"""
    db = next(get_db())
    try:
        user_count = db.query(models.User).count()
        if user_count == 0:
            default_user = models.User(
                name="Mohammad Arsh",
                email="mohammad.arsh@samco.in",
                hashed_password=get_password_hash("sap@portfolio"),
                is_active=True
            )
            db.add(default_user)
            db.commit()
            print("✅ Default user created: email=mohammad.arsh@samco.in, password=sap@portfolio")
    except Exception as e:
        print(f"⚠️ Could not create default user: {e}")
        db.rollback()
    finally:
        db.close()


_ensure_default_user()

app = FastAPI(title="SAP Portfolio Backend", version="1.0.0")


# ─────────────────────────────────────────────
# SIMPLE, CONSISTENT RESPONSE FORMAT
# ─────────────────────────────────────────────
def ok(*, message: str = "Success", data: Any = None) -> Dict[str, Any]:
    return {"success": True, "message": message, "data": data, "errors": None}


def fail(*, message: str = "Request failed", errors: Any = None) -> Dict[str, Any]:
    return {"success": False, "message": message, "data": None, "errors": errors}


def algo_to_dict(a: models.Algo) -> Dict[str, Any]:
    return {"id": a.id, "algoid": a.algoid, "triggerType": a.triggerType}


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
    symbolName: str
    symbol: str | None = None
    assetType: str
    weight: float
    marketProtection: str

    @field_validator("symbolName", "symbol", "assetType", "marketProtection")
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
# AUTHENTICATION ENDPOINTS
# ─────────────────────────────────────────────
@app.post("/auth/register", status_code=201)
def register(data: schemas.UserRegister, db: Session = Depends(get_db)):
    # Check if user already exists
    existing_user = db.query(models.User).filter(models.User.email == data.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Create new user
    hashed_password = get_password_hash(data.password)
    new_user = models.User(
        name=data.name,
        email=data.email,
        hashed_password=hashed_password,
        is_active=True
    )
    
    try:
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
        
        # Create access token
        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": new_user.email}, expires_delta=access_token_expires
        )
        
        return ok(
            message="User registered successfully",
            data={
                "user": {
                    "id": new_user.id,
                    "name": new_user.name,
                    "email": new_user.email,
                    "is_active": new_user.is_active
                },
                "access_token": access_token,
                "token_type": "bearer"
            }
        )
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to register user")


@app.post("/auth/login", status_code=200)
def login(data: schemas.UserLogin, db: Session = Depends(get_db)):
    # Find user by email
    user = db.query(models.User).filter(models.User.email == data.email).first()
    
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    # Verify password
    if not verify_password(data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    # Check if user is active
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User account is inactive")
    
    # Create access token
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )
    
    return ok(
        message="Login successful",
        data={
            "user": {
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "is_active": user.is_active
            },
            "access_token": access_token,
            "token_type": "bearer"
        }
    )


@app.post("/auth/logout", status_code=200)
def logout(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Logout user by blacklisting their token"""
    try:
        token = get_token_from_header(authorization)
        
        # Decode token to get expiration time
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            exp_timestamp = payload.get("exp")
            if exp_timestamp:
                expires_at = datetime.fromtimestamp(exp_timestamp)
            else:
                # Default expiration if not found
                expires_at = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        except JWTError:
            # If token is invalid, still blacklist it
            expires_at = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        
        # Check if already blacklisted
        existing = db.query(models.TokenBlacklist).filter(models.TokenBlacklist.token == token).first()
        if not existing:
            # Add token to blacklist
            blacklisted_token = models.TokenBlacklist(
                token=token,
                expires_at=expires_at
            )
            db.add(blacklisted_token)
            db.commit()
        
        return ok(message="Logout successful", data=None)
    
    except HTTPException:
        # Even if token is invalid, return success for logout
        return ok(message="Logout successful", data=None)
    except Exception as e:
        db.rollback()
        return ok(message="Logout successful", data=None)


@app.get("/auth/me", status_code=200)
def get_current_user_info(current_user: models.User = Depends(get_current_user)):
    """Get current logged-in user information"""
    return ok(
        message="User information fetched successfully",
        data={
            "id": current_user.id,
            "name": current_user.name,
            "email": current_user.email,
            "is_active": current_user.is_active,
            "created_at": current_user.created_at.isoformat() if current_user.created_at else None
        }
    )


@app.delete("/auth/cleanup-tokens", status_code=200)
def cleanup_expired_tokens(db: Session = Depends(get_db)):
    """Clean up expired tokens from blacklist (admin endpoint)"""
    try:
        deleted_count = db.query(models.TokenBlacklist).filter(
            models.TokenBlacklist.expires_at < datetime.utcnow()
        ).delete()
        db.commit()
        
        return ok(
            message=f"Cleanup completed successfully",
            data={"deleted_tokens": deleted_count}
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to cleanup expired tokens")


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
        new_algo = models.Algo(algoid=data.algoid, triggerType=data.triggerType)
        db.add(new_algo)
        db.flush()  # keep everything in one transaction

        symbols = [
            models.Symbol(
                algoid=data.algoid,
                triggerType=data.triggerType,
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
# 2.5) UPDATE ALGO TRIGGER TYPE
# PUT /algos/{algoid}   (alias: PUT /algo/{algoid}/update)
# ─────────────────────────────────────────────
@app.put("/algos/{algoid}", status_code=200)
@app.put("/algo/{algoid}/update", status_code=200, include_in_schema=False)
def update_algo(algoid: str, data: schemas.AlgoUpdate, db: Session = Depends(get_db)):
    algo = db.query(models.Algo).filter(models.Algo.algoid == algoid).first()
    if not algo:
        raise HTTPException(status_code=404, detail=f"Algo ID '{algoid}' not found")

    algo.triggerType = data.triggerType
    db.commit()
    db.refresh(algo)

    return ok(message="Algo updated successfully", data=algo_to_dict(algo))

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
    payload["triggerType"] = algo.triggerType
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
    payload["triggerType"] = algo.triggerType
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

    try:
        resp = requests.post(endpoint, json=data, timeout=15)
    
        try:
            resp_content = resp.json()
        except ValueError:
            resp_content = {"text": resp.text}
        status = resp.status_code
    except requests.RequestException as exc:
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

    # Check if external API returned success or failure
    # External API response format: {"cd": -1, "msg": "Failure", "res": "...", "errors": {...}}
    # cd: -1 means failure, other values (1, 0) might mean success
    # msg: "Failure" or "Success"
    external_cd = resp_content.get("cd")
    external_msg = resp_content.get("msg", "")
    external_res = resp_content.get("res", "")
    external_errors = resp_content.get("errors")
    
    # Determine success or failure based on external API response
    is_success = external_cd != -1 and external_msg.lower() != "failure"
    
    if is_success:
        # Success case
        response_message = external_res if external_res else "Portfolio submitted successfully"
        return ok(
            message=response_message, 
            data={
                "external_status": status, 
                "external_response": resp_content, 
                "history_id": history.id
            }
        )
    else:
        # Failure case - return 400 status code
        response_message = external_res if external_res else external_msg
        return JSONResponse(
            status_code=400,
            content=fail(
                message=response_message,
                errors=external_errors if external_errors else {"external_response": resp_content}
            )
        )


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