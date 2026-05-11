// Stockbot Review — vanilla JS dashboard.
//
// Data shape (new):
//   {
//     prediction_date, result_date (nullable in forecast), mode,
//     by_source: {
//       technical: {summary, predictions[]},
//       multi:     {summary, predictions[]},
//       horizontal:{summary, predictions[]},
//       confluence:{summary, predictions[]}
//     }
//   }
//
// Per-day HTML pages set window.SB_DATA_URL to bypass latest.json
// and load a specific predictions_<DATE>.json.

const SOURCES = [
  { id: "technical",  label: "Technical",  short: "T", desc: "pred_5d top 30" },
  { id: "delivery",   label: "Delivery",   short: "D", desc: "pred_5d top 30" },
  { id: "multi",      label: "Multi-H",    short: "M", desc: "pred_10d top 30" },
  { id: "horizontal", label: "Horizontal", short: "H", desc: "delivery spike top 30" },
  { id: "confluence", label: "Confluence", short: "C", desc: "deliv ∩ tech overlap" },
];

const INR = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 2 });
const PCT = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 2, signDisplay: "exceptZero" });

const state = {
  data: null,
  index: null,
  volumeWatch: null,
  activeSource: "technical",
  filters: {
    direction: "all",
    minConf: 0,
    sort: "default",
  },
  activeTicker: null,
};

async function init() {
  initTheme();
  bindFilters();

  // Per-day pages override SB_DATA_URL with a "../data/..." path; mirror that for the
  // sidebar fetches so they resolve correctly whether we're at /index.html or /d/*.html.
  const dataBase = (window.SB_DATA_URL || "data/latest.json").replace(/[^/]+$/, "");

  try {
    state.index = await fetchJSON(dataBase + "index.json");
    populateDatePicker(state.index);
  } catch (e) { /* ok */ }

  try { state.volumeWatch = await fetchJSON(dataBase + "volume_watch.json"); }
  catch (e) { state.volumeWatch = null; }

  const url = window.SB_DATA_URL || "data/latest.json";
  try {
    state.data = await fetchJSON(url);
  } catch (e) {
    try { state.data = await fetchJSON("data/sample.json"); }
    catch (e2) { showError(`Could not load ${url} or sample.json`); return; }
  }

  // pick a default tab that actually has rows
  const firstWithData = SOURCES.find((s) => (state.data.by_source?.[s.id]?.predictions || []).length > 0);
  if (firstWithData) state.activeSource = firstWithData.id;
  render();
}

async function fetchJSON(path) {
  const r = await fetch(path, { cache: "no-store" });
  if (!r.ok) throw new Error(`fetch ${path}: ${r.status}`);
  return r.json();
}

function showError(msg) {
  document.body.innerHTML = `<pre style="padding:24px;color:#ef4444">${esc(msg)}</pre>`;
}

// ---------- theme ----------
function initTheme() {
  const saved = localStorage.getItem("sb-theme") || "dark";
  document.documentElement.dataset.theme = saved;
  const btn = document.getElementById("themeToggle");
  if (!btn) return;
  btn.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("sb-theme", next);
  });
}

// ---------- date picker ----------
function populateDatePicker(idx) {
  const sel = document.getElementById("datePicker");
  if (!sel) return;
  sel.innerHTML = "";
  for (const d of idx.dates) {
    const opt = document.createElement("option");
    opt.value = d.file || `predictions_${d.date}.json`;
    const acc = d.accuracy_pct != null ? `${d.accuracy_pct.toFixed(1)}%` : (d.mode === "forecast" ? "forecast" : "—");
    opt.textContent = `${d.date}${d.result_date ? " → " + d.result_date : ""}  ·  ${acc}`;
    sel.appendChild(opt);
  }
  sel.addEventListener("change", async (e) => {
    const base = (window.SB_DATA_URL || "data/latest.json").replace(/[^/]+$/, "");
    try {
      state.data = await fetchJSON(base + e.target.value);
      const firstWithData = SOURCES.find((s) => (state.data.by_source?.[s.id]?.predictions || []).length > 0);
      if (firstWithData) state.activeSource = firstWithData.id;
      render();
    } catch (err) { alert("Could not load: " + err.message); }
  });

  const at = document.getElementById("allTime");
  if (at && idx.all_time) {
    const a = idx.all_time;
    at.innerHTML = `All-time accuracy: <b>${a.accuracy_pct.toFixed(1)}%</b> (${a.correct}/${a.total_calls} across ${a.days} day${a.days === 1 ? "" : "s"})`;
  }
}

