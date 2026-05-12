// Stockbot Industries — vanilla JS drill-down.
//
// Data layout (read-only from frontdesign/data/industries/):
//   industries.json                    { industries: [...], stock_index: [...] }
//   <slug>/index.json                  { stocks: [...] }
//   <slug>/<SYMBOL>.json               { daily: [...], news: [...], ... }
//
// Routes (hash-based):
//   #/                  industry grid + search
//   #/<slug>            industry detail (stock table)
//   #/<slug>/<symbol>   stock detail (sparkline + daily table + news feed)

const DATA_ROOT = "data/industries/";

const NUM2 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 2 });
const INT = new Intl.NumberFormat("en-IN");
const PCT_SIGN = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 1, minimumFractionDigits: 1, signDisplay: "exceptZero" });

const state = {
  index: null,                 // industries.json contents
  indexBySlug: new Map(),      // slug -> industry summary
  industryCache: new Map(),    // slug -> per-industry index.json
  stockCache: new Map(),       // "slug/SYMBOL" -> stock JSON
  search: "",
  sort: "n_desc",              // industry-grid sort
  stockSort: { col: "pct_chg_90d", dir: "desc" },  // industry-detail table sort
};

// ---------- bootstrap ----------
async function init() {
  initTheme();
  bindToolbar();
  try {
    state.index = await fetchJSON(DATA_ROOT + "industries.json");
  } catch (e) {
    document.getElementById("indView").innerHTML =
      `<div class="ind-empty">Could not load <code>${DATA_ROOT}industries.json</code>. ` +
      `Run <code>python scripts/build_industry_folders.py</code> first.</div>`;
    return;
  }
  state.indexBySlug = new Map(state.index.industries.map((i) => [i.slug, i]));
  document.getElementById("indGeneratedAt").textContent =
    `as of ${state.index.as_of} · window ${state.index.window_days}d`;
  document.getElementById("indMeta").textContent =
    `${INT.format(state.index.total_stocks)} stocks · ${state.index.n_industries} industries`;

  window.addEventListener("hashchange", route);
  route();
}

async function fetchJSON(path) {
  const r = await fetch(path, { cache: "no-store" });
  if (!r.ok) throw new Error(`fetch ${path}: ${r.status}`);
  return r.json();
}

function initTheme() {
  const saved = localStorage.getItem("sb-theme") || "light";
  document.documentElement.dataset.theme = saved;
  const btn = document.getElementById("themeToggle");
  if (!btn) return;
  btn.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("sb-theme", next);
  });
}

function bindToolbar() {
  const search = document.getElementById("indSearch");
  search.addEventListener("input", (e) => {
    state.search = e.target.value.trim();
    // search only meaningful on the grid view; otherwise jump home
    if (location.hash && location.hash !== "#/" && location.hash !== "#") {
      location.hash = "#/";
    } else {
      renderGrid();
    }
  });
}

