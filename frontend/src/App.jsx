import React, { useEffect, useState, useRef } from 'react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ReferenceLine, ResponsiveContainer,
} from 'recharts'

// ── CSV parsers ──────────────────────────────────────────────────────────────

function parseTrading212CSV(headers, lines) {
  const col = (name) => headers.indexOf(name)
  const iA = col('action')
  const iT = col('time')
  const iTk = col('ticker')
  const iSh = col('no. of shares')
  const iP = col('price / share')
  const iCp = col('currency (price / share)')
  const iTo = col('total')
  const iCt = col('currency (total)')

  return lines.map(line => {
    const v = line.split(',').map(s => s.trim().replace(/^"|"$/g, ''))
    const action = v[iA] || ''
    if (!action.toLowerCase().includes('buy') && !action.toLowerCase().includes('sell')) return null
    const ticker = v[iTk]?.trim()
    if (!ticker) return null
    const date = (v[iT] || '').split(' ')[0]
    if (!date?.match(/^\d{4}-\d{2}-\d{2}$/)) return null
    const qty = parseFloat(v[iSh])
    const price = parseFloat(v[iP])
    if (isNaN(qty) || qty <= 0 || isNaN(price) || price <= 0) return null
    const currency = v[iCp]?.trim() || 'EUR'
    const totalRaw = parseFloat(v[iTo])
    const totalEur = v[iCt]?.trim() === 'EUR' && !isNaN(totalRaw) ? Math.abs(totalRaw) : null
    return {
      date,
      symbol: ticker.toUpperCase(),
      action: action.toLowerCase().includes('buy') ? 'BUY' : 'SELL',
      quantity: qty,
      price,
      currency,
      total_eur: totalEur,
    }
  }).filter(Boolean)
}

function parseSimpleCSV(headers, lines) {
  return lines.map(line => {
    const values = line.split(',').map(v => v.trim().replace(/^"|"$/g, ''))
    const row = {}
    headers.forEach((h, i) => { row[h] = values[i] })
    const qty = parseFloat(row.quantity ?? row.qty ?? row.shares)
    const price = parseFloat(row.price ?? row['buy price'] ?? row.cost)
    return {
      date: row.date,
      symbol: (row.symbol ?? row.ticker)?.toUpperCase(),
      action: (row.action ?? row.type ?? 'BUY').toUpperCase(),
      quantity: qty,
      price,
      currency: 'EUR',
      total_eur: null,
    }
  }).filter(t => t.date && t.symbol && !isNaN(t.quantity) && t.quantity > 0 && !isNaN(t.price) && t.price > 0)
}

function parseCSV(text) {
  const lines = text.trim().split('\n').filter(l => l.trim())
  if (lines.length < 2) return []
  const headers = lines[0].split(',').map(h => h.trim().replace(/^"|"$/g, '').toLowerCase())
  const dataLines = lines.slice(1)
  // Trading212 has "no. of shares" and "time" columns
  if (headers.includes('no. of shares') && headers.includes('time')) {
    return parseTrading212CSV(headers, dataLines)
  }
  return parseSimpleCSV(headers, dataLines)
}

// ────────────────────────────────────────────────────────────────────────────