function bindFilters() {
  document.getElementById("filterDir")?.addEventListener("change", (e) => { state.filters.direction = e.target.value; render(); });
  document.getElementById("filterConf")?.addEventListener("input", (e) => {
    state.filters.minConf = +e.target.value;
    document.getElementById("confVal").textContent = e.target.value;
    render();
  });
  document.getElementById("sortBy")?.addEventListener("change", (e) => { state.filters.sort = e.target.value; render(); });
}

// ---------- render ----------
function render() {
  if (!state.data) return;
  const d = state.data;

  const dateLine = document.getElementById("dateLine");
  if (dateLine) {
    const result = d.result_date || "next trading day (forecast)";
    dateLine.textContent = `Pred: ${d.prediction_date} → ${result}`;
  }
  const gen = document.getElementById("generatedAt");
  if (gen && d.generated_at) gen.textContent = `generated ${d.generated_at.replace("T", " ").slice(0, 19)}`;

  renderSourceTabs(d);
  const blk = d.by_source?.[state.activeSource] || { summary: {}, predictions: [] };
  renderSummaryCards(blk.summary, d.mode);
  const visible = applyFilters(blk.predictions);
  renderVolumeWatch();
  renderDateArchive();
  renderSourceSummary(d);
  renderTable(visible, d.currency || "INR", d.mode);
}

function renderVolumeWatch() {
  const ul = document.getElementById("vwList");
  const countEl = document.getElementById("vwCount");
  if (!ul) return;
  const watches = state.volumeWatch?.watches || [];
  if (countEl) countEl.textContent = watches.length;
  if (watches.length === 0) {
    ul.innerHTML = '<li class="vwempty">No spikes in window. Re-run build after the daily refresh.</li>';
    return;
  }
  ul.innerHTML = watches.map((w) => {
    const pct = w.pct_since_spike;
    const pctCls = pct == null ? "vwitem__pct--flat" : pct > 0 ? "vwitem__pct--pos" : pct < 0 ? "vwitem__pct--neg" : "vwitem__pct--flat";
    const pctTxt = pct == null ? "—" : PCT.format(pct) + "%";
    const ageTxt = w.days_since === 0 ? "today" : `${w.days_since}d ago`;
    return `
      <li class="vwitem" title="${esc(w.company)}  ·  ${w.deliv_qty.toLocaleString("en-IN")} sh vs ${w.baseline_median.toLocaleString("en-IN")} median">
        <div class="vwitem__row1">
          <span class="vwitem__ticker">${esc(w.ticker)}</span>
          <span class="vwitem__ratio">${w.spike_ratio.toFixed(1)}× ↑</span>
        </div>
        <div class="vwitem__row2">
          <span class="vwitem__age">${esc(w.spike_date)} · ${ageTxt}</span>
          <span class="vwitem__pct ${pctCls}">${pctTxt}</span>
        </div>
      </li>`;
  }).join("");
}

function renderDateArchive() {
  const ul = document.getElementById("dateList");
  if (!ul) return;
  const dates = state.index?.dates || [];
  const currentDate = state.data?.prediction_date;
  document.getElementById("sidebarCount").textContent = dates.length;
  ul.innerHTML = dates.slice(0, 30).map((d) => {
    const isActive = d.date === currentDate && (d.mode === state.data?.mode);
    let accClass = "dateitem__acc--low", accText = "—";
    if (d.mode === "forecast") {
      accClass = "dateitem__acc--forecast";
      accText = "fcst";
    } else if (d.accuracy_pct != null) {
      accText = `${d.accuracy_pct.toFixed(0)}%`;
      if (d.accuracy_pct >= 50) accClass = "dateitem__acc--high";
      else if (d.accuracy_pct >= 30) accClass = "dateitem__acc--mid";
    }
    const href = d.page ? `${d.page}` : "#";
    return `
      <a class="dateitem ${isActive ? "is-active" : ""}" href="${esc(href)}" data-date="${esc(d.date)}">
        <div class="dateitem__hdr">
          <span class="dateitem__date">${esc(d.date)}</span>
          <span class="dateitem__acc ${accClass}">${accText}</span>
        </div>
        <div class="dateitem__meta">
          <span>${d.mode === "forecast" ? "→ next day" : "→ " + esc(d.result_date || "")}</span>
          <span>${d.total} picks</span>
        </div>
      </a>`;
  }).join("");
}

