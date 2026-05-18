from fastapi import APIRouter
from .models import Watchlist, AssetInfo, BatchRequest, PortfolioHistoryRequest
from .news_fetcher import NewsFetcher
from .asset_classifier import AssetClassifier
from datetime import datetime, timezone
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

# Cache Fear & Greed for 1 hour
fng_cache = {
    "data": None,
    "timestamp": 0,
    "cache_duration": 60 * 60
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


@router.post("/portfolio/history")
async def portfolio_history(req: PortfolioHistoryRequest):
    """
    Accepts buy/sell transactions (from any broker CSV, incl. Trading212).
    Returns daily portfolio value in EUR, buy markers, per-holding P&L, and summary.
    USD assets are converted to EUR using historical EUR/USD rates from Yahoo Finance.
    """
    transactions = req.transactions
    if not transactions:
        empty = {"total_invested": 0, "total_current": 0, "total_gain_loss": 0, "total_gain_loss_pct": 0}
        return {"chart": [], "buys": [], "holdings": [], "summary": empty}

    tx_list = sorted(
        [{"date": t.date, "symbol": t.symbol.upper(), "action": t.action.upper(),
          "quantity": t.quantity, "price": t.price,
          "currency": (t.currency or "EUR").upper(),
          "total_eur": t.total_eur} for t in transactions],
        key=lambda t: t["date"]
    )

    # Map each symbol to its currency (USD takes priority if any buy uses it)
    symbol_currencies: dict[str, str] = {}
    for t in tx_list:
        sym = t["symbol"]
        if sym not in symbol_currencies or t["currency"] == "USD":
            symbol_currencies[sym] = t["currency"]

    symbols = list(symbol_currencies.keys())
    start_dt = datetime.strptime(tx_list[0]["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.now(tz=timezone.utc)
    start_unix = int(start_dt.timestamp())
    end_unix = int(end_dt.timestamp())

    SUFFIXES = ["", ".DE", ".L", ".MI", ".PA", ".AS", ".BR", ".SW", ".VI", ".LS"]
    req_headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    price_history: dict[str, dict[str, float]] = {}
    eurusd_history: dict[str, float] = {}  # date -> EUR/USD rate (1 EUR = N USD)
    has_usd = any(c == "USD" for c in symbol_currencies.values())

    async with httpx.AsyncClient() as client:
        # Fetch EUR/USD history for converting USD prices → EUR
        if has_usd:
            fx_url = (f"https://query1.finance.yahoo.com/v8/finance/chart/EURUSD%3DX"
                      f"?interval=1d&period1={start_unix}&period2={end_unix}")
            try:
                r = await client.get(fx_url, headers=req_headers, timeout=15)
                if r.status_code == 200:
                    data = r.json()
                    res = (data.get("chart", {}).get("result") or [{}])[0]
                    ts_list = res.get("timestamp", [])
                    closes = (res.get("indicators", {}).get("quote") or [{}])[0].get("close", [])
                    eurusd_history = {
                        datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d"): c
                        for ts, c in zip(ts_list, closes) if ts and c
                    }
                    logger.info(f"[PORTFOLIO] EUR/USD: {len(eurusd_history)} days loaded")
            except Exception as e:
                logger.warning(f"[PORTFOLIO] EUR/USD fetch failed: {e}")

        # Fetch historical daily prices for each symbol
        for symbol in symbols:
            for suffix in SUFFIXES:
                try_symbol = f"{symbol}{suffix}"
                url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{try_symbol}"
                       f"?interval=1d&period1={start_unix}&period2={end_unix}")
                try:
                    r = await client.get(url, headers=req_headers, timeout=15)
                    if r.status_code != 200:
                        continue
                    data = r.json()
                    res = (data.get("chart", {}).get("result") or [None])[0]
                    if not res:
                        continue
                    ts_list = res.get("timestamp", [])
                    closes = (res.get("indicators", {}).get("quote") or [{}])[0].get("close", [])
                    if ts_list and closes:
                        sym_prices = {
                            datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d"): c
                            for ts, c in zip(ts_list, closes) if ts and c
                        }
                        if sym_prices:
                            price_history[symbol] = sym_prices
                            logger.info(f"[PORTFOLIO] {symbol}: {len(sym_prices)} pts via {try_symbol}")
                            break
                except Exception as e:
                    logger.warning(f"[PORTFOLIO] {try_symbol}: {e}")

    # Helper: convert a raw Yahoo price to EUR
    sorted_fx_dates = sorted(eurusd_history)

    def to_eur(symbol: str, raw_price: float, date_str: str) -> float:
        if symbol_currencies.get(symbol, "EUR") != "USD":
            return raw_price
        rate = eurusd_history.get(date_str)
        if rate is None and sorted_fx_dates:
            # Use nearest previous available FX rate
            prev = next((eurusd_history[d] for d in reversed(sorted_fx_dates) if d <= date_str), None)
            rate = prev or sorted_fx_dates and eurusd_history[sorted_fx_dates[0]]
        return raw_price / rate if rate and rate > 0 else raw_price

    all_dates = sorted({d for ph in price_history.values() for d in ph})
    if not all_dates:
        empty = {"total_invested": 0, "total_current": 0, "total_gain_loss": 0, "total_gain_loss_pct": 0}
        return {"chart": [], "buys": [], "holdings": [], "summary": empty}

    # Build daily portfolio value (EUR) timeline
    current_holdings: dict[str, float] = {}
    tx_idx = 0
    chart_data = []

    for date_str in all_dates:
        while tx_idx < len(tx_list) and tx_list[tx_idx]["date"] <= date_str:
            tx = tx_list[tx_idx]
            sym = tx["symbol"]
            delta = tx["quantity"] if tx["action"] == "BUY" else -tx["quantity"]
            current_holdings[sym] = current_holdings.get(sym, 0.0) + delta
            tx_idx += 1

        total = sum(
            shares * to_eur(sym, price_history[sym][date_str], date_str)
            for sym, shares in current_holdings.items()
            if shares > 0 and sym in price_history and date_str in price_history[sym]
        )
        if total > 0:
            chart_data.append({"date": date_str, "value": round(total, 2)})

    # Buy markers
    buys = []
    for tx in tx_list:
        if tx["action"] == "BUY":
            val = next((p["value"] for p in chart_data if p["date"] >= tx["date"]), None)
            buys.append({"date": tx["date"], "symbol": tx["symbol"],
                         "quantity": tx["quantity"], "price": tx["price"], "value": val})

    # Final holdings P&L (all values in EUR)
    holdings_summary = []
    total_invested = 0.0
    total_current = 0.0

    for sym, shares in current_holdings.items():
        if shares <= 0:
            continue

        sym_buys = [t for t in tx_list if t["symbol"] == sym and t["action"] == "BUY"]
        total_bought = sum(t["quantity"] for t in sym_buys)

        # Cost basis in EUR: use broker's total_eur when available (most accurate),
        # otherwise fall back to quantity × price (treated as EUR)
        total_cost_eur = sum(
            (t["total_eur"] if t["total_eur"] is not None else t["quantity"] * t["price"])
            for t in sym_buys
        )
        avg_cost_eur = total_cost_eur / total_bought if total_bought else 0
        adjusted_cost = shares * avg_cost_eur

        # Current price converted to EUR
        current_price_eur = None
        if sym in price_history:
            last_date = max(price_history[sym])
            current_price_eur = to_eur(sym, price_history[sym][last_date], last_date)

        current_value = shares * current_price_eur if current_price_eur is not None else None
        gain_loss = (current_value - adjusted_cost) if current_value is not None else None
        gain_loss_pct = (gain_loss / adjusted_cost * 100) if gain_loss is not None and adjusted_cost > 0 else None

        total_invested += adjusted_cost
        if current_value:
            total_current += current_value

        holdings_summary.append({
            "symbol": sym,
            "shares": round(shares, 6),
            "avg_cost": round(avg_cost_eur, 4),
            "current_price": round(current_price_eur, 4) if current_price_eur is not None else None,
            "current_value": round(current_value, 2) if current_value is not None else None,
            "cost_basis": round(adjusted_cost, 2),
            "gain_loss": round(gain_loss, 2) if gain_loss is not None else None,
            "gain_loss_pct": round(gain_loss_pct, 2) if gain_loss_pct is not None else None,
        })

    holdings_summary.sort(key=lambda h: h["current_value"] or 0, reverse=True)
    total_gl = total_current - total_invested
    total_gl_pct = (total_gl / total_invested * 100) if total_invested > 0 else 0

    return {
        "chart": chart_data,
        "buys": buys,
        "holdings": holdings_summary,
        "summary": {
            "total_invested": round(total_invested, 2),
            "total_current": round(total_current, 2),
            "total_gain_loss": round(total_gl, 2),
            "total_gain_loss_pct": round(total_gl_pct, 2),
        },
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


@router.get("/fear-greed")
async def get_fear_and_greed():
    now = time.time()
    if fng_cache["data"] and (now - fng_cache["timestamp"]) < fng_cache["cache_duration"]:
        return {**fng_cache["data"], "cached": True}

    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            r = await client.get(
                "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Referer": "https://www.cnn.com/markets/fear-and-greed",
                    "Origin": "https://www.cnn.com",
                    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124"',
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": '"Windows"',
                    "sec-fetch-dest": "empty",
                    "sec-fetch-mode": "cors",
                    "sec-fetch-site": "same-site",
                },
            )
            r.raise_for_status()
            raw = r.json()
    except Exception as e:
        logger.warning(f"[FEAR_GREED] Fetch failed: {e}")
        if fng_cache["data"]:
            return {**fng_cache["data"], "cached": True, "stale": True}
        return {"error": str(e)}

    fng = raw.get("fear_and_greed", {})
    data = {
        "score": round(fng.get("score", 0), 1),
        "rating": fng.get("rating", ""),
        "previous_close": round(fng.get("previous_close", 0), 1),
        "previous_1_week": round(fng.get("previous_1_week", 0), 1),
        "previous_1_month": round(fng.get("previous_1_month", 0), 1),
        "previous_1_year": round(fng.get("previous_1_year", 0), 1),
        "timestamp": fng.get("timestamp", ""),
        "cached": False,
    }

    fng_cache["data"] = {k: v for k, v in data.items() if k != "cached"}
    fng_cache["timestamp"] = now

    return data
