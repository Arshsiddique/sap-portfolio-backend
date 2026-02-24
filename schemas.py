from pydantic import BaseModel, field_validator, EmailStr


# ── Auth Schemas ──────────────────────────────
class UserRegister(BaseModel):
    name: str
    email: EmailStr
    password: str

    @field_validator("name")
    @classmethod
    def name_must_not_be_empty(cls, value):
        if not value.strip():
            raise ValueError("name cannot be empty")
        return value.strip()

    @field_validator("password")
    @classmethod
    def password_must_be_valid(cls, value):
        if len(value) < 6:
            raise ValueError("password must be at least 6 characters long")
        return value


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str


class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    is_active: bool

    class Config:
        from_attributes = True


# ── Algo Schemas ──────────────────────────────
class AlgoCreate(BaseModel):
    algoid: str
    triggerType: str = "LTP_UPDATE"

    @field_validator("algoid")
    @classmethod
    def algoid_must_not_be_empty(cls, value):
        if not value.strip():
            raise ValueError("algoid cannot be empty")
        return value.strip()

    @field_validator("triggerType")
    @classmethod
    def trigger_type_must_be_valid(cls, value):
        valid_types = ["LTP_UPDATE", "REBALANCE"]
        if value not in valid_types:
            raise ValueError(f"triggerType must be one of {valid_types}")
        return value


class AlgoResponse(BaseModel):
    id: int
    algoid: str
    triggerType: str

    class Config:
        from_attributes = True


# ── Symbol Schemas ────────────────────────────
class SymbolCreate(BaseModel):
    algoid: str
    symbolName: str
    symbol: str | None = None
    assetType: str
    weight: float
    marketProtection: str

    @field_validator("weight")
    @classmethod
    def weight_must_not_exceed_100(cls, value):
        if value <= 0:
            raise ValueError("Weight must be greater than 0")
        if value > 100:
            raise ValueError("Weight cannot be more than 100")
        return value


class SymbolDelete(BaseModel):
    algoid: str
    symbolName: str


class SymbolResponse(BaseModel):
    id: int
    algoid: str
    triggerType: str
    symbolName: str
    symbol: str | None = None
    assetType: str
    weight: float
    marketProtection: str

    class Config:
        from_attributes = True


class AlgoUpdate(BaseModel):
    triggerType: str

    @field_validator("triggerType")
    @classmethod
    def trigger_type_must_be_valid(cls, value):
        valid_types = ["LTP_UPDATE", "REBALANCE"]
        if value not in valid_types:
            raise ValueError(f"triggerType must be one of {valid_types}")
        return value