function renderSourceSummary(d) {
  const host = document.getElementById("srcSumm");
  if (!host) return;
  host.innerHTML = SOURCES.map((s) => {
    const blk = d.by_source?.[s.id] || {};
    const summ = blk.summary || {};
    const n = (blk.predictions || []).length;
    let stat;
    if (n === 0) stat = '<span class="muted">no data</span>';
    else if (summ.accuracy_pct != null) stat = `<b>${summ.correct}/${summ.total}</b> · ${summ.accuracy_pct.toFixed(1)}%`;
    else stat = `<b>${n}</b> picks`;
    return `
      <div class="srcsumm__row ${state.activeSource === s.id ? "is-active" : ""}" data-src="${s.id}">
        <div class="srcsumm__left">
          <span class="src-badge src-badge--${s.short}">${s.short}</span>
          <span class="srcsumm__name">${s.label}</span>
        </div>
        <span class="srcsumm__stat">${stat}</span>
      </div>`;
  }).join("");
  host.querySelectorAll(".srcsumm__row").forEach((el) => {
    el.addEventListener("click", () => {
      const id = el.dataset.src;
      const blk = d.by_source?.[id];
      if (!blk || (blk.predictions || []).length === 0) return;
      state.activeSource = id;
      render();
    });
  });
}

function renderSourceTabs(d) {
  const host = document.getElementById("srcTabs");
  if (!host) return;
  host.innerHTML = SOURCES.map((s) => {
    const blk = d.by_source?.[s.id] || { predictions: [], summary: {} };
    const n = (blk.predictions || []).length;
    const acc = blk.summary?.accuracy_pct;
    const subtxt = n === 0
      ? '<span class="srctab__empty">no data</span>'
      : (acc != null ? `${blk.summary.correct}/${n} · ${acc.toFixed(1)}%` : `${n} forecasts`);
    return `
      <button class="srctab ${state.activeSource === s.id ? "is-active" : ""}" data-src="${s.id}" ${n === 0 ? "disabled" : ""}>
        <span class="srctab__letter src-badge src-badge--${s.short}">${s.short}</span>
        <span class="srctab__main">
          <span class="srctab__label">${s.label}</span>
          <span class="srctab__sub">${subtxt}</span>
        </span>
      </button>`;
  }).join("");
  host.querySelectorAll(".srctab").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.disabled) return;
      state.activeSource = btn.dataset.src;
      render();
    });
  });
}

function renderSummaryCards(s, mode) {
  const cards = [
    { label: "Total picks", value: s.total ?? 0 },
    { label: "Correct", value: s.correct != null ? s.correct : "—", cls: s.correct ? "card--good" : "" },
    { label: "Accuracy", value: s.accuracy_pct != null ? `${s.accuracy_pct.toFixed(1)}%` : (mode === "forecast" ? "pending" : "—") },
    { label: "Avg move", value: s.avg_move_pct != null ? PCT.format(s.avg_move_pct) + "%" : "—" },
    { label: "Best", value: s.best ? s.best.ticker : "—", sub: s.best ? PCT.format(s.best.pct) + "%" : "", cls: "card--good" },
    { label: "Worst", value: s.worst ? s.worst.ticker : "—", sub: s.worst ? PCT.format(s.worst.pct) + "%" : "", cls: "card--bad" },
  ];
  document.getElementById("summaryCards").innerHTML = cards
    .map((c) => `
      <div class="card ${c.cls || ""}">
        <div class="card__label">${c.label}</div>
        <div class="card__value">${c.value}</div>
        ${c.sub ? `<div class="card__sub">${c.sub}</div>` : ""}
      </div>`)
    .join("");
}

