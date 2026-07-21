const TF_HOURS = {
  "5m":5/60,"15m":15/60,"30m":30/60,
  "1h":1,"2h":2,"4h":4,"6h":6,"12h":12,
  "1d":24,"7d":168,"30d":720
};
const BUCKET_MINUTES = {
  "5m":1,"15m":1,"30m":1,"1h":5,"2h":5,
  "4h":15,"6h":15,"12h":30,"1d":30,"7d":60,"30d":240
};
const SPARSE_TIMEFRAMES = new Set(["5m","15m","30m","1h","2h"]);
function getETOffsetMinutes(utcMs) {
  const dtf = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', hour12: false,
    year:'numeric', month:'2-digit', day:'2-digit',
    hour:'2-digit', minute:'2-digit', second:'2-digit'
  });
  const parts = dtf.formatToParts(new Date(utcMs));
  const map = {};
  parts.forEach(p => { if (p.type !== 'literal') map[p.type] = p.value; });
  const hour = map.hour === '24' ? '00' : map.hour;
  const asUTC = Date.UTC(map.year, map.month - 1, map.day, hour, map.minute, map.second);
  return (utcMs - asUTC) / 60000;
}

function etStringToUtcMs(tsStr) {
  const clean = tsStr.replace(" ET", "").trim();
  const [datePart, timePart] = clean.split(" ");
  const [y, m, d] = datePart.split("-").map(Number);
  const [hh, mm] = timePart.split(":").map(Number);
  const guessUtc = Date.UTC(y, m - 1, d, hh, mm);
  const offsetMin = getETOffsetMinutes(guessUtc);
  return guessUtc + offsetMin * 60000;
}

function roundToBucketFromMs(ms, bucketMin) {
  const bucketMs = bucketMin * 60000;
  const flooredMs = ms - (ms % bucketMs);
  const d = new Date(flooredMs);
  return d.getUTCFullYear()+"-"+String(d.getUTCMonth()+1).padStart(2,"0")+"-"+String(d.getUTCDate()).padStart(2,"0")+" "+String(d.getUTCHours()).padStart(2,"0")+":"+String(d.getUTCMinutes()).padStart(2,"0");
}
function formatTickLabel(raw, tf) {
  const d = new Date(raw.replace(" ","T")+"Z");
  if (["5m","15m","30m","1h","2h","4h","6h","12h"].includes(tf))
    return d.toLocaleTimeString("en-US",{hour:"numeric",minute:"2-digit",hour12:true,timeZone:"America/New_York"});
  if (tf === "1d")
    return d.toLocaleTimeString("en-US",{hour:"numeric",hour12:true,timeZone:"America/New_York"});
  return d.toLocaleDateString("en-US",{month:"short",day:"numeric",timeZone:"America/New_York"});
}

function formatFullDate(raw) {
  const d = new Date(raw.replace(" ","T")+"Z");
  return d.toLocaleDateString("en-US",{month:"short",day:"numeric",hour:"numeric",minute:"2-digit",hour12:true,timeZone:"America/New_York"});
}

function roundToBucket(tsStr, bucketMin) {
  const ms = new Date(tsStr.replace(" ","T")+"Z").getTime();
  return roundToBucketFromMs(ms, bucketMin);
}
function parseMsgTimestamp(raw) {
  const s = raw.replace(" ", "T");
  return new Date(s.endsWith("Z") ? s : s + "Z").getTime();
}
// Sliding trailing-window message counts.
// At every 1-minute step `t`, counts messages with timestamp in (t - windowMs, t].
// `sortedTimesMs` must be ascending. `stepsMs` must be ascending.
function computeSlidingVolume(sortedTimesMs, stepsMs, windowMs) {
  const counts = new Array(stepsMs.length);
  let left = 0, right = 0;
  for (let i = 0; i < stepsMs.length; i++) {
    const t = stepsMs[i];
    while (right < sortedTimesMs.length && sortedTimesMs[right] <= t) right++;
    while (left < right && sortedTimesMs[left] <= t - windowMs) left++;
    counts[i] = right - left;
  }
  return counts;
}

