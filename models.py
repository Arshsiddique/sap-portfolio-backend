from sqlalchemy import Column, Integer, String, Float, Text, DateTime, Boolean, func
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    email = Column(String(100), nullable=False, unique=True, index=True)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class Algo(Base):
    __tablename__ = "algos"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    algoid = Column(String(100), nullable=False, unique=True)
    triggerType = Column(String(100), nullable=False, default="LTP_UPDATE")


class Symbol(Base):
    __tablename__ = "symbols"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    algoid = Column(String(100), nullable=False, index=True)
    triggerType = Column(String(100), nullable=False)
    symbolName = Column(String(100), nullable=False)
    symbol = Column(String(100), nullable=True)
    assetType = Column(String(100), nullable=False)
    weight = Column(Float, nullable=False)
    marketProtection = Column(String(100), nullable=False)


class PortfolioHistory(Base):
    __tablename__ = "portfolio_history"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    sapAlgoId = Column(String(100), nullable=True, index=True)
    request_body = Column(Text, nullable=False)
    response_body = Column(Text, nullable=True)
    status_code = Column(Integer, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class TokenBlacklist(Base):
    __tablename__ = "token_blacklist"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    token = Column(String(500), nullable=False, unique=True, index=True)
    blacklisted_at = Column(DateTime, server_default=func.now(), nullable=False)
    expires_at = Column(DateTime, nullable=False)