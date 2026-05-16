from pydantic import BaseModel
from typing import List, Optional


class NewsItem(BaseModel):
    title: str
    link: str
    published: Optional[str] = None
    summary: Optional[str] = None
    symbol: Optional[str] = None
    source: Optional[str] = None
    relevance_score: Optional[int] = None  # 0-100 for ETFs only


class Watchlist(BaseModel):
    symbols: List[str]


class AssetInfo(BaseModel):
    symbol: str
    type: str  # "stock" or "etf"
    confidence: float  # 0-1

class BatchRequest(BaseModel):
    symbols: List[str]


class PortfolioTransaction(BaseModel):
    date: str               # "YYYY-MM-DD"
    symbol: str
    quantity: float
    price: float            # price per share in original currency
    action: str = "BUY"
    currency: str = "EUR"   # currency of price (EUR or USD)
    total_eur: Optional[float] = None  # exact EUR cost from broker (most accurate)


class PortfolioHistoryRequest(BaseModel):
    transactions: List[PortfolioTransaction]
