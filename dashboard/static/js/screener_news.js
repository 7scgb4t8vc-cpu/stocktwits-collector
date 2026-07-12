function computeAbnormalMessages(rows) {
  const withEng = rows.map(r => ({ ...r, _eng: (parseInt(r.likes) || 0) + (parseInt(r.reshares) || 0) }));
  const engs  = withEng.map(r => r._eng);
  const mean  = engs.reduce((a, b) => a + b, 0) / (engs.length || 1);
  const variance = engs.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / (engs.length || 1);
  const stdev = Math.sqrt(variance);
  const threshold = mean + 2 * stdev;
  return withEng.filter(r => r._eng > threshold && r._eng > 0);
}
function filterImportantMessages(rows) {
  if (!rows.length) return [];
  const withEng = rows.map(r => ({ ...r, _eng: (parseInt(r.likes) || 0) + (parseInt(r.reshares) || 0) }));
  const engs = withEng.map(r => r._eng);
  const mean = engs.reduce((a, b) => a + b, 0) / engs.length;
  const variance = engs.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / engs.length;
  const stdev = Math.sqrt(variance);
  const threshold = mean + 2 * stdev;
  const MIN_ENGAGEMENT = 3; // floor, so "unusual" can't just mean 1 like on a dead stock

  let picked = withEng.filter(r => r._eng > threshold && r._eng >= MIN_ENGAGEMENT);

  // Safety net: if nothing clears the bar (common for quiet small stocks),
  // just show the single most-engaged message instead of leaving it empty
  if (!picked.length) {
    const sorted = [...withEng].sort((a, b) => b._eng - a._eng);
    picked = sorted.slice(0, 3); // show a few most recent/relevant even with low engagement
  }
  return picked;
}
async function renderNewsCards(filteredRows) {
  const container = document.getElementById("news-cards");

  const minPosts = parseInt(document.getElementById("f-posts").value) || 0;

  let rows = filteredRows || [];
  if (minPosts) {
    rows = rows.filter(r => (socialCountMap[r.symbol] || 0) >= minPosts);
  }
  rows = rows.slice(0, 50);

  const symbols = rows.map(r => r.symbol);

  clearTimeout(_newsActiveSymbolsTimer);
  _newsActiveSymbolsTimer = setTimeout(() => {
    fetch("/api/active-symbols", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbols: symbols.slice(0, 50) }),
    });
  }, 2000);

  const bySymbol = {};
  allSocialRows.forEach(r => {
    if (!bySymbol[r.symbol]) bySymbol[r.symbol] = [];
    bySymbol[r.symbol].push(r);
  });

  const allMsgsForFiltered = symbols.flatMap(s => bySymbol[s] || []);
  document.getElementById("stat-total").textContent   = rows.length;

  if (!symbols.length) {
    container.innerHTML = '<div class="empty">No stocks match your filters.</div>';
    return;
  }

  container.innerHTML = symbols.map(s => `<div class="news-card" id="news-card-${s}"><div class="loading">Loading ${s}...</div></div>`).join("");

  for (const s of symbols) {
    const stockRow = allRows.find(r => r.symbol === s) || {};
    const msgs = filterImportantMessages(bySymbol[s] || []).sort((a, b) => (b.timestamp || "").localeCompare(a.timestamp || ""));
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
            <span class="news-toolbar-label">Window</span>
            <div class="news-tf-strip">
              ${NEWS_TF_OPTIONS.map(tf=>`<button class="news-tf-btn${tf==='1d'?' news-tf-active':''}" data-symbol="${s}" data-tf="${tf}" onclick="setNewsTf('${s}','${tf}')">${NEWS_TF_LABELS[tf]}</button>`).join("")}
            </div>
            <button class="news-tf-btn" id="news-now-${s}" onclick="resetNewsNow('${s}')" disabled>Now</button>
            <input type="datetime-local" id="news-date-${s}" class="date-picker" title="Jump to date/time">
          </div>
          </div>
          <div id="rolling-tooltip-${s}" class="rolling-tooltip"></div>
          <div id="news-drag-${s}" style="cursor:grab;">
            <div class="news-chart-title">Price vs Message Volume</div>
            <canvas id="corr-${s}" height="130"></canvas>
          </div>
        </div>
        <div class="news-card-sidepanel">
          ${renderStatPanel(stockRow)}
        </div>
      </div>
      <div class="news-card-messages">
        ${msgs.length ? msgs.map(m => {
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
        }).join("") : '<div class="empty" style="font-size:12px;">No messages yet.</div>'}
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
  if (!_newsCardState[symbol]) _newsCardState[symbol] = { tf: "1d", bucket: null, viewEnd: null };
  return _newsCardState[symbol];
}

