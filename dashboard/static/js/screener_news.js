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
      <canvas id="spark-${s}" height="40"></canvas>
      <div class="news-card-stats">
        <span>Mkt Cap: ${formatMarketCap(stockRow.market_cap)}</span>
        <span>P/E: ${stockRow.p_e || "—"}</span>
        <span>Vol: ${formatVolume(stockRow.volume)}</span>
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
    loadSparkline(s, document.getElementById("news-interval").value);
  }
}

async function loadSparkline(symbol, interval) {
  try {
    const res = await fetch(`/api/ohlc/${symbol}?interval=${interval}`);
    const data = await res.json();
    const candles = data.candles || [];
    const closes = candles.map(c => c.close);
    const labels = candles.map(c => c.date);
    const canvas = document.getElementById(`spark-${symbol}`);
    if (!canvas || !closes.length) return;
    new Chart(canvas, {
      type: "line",
      data: { labels, datasets: [{ data: closes, borderColor: "#58a6ff", backgroundColor: "rgba(88,166,255,0.08)", borderWidth: 1.5, pointRadius: 0, tension: 0.25, fill: true }] },
      options: {
        responsive: true,
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        scales: { x: { display: false }, y: { display: false } },
      }
    });
  } catch (e) {}
}

function escapeHtmlNews(text) {
  const txt = document.createElement("textarea");
  txt.innerHTML = String(text);
  return txt.value;
}
