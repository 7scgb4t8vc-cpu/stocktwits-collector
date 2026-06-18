/**
 * Global refresh scheduler for StockTwits Dashboard.
 * Each page registers its load function via:
 *   window.registerPageLoader(myLoadFn)
 * The scheduler calls it immediately, then on every interval tick.
 */

(function () {
  const STORAGE_KEY = "st_refresh_interval";

  let _loader       = null;
  let _timer        = null;
  let _countdown    = null;
  let _nextAt       = 0;

  // ── Persist interval choice across pages ──────────────────────────
  function getSavedInterval() {
    return parseInt(localStorage.getItem(STORAGE_KEY) || "3600", 10);
  }

  function saveInterval(secs) {
    localStorage.setItem(STORAGE_KEY, secs);
  }

  // ── Countdown display ─────────────────────────────────────────────
  function formatCountdown(secs) {
    if (secs >= 86400) {
      const d = Math.floor(secs / 86400);
      const h = Math.floor((secs % 86400) / 3600);
      return `next in ${d}d ${h}h`;
    }
    if (secs >= 3600) {
      const h = Math.floor(secs / 3600);
      const m = Math.floor((secs % 3600) / 60);
      return `next in ${h}h ${m}m`;
    }
    if (secs >= 60) {
      const m = Math.floor(secs / 60);
      const s = secs % 60;
      return `next in ${m}m ${s}s`;
    }
    return `next in ${secs}s`;
  }

  function tickCountdown() {
    const el = document.getElementById("refresh-countdown");
    if (!el) return;
    const remaining = Math.max(0, Math.round((_nextAt - Date.now()) / 1000));
    el.textContent = formatCountdown(remaining);
  }

  // ── Flash dot on refresh ──────────────────────────────────────────
  function flashDot() {
    const dot = document.getElementById("refresh-dot");
    if (!dot) return;
    dot.style.background = "#EF9F27";
    setTimeout(() => { dot.style.background = ""; }, 600);
  }

  // ── Core scheduler ────────────────────────────────────────────────
  function schedule(intervalSecs) {
    if (_timer)    clearInterval(_timer);
    if (_countdown) clearInterval(_countdown);

    _nextAt = Date.now() + intervalSecs * 1000;

    _timer = setInterval(() => {
      _nextAt = Date.now() + intervalSecs * 1000;
      flashDot();
      if (typeof _loader === "function") _loader();
    }, intervalSecs * 1000);

    _countdown = setInterval(tickCountdown, 1000);
    tickCountdown();
  }

  // ── Public API ────────────────────────────────────────────────────
  window.registerPageLoader = function (fn) {
    _loader = fn;
    fn(); // run immediately on page load

    const intervalSecs = getSavedInterval();
    schedule(intervalSecs);

    // Sync selector to saved value
    const sel = document.getElementById("global-interval");
    if (sel) sel.value = String(intervalSecs);
  };

  // ── Wire up the navbar selector ───────────────────────────────────
  document.addEventListener("DOMContentLoaded", () => {
    const sel = document.getElementById("global-interval");
    if (!sel) return;

    // Set to saved value
    sel.value = String(getSavedInterval());

    sel.addEventListener("change", () => {
      const secs = parseInt(sel.value, 10);
      saveInterval(secs);
      schedule(secs);
    });
  });
})();