function sliceRollingData(fullData, tf, viewEndMs, bucketMinOverride) {
  const bucketMin = bucketMinOverride || BUCKET_MINUTES[tf] || 30;
  const tfMs = (TF_HOURS[tf] || 24) * 3600000;
  const endMs = viewEndMs || Date.now();
  const startMs = endMs - tfMs;

  // --- bucketed slots (still used for sentiment bars) ---
  const slots = [];
  let t = startMs - (startMs % (bucketMin * 60000));
  while (t <= endMs) {
    const d = new Date(t);
    slots.push(d.getUTCFullYear()+"-"+String(d.getUTCMonth()+1).padStart(2,"0")+"-"+String(d.getUTCDate()).padStart(2,"0")+" "+String(d.getUTCHours()).padStart(2,"0")+":"+String(d.getUTCMinutes()).padStart(2,"0"));
    t += bucketMin * 60000;
  }

  const sentMap = {};
  for (const m of (fullData.messages||[])) {
    const rMs = parseMsgTimestamp(m.created_at);
    if (rMs < startMs || rMs > endMs) continue;
    const bucket = roundToBucket(m.created_at, bucketMin);
    if (!sentMap[bucket]) sentMap[bucket] = {bullish:0,bearish:0,neutral:0,mixed:0};
    sentMap[bucket][m.nlp_label] = (sentMap[bucket][m.nlp_label]||0) + 1;
  }

  // --- moving-average slots for volume/price: step 1 minute, trailing window = bucketMin (the selected INTERVAL) ---
  const windowMs = bucketMin * 60000;
  const maStepMs = 60000;
  const maStartMs = startMs - (startMs % maStepMs);
  const maStepsMs = [];
  for (let tm = maStartMs; tm <= endMs; tm += maStepMs) maStepsMs.push(tm);
  const maTimestamps = maStepsMs.map(ms => roundToBucketFromMs(ms, 1));

  // messages need to be pulled from windowMs before startMs too, since early points still need lookback
  const sortedTimesMs = (fullData.messages||[])
    .map(m => parseMsgTimestamp(m.created_at))
    .filter(rMs => !isNaN(rMs) && rMs > maStartMs - windowMs && rMs <= endMs)
    .sort((a,b) => a-b);

  const slidingCounts = computeSlidingVolume(sortedTimesMs, maStepsMs, windowMs);
function emaSmooth(values, alpha = 0.3) {
  const out = new Array(values.length);
  out[0] = values[0];
  for (let i = 1; i < values.length; i++) {
    out[i] = alpha * values[i] + (1 - alpha) * out[i - 1];
  }
  return out;
}
const smoothedCounts = emaSmooth(slidingCounts);
  // price forward-filled at 1-minute resolution to match maStepsMs
  const priceMap = {};
  for (const p of (fullData.price_ticks||[])) {
    const rMs = etStringToUtcMs(p.timestamp);
    if (rMs < maStartMs || rMs > endMs) continue;
    priceMap[roundToBucketFromMs(rMs, 1)] = p.price;
  }
  let lastPrice = null;
  const filledPrice = maTimestamps.map(ts => {
    if (priceMap[ts] !== undefined) lastPrice = priceMap[ts];
    return lastPrice;
  });
  const firstKnownIdx = filledPrice.findIndex(v => v !== null);
  if (firstKnownIdx > 0) {
    for (let i = 0; i < firstKnownIdx; i++) filledPrice[i] = filledPrice[firstKnownIdx];
  }

  return {
    volume_series:    maTimestamps.map((ts,i)=>({timestamp:ts,count:smoothedCounts[i]})),
    sentiment_series: slots.map(ts=>({timestamp:ts,...(sentMap[ts]||{bullish:0,bearish:0,neutral:0,mixed:0})})),
    correlation_series: maTimestamps.map((ts,i)=>({timestamp:ts,msg_count:smoothedCounts[i],price:filledPrice[i]})),
  };
}

function updateSharedTooltip(tooltipElId, raw, items) {
  const el = document.getElementById(tooltipElId);
  if (!el) return;
  if (!raw) { el.innerHTML = ""; return; }
  el.innerHTML = `<span class="tt-time">${formatFullDate(raw)}</span>` +
    items.map(i=>`<span class="tt-item"><span class="tt-dot" style="background:${i.color}"></span><span class="tt-label">${i.label}:</span><span class="tt-val">${i.val}</span></span>`).join("");
}

function makeSharedTooltip(tooltipElId, tf, getItems) {
  return (context) => {
    const { tooltip } = context;
    if (tooltip.opacity === 0) { updateSharedTooltip(tooltipElId, null, []); return; }
    updateSharedTooltip(tooltipElId, tooltip.title?.[0] || null, getItems(tooltip));
  };
}