export default function App() {
  // ── Watchlist state ──
  const [watchlist, setWatchlist] = useState([])
  const [input, setInput] = useState('')
  const [news, setNews] = useState([])
  const [loading, setLoading] = useState(false)
  const [cacheInfo, setCacheInfo] = useState(null)
  const [selectedSymbol, setSelectedSymbol] = useState(null)
  const [assetTypes, setAssetTypes] = useState({})
  const [prices, setPrices] = useState({})

  // ── Fear & Greed ──
  const [fng, setFng] = useState(null)
  const [fngOpen, setFngOpen] = useState(false)

  useEffect(() => {
    fetch('/api/fear-greed')
      .then(r => r.json())
      .then(d => { if (!d.error) setFng(d) })
      .catch(() => {})
  }, [])

  // ── Tab ──
  const [activeTab, setActiveTab] = useState('watchlist')

  // ── Portfolio state ──
  const [portfolioTransactions, setPortfolioTransactions] = useState([])
  const [portfolioData, setPortfolioData] = useState(null)
  const [portfolioLoading, setPortfolioLoading] = useState(false)
  const [portfolioError, setPortfolioError] = useState(null)
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef(null)

  useEffect(() => {
    fetch('/api/watchlist')
      .then(r => r.json())
      .then(d => setWatchlist(d.watchlist || []))
  }, [])

  // Auto-classify and fetch prices whenever the watchlist symbols change
  useEffect(() => {
    if (watchlist.length === 0) return
    fetch('/api/assets/watchlist/batch')
      .then(r => r.json())
      .then(data => {
        const types = {}
        for (const [symbol, info] of Object.entries(data)) types[symbol] = info.type || 'unknown'
        setAssetTypes(types)
      })
      .catch(err => console.warn('Classification failed:', err))
    fetch('/api/prices')
      .then(r => r.json())
      .then(data => setPrices(data))
      .catch(err => console.warn('Price fetch failed:', err))
  }, [watchlist.join(',')])

  // ── Watchlist functions ──
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
        for (const [symbol, info] of Object.entries(data)) types[symbol] = info.type || 'unknown'
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
      body: JSON.stringify({ symbols: newList }),
    }).then(() => { setWatchlist(newList); setSelectedSymbol(s); setInput('') })
  }

  function removeSymbol(symbol) {
    const newList = watchlist.filter(s => s !== symbol)
    fetch('/api/watchlist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbols: newList }),
    }).then(() => {
      setWatchlist(newList)
      if (selectedSymbol === symbol) setSelectedSymbol(newList[0] || null)
    })
  }

  // ── Portfolio functions ──
  async function handleFile(file) {
    if (!file) return
    setPortfolioError(null)
    const text = await file.text()
    const transactions = parseCSV(text)
    if (transactions.length === 0) {
      setPortfolioError('No valid transactions found. Check the CSV format.')
      return
    }
    setPortfolioTransactions(transactions)
    await analyzePortfolio(transactions)
  }

  async function analyzePortfolio(transactions) {
    setPortfolioLoading(true)
    setPortfolioError(null)
    try {
      const r = await fetch('/api/portfolio/history', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ transactions }),
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      setPortfolioData(await r.json())
    } catch (e) {
      setPortfolioError(`Failed to analyze portfolio: ${e.message}`)
    } finally {
      setPortfolioLoading(false)
    }
  }

  function downloadSampleCSV() {
    const csv = [
      'date,symbol,action,quantity,price',
      '2023-01-10,AAPL,BUY,10,130.73',
      '2023-03-15,MSFT,BUY,5,280.50',
      '2023-06-01,VWCE,BUY,2,95.00',
      '2024-01-05,SPY,BUY,3,467.90',
    ].join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = 'portfolio_sample.csv'; a.click()
    URL.revokeObjectURL(url)
  }

  // ── Chart helpers ──
  const formatChartDate = (dateStr) => {
    if (!dateStr) return ''
    const [y, m] = dateStr.split('-')
    const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    return `${months[parseInt(m, 10) - 1]} '${y.slice(2)}`
  }

  const formatCurrency = (v) => {
    if (v >= 1_000_000) return `€${(v / 1_000_000).toFixed(1)}M`
    if (v >= 1_000) return `€${(v / 1_000).toFixed(0)}k`
    return `€${v.toFixed(0)}`
  }

  const eur = (n, d = 0) =>
    n != null ? `€${n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}` : '—'

  // ── Watchlist helpers ──
  const selectedNews = selectedSymbol ? news.filter(n => n.symbol === selectedSymbol) : []
  const newsCountBySymbol = {}
  news.forEach(item => {
    if (item.symbol) newsCountBySymbol[item.symbol] = (newsCountBySymbol[item.symbol] || 0) + 1
  })

  const getRelevanceColor = score => {
    if (!score) return 'rgba(255,255,255,0.2)'
    if (score >= 80) return '#34d399'
    if (score >= 60) return '#fbbf24'
    if (score >= 40) return '#fb923c'
    return '#f87171'
  }

  const stripHtml = html => html?.replace(/<[^>]*>/g, '').trim() || ''

  const today = new Date().toLocaleDateString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric',
  }).toUpperCase()

  const fngCol = fng
    ? fng.score >= 75 ? '#22c55e'
    : fng.score >= 55 ? '#84cc16'
    : fng.score >= 45 ? '#eab308'
    : fng.score >= 25 ? '#f97316'
    : '#ef4444'
    : '#fff'

  return (
    <div className="container">
      <header>
        <h1>News Stocks</h1>
        <span className="header-sub">{today}</span>
      </header>

      {/* ── Tab bar ── */}
      <div className="tab-bar">
        <button className={`tab-btn${activeTab === 'watchlist' ? ' active' : ''}`} onClick={() => setActiveTab('watchlist')}>
          Watchlist
        </button>
        <button className={`tab-btn${activeTab === 'portfolio' ? ' active' : ''}`} onClick={() => setActiveTab('portfolio')}>
          Portfolio
        </button>
      </div>

      {/* ════════════════ WATCHLIST TAB ════════════════ */}
      {activeTab === 'watchlist' && (
        <>
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
        </>
      )}

      {/* ════════════════ PORTFOLIO TAB ════════════════ */}
      {activeTab === 'portfolio' && (
        <div className="pf-section">

          {!portfolioData && !portfolioLoading && (
            <div
              className={`csv-drop-zone${dragOver ? ' drag-over' : ''}`}
              onClick={() => fileInputRef.current?.click()}
              onDragOver={e => { e.preventDefault(); setDragOver(true) }}
              onDragLeave={() => setDragOver(false)}
              onDrop={e => { e.preventDefault(); setDragOver(false); handleFile(e.dataTransfer.files[0]) }}
            >
              <div className="drop-icon">⬆</div>
              <p className="drop-title">Drop your broker CSV or <span className="drop-link">click to browse</span></p>
              <p className="drop-sub">Supports <strong>Trading212 exports</strong> directly. All values shown in EUR.</p>
              <button type="button" className="sample-btn" onClick={e => { e.stopPropagation(); downloadSampleCSV() }}>
                Download simple format sample
              </button>
              <input ref={fileInputRef} type="file" accept=".csv" style={{ display: 'none' }}
                onChange={e => handleFile(e.target.files[0])} />
            </div>
          )}

          {portfolioError && <div className="pf-error">{portfolioError}</div>}
          {portfolioLoading && <div className="loading">Fetching historical prices and EUR/USD rates…</div>}

          {portfolioData && !portfolioLoading && (
            <>
              <div className="pf-toolbar">
                <button type="button" className="pf-reset-btn"
                  onClick={() => { setPortfolioData(null); setPortfolioTransactions([]) }}>
                  ← Upload new CSV
                </button>
                <span className="pf-tx-count">{portfolioTransactions.length} transactions loaded</span>
              </div>

              {/* Summary cards */}
              <div className="pf-summary-row">
                <div className="pf-card">
                  <div className="pf-card-label">Total Invested</div>
                  <div className="pf-card-value">{eur(portfolioData.summary.total_invested)}</div>
                </div>
                <div className="pf-card">
                  <div className="pf-card-label">Current Value</div>
                  <div className="pf-card-value">{eur(portfolioData.summary.total_current)}</div>
                </div>
                <div className={`pf-card${portfolioData.summary.total_gain_loss >= 0 ? ' pf-gain' : ' pf-loss'}`}>
                  <div className="pf-card-label">Total Gain / Loss</div>
                  <div className="pf-card-value">
                    {portfolioData.summary.total_gain_loss >= 0 ? '+' : '−'}{eur(Math.abs(portfolioData.summary.total_gain_loss))}
                  </div>
                </div>
                <div className={`pf-card${portfolioData.summary.total_gain_loss_pct >= 0 ? ' pf-gain' : ' pf-loss'}`}>
                  <div className="pf-card-label">ROI</div>
                  <div className="pf-card-value">
                    {portfolioData.summary.total_gain_loss_pct >= 0 ? '+' : ''}{portfolioData.summary.total_gain_loss_pct.toFixed(2)}%
                  </div>
                </div>
              </div>

              {/* Chart */}
              {portfolioData.chart.length > 1 && (
                <div className="pf-chart-box">
                  <div className="pf-section-heading">Portfolio Value Over Time (EUR)</div>
                  <ResponsiveContainer width="100%" height={300}>
                    <AreaChart data={portfolioData.chart} margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
                      <defs>
                        <linearGradient id="pfGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#5b8df5" stopOpacity={0.28} />
                          <stop offset="95%" stopColor="#5b8df5" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                      <XAxis dataKey="date" tick={{ fontSize: 10, fill: 'rgba(255,255,255,0.32)' }}
                        tickFormatter={formatChartDate} minTickGap={55} axisLine={{ stroke: 'rgba(255,255,255,0.07)' }} tickLine={false} />
                      <YAxis tick={{ fontSize: 10, fill: 'rgba(255,255,255,0.32)' }} tickFormatter={formatCurrency} width={58} axisLine={false} tickLine={false} />
                      <Tooltip
                        formatter={v => [
                          `€${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
                          'Value'
                        ]}
                        labelFormatter={d => d}
                        contentStyle={{ fontSize: 12, border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, background: 'rgba(15,17,32,0.88)', color: '#e2e5f0', backdropFilter: 'blur(12px)' }}
                        cursor={{ stroke: 'rgba(255,255,255,0.1)', strokeWidth: 1 }}
                      />
                      <Area type="monotone" dataKey="value" stroke="#5b8df5" strokeWidth={2}
                        fill="url(#pfGrad)" dot={false} activeDot={{ r: 4, fill: '#5b8df5', stroke: 'rgba(91,141,245,0.3)', strokeWidth: 4 }} />
                      {portfolioData.buys.map((buy, i) => (
                        <ReferenceLine key={i} x={buy.date} stroke="#34d399"
                          strokeDasharray="4 3" strokeWidth={1.5}
                          label={portfolioData.buys.length <= 12
                            ? { value: buy.symbol, position: 'insideTopLeft', fontSize: 8, fill: '#34d399', dy: -4 }
                            : undefined}
                        />
                      ))}
                    </AreaChart>
                  </ResponsiveContainer>
                  <p className="pf-chart-note">Green dashed lines mark buy events. USD assets converted to EUR using historical rates.</p>
                </div>
              )}

              {/* Holdings table */}
              {portfolioData.holdings.length > 0 && (
                <div className="pf-table-box">
                  <div className="pf-section-heading">Current Holdings (EUR)</div>
                  <div className="pf-table-scroll">
                    <table className="pf-table">
                      <thead>
                        <tr>
                          <th>Symbol</th><th>Shares</th><th>Avg Cost</th>
                          <th>Current Price</th><th>Cost Basis</th>
                          <th>Current Value</th><th>Gain / Loss</th><th>ROI</th>
                        </tr>
                      </thead>
                      <tbody>
                        {portfolioData.holdings.map(h => (
                          <tr key={h.symbol}>
                            <td><strong>{h.symbol}</strong></td>
                            <td>{h.shares}</td>
                            <td>{eur(h.avg_cost, 2)}</td>
                            <td>{h.current_price != null ? eur(h.current_price, 2) : '—'}</td>
                            <td>{eur(h.cost_basis)}</td>
                            <td>{h.current_value != null ? eur(h.current_value) : '—'}</td>
                            <td className={h.gain_loss >= 0 ? 'td-gain' : 'td-loss'}>
                              {h.gain_loss != null
                                ? `${h.gain_loss >= 0 ? '+' : '−'}${eur(Math.abs(h.gain_loss))}`
                                : '—'}
                            </td>
                            <td className={h.gain_loss_pct >= 0 ? 'td-gain' : 'td-loss'}>
                              {h.gain_loss_pct != null
                                ? `${h.gain_loss_pct >= 0 ? '+' : ''}${h.gain_loss_pct.toFixed(1)}%`
                                : '—'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Transactions log */}
              <div className="pf-table-box">
                <div className="pf-section-heading">Transactions ({portfolioTransactions.length})</div>
                <div className="pf-table-scroll">
                  <table className="pf-table">
                    <thead>
                      <tr><th>Date</th><th>Symbol</th><th>Action</th><th>Qty</th><th>Price</th><th>Total (EUR)</th></tr>
                    </thead>
                    <tbody>
                      {portfolioTransactions.map((t, i) => (
                        <tr key={i}>
                          <td>{t.date}</td>
                          <td><strong>{t.symbol}</strong></td>
                          <td><span className={`tx-badge ${t.action === 'BUY' ? 'tx-buy' : 'tx-sell'}`}>{t.action}</span></td>
                          <td>{t.quantity}</td>
                          <td>{t.price.toFixed(4)} {t.currency}</td>
                          <td>{t.total_eur != null ? eur(t.total_eur, 2) : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {/* ── Fear & Greed sidebar ── */}
      {fng && (
        <>
          <button
            className={`fng-toggle${fngOpen ? ' hidden' : ''}`}
            onClick={() => setFngOpen(true)}
            aria-label="Open Fear & Greed index"
          >
            <span className="fng-toggle-label">State Of The Market</span>
          </button>

          <div
            className={`fng-sidebar${fngOpen ? ' open' : ''}`}
            onClick={() => setFngOpen(false)}
            title="Click to close"
          >
            <div className="fng-info-panel">
              <span className="fng-vind-score" style={{ color: fngCol }}>{fng.score}</span>
              <span className="fng-vind-rating">{fng.rating}</span>
              <span className="fng-vind-prev">prev {fng.previous_close}</span>
            </div>
            <div className="fng-bar-area">
              <span className="fng-slab">Extreme Greed</span>
              <div className="fng-vbar">
                <div className="fng-vind" style={{ top: `${(1 - fng.score / 100) * 100}%` }}>
                  <div className="fng-vind-dot" style={{ boxShadow: `0 0 10px 2px ${fngCol}66` }} />
                </div>
              </div>
              <span className="fng-slab">Extreme Fear</span>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