function applyFilters(rows) {
  const f = state.filters;
  let out = rows.filter((r) => {
    if (f.direction !== "all" && r.predicted_direction !== f.direction) return false;
    if ((r.multi_factor_score ?? 0) < f.minConf) return false;
    return true;
  });
  switch (f.sort) {
    case "gain": out.sort((a, b) => (b.price_change_pct ?? -1e9) - (a.price_change_pct ?? -1e9)); break;
    case "loss": out.sort((a, b) => (a.price_change_pct ?? 1e9) - (b.price_change_pct ?? 1e9)); break;
    case "accuracy":
      out.sort((a, b) => (b.prediction_correct === a.prediction_correct ? 0 : b.prediction_correct ? 1 : -1));
      break;
    case "confidence": out.sort((a, b) => (b.multi_factor_score ?? 0) - (a.multi_factor_score ?? 0)); break;
    default: /* keep model rank */ break;
  }
  return out;
}

function renderTable(rows, currency, mode) {
  const sym = currency === "USD" ? "$" : "₹";
  const tb = document.getElementById("rows");
  tb.innerHTML = rows
    .map((r, i) => {
      const pct = r.price_change_pct;
      const isForecast = pct == null;
      const chgCls = isForecast ? "chg--flat" : pct > 0 ? "chg--pos" : pct < 0 ? "chg--neg" : "chg--flat";
      const isSideways = r.predicted_direction === "sideways";
      let resultKind, resultLabel;
      if (isForecast) {
        resultKind = "neutral";
        resultLabel = "pending";
      } else if (isSideways) {
        resultKind = r.prediction_correct ? "ok" : "neutral";
        resultLabel = r.prediction_correct ? "✓ correct" : "✗ wrong";
      } else {
        resultKind = r.prediction_correct ? "ok" : "no";
        resultLabel = r.prediction_correct ? "✓ correct" : "✗ wrong";
      }
      const sigBadges = (r.technical_signals || []).map((s) => `<span class="badge">${esc(s)}</span>`).join("");
      const sup = (r.horizontal_levels?.support || []).map((v) => INR.format(v)).join(" / ") || "—";
      const res = (r.horizontal_levels?.resistance || []).map((v) => INR.format(v)).join(" / ") || "—";
      const conf = Math.max(0, Math.min(100, r.multi_factor_score ?? 0));
      const last = r.current_price != null ? `${sym}${INR.format(r.current_price)}` : `<span class="muted">—</span>`;
      const chgVal = r.price_change != null ? `${r.price_change >= 0 ? "+" : ""}${INR.format(r.price_change)}` : "—";
      const pctVal = pct != null ? PCT.format(pct) + "%" : "—";
      return `
        <tr data-result="${resultKind}" data-ticker="${esc(r.ticker)}">
          <td>${i + 1}</td>
          <td class="ticker-cell">${esc(r.ticker)}</td>
          <td class="company-cell" title="${esc(r.company || "")}">${esc(r.company || "")}</td>
          <td><span class="pill pill--${r.predicted_direction}">${r.predicted_direction}</span></td>
          <td class="num">${sym}${INR.format(r.previous_close)}</td>
          <td class="num">${last}</td>
          <td class="num ${chgCls}">${chgVal}</td>
          <td class="num ${chgCls}">${pctVal}</td>
          <td><span class="result result--${resultKind === "ok" ? "ok" : resultKind === "no" ? "no" : "neutral"}">${resultLabel}</span></td>
          <td>${sigBadges || `<span class="muted">—</span>`}</td>
          <td class="num">
            <div class="conf-cell">
              <span class="conf-num">${conf}</span>
              <div class="conf-bar"><span style="width:${conf}%"></span></div>
            </div>
          </td>
          <td class="sr-cell"><b>S</b> ${sup}<br><b>R</b> ${res}</td>
          <td class="grow"><span class="muted">${esc(r.notes || "")}</span></td>
        </tr>`;
    })
    .join("");
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

init();