function renderRollingCharts(ids, sliced, tf) {
  const chartDefaults = {
    plugins: { legend: { labels: { color:"#8b949e" } } },
    scales: {
      x: { ticks: { color:"#8b949e", maxTicksLimit:8, callback: function(val){ return formatTickLabel(this.getLabelForValue(val), tf); } }, grid: { color:"rgba(255,255,255,0.05)" } },
      y: { ticks: { color:"#8b949e" }, grid: { color:"rgba(255,255,255,0.05)" } },
    }
  };

  const out = {};
  const hasPrice = sliced.correlation_series.some(d=>d.price!==null&&d.price!==undefined);

  const corrCanvas = document.getElementById(ids.correlation);
  if (corrCanvas) {
    out.correlation = new Chart(corrCanvas, {
      type:"line",
      data:{
        labels: sliced.correlation_series.map(d=>d.timestamp),
        datasets:[
          { label:"Message Volume", data:sliced.correlation_series.map(d=>d.msg_count), borderColor:"#3fb950", backgroundColor:"rgba(63,185,80,0.1)", borderWidth:2, pointRadius:0, tension:0.3, yAxisID:"yMsg" },
          ...(hasPrice?[{ label:"Price ($)", data:sliced.correlation_series.map(d=>d.price), borderColor:"#58a6ff", backgroundColor:"rgba(88,166,255,0.1)", borderWidth:2, pointRadius:0, tension:0.3, yAxisID:"yPrice", spanGaps:true }]:[]),
        ]
      },
      options:{
        interaction:{mode:"index",intersect:false},
        plugins:{
          legend:{labels:{color:"#8b949e"}},
          tooltip:{enabled:false,external:makeSharedTooltip(ids.tooltip, tf, tt=>tt.dataPoints.map(dp=>({
            color:dp.dataset.borderColor,
            label:dp.dataset.label,
            val:dp.dataset.label==="Price ($)"?`$${dp.parsed.y!==null?dp.parsed.y.toFixed(2):"—"}`:dp.parsed.y
          })))}
        },
        scales:{
          x:{ticks:{color:"#8b949e",maxTicksLimit:8,callback:function(val){return formatTickLabel(this.getLabelForValue(val),tf);}},grid:{color:"rgba(255,255,255,0.05)"}},
          yMsg:{type:"linear",position:"left",ticks:{color:"#3fb950"},grid:{color:"rgba(255,255,255,0.05)"},title:{display:true,text:"Messages",color:"#3fb950",font:{size:10}}},
          yPrice:{type:"linear",position:"right",ticks:{color:"#58a6ff",callback:v=>`$${v.toFixed(2)}`},grid:{drawOnChartArea:false},title:{display:true,text:"Price",color:"#58a6ff",font:{size:10}},display:hasPrice},
        }
      }
    });
  }

  const volCanvas = document.getElementById(ids.volume);
  if (volCanvas) {
    out.volume = new Chart(volCanvas,{
      type:"line",
      data:{labels:sliced.volume_series.map(v=>v.timestamp),datasets:[{label:"Messages",data:sliced.volume_series.map(v=>v.count),borderColor:"#3fb950",backgroundColor:"rgba(63,185,80,0.1)",borderWidth:2,pointRadius:0,tension:0.3,fill:true}]},
      options:{...chartDefaults,interaction:{mode:"index",intersect:false},plugins:{legend:{display:false},tooltip:{enabled:false,external:makeSharedTooltip(ids.tooltip, tf, tt=>tt.dataPoints.map(dp=>({color:"#3fb950",label:"Volume",val:dp.parsed.y})))}},scales:{x:{...chartDefaults.scales.x,ticks:{display:false}},y:{...chartDefaults.scales.y}}}
    });
  }

  const sentCanvas = document.getElementById(ids.sentiment);
  if (sentCanvas) {
    out.sentiment = new Chart(sentCanvas,{
      type:"bar",
      data:{
        labels:sliced.sentiment_series.map(s=>s.timestamp),
        datasets:[
          {label:"Bullish",data:sliced.sentiment_series.map(s=>s.bullish),backgroundColor:"#3fb950"},
          {label:"Bearish",data:sliced.sentiment_series.map(s=>s.bearish),backgroundColor:"#f85149"},
          {label:"Neutral",data:sliced.sentiment_series.map(s=>s.neutral),backgroundColor:"#8b949e"},
          {label:"Mixed",  data:sliced.sentiment_series.map(s=>s.mixed),  backgroundColor:"#d29922"},
        ]
      },
      options:{...chartDefaults,interaction:{mode:"index",intersect:false},plugins:{...chartDefaults.plugins,tooltip:{enabled:false,external:makeSharedTooltip(ids.tooltip, tf, tt=>tt.dataPoints.filter(dp=>dp.parsed.y>0).map(dp=>({color:dp.dataset.backgroundColor,label:dp.dataset.label,val:dp.parsed.y})))}},scales:{x:{...chartDefaults.scales.x,stacked:true,ticks:{display:false}},y:{...chartDefaults.scales.y,stacked:true}}}
    });
  }

  return out;
}

function destroyRollingCharts(chartsObj) {
  if (!chartsObj) return;
  Object.values(chartsObj).forEach(c=>{ try{c.destroy();}catch(e){} });
}