// ---------- router ----------
function route() {
  const hash = (location.hash || "").replace(/^#\/?/, "");
  const parts = hash ? hash.split("/").filter(Boolean) : [];
  const view = document.getElementById("indView");
  if (parts.length === 0) {
    document.getElementById("indSearch").placeholder =
      "Search industries or stocks (e.g. steel, RELIANCE, banks)…";
    renderGrid();
  } else if (parts.length === 1) {
    document.getElementById("indSearch").value = "";
    state.search = "";
    document.getElementById("indSearch").placeholder = "Search… (or click an industry)";
    renderIndustryDetail(decodeURIComponent(parts[0]));
  } else {
    document.getElementById("indSearch").value = "";
    state.search = "";
    document.getElementById("indSearch").placeholder = "Search… (or click an industry)";
    renderStockDetail(decodeURIComponent(parts[0]), decodeURIComponent(parts[1]));
  }
  view.scrollIntoView({ block: "start", behavior: "instant" });
}

// ---------- views ----------
function renderGrid() {
  const view = document.getElementById("indView");
  const q = state.search.toLowerCase();

  // If the query matches a stock symbol/name, surface that first.
  let stockMatches = [];
  if (q.length >= 2) {
    stockMatches = state.index.stock_index
      .filter((s) => s.symbol.toLowerCase().includes(q) ||
                     (s.name || "").toLowerCase().includes(q))
      .slice(0, 12);
  }

  let industries = state.index.industries.slice();
  if (q) {
    industries = industries.filter((i) =>
      i.name.toLowerCase().includes(q) || i.slug.toLowerCase().includes(q));
  }
  industries.sort(industrySorter(state.sort));

  const sortOpts = [
    ["n_desc", "Most stocks"],
    ["pct_desc", "Best 90d %"],
    ["pct_asc", "Worst 90d %"],
    ["breadth_desc", "Best breadth"],
    ["news_desc", "Most news"],
    ["name_asc", "A → Z"],
  ];

  view.innerHTML = `
    <section class="ind-headline">
      <h1 class="ind-h1">Industries</h1>
      <p class="ind-lede">
        Indian listed universe filtered to <b>price ≥ ₹${state.index.universe_filter.min_price}</b>
        and <b>m-cap ≥ ₹${INT.format(state.index.universe_filter.min_mcap_cr)} cr</b>.
        Each tile rolls up the last <b>${state.index.window_days} days</b> of price, delivery and
        announcement flow. Click a tile to see its constituents.
      </p>
    </section>

    <section class="ind-sortrow">
      <span class="muted">Sort</span>
      ${sortOpts.map(([k, lab]) =>
        `<button class="chip ${k === state.sort ? "chip--on" : ""}" data-sort="${k}">${lab}</button>`
      ).join("")}
    </section>

    ${stockMatches.length ? `
      <section class="ind-section">
        <h2 class="ind-h2">Stock matches</h2>
        <div class="stock-quicklist">
          ${stockMatches.map((s) => `
            <a class="stock-quick" href="#/${esc(s.industry_slug)}/${esc(s.symbol)}">
              <span class="stock-quick__sym">${esc(s.symbol)}</span>
              <span class="stock-quick__name">${esc(s.name || "")}</span>
              <span class="stock-quick__slug">${esc(s.industry_slug)}</span>
            </a>
          `).join("")}
        </div>
      </section>
    ` : ""}

    <section class="industry-grid">
      ${industries.map((i, idx) => industryCard(i, idx)).join("") || `<div class="ind-empty">No industries match “${esc(q)}”.</div>`}
    </section>
  `;

  view.querySelectorAll("[data-sort]").forEach((b) => {
    b.addEventListener("click", () => {
      state.sort = b.dataset.sort;
      renderGrid();
    });
  });
}

function industrySorter(key) {
  switch (key) {
    case "pct_desc":     return (a, b) => (b.median_pct_chg_90d ?? -1e9) - (a.median_pct_chg_90d ?? -1e9);
    case "pct_asc":      return (a, b) => (a.median_pct_chg_90d ??  1e9) - (b.median_pct_chg_90d ??  1e9);
    case "breadth_desc": return (a, b) => (b.pct_stocks_up ?? 0) - (a.pct_stocks_up ?? 0);
    case "news_desc":    return (a, b) => (b.news_count_90d ?? 0) - (a.news_count_90d ?? 0);
    case "name_asc":     return (a, b) => a.name.localeCompare(b.name);
    case "n_desc":
    default:             return (a, b) => b.n_stocks - a.n_stocks;
  }
}

function industryCard(i, idx = 0) {
  const pct = i.median_pct_chg_90d;
  const breadth = i.pct_stocks_up ?? 0;
  const heatClass = pct == null
    ? "heat--flat"
    : pct >= 8 ? "heat--hot"
    : pct >= 2 ? "heat--warm"
    : pct >= -2 ? "heat--flat"
    : pct >= -8 ? "heat--cool"
    : "heat--cold";

  return `
    <a class="industry-card" href="#/${esc(i.slug)}" style="--i:${idx}">
      <div class="industry-card__head">
        <div class="industry-card__name">${esc(i.name)}</div>
        <span class="heat-badge ${heatClass}">${pct == null ? "—" : PCT_SIGN.format(pct) + "%"}</span>
      </div>
      <div class="industry-card__stats">
        <span><b>${INT.format(i.n_stocks)}</b> stocks</span>
        <span>${i.median_delivery_pct == null ? "—" : NUM2.format(i.median_delivery_pct)}% deliv</span>
        <span>${i.news_count_90d == null ? "—" : INT.format(i.news_count_90d)} news</span>
      </div>
      <div class="breadth-bar" title="${breadth.toFixed ? breadth.toFixed(0) : breadth}% of stocks positive over ${state.index.window_days}d">
        <span style="width:${Math.max(0, Math.min(100, breadth))}%"></span>
      </div>
    </a>
  `;
}

async function renderIndustryDetail(slug) {
  const view = document.getElementById("indView");
  const ind = state.indexBySlug.get(slug);
  if (!ind) {
    view.innerHTML = `<div class="ind-empty">Unknown industry: <code>${esc(slug)}</code>.
      <a href="#/">Back to grid</a>.</div>`;
    return;
  }

  view.innerHTML = `
    <nav class="crumbs"><a href="#/">Industries</a> <span>›</span> <span>${esc(ind.name)}</span></nav>
    <section class="ind-headline">
      <h1 class="ind-h1">${esc(ind.name)}</h1>
      <p class="ind-lede">${INT.format(ind.n_stocks)} eligible stocks ·
        median 90d ${ind.median_pct_chg_90d == null ? "—" : PCT_SIGN.format(ind.median_pct_chg_90d) + "%"} ·
        ${ind.pct_stocks_up == null ? "—" : ind.pct_stocks_up.toFixed(0) + "% positive"} ·
        ${ind.news_count_90d == null ? "—" : INT.format(ind.news_count_90d)} announcements (90d)
      </p>
    </section>
    <section class="ind-loading"><span class="spinner"></span> loading constituents…</section>
  `;

  let detail = state.industryCache.get(slug);
  if (!detail) {
    try {
      detail = await fetchJSON(`${DATA_ROOT}${encodeURIComponent(slug)}/index.json`);
    } catch (e) {
      view.querySelector(".ind-loading").outerHTML =
        `<div class="ind-empty">Failed to load <code>${slug}/index.json</code>.</div>`;
      return;
    }
    state.industryCache.set(slug, detail);
  }

  // Drop the loading placeholder, render the table
  const loading = view.querySelector(".ind-loading");
  if (loading) loading.remove();

  const stocks = detail.stocks.slice();
  stocks.sort(stockSorter(state.stockSort));

  const colCfg = [
    ["symbol", "Symbol", "left"],
    ["name", "Company", "left grow"],
    ["price", "Price", "num"],
    ["market_cap_cr", "Mcap (₹ cr)", "num"],
    ["pct_chg_90d", "90d %", "num"],
    ["news_count", "News", "num"],
  ];

  const tbl = `
    <section class="tablewrap">
      <table class="ptable stocktable">
        <thead><tr>
          ${colCfg.map(([k, lab, cls]) => {
            const dir = state.stockSort.col === k ? state.stockSort.dir : "";
            const arrow = dir === "asc" ? " ↑" : dir === "desc" ? " ↓" : "";
            return `<th class="${cls}" data-sort="${k}">${lab}${arrow}</th>`;
          }).join("")}
        </tr></thead>
        <tbody>
          ${stocks.map((s) => `
            <tr class="stockrow" data-href="#/${esc(slug)}/${esc(s.symbol)}">
              <td class="ticker"><a href="#/${esc(slug)}/${esc(s.symbol)}">${esc(s.symbol)}</a></td>
              <td class="grow">${esc(s.name || "")}</td>
              <td class="num">${s.price == null ? "—" : NUM2.format(s.price)}</td>
              <td class="num">${s.market_cap_cr == null ? "—" : INT.format(Math.round(s.market_cap_cr))}</td>
              <td class="num ${pctClass(s.pct_chg_90d)}">${s.pct_chg_90d == null ? "—" : PCT_SIGN.format(s.pct_chg_90d) + "%"}</td>
              <td class="num">${INT.format(s.news_count || 0)}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </section>
  `;
  view.insertAdjacentHTML("beforeend", tbl);

  view.querySelectorAll("th[data-sort]").forEach((th) => {
    th.addEventListener("click", () => {
      const col = th.dataset.sort;
      if (state.stockSort.col === col) {
        state.stockSort.dir = state.stockSort.dir === "asc" ? "desc" : "asc";
      } else {
        state.stockSort.col = col;
        state.stockSort.dir = (col === "symbol" || col === "name") ? "asc" : "desc";
      }
      renderIndustryDetail(slug);
    });
  });
  view.querySelectorAll("tr.stockrow").forEach((tr) => {
    tr.addEventListener("click", (e) => {
      if (e.target.tagName === "A") return;
      location.hash = tr.dataset.href;
    });
  });
}

function stockSorter({ col, dir }) {
  const m = dir === "asc" ? 1 : -1;
  return (a, b) => {
    const av = a[col], bv = b[col];
    if (typeof av === "string" || typeof bv === "string") {
      return m * String(av || "").localeCompare(String(bv || ""));
    }
    return m * ((av ?? -Infinity) - (bv ?? -Infinity));
  };
}

function pctClass(v) {
  if (v == null) return "";
  if (v > 0) return "pos";
  if (v < 0) return "neg";
  return "";
}

async function renderStockDetail(slug, symbol) {
  const view = document.getElementById("indView");
  const ind = state.indexBySlug.get(slug);
  view.innerHTML = `
    <nav class="crumbs">
      <a href="#/">Industries</a> <span>›</span>
      <a href="#/${esc(slug)}">${esc(ind ? ind.name : slug)}</a> <span>›</span>
      <span>${esc(symbol)}</span>
    </nav>
    <section class="ind-loading"><span class="spinner"></span> loading ${esc(symbol)}…</section>
  `;

  const key = `${slug}/${symbol}`;
  let stock = state.stockCache.get(key);
  if (!stock) {
    try {
      stock = await fetchJSON(`${DATA_ROOT}${encodeURIComponent(slug)}/${encodeURIComponent(symbol)}.json`);
    } catch (e) {
      view.querySelector(".ind-loading").outerHTML =
        `<div class="ind-empty">Failed to load ${esc(symbol)}.</div>`;
      return;
    }
    state.stockCache.set(key, stock);
  }

  view.querySelector(".ind-loading").remove();

  const last = stock.daily[stock.daily.length - 1] || {};
  const first = stock.daily[0] || {};
  const pct = first.close && last.close
    ? ((last.close - first.close) / first.close) * 100
    : null;
  const closes = stock.daily.map((d) => d.close).filter((v) => v != null);
  const recentRows = stock.daily.slice(-15).reverse();

  view.insertAdjacentHTML("beforeend", `
    <section class="stock-detail">
      <div class="stock-left">
        <header class="stock-head">
          <div>
            <div class="stock-sym">${esc(stock.symbol)}</div>
            <div class="stock-name">${esc(stock.name || "")}</div>
          </div>
          <div class="stock-price">
            <div class="stock-price__val">₹${last.close == null ? "—" : NUM2.format(last.close)}</div>
            <div class="stock-price__chg ${pctClass(pct)}">
              ${pct == null ? "—" : PCT_SIGN.format(pct) + "%"}
              <span class="muted"> · ${stock.window_days}d</span>
            </div>
          </div>
        </header>

        <div class="stock-meta">
          <span>Industry: <a href="#/${esc(slug)}">${esc(stock.industry)}</a></span>
          ${stock.sector ? `<span>Sector: ${esc(stock.sector)}</span>` : ""}
          ${stock.market_cap_cr ? `<span>Mcap: ₹${INT.format(Math.round(stock.market_cap_cr))} cr</span>` : ""}
          ${stock.isin ? `<span>ISIN: ${esc(stock.isin)}</span>` : ""}
        </div>

        <div class="sparkline-wrap">
          ${sparkline(closes)}
          <div class="sparkline-axis">
            <span>${esc(first.date || "")}</span>
            <span>${esc(last.date || "")}</span>
          </div>
        </div>

        <h3 class="ind-h3">Recent daily activity</h3>
        <div class="tablewrap">
          <table class="ptable stocktable">
            <thead><tr>
              <th>Date</th>
              <th class="num">Close</th>
              <th class="num">Δ</th>
              <th class="num">Volume</th>
              <th class="num">Deliverable qty</th>
              <th class="num">Deliv %</th>
            </tr></thead>
            <tbody>
              ${recentRows.map((r) => {
                const ch = r.close != null && r.prev_close
                  ? ((r.close - r.prev_close) / r.prev_close) * 100
                  : null;
                return `
                  <tr>
                    <td>${esc(r.date)}</td>
                    <td class="num">${r.close == null ? "—" : NUM2.format(r.close)}</td>
                    <td class="num ${pctClass(ch)}">${ch == null ? "—" : PCT_SIGN.format(ch) + "%"}</td>
                    <td class="num">${r.volume == null ? "—" : INT.format(r.volume)}</td>
                    <td class="num">${r.deliverable_qty == null ? "—" : INT.format(r.deliverable_qty)}</td>
                    <td class="num">${r.delivery_pct == null ? "—" : NUM2.format(r.delivery_pct)}</td>
                  </tr>
                `;
              }).join("")}
            </tbody>
          </table>
        </div>
      </div>

      <aside class="stock-right">
        <h3 class="ind-h3">News &amp; announcements
          <span class="muted news-count">${stock.news.length} in last ${stock.window_days}d</span>
        </h3>
        ${renderNewsFeed(stock.news)}
      </aside>
    </section>
  `);
}

function sparkline(values, w = 720, h = 160) {
  if (!values || values.length < 2) return `<div class="sparkline-empty muted">No price history</div>`;
  const min = Math.min(...values), max = Math.max(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * w;
    const y = h - ((v - min) / span) * h;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
  const last = values[values.length - 1];
  const lastY = h - ((last - min) / span) * h;
  const up = values[values.length - 1] >= values[0];
  return `
    <svg class="sparkline ${up ? "up" : "down"}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
      <polyline points="${pts}" />
      <circle cx="${w}" cy="${lastY.toFixed(2)}" r="3" />
    </svg>
  `;
}

function renderNewsFeed(news) {
  if (!news || news.length === 0) {
    return `<div class="ind-empty">No announcements in this window.</div>`;
  }
  return `
    <ul class="news-feed">
      ${news.map((n) => `
        <li>
          <div class="news-feed__row">
            <span class="news-date">${esc((n.sort_date || "").slice(0, 10))}</span>
            <span class="news-desc">${esc(n.desc || "Announcement")}</span>
          </div>
          ${n.attchmntFile
            ? `<a class="news-link" href="${esc(n.attchmntFile)}" target="_blank" rel="noopener noreferrer">Open PDF →</a>`
            : `<span class="muted">no attachment</span>`}
        </li>
      `).join("")}
    </ul>
  `;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

init();