async function loadRollingChart(symbol) {
  try {
    const res = await fetch(`/api/charts/${symbol}/full`);
    _newsFullData[symbol] = await res.json();
    syncNewsDatePicker(symbol);
    updateNewsChart(symbol);
  } catch(e) {}
}

function updateNewsChart(symbol) {
  const fullData = _newsFullData[symbol];
  if (!fullData) return;
  const state = newsCardState(symbol);
  const sliced = sliceRollingData(fullData, state.tf, state.viewEnd, state.bucket);
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
function syncNewsDatePicker(symbol) {
  const picker = document.getElementById(`news-date-${symbol}`);
  if (!picker) return;
  const state = newsCardState(symbol);
  if (state.viewEnd) {
    const d = new Date(state.viewEnd);
    const pad = n => String(n).padStart(2, "0");
    picker.value = `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  } else {
    picker.value = "";
  }
  const nowBtn = document.getElementById(`news-now-${symbol}`);
  if (nowBtn) nowBtn.disabled = !state.viewEnd;
}

function resetNewsNow(symbol) {
  newsCardState(symbol).viewEnd = null;
  syncNewsDatePicker(symbol);
  updateNewsChart(symbol);
}

// ── Drag to pan (per-card) ─────────────────────────────────────────────
const _newsDragState = { symbol: null, startX: null, startViewEnd: null, pending: false };

function setupNewsDragGlobal() {
  window.addEventListener("mousemove", (e) => {
    const st = _newsDragState;
    if (!st.pending) return;
    const dx = e.clientX - st.startX;
    if (Math.abs(dx) < 3) return;
    const area = document.getElementById(`news-drag-${st.symbol}`);
    if (!area) return;
    const state = newsCardState(st.symbol);
    const tfMs = (TF_HOURS[state.tf] || 24) * 3600000;
    const msPerPx = tfMs / area.offsetWidth;
    const newEnd = st.startViewEnd - dx * msPerPx;
    state.viewEnd = newEnd > Date.now() ? null : newEnd;
    syncNewsDatePicker(st.symbol);
    updateNewsChart(st.symbol);
  });
  window.addEventListener("mouseup", () => {
    const st = _newsDragState;
    if (!st.pending) return;
    const area = document.getElementById(`news-drag-${st.symbol}`);
    if (area) area.classList.remove("dragging");
    st.pending = false;
    st.symbol = null;
  });
}
setupNewsDragGlobal();
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
document.getElementById("news-cards").addEventListener("mousedown", (e) => {
  const area = e.target.closest("[id^='news-drag-']");
  if (!area) return;
  const symbol = area.id.replace("news-drag-", "");
  _newsDragState.symbol = symbol;
  _newsDragState.startX = e.clientX;
  _newsDragState.startViewEnd = newsCardState(symbol).viewEnd || Date.now();
  _newsDragState.pending = true;
  area.classList.add("dragging");
});

document.getElementById("news-cards").addEventListener("change", (e) => {
  const input = e.target.closest("[id^='news-date-']");
  if (!input) return;
  const symbol = input.id.replace("news-date-", "");
  const state = newsCardState(symbol);
  if (!input.value) {
    state.viewEnd = null;
  } else {
    const ms = new Date(input.value).getTime();
    state.viewEnd = ms > Date.now() ? null : ms;
  }
  syncNewsDatePicker(symbol);
  updateNewsChart(symbol);
});
