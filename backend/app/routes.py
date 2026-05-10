from fastapi import APIRouter
from .models import Watchlist, AssetInfo, BatchRequest
from .news_fetcher import NewsFetcher
from .asset_classifier import AssetClassifier
import time
import logging
import httpx

logger = logging.getLogger(__name__)

router = APIRouter()
fetcher = NewsFetcher()

# Cache news for 30 minutes
news_cache = {
    "data": [],
    "timestamp": 0,
    "cache_duration": 30 * 60  # 30 minutes in seconds
}


@router.get("/watchlist")
def get_watchlist():
    return {"watchlist": fetcher.get_watchlist()}


@router.post("/watchlist")
async def set_watchlist(w: Watchlist):
    fetcher.set_watchlist(w.symbols)
    # Invalidate cache when watchlist changes
    news_cache["timestamp"] = 0
    return {"watchlist": fetcher.get_watchlist()}


@router.get("/asset/{symbol}")
def get_asset_info(symbol: str):
    """Get asset type and classification info (single symbol)"""
    symbol = symbol.upper()
    logger.info(f"[API] get_asset_info called for {symbol}")
    
    # Use batch classification if not cached (more efficient)
    info = AssetClassifier.classify_asset(symbol)
    fetcher.asset_types[symbol] = info.get("type", "unknown")
    return info


@router.post("/assets/batch")
def batch_classify_assets(req: BatchRequest):
    """
    OPTIMIZED: Classify multiple symbols in ONE API call
    Returns: {"AAPL": {...}, "SPY": {...}, ...}
    
    Example: POST /api/assets/batch with body: {"symbols": ["AAPL", "SPY", "QQQ"]}
    """
    if not req.symbols:
        return {}
    
    logger.info(f"[API] batch_classify_assets called for {len(req.symbols)} symbols")
    
    # Use batch classification (1 API call for ALL symbols!)
    results = AssetClassifier.classify_batch(req.symbols)
    
    # Update internal cache
    for symbol, info in results.items():
        fetcher.asset_types[symbol] = info.get("type", "unknown")
    
    return results


@router.get("/assets/watchlist/batch")
def classify_watchlist_batch():
    """
    OPTIMIZED: Classify ALL watchlist symbols in ONE API call
    Perfect for app startup - classifies 10+ symbols with just 1 request!
    """
    watchlist = fetcher.get_watchlist()
    
    if not watchlist:
        return {}
    
    logger.info(f"[API] Batch classifying entire watchlist ({len(watchlist)} symbols)")
    
    # Use batch classification (1 API call for entire watchlist!)
    results = AssetClassifier.classify_batch(watchlist)
    
    # Update internal cache
    for symbol, info in results.items():
        fetcher.asset_types[symbol] = info.get("type", "unknown")
    
    return results


@router.get("/prices")
async def get_prices():
    """
    Fetch current price and 24h % change for all watchlist symbols via Yahoo Finance.
    Automatically tries common exchange suffixes (.DE, .L, …) for European assets.
    """
    watchlist = fetcher.get_watchlist()
    if not watchlist:
        return {}

    SUFFIXES = ["", ".DE", ".L", ".MI", ".PA", ".AS", ".BR", ".SW", ".VI", ".LS"]
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    results = {}

    async with httpx.AsyncClient() as client:
        for symbol in watchlist:
            for suffix in SUFFIXES:
                try_symbol = f"{symbol}{suffix}"
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{try_symbol}?interval=1d&range=5d"
                try:
                    r = await client.get(url, headers=headers, timeout=8)
                    if r.status_code != 200:
                        continue
                    data = r.json()
                    result_list = data.get("chart", {}).get("result", [])
                    if not result_list:
                        continue
                    meta = result_list[0].get("meta", {})
                    price = meta.get("regularMarketPrice")
                    prev = meta.get("previousClose") or meta.get("chartPreviousClose")
                    if price and prev and price > 0:
                        results[symbol] = {
                            "price": round(price, 2),
                            "prev_close": round(prev, 2),
                            "change_pct": round((price - prev) / prev * 100, 2),
                            "currency": meta.get("currency", ""),
                            "yahoo_symbol": try_symbol,
                        }
                        break
                except Exception as e:
                    logger.warning(f"Price fetch failed for {try_symbol}: {e}")
            if symbol not in results:
                results[symbol] = None  # no data found

    return results


