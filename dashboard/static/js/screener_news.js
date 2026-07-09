function computeAbnormalMessages(rows) {
  const withEng = rows.map(r => ({ ...r, _eng: (parseInt(r.likes) || 0) + (parseInt(r.reshares) || 0) }));
  const engs  = withEng.map(r => r._eng);
  const mean  = engs.reduce((a, b) => a + b, 0) / (engs.length || 1);
  const variance = engs.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / (engs.length || 1);
  const stdev = Math.sqrt(variance);
  const threshold = mean + 2 * stdev;
  return withEng.filter(r => r._eng > threshold && r._eng > 0);
}

async function renderNewsCards() {
  const sym = document.getElementById("filter-symbol").value.toUpperCase();
  const container = document.getElementById("news-cards");

  let abnormal = computeAbnormalMessages(allSocialRows);
  if (sym) abnormal = abnormal.filter(r => r.symbol.includes(sym));

  const bySymbol = {};
  abnormal.forEach(r => {
    if (!bySymbol[r.symbol]) bySymbol[r.symbol] = [];
    bySymbol[r.symbol].push(r);
  });
  const symbols = Object.keys(bySymbol).sort();

  document.getElementById("stat-total").textContent   = abnormal.length;
  document.getElementById("stat-bullish").textContent = abnormal.filter(r => (r.nlp_label || "").toLowerCase() === "bullish").length;
  document.getElementById("stat-bearish").textContent = abnormal.filter(r => (r.nlp_label || "").toLowerCase() === "bearish").length;

  if (!symbols.length) {
    container.innerHTML = '<div class="empty">No abnormally high-engagement messages found.</div>';
    return;
  }

  container.innerHTML = symbols.map(s => `<div class="news-card" id="news-card-${s}"><div class="loading">Loading ${s}...</div></div>`).join("");

  for (const s of symbols) {
    const stockRow = allRows.find(r => r.symbol === s) || {};
    const msgs = bySymbol[s].sort((a, b) => (b.timestamp || "").localeCompare(a.timestamp || ""));
    const change = parseFloat(stockRow.change) || 0;
    const card = document.getElementById(`news-card-${s}`);
    card.innerHTML = `
      <div class="news-card-header">
        <a class="ticker-link" href="/charts?symbol=${s}"><strong>${s}</strong></a>
        <span class="news-card-price">$${stockRow.price || "—"}</span>
        <span class="${change > 0 ? "positive" : change < 0 ? "negative" : ""}">${stockRow.change ? (change > 0 ? "+" : "") + stockRow.change + "%" : ""}</span>
      </div>
      <div class="news-card-body">
        <div class="news-card-charts">
          <div id="rolling-tooltip-${s}" class="rolling-tooltip"></div>
          <div style="display:flex;flex-direction:column;gap:1rem;margin-bottom:0.75rem;">
            <div>
              <div class="news-chart-title">Price vs Message Volume</div>
              <canvas id="corr-${s}" height="70"></canvas>
            </div>
            <div>
              <div class="news-chart-title">Message Volume</div>
              <canvas id="vol-${s}" height="30"></canvas>
            </div>
            <div>
              <div class="news-chart-title">Sentiment Breakdown</div>
              <canvas id="sent-${s}" height="30"></canvas>
            </div>
          </div>
        </div>
        <div class="news-card-sidepanel">
          ${renderStatPanel(stockRow)}
        </div>
      </div>
      <div class="news-card-messages">
        ${msgs.map(m => {
          const label = (m.nlp_label || "neutral").toLowerCase();
          return `
          <div class="news-msg sentiment-${label}">
            <div class="news-msg-meta">
              <span>${m.timestamp}</span>
              <span class="sent-badge sent-${label}">${label}</span>
              <span class="news-msg-eng">👍 ${m.likes || 0} · 🔁 ${m.reshares || 0}</span>
            </div>
            <p class="news-msg-text">${escapeHtmlNews(m.message)}</p>
          </div>`;
        }).join("")}
      </div>`;
    loadRollingChart(s, document.getElementById("news-interval").value);
  }
}

const _newsRollingCharts = {};
const NEWS_INTERVAL_TO_TF = { "5m":"1h", "1h":"1d", "1d":"7d", "1w":"30d" };

async function loadRollingChart(symbol, interval) {
  try {
    const res = await fetch(`/api/charts/${symbol}/full`);
    const fullData = await res.json();
    const tf = NEWS_INTERVAL_TO_TF[interval] || "1d";
    const sliced = sliceRollingData(fullData, tf, null);

    if (_newsRollingCharts[symbol]) destroyRollingCharts(_newsRollingCharts[symbol]);

    _newsRollingCharts[symbol] = renderRollingCharts(
      { correlation: `corr-${symbol}`, volume: `vol-${symbol}`, sentiment: `sent-${symbol}`, tooltip: `rolling-tooltip-${symbol}` },
      sliced,
      tf
    );
  } catch (e) {}
}

function escapeHtmlNews(text) {
  const txt = document.createElement("textarea");
  txt.innerHTML = String(text);
  return txt.value;
}
function statRow(label, value) {
  return `<div class="news-sidepanel-row"><span class="news-sidepanel-label">${label}</span><span class="news-sidepanel-value">${value !== undefined && value !== null && value !== "" ? value : "—"}</span></div>`;
}

function renderStatPanel(r) {
  return `
    <div class="news-sidepanel-company">${r.company || r.symbol}</div>
    <div class="news-sidepanel-meta">${r.country || ""}${r.industry ? " · " + r.industry : ""}</div>
    ${statRow("Market Cap", formatMarketCap(r.market_cap))}
    ${statRow("EPS (TTM)", r.eps_ttm)}
    ${statRow("P/E", r.p_e)}
    ${statRow("EPS this Y", r.eps_growth_this_year)}
    ${statRow("Forward P/E", r.forward_p_e)}
    ${statRow("EPS next Y", r.eps_growth_next_year)}
    ${statRow("PEG", r.peg)}
    ${statRow("EPS past 5Y", r.eps_growth_past_5_years)}
    ${statRow("P/S", r.p_s)}
    ${statRow("EPS next 5Y", r.eps_growth_next_5_years)}
    ${statRow("P/B", r.p_b)}
    ${statRow("EPS Q/Q", r.eps_growth_quarter_over_quarter)}
    ${statRow("Dividend", r.dividend_yield)}
    ${statRow("Sales Q/Q", r.sales_growth_quarter_over_quarter)}
    ${statRow("Insider Own", r.insider_ownership)}
    ${statRow("Inst Own", r.institutional_ownership)}
    ${statRow("Insider Trans", r.insider_transactions)}
    ${statRow("Inst Trans", r.institutional_transactions)}
    ${statRow("Short Float", r.short_float)}
    ${statRow("Earnings", r.earnings_date)}
    ${statRow("Analyst Recom", r.analyst_recom)}
    ${statRow("Target Price", r.target_price)}
    ${statRow("Avg Volume", formatVolume(r.average_volume))}
    ${statRow("52W Range", format52wRange(r))}
  `;
}
function format52wRange(r) {
  const price = parseFloat(r.price);
  const highPct = parseFloat(r["52_week_high"]);
  const lowPct = parseFloat(r["52_week_low"]);
  if (isNaN(price) || isNaN(highPct) || isNaN(lowPct)) return "—";
  const high = price / (1 + highPct / 100);
  const low = price / (1 + lowPct / 100);
  return `${low.toFixed(2)} - ${high.toFixed(2)}`;
}
