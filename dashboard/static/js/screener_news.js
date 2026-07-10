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
  clearTimeout(_newsActiveSymbolsTimer);
  _newsActiveSymbolsTimer = setTimeout(() => {
    fetch("/api/active-symbols", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbols: symbols.slice(0, 50) }),
    });
  }, 2000);
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
          <div class="news-toolbar-row">
            <span class="news-toolbar-label">Window</span>
            <div class="news-tf-strip">
              ${NEWS_TF_OPTIONS.map(tf=>`<button class="news-tf-btn${tf==='1d'?' news-tf-active':''}" data-symbol="${s}" data-tf="${tf}" onclick="setNewsTf('${s}','${tf}')">${NEWS_TF_LABELS[tf]}</button>`).join("")}
            </div>
          </div>
          <div class="news-toolbar-row">
            <span class="news-toolbar-label">Interval</span>
            <div class="news-bucket-strip">
              ${Object.keys(NEWS_BUCKET_OPTIONS).map(b=>`<button class="news-bucket-btn" data-symbol="${s}" data-bucket="${b}" onclick="setNewsBucket('${s}','${b}')">${b}</button>`).join("")}
            </div>
          </div>
          <div id="rolling-tooltip-${s}" class="rolling-tooltip"></div>
          <div>
            <div class="news-chart-title">Price vs Message Volume</div>
            <canvas id="corr-${s}" height="130"></canvas>
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
    loadRollingChart(s);
  }
}

const _newsCardState = {};
const _newsFullData = {};
const _newsRollingCharts = {};
let _newsActiveSymbolsTimer = null;

const NEWS_TF_OPTIONS = ["5m","15m","30m","1h","2h","4h","6h","12h","1d","7d","30d"];
const NEWS_TF_LABELS = {"5m":"5m","15m":"15m","30m":"30m","1h":"1H","2h":"2H","4h":"4H","6h":"6H","12h":"12H","1d":"D","7d":"W","30d":"M"};
const NEWS_BUCKET_OPTIONS = {"1m":1,"3m":3,"5m":5,"15m":15,"30m":30,"1h":60,"d":1440,"w":10080,"m":43200};

function newsCardState(symbol) {
  if (!_newsCardState[symbol]) _newsCardState[symbol] = { tf: "1d", bucket: null };
  return _newsCardState[symbol];
}

async function loadRollingChart(symbol) {
  try {
    const res = await fetch(`/api/charts/${symbol}/full`);
    _newsFullData[symbol] = await res.json();
    updateNewsChart(symbol);
  } catch(e) {}
}

function updateNewsChart(symbol) {
  const fullData = _newsFullData[symbol];
  if (!fullData) return;
  const state = newsCardState(symbol);
  const sliced = sliceRollingData(fullData, state.tf, null, state.bucket);
  if (_newsRollingCharts[symbol]) destroyRollingCharts(_newsRollingCharts[symbol]);
  _newsRollingCharts[symbol] = renderRollingCharts(
    { correlation: `corr-${symbol}`, tooltip: `rolling-tooltip-${symbol}` },
    sliced,
    state.tf
  );
}

function setNewsTf(symbol, tf) {
  newsCardState(symbol).tf = tf;
  document.querySelectorAll(`.news-tf-btn[data-symbol="${symbol}"]`).forEach(b=>{
    b.classList.toggle("news-tf-active", b.dataset.tf === tf);
  });
  updateNewsChart(symbol);
}

function setNewsBucket(symbol, bucketLabel) {
  newsCardState(symbol).bucket = NEWS_BUCKET_OPTIONS[bucketLabel];
  document.querySelectorAll(`.news-bucket-btn[data-symbol="${symbol}"]`).forEach(b=>{
    b.classList.toggle("news-bucket-active", b.dataset.bucket === bucketLabel);
  });
  updateNewsChart(symbol);
}

function escapeHtmlNews(text) {
  const txt = document.createElement("textarea");
  txt.innerHTML = String(text);
  return txt.value;
}
function statPair(label, value) {
  return `<div class="news-sidepanel-pair"><span class="news-sidepanel-label">${label}</span><span class="news-sidepanel-value">${value !== undefined && value !== null && value !== "" ? value : "—"}</span></div>`;
}
function format52wRange(r) {
  const low = parseFloat(r["52w_low"]);
  const high = parseFloat(r["52w_high"]);
  if (isNaN(low) || isNaN(high)) return "—";
  return `$${low.toFixed(2)} - $${high.toFixed(2)}`;
}
function renderStatPanel(r) {
  const leftCol = [
    ["Market Cap", formatMarketCap(r.market_cap)],
    ["P/E", r.p_e],
    ["Forward P/E", r.forward_p_e],
    ["PEG", r.peg],
    ["P/S", r.p_s],
    ["P/B", r.p_b],
    ["Dividend", r.dividend_yield],
    ["Insider Own", r.insider_ownership],
    ["Insider Trans", r.insider_transactions],
    ["Short Float", r.short_float],
    ["Analyst Recom", r.analyst_recom],
    ["Avg Volume", formatVolume(r.average_volume)],
  ];
  const rightCol = [
    ["EPS (TTM)", r.eps_ttm],
    ["EPS this Y", r.eps_growth_this_year],
    ["EPS next Y", r.eps_growth_next_year],
    ["EPS past 5Y", r.eps_growth_past_5_years],
    ["EPS next 5Y", r.eps_growth_next_5_years],
    ["EPS Q/Q", r.eps_growth_quarter_over_quarter],
    ["Sales Q/Q", r.sales_growth_quarter_over_quarter],
    ["Inst Own", r.institutional_ownership],
    ["Inst Trans", r.institutional_transactions],
    ["Earnings", (r.earnings_date || "").split(" ")[0]],
    ["Target Price", r.target_price],
    ["52W Range", format52wRange(r)],
  ];

  const rows = leftCol.map((pair, i) => statPair(...pair) + statPair(...rightCol[i]));

  return `
    <div class="news-sidepanel-company">${r.company || r.symbol}</div>
    <div class="news-sidepanel-meta">${r.country || ""}${r.industry ? " · " + r.industry : ""}</div>
    <div class="news-sidepanel-grid">
      ${rows.join("")}
    </div>
  `;
}