@router.get("/news")
async def get_news():
    current_time = time.time()
    
    # Check if cache is still valid (less than 30 minutes old)
    if news_cache["timestamp"] > 0 and (current_time - news_cache["timestamp"]) < news_cache["cache_duration"]:
        # Return cached data
        return {
            "count": len(news_cache["data"]),
            "items": news_cache["data"],
            "cached": True,
            "cache_age_seconds": int(current_time - news_cache["timestamp"])
        }
    
    # Fetch new data
    items = await fetcher.fetch_news_for_watchlist()
    
    # Update cache
    news_cache["data"] = items
    news_cache["timestamp"] = current_time
    
    return {
        "count": len(items),
        "items": items,
        "cached": False
    }


@router.get("/news/etf/{symbol}")
async def get_etf_news(symbol: str):
    """
    Get semantically relevant news for an ETF
    
    Uses Gemini to evaluate if each news item is relevant to the ETF's theme/index
    Only returns news classified as relevant.
    
    Example: GET /api/news/etf/SPY returns only S&P 500 relevant news
    """
    symbol = symbol.upper()
    logger.info(f"[API] get_etf_news called for {symbol}")
    
    # First classify the symbol to confirm it's an ETF
    asset_info = AssetClassifier.classify_asset(symbol)
    asset_type = asset_info.get("type", "unknown")
    
    if asset_type != "etf":
        logger.warning(f"Symbol {symbol} is not an ETF (type: {asset_type})")
        return {
            "symbol": symbol,
            "count": 0,
            "items": [],
            "note": f"Symbol {symbol} is not an ETF"
        }
    
    # Get expanded search queries for the ETF (uses metadata, no API call)
    search_queries = AssetClassifier.get_etf_search_queries(symbol, asset_type)
    logger.info(f"[ETF_NEWS] Search queries for {symbol}: {search_queries[:3]}")
    
    # Fetch news with expanded queries
    all_news = []
    seen_hashes = set()
    try:
        async with httpx.AsyncClient() as client:
            for query in search_queries[:5]:
                try:
                    # raw_query uses the ETF metadata query directly; symbol ensures correct tagging
                    news_items = await fetcher._fetch_google_news(client, symbol, seen_hashes, raw_query=query)
                    all_news.extend(news_items)
                    logger.info(f"[ETF_NEWS] Found {len(news_items)} items for query: {query}")
                except Exception as e:
                    logger.warning(f"[ETF_NEWS] Failed to search for query '{query}': {e}")

            logger.info(f"[ETF_NEWS] {symbol}: Found {len(all_news)} unique news items before filtering")

            relevant_news = AssetClassifier.analyze_news_relevance(symbol, "etf", all_news)

            logger.info(f"[ETF_NEWS] {symbol}: {len(relevant_news)} items are semantically relevant")

            return {
                "symbol": symbol,
                "type": asset_type,
                "count": len(relevant_news),
                "items": relevant_news,
                "all_checked": len(all_news)
            }
        
    except Exception as e:
        logger.error(f"[ETF_NEWS] Error fetching news for {symbol}: {e}")
        return {
            "symbol": symbol,
            "count": 0,
            "items": [],
            "error": str(e)
        }


@router.post("/news/etfs/batch")
async def batch_etf_news(req: BatchRequest):
    """
    OPTIMIZED: Analyze news relevance for MULTIPLE ETFs in ONE Gemini call (30-min cache)
    
    Reduces API calls: 5 ETFs = 1 request instead of 5
    Uses 30-minute cache for relevance analysis
    
    Example: POST /api/news/etfs/batch with body: {"symbols": ["SPY", "QQQ", "IWM"]}
    """
    if not req.symbols:
        return {"results": {}}
    
    symbols = [s.upper() for s in req.symbols]
    logger.info(f"[API] batch_etf_news called for {len(symbols)} ETFs")
    
    results = {}
    
    for symbol in symbols:
        # Get the single ETF news (uses cache automatically)
        etf_result = await get_etf_news(symbol)
        results[symbol] = etf_result
    
    logger.info(f"[BATCH_ETF_NEWS] Processed {len(symbols)} ETFs, total items: {sum(r.get('count', 0) for r in results.values())}")
    
    return {
        "symbols": symbols,
        "total_etfs": len(symbols),
        "total_items": sum(r.get('count', 0) for r in results.values()),
        "results": results
    }
