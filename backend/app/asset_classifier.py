import os
import json
import logging
import time
import hashlib
from typing import Optional, Dict, List
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables first (before importing genai)
# Find .env in project root
env_path = os.path.join(os.path.dirname(__file__), '..', '..', '.env')
load_dotenv(dotenv_path=env_path, override=True)

from google import genai

logger = logging.getLogger(__name__)

# Load ETF metadata
ETF_METADATA_FILE = os.path.join(os.path.dirname(__file__), 'etf_metadata.json')

def _load_etf_metadata() -> Dict[str, Dict]:
    """Load ETF metadata from disk"""
    if os.path.exists(ETF_METADATA_FILE):
        try:
            with open(ETF_METADATA_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load ETF metadata: {e}")
    return {}

def _save_etf_metadata(data: Dict[str, Dict]):
    """Save updated ETF metadata to disk"""
    try:
        with open(ETF_METADATA_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        logger.info(f"Successfully saved updated ETF metadata to {ETF_METADATA_FILE}")
    except Exception as e:
        logger.error(f"Failed to save ETF metadata: {e}")

# Persistent cache file for classifications (survives app restarts)
CACHE_FILE = os.path.join(os.path.dirname(__file__), '..', '..', '.asset_cache.json')

# Persistent cache file for ETF relevance analysis (30-min TTL)
ETF_RELEVANCE_CACHE_FILE = os.path.join(os.path.dirname(__file__), '..', '..', '.etf_relevance_cache.json')

class AssetCache:
    """Persistent cache for asset classifications"""
    
    @staticmethod
    def load():
        """Load cache from disk"""
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load cache: {e}")
        return {}
    
    @staticmethod
    def save(data):
        """Save cache to disk"""
        try:
            with open(CACHE_FILE, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save cache: {e}")
    
    @staticmethod
    def get(symbol: str) -> Optional[Dict]:
        """Get cached classification"""
        cache = AssetCache.load()
        return cache.get(symbol.upper())
    
    @staticmethod
    def set(symbol: str, data: Dict):
        """Set cached classification"""
        cache = AssetCache.load()
        cache[symbol.upper()] = data
        AssetCache.save(cache)


class EtfRelevanceCache:
    """Cache for ETF semantic relevance analysis (30-min TTL)"""
    CACHE_TTL = 30 * 60  # 30 minutes in seconds
    
    @staticmethod
    def load():
        """Load cache from disk"""
        if os.path.exists(ETF_RELEVANCE_CACHE_FILE):
            try:
                with open(ETF_RELEVANCE_CACHE_FILE, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load ETF relevance cache: {e}")
        return {}
    
    @staticmethod
    def save(data):
        """Save cache to disk"""
        try:
            with open(ETF_RELEVANCE_CACHE_FILE, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save ETF relevance cache: {e}")
    
    @staticmethod
    def get(symbol: str, news_hash: str) -> Optional[Dict]:
        """Get cached relevance for symbol + news combination"""
        cache = EtfRelevanceCache.load()
        symbol_upper = symbol.upper()
        
        if symbol_upper not in cache:
            return None
        
        entry = cache[symbol_upper]
        
        # Check if cache is still valid (30 minutes)
        timestamp = entry.get("timestamp", 0)
        if time.time() - timestamp > EtfRelevanceCache.CACHE_TTL:
            logger.info(f"[RELEVANCE_CACHE_EXPIRED] {symbol}: older than 30 minutes")
            return None
        
        # Get relevance for this specific news item
        relevance_map = entry.get("relevance_map", {})
        return relevance_map.get(news_hash)
    
    @staticmethod
    def set(symbol: str, relevance_map: Dict[str, bool]):
        """Set cached relevance for symbol (with all news hashes)"""
        cache = EtfRelevanceCache.load()
        symbol_upper = symbol.upper()
        
        cache[symbol_upper] = {
            "timestamp": time.time(),
            "relevance_map": relevance_map  # Hash -> True/False
        }
        EtfRelevanceCache.save(cache)
        logger.info(f"[RELEVANCE_CACHE_SET] {symbol}: {len(relevance_map)} items cached for 30 min")


# Global CLIENT variable that will be initialized lazily
CLIENT = None
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_API_KEY = None

def _initialize_gemini():
    """Lazily initialize Gemini API on first use"""
    global CLIENT, GEMINI_API_KEY

    if CLIENT is not None:
        return  # Already initialized

    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    logger.info(f"[GEMINI] API key found: {bool(GEMINI_API_KEY)}")

    if GEMINI_API_KEY:
        try:
            CLIENT = genai.Client(api_key=GEMINI_API_KEY)
            logger.info(f"✓ Gemini API initialized (model: {GEMINI_MODEL})")
        except Exception as e:
            logger.error(f"✗ Failed to initialize Gemini API: {e}")
            CLIENT = None
    else:
        logger.warning("⚠ GEMINI_API_KEY not found in environment")

def _parse_gemini_json(text: str):
    """Extrai JSON de forma robusta ignorando markdown ou texto extra"""
    try:
        text = text.strip()
        # Procura o início e fim de um objeto ou array JSON
        start_idx = min([i for i in [text.find('{'), text.find('[')] if i != -1] or [0])
        end_idx = max([i for i in [text.rfind('}'), text.rfind(']')] if i != -1] or [len(text)])
        
        json_str = text[start_idx:end_idx + 1]
        return json.loads(json_str)
    except Exception as e:
        logger.error(f"Erro ao parsear JSON do Gemini: {e}. Texto original: {text[:100]}...")
        return json.loads(text) # Fallback

# ⚠️  IMPORTANT: Gemini Free Tier Limits (May 2026)
# - Rate: 2 requests/minute (RPM) for Gemini 2.5 Flash
# - Daily: 250 requests per day maximum
# - No caching: Context caching not available on free tier
# - Data usage: Free tier data may be used to improve Google products
# - Billing: Project must have NO billing account to stay on free tier

# Store request timestamps to enforce rate limit
request_timestamps = []
RATE_LIMIT = 2  # STRICT: Free tier allows only 2 requests per minute for Flash models
RATE_WINDOW = 60  # 1 minute window

# Daily request counter (250 max per day)
daily_request_count = 0
daily_request_limit = 250  # Max 250 requests per day on free tier
last_reset_date = datetime.now().date()


def check_daily_request_limit():
    """Check if we haven't exceeded 250 requests/day limit"""
    global daily_request_count, last_reset_date
    
    # Reset daily counter if new day
    if datetime.now().date() > last_reset_date:
        daily_request_count = 0
        last_reset_date = datetime.now().date()
        logger.info("Daily request counter reset")
    
    if daily_request_count >= daily_request_limit:
        logger.error(f"DAILY REQUEST LIMIT REACHED: {daily_request_count}/{daily_request_limit}")
        return False
    
    return True

def check_rate_limit():
    """Enforce rate limit (2 req/min for Gemini 2.5 Flash free tier)"""
    global request_timestamps
    
    while True:
        current_time = time.time()
        # Remove timestamps older than 1 minute
        request_timestamps = [ts for ts in request_timestamps if current_time - ts < RATE_WINDOW]
        
        if len(request_timestamps) < RATE_LIMIT:
            request_timestamps.append(current_time)
            break
            
        # Wait until oldest request is > 60s old
        oldest = request_timestamps[0]
        wait_time = max(0, RATE_WINDOW - (current_time - oldest) + 1.0)
        logger.warning(f"⏳ Gemini Rate Limit: A aguardar {wait_time:.1f}s para não bloquear a API...")
        time.sleep(wait_time)


def increment_daily_request_count():
    """Track API requests against daily 250-request limit"""
    global daily_request_count
    daily_request_count += 1
    
    usage_percent = (daily_request_count / daily_request_limit) * 100
    
    if usage_percent >= 90:
        logger.warning(f"🔴 API quota CRITICAL: {daily_request_count}/{daily_request_limit} requests used!")
    elif usage_percent >= 80:
        logger.warning(f"⚠️  API quota at {usage_percent:.0f}% ({daily_request_count}/{daily_request_limit}))")
    elif usage_percent >= 50:
        logger.info(f"📊 API usage: {daily_request_count}/{daily_request_limit} requests")


def estimate_token_usage(prompt: str, response: str = "") -> int:
    """Rough estimation: ~4 characters = 1 token"""
    return (len(prompt) + len(response)) // 4


def check_token_limit(estimated_tokens: int = 0) -> bool:
    """Check if we have token budget (free tier has plenty, this is safety)"""
    # Gemini free tier is very generous with tokens, so we always return True
    return True


def update_token_usage(tokens_used: int = 0):
    """Track token usage (free tier doesn't have strict limits)"""
    pass  # Token tracking is optional for free tier


class AssetClassifier:
    """Classify assets as ETF or Stock with batch processing and persistent caching"""

    @staticmethod
    def _enrich_etf_metadata(symbol: str, name: str):
        """
        AUTONOMOUS STUDY: Perform a deep dive on a new ETF symbol
        Saves results to etf_metadata.json for future high-quality news fetching
        """
        symbol = symbol.upper()
        _initialize_gemini()
        
        if not CLIENT:
            return

        prompt = f"""Perform a detailed analysis of the ETF with symbol '{symbol}' ({name}).

Return a JSON object exactly in this format (no markdown):
{{
  "ticker": "{symbol}",
  "name": "{name if name else 'Full Name'}",
  "index": "The primary index or benchmark it tracks",
  "type": "etf",
  "description": "Short but precise investment strategy",
  "asset_class": "Specific asset class (e.g. Technology Sector, Global Bonds)",
  "related_tickers": ["3-4 closely related or competitor ETF tickers"],
  "keywords": ["5-7 specific search keywords to find news for this ETF"]
}}"""

        try:
            if not check_daily_request_limit(): return
            check_rate_limit()
            logger.info(f"[STUDY_START] Performing autonomous study on new ETF: {symbol}")
            response = CLIENT.models.generate_content(model=GEMINI_MODEL, contents=prompt)
            data = _parse_gemini_json(response.text)
            
            metadata = _load_etf_metadata()
            metadata[symbol] = data
            _save_etf_metadata(metadata)
            logger.info(f"[STUDY_COMPLETE] ETF {symbol} enriched and saved to metadata database")
        except Exception as e:
            logger.warning(f"[STUDY_FAILED] Could not enrich metadata for {symbol}: {e}")

    @staticmethod
    def classify_asset(symbol: str) -> Dict[str, any]:
        """
        Classify if symbol is ETF or Stock (single symbol, with persistent cache)
        Returns: {"symbol": "AAPL", "type": "stock|etf", "confidence": 0.95, "description": "..."}
        
        Strategy: Check persistent cache FIRST before making API call
        """
        symbol = symbol.upper()
        
        # Check persistent cache first (survives app restarts)
        cached = AssetCache.get(symbol)
        if cached and not cached.get("error"):
            logger.info(f"[DISK_CACHE_HIT] {symbol} -> {cached.get('type')}")
            return cached

        # Initialize Gemini on first use
        _initialize_gemini()
        
        if not CLIENT:
            logger.error(f"[CLIENT_NOT_CONFIGURED] Gemini API not configured")
            return {"symbol": symbol, "type": "unknown", "confidence": 0, "description": ""}

        prompt = f"""Classify the financial asset with symbol '{symbol}'.
            
Respond with ONLY valid JSON (no markdown, no code blocks):
{{
  "type": "etf" or "stock",
  "confidence": 0.0-1.0,
  "name": "full name if known",
  "description": "brief description"
}}

Consider:
- Symbols with multiple holdings (SPY, QQQ, VOO, etc) = ETF
- Individual company tickers = stock
- If uncertain, indicate low confidence"""

        # Tentativa de pedido com Retry para erro 429
        max_retries = 2
        for attempt in range(max_retries):
            try:
                if not check_daily_request_limit():
                    break
                
                check_rate_limit()
                logger.info(f"[API_CALL_SINGLE] {symbol}... (Attempt {attempt+1}/{max_retries})")
                
                response = CLIENT.models.generate_content(model=GEMINI_MODEL, contents=prompt)
                text = response.text.strip()
                increment_daily_request_count()

                result = _parse_gemini_json(text)
                result["symbol"] = symbol
                AssetCache.set(symbol, result)
                
                if result.get("type") == "etf":
                    meta = _load_etf_metadata()
                    if symbol not in meta:
                        AssetClassifier._enrich_etf_metadata(symbol, result.get("name", ""))
                return result

            except Exception as e:
                if ("429" in str(e) or "quota" in str(e).lower()) and attempt < max_retries - 1:
                    wait_time = 60 # Espera um minuto completo
                    logger.warning(f"⚠️ Quota excedida no Google. A aguardar {wait_time}s para nova tentativa...")
                    time.sleep(wait_time)
                    continue
                raise e
        
        # Se chegar aqui, falhou após retries
        return {"symbol": symbol, "type": "unknown", "confidence": 0, "description": "Exceeded retries after quota error"}

    @staticmethod
    def classify_batch(symbols: List[str]) -> Dict[str, Dict]:
        """
        OPTIMIZED: Classify multiple symbols in ONE API call (batch processing)
        Returns: {"AAPL": {...}, "SPY": {...}, ...}
        
        Strategy: One request for 10+ symbols = 10x fewer API calls!
        """
        symbols = [s.upper() for s in symbols]
        
        # Check persistent cache for all symbols
        cached_results = {}
        uncached_symbols = []
        
        for symbol in symbols:
            cached = AssetCache.get(symbol)
            if cached and not cached.get("error"):
                cached_results[symbol] = cached
                logger.info(f"[DISK_CACHE_HIT] {symbol}")
            else:
                uncached_symbols.append(symbol)
        
        # If all symbols are cached, return immediately
        if not uncached_symbols:
            logger.info(f"[BATCH_ALL_CACHED] {len(symbols)} symbols from disk cache")
            return cached_results
        
        # Initialize Gemini on first use
        _initialize_gemini()
        
        if not CLIENT:
            logger.error(f"[CLIENT_NOT_CONFIGURED] Gemini API not configured")
            # Return cached results + unknown for uncached
            for symbol in uncached_symbols:
                cached_results[symbol] = {"symbol": symbol, "type": "unknown", "confidence": 0, "description": ""}
            return cached_results
        
        # Build prompt for batch classification
        symbols_list = ", ".join(uncached_symbols)
        prompt = f"""Classify these {len(uncached_symbols)} financial asset symbols:
{symbols_list}

Respond with ONLY valid JSON array (no markdown, no code blocks):
[
  {{"symbol": "SYMBOL1", "type": "etf|stock", "confidence": 0.0-1.0, "name": "full name", "description": "brief"}},
  {{"symbol": "SYMBOL2", "type": "etf|stock", "confidence": 0.0-1.0, "name": "full name", "description": "brief"}},
  ...
]

Rules:
- Symbols with multiple holdings (SPY, QQQ, VOO, IVV, etc) = ETF
- Individual company tickers = stock
- If uncertain, use low confidence (0.3-0.5)"""

        max_retries = 2
        for attempt in range(max_retries):
            try:
                if not check_daily_request_limit():
                    break
                
                check_rate_limit()
                logger.info(f"[BATCH_API_CALL] {len(uncached_symbols)} symbols (Attempt {attempt+1}/{max_retries})")
                
                response = CLIENT.models.generate_content(model=GEMINI_MODEL, contents=prompt)
                text = response.text.strip()
                increment_daily_request_count()

                batch_results = _parse_gemini_json(text)
                if not isinstance(batch_results, list):
                    batch_results = [batch_results]
                
                etf_metadata = _load_etf_metadata()
                for item in batch_results:
                    symbol = item.get("symbol", "").upper()
                    if symbol:
                        cached_results[symbol] = item
                        AssetCache.set(symbol, item)
                        # Enrich metadata for newly discovered ETFs not yet in local database
                        if item.get("type") == "etf" and symbol not in etf_metadata:
                            AssetClassifier._enrich_etf_metadata(symbol, item.get("name", ""))
                return cached_results

            except Exception as e:
                if ("429" in str(e) or "quota" in str(e).lower()) and attempt < max_retries - 1:
                    wait_time = 60
                    logger.warning(f"⚠️ Quota excedida em Batch. A aguardar {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                
                # Fallback final se falhar
                error_str = str(e)
                for symbol in uncached_symbols:
                    res = {"symbol": symbol, "type": "unknown", "error": "api_quota_exceeded"}
                    cached_results[symbol] = res
                    AssetCache.set(symbol, res)
                return cached_results
        return cached_results

    @staticmethod
    def analyze_news_relevance(symbol: str, asset_type: str, news_items: List[Dict]) -> List[Dict]:
        """
        Only for ETFs - evaluates if news is semantically relevant to the ETF's theme/index
        Uses 30-minute cache to avoid repeated Gemini calls
        """
        if asset_type == "stock":
            return news_items
        
        if not news_items:
            return []
        
        symbol_upper = symbol.upper()
        news_hashes = {}
        for item in news_items:
            content_hash = hashlib.md5(f"{item.get('title', '')}:{item.get('link', '')}".encode()).hexdigest()
            news_hashes[content_hash] = item
        
        cached_relevance = {}
        uncached_items = []
        
        for news_hash, item in news_hashes.items():
            cached = EtfRelevanceCache.get(symbol_upper, news_hash)
            if cached is not None:
                cached_relevance[news_hash] = cached
            else:
                uncached_items.append((news_hash, item))
        
        if not uncached_items:
            filtered_items = []
            for news_hash, item in news_hashes.items():
                rel_info = cached_relevance.get(news_hash)
                if isinstance(rel_info, dict) and rel_info.get("relevant"):
                    item["relevance_score"] = rel_info.get("score", 70)
                    filtered_items.append(item)
                elif rel_info is True:
                    item["relevance_score"] = 70
                    filtered_items.append(item)
            return filtered_items
        
        _initialize_gemini()
        if not CLIENT:
            return news_items
        
        etf_meta_all = _load_etf_metadata()
        etf_meta = etf_meta_all.get(symbol_upper, {})
        etf_index = etf_meta.get("index", symbol_upper)
        etf_description = etf_meta.get("description", "")
        related_tickers = etf_meta.get("related_tickers", [])
        
        uncached_titles = [f"{i}. {item['title']}" for i, (_, item) in enumerate(uncached_items[:20])]
        news_list = "\n".join(uncached_titles)
        related_str = f", {', '.join(related_tickers)}" if related_tickers else ""
        
        prompt = f"""Evaluate semantic relevance of news to the {symbol_upper} ETF.
ETF Context:
- Index: {etf_index}
- Description: {etf_description}
- Related tickers: {symbol_upper}{related_str}

News items to evaluate:
{news_list}

Respond with ONLY JSON array:
[
  {{"index": 0, "relevant": true, "score": 95}},
  ...
]"""

        max_retries = 2
        for attempt in range(max_retries):
            try:
                if not check_daily_request_limit(): break
                check_rate_limit()
                logger.info(f"[RELEVANCE_API_CALL] {symbol} (Attempt {attempt+1}/{max_retries})")
                
                response = CLIENT.models.generate_content(model=GEMINI_MODEL, contents=prompt)
                text = response.text.strip()
                increment_daily_request_count()

                relevance_data = _parse_gemini_json(text)
                api_relevance = {item['index']: item for item in relevance_data}
                
                all_relevance = {}
                for news_hash in news_hashes.keys():
                    all_relevance[news_hash] = cached_relevance.get(news_hash)
                
                for idx, (news_hash, item) in enumerate(uncached_items[:20]):
                    rel_info = api_relevance.get(idx, {"relevant": True, "score": 70})
                    all_relevance[news_hash] = rel_info
                
                EtfRelevanceCache.set(symbol_upper, all_relevance)
                
                filtered_items = []
                for news_hash, item in news_hashes.items():
                    rel_info = all_relevance.get(news_hash, {"relevant": True, "score": 0})
                    if isinstance(rel_info, dict) and rel_info.get("relevant"):
                        item["relevance_score"] = rel_info.get("score", 70)
                        filtered_items.append(item)
                    elif rel_info is True:
                        item["relevance_score"] = 70
                        filtered_items.append(item)
                return filtered_items

            except Exception as e:
                if ("429" in str(e) or "quota" in str(e).lower()) and attempt < max_retries - 1:
                    wait_time = 60
                    logger.warning(f"⚠️ Quota excedida em Relevância. A aguardar {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                logger.warning(f"Error analyzing relevance for {symbol}: {e}")
                return news_items
        return news_items

    @staticmethod
    def get_etf_search_queries(symbol: str, asset_type: str) -> List[str]:
        """
        For ETFs: Generate search queries from metadata + keywords
        For Stocks: Return basic query
        
        Non-API: Uses local ETF metadata to avoid API calls
        """
        symbol_upper = symbol.upper()
        
        # For stocks, return basic query
        if asset_type == "stock":
            return [f"{symbol}"]
        
        # Load ETF metadata
        etf_meta_all = _load_etf_metadata()
        etf_meta = etf_meta_all.get(symbol_upper, {})
        
        if not etf_meta:
            logger.warning(f"No ETF metadata for {symbol}, using basic query")
            return [f"{symbol} ETF"]
        
        # Build search queries from metadata
        queries = []
        
        # Add ticker itself
        queries.append(symbol_upper)
        
        # Add index name
        if etf_meta.get("index"):
            queries.append(etf_meta["index"])
        
        # Add relevant keywords
        keywords = etf_meta.get("keywords", [])
        queries.extend(keywords[:3])  # First 3 keywords
        
        # Add related tickers for broader coverage
        related = etf_meta.get("related_tickers", [])
        for ticker in related[:2]:  # First 2 related tickers
            queries.append(f"{ticker} OR {symbol_upper}")
        
        # Remove duplicates while preserving order
        seen = set()
        unique_queries = []
        for q in queries:
            if q.upper() not in seen:
                seen.add(q.upper())
                unique_queries.append(q)
        
        logger.info(f"[ETF_QUERIES] {symbol}: {unique_queries[:5]}")
        return unique_queries[:7]  # Return up to 7 queries
