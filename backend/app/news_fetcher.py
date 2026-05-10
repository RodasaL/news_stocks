import feedparser
import hashlib
import httpx
import json
import asyncio
from typing import List, Dict
from urllib.parse import quote_plus
from .models import NewsItem
from .asset_classifier import AssetClassifier
import logging

logger = logging.getLogger(__name__)


class NewsFetcher:
    def __init__(self):
        self.watchlist: List[str] = []
        self.asset_types: Dict[str, str] = {}  # symbol -> "stock" or "etf"
        
        # Multiple RSS sources (free, no API key needed)
        self.rss_sources = {
            "google_news": "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en",
            "yahoo_finance": "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}",
            "marketwatch": "https://feeds.marketwatch.com/rss/search?q={query}",
            "reddit_stocks": "https://www.reddit.com/r/stocks/new.json?limit=25",
            "reddit_investing": "https://www.reddit.com/r/investing/new.json?limit=25",
            "seeking_alpha": "https://seekingalpha.com/api/v3/news?filter[symbol]={symbol}&limit=20",
        }

    def set_watchlist(self, symbols: List[str]):
        self.watchlist = [s.upper() for s in symbols]
        # A classificação agora é "pendente". 
        # O frontend chamará o endpoint de batch para classificar tudo de uma vez
        # ou será feita sob demanda na busca de notícias.
        logger.info(f"[WATCHLIST] Updated to: {self.watchlist}")

    def get_watchlist(self) -> List[str]:
        return self.watchlist

    def _get_content_hash(self, title: str, link: str) -> str:
        """Create a hash of title+link to detect duplicates"""
        content = f"{title}:{link}".encode()
        return hashlib.md5(content).hexdigest()

    async def _fetch_google_news(self, client: httpx.AsyncClient, symbol: str, seen_hashes: set, raw_query: str = None) -> List[dict]:
        """Fetch from Google News RSS. If raw_query is given, uses it directly; otherwise builds a composite query from symbol."""
        items = []
        try:
            query = raw_query if raw_query else f"{symbol} stock OR {symbol} ETF OR {symbol} finance OR {symbol} earnings"
            url = self.rss_sources["google_news"].format(query=quote_plus(query))
            response = await client.get(url, timeout=10)
            feed = feedparser.parse(response.content)
            
            for entry in feed.entries[:10]:
                content_hash = self._get_content_hash(
                    entry.get("title", ""),
                    entry.get("link", "")
                )
                
                if content_hash not in seen_hashes:
                    seen_hashes.add(content_hash)
                    items.append({
                        "title": entry.get("title", ""),
                        "link": entry.get("link", ""),
                        "published": entry.get("published", None),
                        "summary": entry.get("summary", None),
                        "symbol": symbol,
                        "source": "Google News"
                    })
        except Exception as e:
            logger.warning(f"Google News fetch failed for {symbol}: {e}")
        
        return items

    async def _fetch_yahoo_finance(self, client: httpx.AsyncClient, symbol: str, seen_hashes: set) -> List[dict]:
        """Fetch from Yahoo Finance RSS"""
        items = []
        try:
            url = self.rss_sources["yahoo_finance"].format(symbol=symbol)
            response = await client.get(url, timeout=10)
            feed = feedparser.parse(response.content)
            
            for entry in feed.entries[:10]:
                content_hash = self._get_content_hash(
                    entry.get("title", ""),
                    entry.get("link", "")
                )
                
                if content_hash not in seen_hashes:
                    seen_hashes.add(content_hash)
                    items.append({
                        "title": entry.get("title", ""),
                        "link": entry.get("link", ""),
                        "published": entry.get("published", None),
                        "summary": entry.get("summary", None),
                        "symbol": symbol,
                        "source": "Yahoo Finance"
                    })
        except Exception as e:
            logger.warning(f"Yahoo Finance fetch failed for {symbol}: {e}")
        
        return items

    async def _fetch_marketwatch(self, client: httpx.AsyncClient, symbol: str, seen_hashes: set) -> List[dict]:
        """Fetch from MarketWatch RSS"""
        items = []
        try:
            query = f"{symbol} stock"
            url = self.rss_sources["marketwatch"].format(query=quote_plus(query))
            response = await client.get(url, timeout=10)
            feed = feedparser.parse(response.content)
            
            for entry in feed.entries[:8]:
                content_hash = self._get_content_hash(
                    entry.get("title", ""),
                    entry.get("link", "")
                )
                
                if content_hash not in seen_hashes:
                    seen_hashes.add(content_hash)
                    items.append({
                        "title": entry.get("title", ""),
                        "link": entry.get("link", ""),
                        "published": entry.get("published", None),
                        "summary": entry.get("summary", None),
                        "symbol": symbol,
                        "source": "MarketWatch"
                    })
        except Exception as e:
            logger.warning(f"MarketWatch fetch failed for {symbol}: {e}")
        
        return items

    async def _fetch_reddit(self, client: httpx.AsyncClient, subreddit: str, seen_hashes: set) -> List[dict]:
        """Fetch from Reddit subreddit (underground source)"""
        items = []
        try:
            url = self.rss_sources[f"reddit_{subreddit}"]
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            
            response = await client.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                
                for post in data.get("data", {}).get("children", [])[:15]:
                    post_data = post.get("data", {})
                    title = post_data.get("title", "")
                    link = f"https://reddit.com{post_data.get('permalink', '')}"
                    
                    content_hash = self._get_content_hash(title, link)
                    
                    if content_hash not in seen_hashes:
                        seen_hashes.add(content_hash)
                        items.append({
                            "title": title,
                            "link": link,
                            "published": None,
                            "summary": post_data.get("selftext", "")[:200],
                            "symbol": None,
                            "source": f"Reddit r/{subreddit}"
                        })
        except Exception as e:
            logger.warning(f"Reddit fetch failed for r/{subreddit}: {e}")
        
        return items

    async def fetch_news_for_watchlist(self) -> List[dict]:
        """Fetch news from all sources and deduplicate"""
        all_items = []
        tasks = []
        request_hashes = set() # Limpo a cada request para não "apagar" notícias antigas
        
        async with httpx.AsyncClient() as client:
            for symbol in self.watchlist:
                tasks.append(self._fetch_google_news(client, symbol, request_hashes))
                tasks.append(self._fetch_yahoo_finance(client, symbol, request_hashes))
                tasks.append(self._fetch_marketwatch(client, symbol, request_hashes))
            
            tasks.append(self._fetch_reddit(client, "stocks", request_hashes))
            tasks.append(self._fetch_reddit(client, "investing", request_hashes))
            
            results = await asyncio.gather(*tasks)
            for result in results:
                all_items.extend(result)
        
        # Analyze relevance for ETFs
        items_by_symbol = {}
        for item in all_items:
            symbol = item.get("symbol")
            if symbol not in items_by_symbol:
                items_by_symbol[symbol] = []
            items_by_symbol[symbol].append(item)
        
        # Identificar símbolos que precisam de classificação
        missing_classification = [
            s for s in items_by_symbol.keys() 
            if s and (s not in self.asset_types or self.asset_types[s] == "unknown")
        ]
        
        if missing_classification:
            batch_results = AssetClassifier.classify_batch(missing_classification)
            for symbol, info in batch_results.items():
                self.asset_types[symbol] = info.get("type", "unknown")

        # Apply relevance analysis for ETFs
        for symbol, items in items_by_symbol.items():
            if not symbol: continue

            asset_type = self.asset_types.get(symbol, "unknown")
            if asset_type == "etf":
                items = AssetClassifier.analyze_news_relevance(symbol, asset_type, items)
                items_by_symbol[symbol] = items
        
        # Flatten back
        all_items = []
        for items in items_by_symbol.values():
            all_items.extend(items)
        
        # Sort by published date (newest first)
        all_items.sort(
            key=lambda x: x.get("published") or "",
            reverse=True
        )
        
        return all_items
