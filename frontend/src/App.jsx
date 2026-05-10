import React, { useEffect, useState } from 'react'

export default function App() {
  const [watchlist, setWatchlist] = useState([])
  const [input, setInput] = useState('')
  const [news, setNews] = useState([])
  const [loading, setLoading] = useState(false)
  const [cacheInfo, setCacheInfo] = useState(null)
  const [selectedSymbol, setSelectedSymbol] = useState(null)
  const [assetTypes, setAssetTypes] = useState({})
  const [prices, setPrices] = useState({})

  useEffect(() => {
    fetch('/api/watchlist')
      .then(r => r.json())
      .then(d => setWatchlist(d.watchlist || []))
  }, [])

  const refreshNews = () => {
    if (watchlist.length === 0) return
    setLoading(true)

    fetch('/api/news')
      .then(r => r.json())
      .then(d => {
        setNews(d.items || [])
        setCacheInfo({ cached: d.cached, cacheAge: d.cache_age_seconds })
        setLoading(false)
        if (!selectedSymbol && watchlist.length > 0) setSelectedSymbol(watchlist[0])
      })
      .catch(() => setLoading(false))

    fetch('/api/assets/watchlist/batch')
      .then(r => r.json())
      .then(data => {
        const types = {}
        for (const [symbol, info] of Object.entries(data)) {
          types[symbol] = info.type || 'unknown'
        }
        setAssetTypes(types)
      })
      .catch(err => console.warn('Classification failed:', err))

    fetch('/api/prices')
      .then(r => r.json())
      .then(data => setPrices(data))
      .catch(err => console.warn('Price fetch failed:', err))
  }

  function addSymbol(e) {
    e.preventDefault()
    const s = input.trim().toUpperCase()
    if (!s) return
    const newList = Array.from(new Set([s, ...watchlist]))
    fetch('/api/watchlist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbols: newList })
    }).then(() => {
      setWatchlist(newList)
      setSelectedSymbol(s)
      setInput('')
    })
  }

  function removeSymbol(symbol) {
    const newList = watchlist.filter(s => s !== symbol)
    fetch('/api/watchlist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbols: newList })
    }).then(() => {
      setWatchlist(newList)
      if (selectedSymbol === symbol) setSelectedSymbol(newList[0] || null)
    })
  }

  const selectedNews = selectedSymbol ? news.filter(n => n.symbol === selectedSymbol) : []

  const newsCountBySymbol = {}
  news.forEach(item => {
    if (item.symbol) newsCountBySymbol[item.symbol] = (newsCountBySymbol[item.symbol] || 0) + 1
  })

  const getRelevanceColor = score => {
    if (!score) return '#9ca3af'
    if (score >= 80) return '#16a34a'
    if (score >= 60) return '#d97706'
    if (score >= 40) return '#ea580c'
    return '#dc2626'
  }

  const stripHtml = html => html?.replace(/<[^>]*>/g, '').trim() || ''

  const today = new Date().toLocaleDateString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric'
  }).toUpperCase()

  return (
    <div className="container">
      <header>
        <h1>News Stocks</h1>
        <span className="header-sub">{today}</span>
      </header>

      <form onSubmit={addSymbol} className="form">
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          placeholder="Add symbol — AAPL, SPY, VWCE.DE..."
          autoComplete="off"
        />
        <button type="submit">Add</button>
        <button
          type="button"
          onClick={refreshNews}
          className="refresh-btn"
          disabled={loading || watchlist.length === 0}
        >
          {loading ? 'Fetching...' : 'Fetch & Analyse'}
        </button>
      </form>

      {watchlist.length === 0 && (
        <div className="empty"><p>Add a symbol to get started.</p></div>
      )}

      {watchlist.length > 0 && (
        <>
          {cacheInfo && (
            <div className={`cache-info ${cacheInfo.cached ? 'cached' : 'fresh'}`}>
              {cacheInfo.cached
                ? `Cached — ${cacheInfo.cacheAge}s old · refreshes every 30 min`
                : 'Fresh data — just fetched'}
            </div>
          )}

          {/* Portfolio grid — circular cards */}
          <div className="portfolio-grid">
            {watchlist.map(symbol => {
              const assetType = assetTypes[symbol]
              const typeLabel = assetType === 'etf' ? 'ETF' : assetType === 'stock' ? 'Stock' : '...'
              const typeClass = assetType === 'etf' ? 'etf' : assetType === 'stock' ? 'stock' : 'loading'
              const priceData = prices[symbol]
              const changePct = priceData?.change_pct
              const isPositive = changePct != null && changePct >= 0
              const isNegative = changePct != null && changePct < 0
              const count = newsCountBySymbol[symbol] || 0
              const isActive = selectedSymbol === symbol

              return (
                <div className="card-wrapper" key={symbol}>
                  <div
                    className={`portfolio-card ${isActive ? 'active' : ''} ${isPositive ? 'up' : isNegative ? 'down' : ''}`}
                    onClick={() => setSelectedSymbol(symbol)}
                  >
                    <div className="card-inner">
                      <div className="card-ticker">{symbol}</div>
                      <span className={`card-type-badge ${typeClass}`}>{typeLabel}</span>

                      {changePct != null ? (
                        <>
                          <div className={`card-pct ${isPositive ? 'pct-up' : 'pct-down'}`}>
                            {isPositive ? '▲' : '▼'} {Math.abs(changePct).toFixed(2)}%
                          </div>
                          <div className="card-price">
                            {priceData.currency && priceData.currency !== 'USD' ? priceData.currency + ' ' : '$'}
                            {priceData.price.toLocaleString()}
                          </div>
                        </>
                      ) : (
                        <div className="card-no-data">—</div>
                      )}

                      <div className="card-news-info">
                        {count} <span className="card-news-label">{count === 1 ? 'article' : 'articles'}</span>
                      </div>
                    </div>
                  </div>
                  <button
                    type="button"
                    className="card-remove"
                    onClick={e => { e.stopPropagation(); removeSymbol(symbol) }}
                  >✕</button>
                </div>
              )
            })}
          </div>

          {loading && <div className="loading">Loading...</div>}

          {selectedSymbol && !loading && (
            <div className="news-section">
              <div className="news-header">
                <h2>
                  {selectedSymbol}
                  {assetTypes[selectedSymbol] === 'etf' && ' — ETF'}
                  {assetTypes[selectedSymbol] === 'stock' && ' — Stock'}
                </h2>
                <span className="news-count-tag">
                  {selectedNews.length} {selectedNews.length === 1 ? 'article' : 'articles'}
                </span>
              </div>

              {selectedNews.length === 0 && (
                <div className="empty"><p>No news found for {selectedSymbol}.</p></div>
              )}

              <div className="feed">
                {selectedNews.map((n, i) => (
                  <a key={i} className="card" href={n.link} target="_blank" rel="noreferrer">
                    <h3>{n.title}</h3>
                    <div className="card-meta">
                      <span className="source">{n.source}</span>
                      {n.published && (
                        <>
                          <span className="meta-dot">·</span>
                          <span className="date">{n.published.split('T')[0]}</span>
                        </>
                      )}
                      {n.relevance_score != null && (
                        <span
                          className="relevance-badge"
                          style={{ borderColor: getRelevanceColor(n.relevance_score), color: getRelevanceColor(n.relevance_score) }}
                        >
                          {n.relevance_score}%
                        </span>
                      )}
                    </div>
                    {n.summary && (
                      <p className="summary">{stripHtml(n.summary).substring(0, 220)}...</p>
                    )}
                  </a>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
