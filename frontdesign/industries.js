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
const LIVE_URL = "data/industries/_live_news.json";
// Sibling of latest.json — not under data/industries/ because build_industry_folders.py
// wipes that directory nightly and would prune the rolling 2-day cache.
const AFTER_MARKET_URL = "data/_after_market_news.json";
const HISTORY_URL = "data/_news_history.json";
const LIVE_POLL_MS = 30_000;
const LIVE_TICKER_MAX = 8;
const LIVE_NEW_FADE_MS = 60_000;
const AFTER_MARKET_CUTOFF = "15:30";  // HH:MM IST — items at/after this are "after-market"
const MONTHS_SHORT = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

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
  live: {
    items: [],                 // most recent live announcements (whole universe)
    seenSeqs: new Set(),       // seq_ids ever observed (for NEW-pill detection)
    polledAt: null,
    marketState: null,
    currentSymbol: null,       // tracks which stock-detail view is rendered
    newSeqs: new Set(),        // seq_ids first surfaced since the most recent poll
  },
  afterMarket: {
    items: [],                 // rolling N-day after-market filings
    tradingDays: [],           // ["YYYY-MM-DD", ...] newest first
    updatedAt: null,
    selectedDay: null,         // currently viewed day; defaults to most recent
    selectedSlug: null,        // currently expanded industry tile (null = none)
  },
  history: {
    items: [],                 // append-only archive of every announcement
    tradingDays: [],           // ["YYYY-MM-DD", ...] newest first
    updatedAt: null,
    selectedDay: null,         // currently viewed day; defaults to most recent non-today
    selectedSlug: null,
  },
  newsFilter: { slug: "all", q: "" },
};
let livePollTimer = null;

// ---------- bootstrap ----------
async function init() {
  initTheme();
  initNavToggle();
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
  startLivePolling();
}

// Compute current NSE market state from the user's clock converted to IST.
// Independent of _live_news.json so the indicator stays accurate even when
// polling is stalled (GitHub Actions dropping cron, etc.).
function currentMarketStateIST() {
  const ist = new Date(new Date().toLocaleString("en-US", { timeZone: "Asia/Kolkata" }));
  const day = ist.getDay();          // 0=Sun, 6=Sat
  if (day === 0 || day === 6) return "closed";
  const minutes = ist.getHours() * 60 + ist.getMinutes();
  if (minutes < 9 * 60) return "closed";
  if (minutes < 9 * 60 + 15) return "preopen";
  if (minutes < 15 * 60 + 30) return "open";
  return "closed";
}

// ---------- live polling ----------
async function pollLive() {
  const bust = Math.floor(Date.now() / 30_000);
  const [liveRes, afterRes, histRes] = await Promise.allSettled([
    fetch(`${LIVE_URL}?t=${bust}`, { cache: "no-store" }),
    fetch(`${AFTER_MARKET_URL}?t=${bust}`, { cache: "no-store" }),
    fetch(HISTORY_URL),
  ]);

  let firstPoll = state.live.polledAt === null;
  let newSeqs = [];
  if (liveRes.status === "fulfilled" && liveRes.value.ok) {
    try {
      const next = await liveRes.value.json();
      const incomingSeqs = new Set((next.items || []).map((i) => i.seq_id));
      for (const seq of incomingSeqs) {
        if (!state.live.seenSeqs.has(seq)) newSeqs.push(seq);
      }
      state.live.items = next.items || [];
      state.live.polledAt = next.polled_at;
      state.live.marketState = next.market_state;
      state.live.newSeqs = firstPoll ? new Set() : new Set(newSeqs);
      incomingSeqs.forEach((s) => state.live.seenSeqs.add(s));
    } catch (e) { console.warn("Failed to parse _live_news.json:", e); }
  }

  if (afterRes.status === "fulfilled" && afterRes.value.ok) {
    try {
      const am = await afterRes.value.json();
      state.afterMarket.items = am.items || [];
      state.afterMarket.tradingDays = am.trading_days || [];
      state.afterMarket.updatedAt = am.updated_at || null;
    } catch (e) { console.warn("Failed to parse _after_market_news.json:", e); }
  }

  if (histRes.status === "fulfilled" && histRes.value.ok) {
    try {
      const h = await histRes.value.json();
      state.history.items = h.items || [];
      state.history.tradingDays = h.trading_days || [];
      state.history.updatedAt = h.updated_at || null;
    } catch (e) { console.warn("Failed to parse _news_history.json:", e); }
  }

  renderLiveTicker();
  updateNavNewsBadge();
  if (!firstPoll && newSeqs.length) {
    mergeLiveIntoStockDetail(newSeqs);
    if (location.hash === "#/news" || location.hash.startsWith("#/news")) {
      renderNewsPage();
    }
  } else if (firstPoll && (location.hash === "#/news" || location.hash.startsWith("#/news"))) {
    renderNewsPage();
  }
  if (location.hash === "#/after-market" || location.hash.startsWith("#/after-market")) {
    renderAfterMarketPage();
  }
  if (location.hash === "#/yesterday" || location.hash.startsWith("#/yesterday")) {
    renderYesterdayPage();
  }
  if (newSeqs.length) {
    setTimeout(() => {
      state.live.newSeqs = new Set();
    }, LIVE_NEW_FADE_MS);
  }
}

function updateNavNewsBadge() {
  const newsBadge = document.getElementById("navNewsBadge");
  if (newsBadge) {
    const n = state.live.items.length;
    newsBadge.hidden = n === 0;
    newsBadge.textContent = String(n);
  }
  const afterBadge = document.getElementById("navAfterBadge");
  if (afterBadge) {
    const n = state.afterMarket.items.length;
    afterBadge.hidden = n === 0;
    afterBadge.textContent = String(n);
  }
  const ydayBadge = document.getElementById("navYdayBadge");
  if (ydayBadge) {
    // Count items from prior trading days only (today doesn't belong on "Yesterday")
    const days = state.history.tradingDays || [];
    const todayStr = days[0] || "";
    let n = 0;
    for (const it of state.history.items) {
      const d = (it.sort_date || "").slice(0, 10);
      if (d && d !== todayStr) n++;
    }
    ydayBadge.hidden = n === 0;
    ydayBadge.textContent = String(n);
  }
}

function startLivePolling() {
  if (livePollTimer) return;
  pollLive();
  livePollTimer = setInterval(pollLive, LIVE_POLL_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      clearInterval(livePollTimer);
      livePollTimer = null;
    } else if (!livePollTimer) {
      pollLive();
      livePollTimer = setInterval(pollLive, LIVE_POLL_MS);
    }
  });
}

function renderNewsPage() {
  const view = document.getElementById("indView");
  const all = state.live.items || [];
  const q = (state.newsFilter.q || "").toLowerCase();
  const slug = state.newsFilter.slug;

  // Industry counts (over the whole live set, regardless of text filter)
  const slugCounts = new Map();
  for (const it of all) {
    slugCounts.set(it.industry_slug, (slugCounts.get(it.industry_slug) || 0) + 1);
  }
  const slugChips = [...slugCounts.entries()]
    .map(([s, n]) => ({
      slug: s,
      name: state.indexBySlug.get(s)?.name || s,
      n,
    }))
    .sort((a, b) => b.n - a.n)
    .slice(0, 12);

  const filtered = all.filter((it) => {
    if (slug !== "all" && it.industry_slug !== slug) return false;
    if (!q) return true;
    const hay = `${it.symbol} ${it.industry_slug} ${it.desc || ""} ${it.sm_name || ""}`.toLowerCase();
    return hay.includes(q);
  });

  const polled = state.live.polledAt
    ? state.live.polledAt.slice(11, 16)
    : "—";
  const marketState = currentMarketStateIST();
  const marketLabel = {
    open: "Market open",
    preopen: "Pre-open",
    closed: "Market closed",
  }[marketState] || "Market —";

  view.innerHTML = `
    <nav class="crumbs">
      <a href="#/">Industries</a> <span>›</span> <span>News desk</span>
    </nav>

    <section class="ind-headline news-headline">
      <div class="news-headline__main">
        <h1 class="ind-h1">The Announcement Desk</h1>
        <p class="ind-lede">
          Every corporate filing posted to NSE for stocks in the eligible universe,
          as it arrives. The page refreshes every 30&nbsp;seconds while the market
          is open — new items pulse in at the top with a saffron flash.
        </p>
      </div>
      <aside class="news-headline__meta">
        <div class="news-meta-row">
          <span class="news-meta-dot ${marketState ? `news-meta-dot--${marketState}` : ""}"></span>
          <span class="news-meta-label">${esc(marketLabel)}</span>
        </div>
        <div class="news-meta-row">
          <span class="news-meta-label">Polled</span>
          <span class="news-meta-value">${esc(polled)} IST</span>
        </div>
        <div class="news-meta-row">
          <span class="news-meta-label">Items</span>
          <span class="news-meta-value">${INT.format(filtered.length)}${filtered.length !== all.length ? ` / ${INT.format(all.length)}` : ""}</span>
        </div>
      </aside>
    </section>

    ${slugChips.length ? `
      <section class="news-filter-row">
        <span class="muted">Industry</span>
        <button class="chip ${slug === "all" ? "chip--on" : ""}" data-slug="all">All${all.length ? ` (${INT.format(all.length)})` : ""}</button>
        ${slugChips.map((c) => `
          <button class="chip ${slug === c.slug ? "chip--on" : ""}" data-slug="${esc(c.slug)}">
            ${esc(c.name)} <span class="chip__n">${INT.format(c.n)}</span>
          </button>
        `).join("")}
      </section>
    ` : ""}

    ${filtered.length === 0
      ? `<section class="ind-empty">
           ${all.length === 0
              ? "No announcements yet today. New items will appear here as NSE files come in."
              : "Nothing matches your filter."}
         </section>`
      : `<ol class="news-list-page">
           ${filtered.map((it) => newsRowHTML(it)).join("")}
         </ol>`}
  `;

  view.querySelectorAll("button[data-slug]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.newsFilter.slug = btn.dataset.slug;
      renderNewsPage();
    });
  });

  // Schedule NEW-pill auto-fade
  if (state.live.newSeqs.size) {
    setTimeout(() => {
      document.querySelectorAll(".news-list-page li.news--new").forEach((li) => {
        li.classList.remove("news--new");
      });
    }, LIVE_NEW_FADE_MS);
  }
}

function formatShortDate(yyyymmdd) {
  // "2026-05-12" -> "12 May"
  if (!yyyymmdd || yyyymmdd.length < 10) return yyyymmdd || "";
  const y = yyyymmdd.slice(0, 4);
  const m = parseInt(yyyymmdd.slice(5, 7), 10);
  const d = parseInt(yyyymmdd.slice(8, 10), 10);
  if (!m || !d) return yyyymmdd;
  return `${d} ${MONTHS_SHORT[m - 1]}`;
}

function formatDayHeading(yyyymmdd, isToday) {
  if (!yyyymmdd) return "";
  const short = formatShortDate(yyyymmdd);
  return isToday ? `Today · ${short}` : short;
}

function renderAfterMarketPage() {
  const view = document.getElementById("indView");
  const items = state.afterMarket.items || [];
  const tradingDays = (state.afterMarket.tradingDays || []).slice();
  // Defensive: derive trading_days from items if the file omitted them
  if (tradingDays.length === 0 && items.length) {
    const seen = new Set();
    for (const it of items) {
      const d = (it.sort_date || "").slice(0, 10);
      if (d) seen.add(d);
    }
    tradingDays.push(...[...seen].sort().reverse());
  }
  const todayStr = tradingDays[0] || "";

  // Default selection to the most recent day; reset if stale
  if (!state.afterMarket.selectedDay || !tradingDays.includes(state.afterMarket.selectedDay)) {
    state.afterMarket.selectedDay = todayStr;
  }
  const selectedDay = state.afterMarket.selectedDay;

  const updated = state.afterMarket.updatedAt
    ? state.afterMarket.updatedAt.slice(11, 16)
    : (state.live.polledAt ? state.live.polledAt.slice(11, 16) : "—");
  const marketState = currentMarketStateIST();
  const marketLabel = {
    open: "Market open",
    preopen: "Pre-open",
    closed: "Market closed",
  }[marketState] || "Market —";

  // Per-day filing counts (for the date strip)
  const countByDay = new Map();
  for (const day of tradingDays) countByDay.set(day, 0);
  for (const it of items) {
    const d = (it.sort_date || "").slice(0, 10);
    if (countByDay.has(d)) countByDay.set(d, countByDay.get(d) + 1);
  }

  // Industry roll-up for the *selected* day
  const dayItems = items.filter((it) => (it.sort_date || "").slice(0, 10) === selectedDay);
  const byIndustry = new Map();
  const companiesByIndustry = new Map();
  for (const it of dayItems) {
    const slug = it.industry_slug || "unknown";
    if (!byIndustry.has(slug)) {
      byIndustry.set(slug, []);
      companiesByIndustry.set(slug, new Set());
    }
    byIndustry.get(slug).push(it);
    if (it.symbol) companiesByIndustry.get(slug).add(it.symbol);
  }
  const groups = [...byIndustry.entries()]
    .map(([slug, list]) => ({
      slug,
      name: state.indexBySlug.get(slug)?.name || slug,
      items: list.sort((a, b) => (b.sort_date || "").localeCompare(a.sort_date || "")),
      uniq: companiesByIndustry.get(slug).size,
    }))
    .sort((a, b) => b.items.length - a.items.length || a.name.localeCompare(b.name));

  // Auto-collapse the open tile if it doesn't appear on the chosen day
  const openSlugs = new Set(groups.map((g) => g.slug));
  if (state.afterMarket.selectedSlug && !openSlugs.has(state.afterMarket.selectedSlug)) {
    state.afterMarket.selectedSlug = null;
  }
  const selectedSlug = state.afterMarket.selectedSlug;

  const isToday = selectedDay === todayStr;
  const dayTotal = dayItems.length;
  const sectorCount = groups.length;

  // ---- Date strip
  const dayChipsHTML = tradingDays.map((day) => {
    const n = countByDay.get(day) || 0;
    const isActive = day === selectedDay;
    const dLabel = day === todayStr ? "Today" : formatShortDate(day);
    const yyyy = day.slice(0, 4);
    return `
      <button type="button"
              class="am-day ${isActive ? "am-day--on" : ""}"
              data-am-day="${esc(day)}">
        <span class="am-day__label">${esc(dLabel)}</span>
        <span class="am-day__year">${esc(yyyy)}</span>
        <span class="am-day__count">${INT.format(n)}</span>
      </button>`;
  }).join("");

  // ---- Tile grid (with inline detail drawer after the active tile)
  const tilesHTML = groups.map((g, idx) => {
    const active = g.slug === selectedSlug;
    const tile = `
      <button type="button"
              class="am-tile ${active ? "am-tile--on" : ""}"
              data-am-slug="${esc(g.slug)}"
              aria-expanded="${active ? "true" : "false"}">
        <span class="am-tile__rank">${String(idx + 1).padStart(2, "0")}</span>
        <span class="am-tile__name">${esc(g.name)}</span>
        <span class="am-tile__meta">
          <span class="am-tile__count">${INT.format(g.items.length)}</span>
          <span class="am-tile__unit">filing${g.items.length === 1 ? "" : "s"}</span>
        </span>
        <span class="am-tile__sub">${INT.format(g.uniq)} compan${g.uniq === 1 ? "y" : "ies"}</span>
        <span class="am-tile__caret" aria-hidden="true">${active ? "▾" : "▸"}</span>
      </button>`;

    if (!active) return tile;

    const detailRows = g.items.map((it) => newsRowHTML(it, { showShortDate: !isToday })).join("");
    const drawer = `
      <section class="am-drawer" data-am-drawer>
        <header class="am-drawer__head">
          <div class="am-drawer__title">
            <span class="am-drawer__eyebrow">Filings · ${esc(formatShortDate(selectedDay))}${isToday ? " · Today" : ""}</span>
            <h3 class="am-drawer__name">
              <a href="#/${esc(g.slug)}">${esc(g.name)} →</a>
            </h3>
          </div>
          <div class="am-drawer__stats">
            <span><b>${INT.format(g.items.length)}</b> filings</span>
            <span><b>${INT.format(g.uniq)}</b> ${g.uniq === 1 ? "company" : "companies"}</span>
          </div>
          <button type="button" class="am-drawer__close" data-am-close aria-label="Collapse">✕</button>
        </header>
        <ol class="news-list-page am-drawer__list">${detailRows}</ol>
      </section>`;
    return tile + drawer;
  }).join("");

  view.innerHTML = `
    <nav class="crumbs">
      <a href="#/">Industries</a> <span>›</span> <span>After-Market Filings</span>
    </nav>

    <section class="ind-headline news-headline">
      <div class="news-headline__main">
        <h1 class="ind-h1">After-Market Filings</h1>
        <p class="ind-lede">
          NSE corporate announcements filed after 15:30 IST — board-meeting
          outcomes, quarterly results, dividends, Reg 30 disclosures.
          Pick a day, then click a sector tile to read its filings.
          Page updates every 30 seconds.
        </p>
      </div>
      <aside class="news-headline__meta">
        <div class="news-meta-row">
          <span class="news-meta-dot ${marketState ? `news-meta-dot--${marketState}` : ""}"></span>
          <span class="news-meta-label">${esc(marketLabel)}</span>
        </div>
        <div class="news-meta-row">
          <span class="news-meta-label">Updated</span>
          <span class="news-meta-value">${esc(updated)} IST</span>
        </div>
        <div class="news-meta-row">
          <span class="news-meta-label">In window</span>
          <span class="news-meta-value">${INT.format(tradingDays.length)} day${tradingDays.length === 1 ? "" : "s"}</span>
        </div>
      </aside>
    </section>

    ${tradingDays.length === 0
      ? `<section class="ind-empty">
           No after-market filings yet — the first window opens at 15:30 IST.
         </section>`
      : `
        <section class="am-daystrip" aria-label="Trading day">
          <span class="am-daystrip__label">Day</span>
          <div class="am-daystrip__rail" id="amDayRail">
            ${dayChipsHTML}
          </div>
        </section>

        <section class="am-summary">
          <span class="am-summary__num">${INT.format(dayTotal)}</span>
          <span class="am-summary__txt">filing${dayTotal === 1 ? "" : "s"} across ${INT.format(sectorCount)} sector${sectorCount === 1 ? "" : "s"} · ${esc(formatShortDate(selectedDay))}${isToday ? " (today)" : ""}</span>
        </section>

        ${groups.length === 0
          ? `<section class="ind-empty">
               No after-market filings recorded for ${esc(formatShortDate(selectedDay))}.
             </section>`
          : `<section class="am-grid" id="amGrid">${tilesHTML}</section>`}
      `}
  `;

  // ---- event delegation
  view.querySelectorAll("button[data-am-day]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.afterMarket.selectedDay = btn.dataset.amDay;
      state.afterMarket.selectedSlug = null;
      renderAfterMarketPage();
    });
  });
  view.querySelectorAll("button[data-am-slug]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const next = btn.dataset.amSlug;
      state.afterMarket.selectedSlug = state.afterMarket.selectedSlug === next ? null : next;
      renderAfterMarketPage();
      // After re-render, scroll the open drawer into view
      requestAnimationFrame(() => {
        const drawer = view.querySelector("[data-am-drawer]");
        if (drawer) drawer.scrollIntoView({ block: "nearest", behavior: "smooth" });
      });
    });
  });
  view.querySelectorAll("[data-am-close]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      state.afterMarket.selectedSlug = null;
      renderAfterMarketPage();
    });
  });
}

function renderYesterdayPage() {
  const view = document.getElementById("indView");
  const items = state.history.items || [];
  const tradingDays = (state.history.tradingDays || []).slice();
  // Defensive: derive trading_days from items if the file omitted them
  if (tradingDays.length === 0 && items.length) {
    const seen = new Set();
    for (const it of items) {
      const d = (it.sort_date || "").slice(0, 10);
      if (d) seen.add(d);
    }
    tradingDays.push(...[...seen].sort().reverse());
  }
  const todayStr = tradingDays[0] || "";
  // For the "Yesterday" page, prefer the second-most-recent day on first land
  const defaultDay = tradingDays.length > 1 ? tradingDays[1] : todayStr;

  if (!state.history.selectedDay || !tradingDays.includes(state.history.selectedDay)) {
    state.history.selectedDay = defaultDay;
  }
  const selectedDay = state.history.selectedDay;

  const updated = state.history.updatedAt
    ? state.history.updatedAt.slice(11, 16)
    : (state.live.polledAt ? state.live.polledAt.slice(11, 16) : "—");
  const marketState = currentMarketStateIST();
  const marketLabel = {
    open: "Market open",
    preopen: "Pre-open",
    closed: "Market closed",
  }[marketState] || "Market —";

  const countByDay = new Map();
  for (const day of tradingDays) countByDay.set(day, 0);
  for (const it of items) {
    const d = (it.sort_date || "").slice(0, 10);
    if (countByDay.has(d)) countByDay.set(d, countByDay.get(d) + 1);
  }

  const dayItems = items.filter((it) => (it.sort_date || "").slice(0, 10) === selectedDay);
  const byIndustry = new Map();
  const companiesByIndustry = new Map();
  for (const it of dayItems) {
    const slug = it.industry_slug || "unknown";
    if (!byIndustry.has(slug)) {
      byIndustry.set(slug, []);
      companiesByIndustry.set(slug, new Set());
    }
    byIndustry.get(slug).push(it);
    if (it.symbol) companiesByIndustry.get(slug).add(it.symbol);
  }
  const groups = [...byIndustry.entries()]
    .map(([slug, list]) => ({
      slug,
      name: state.indexBySlug.get(slug)?.name || slug,
      items: list.sort((a, b) => (b.sort_date || "").localeCompare(a.sort_date || "")),
      uniq: companiesByIndustry.get(slug).size,
    }))
    .sort((a, b) => b.items.length - a.items.length || a.name.localeCompare(b.name));

  const openSlugs = new Set(groups.map((g) => g.slug));
  if (state.history.selectedSlug && !openSlugs.has(state.history.selectedSlug)) {
    state.history.selectedSlug = null;
  }
  const selectedSlug = state.history.selectedSlug;

  const isToday = selectedDay === todayStr;
  const dayTotal = dayItems.length;
  const sectorCount = groups.length;

  const dayChipsHTML = tradingDays.map((day) => {
    const n = countByDay.get(day) || 0;
    const isActive = day === selectedDay;
    const dLabel = day === todayStr ? "Today" : formatShortDate(day);
    const yyyy = day.slice(0, 4);
    return `
      <button type="button"
              class="am-day ${isActive ? "am-day--on" : ""}"
              data-yd-day="${esc(day)}">
        <span class="am-day__label">${esc(dLabel)}</span>
        <span class="am-day__year">${esc(yyyy)}</span>
        <span class="am-day__count">${INT.format(n)}</span>
      </button>`;
  }).join("");

  const tilesHTML = groups.map((g, idx) => {
    const active = g.slug === selectedSlug;
    const tile = `
      <button type="button"
              class="am-tile ${active ? "am-tile--on" : ""}"
              data-yd-slug="${esc(g.slug)}"
              aria-expanded="${active ? "true" : "false"}">
        <span class="am-tile__rank">${String(idx + 1).padStart(2, "0")}</span>
        <span class="am-tile__name">${esc(g.name)}</span>
        <span class="am-tile__meta">
          <span class="am-tile__count">${INT.format(g.items.length)}</span>
          <span class="am-tile__unit">item${g.items.length === 1 ? "" : "s"}</span>
        </span>
        <span class="am-tile__sub">${INT.format(g.uniq)} compan${g.uniq === 1 ? "y" : "ies"}</span>
        <span class="am-tile__caret" aria-hidden="true">${active ? "▾" : "▸"}</span>
      </button>`;

    if (!active) return tile;

    const detailRows = g.items.map((it) => newsRowHTML(it, { showShortDate: !isToday })).join("");
    const drawer = `
      <section class="am-drawer" data-yd-drawer>
        <header class="am-drawer__head">
          <div class="am-drawer__title">
            <span class="am-drawer__eyebrow">News · ${esc(formatShortDate(selectedDay))}${isToday ? " · Today" : ""}</span>
            <h3 class="am-drawer__name">
              <a href="#/${esc(g.slug)}">${esc(g.name)} →</a>
            </h3>
          </div>
          <div class="am-drawer__stats">
            <span><b>${INT.format(g.items.length)}</b> items</span>
            <span><b>${INT.format(g.uniq)}</b> ${g.uniq === 1 ? "company" : "companies"}</span>
          </div>
          <button type="button" class="am-drawer__close" data-yd-close aria-label="Collapse">✕</button>
        </header>
        <ol class="news-list-page am-drawer__list">${detailRows}</ol>
      </section>`;
    return tile + drawer;
  }).join("");

  view.innerHTML = `
    <nav class="crumbs">
      <a href="#/">Industries</a> <span>›</span> <span>Yesterday's News</span>
    </nav>

    <section class="ind-headline news-headline">
      <div class="news-headline__main">
        <h1 class="ind-h1">Yesterday's News</h1>
        <p class="ind-lede">
          Every NSE announcement we've polled, kept forever. Use the day strip to
          step back through trading days; click a sector tile to read its filings.
          Defaults to the most recent prior trading day.
        </p>
      </div>
      <aside class="news-headline__meta">
        <div class="news-meta-row">
          <span class="news-meta-dot ${marketState ? `news-meta-dot--${marketState}` : ""}"></span>
          <span class="news-meta-label">${esc(marketLabel)}</span>
        </div>
        <div class="news-meta-row">
          <span class="news-meta-label">Updated</span>
          <span class="news-meta-value">${esc(updated)} IST</span>
        </div>
        <div class="news-meta-row">
          <span class="news-meta-label">Archive</span>
          <span class="news-meta-value">${INT.format(tradingDays.length)} day${tradingDays.length === 1 ? "" : "s"} · ${INT.format(items.length)} items</span>
        </div>
      </aside>
    </section>

    ${tradingDays.length === 0
      ? `<section class="ind-empty">
           No history yet — items accumulate as the poller runs.
         </section>`
      : `
        <section class="am-daystrip" aria-label="Trading day">
          <span class="am-daystrip__label">Day</span>
          <div class="am-daystrip__rail" id="ydDayRail">
            ${dayChipsHTML}
          </div>
        </section>

        <section class="am-summary">
          <span class="am-summary__num">${INT.format(dayTotal)}</span>
          <span class="am-summary__txt">item${dayTotal === 1 ? "" : "s"} across ${INT.format(sectorCount)} sector${sectorCount === 1 ? "" : "s"} · ${esc(formatShortDate(selectedDay))}${isToday ? " (today)" : ""}</span>
        </section>

        ${groups.length === 0
          ? `<section class="ind-empty">
               No items recorded for ${esc(formatShortDate(selectedDay))}.
             </section>`
          : `<section class="am-grid" id="ydGrid">${tilesHTML}</section>`}
      `}
  `;

  view.querySelectorAll("button[data-yd-day]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.history.selectedDay = btn.dataset.ydDay;
      state.history.selectedSlug = null;
      renderYesterdayPage();
    });
  });
  view.querySelectorAll("button[data-yd-slug]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const next = btn.dataset.ydSlug;
      state.history.selectedSlug = state.history.selectedSlug === next ? null : next;
      renderYesterdayPage();
      requestAnimationFrame(() => {
        const drawer = view.querySelector("[data-yd-drawer]");
        if (drawer) drawer.scrollIntoView({ block: "nearest", behavior: "smooth" });
      });
    });
  });
  view.querySelectorAll("[data-yd-close]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      state.history.selectedSlug = null;
      renderYesterdayPage();
    });
  });
}

function newsRowHTML(it, opts = {}) {
  const isNew = state.live.newSeqs.has(it.seq_id);
  const tm = (it.sort_date || "").slice(11, 16);
  const date = (it.sort_date || "").slice(0, 10);
  const indName = state.indexBySlug.get(it.industry_slug)?.name || it.industry_slug;
  const showShortDate = opts.showShortDate === true;  // "May 12" instead of "2026-05-12"
  const dateLabel = showShortDate ? formatShortDate(date) : date;
  return `
    <li class="news-row ${isNew ? "news--new" : ""}">
      <div class="news-row__time">
        <span class="news-row__hhmm">${esc(tm)}</span>
        <span class="news-row__date">${esc(dateLabel)}</span>
        ${isNew ? `<span class="news-pill">New</span>` : ""}
      </div>
      <div class="news-row__body">
        <div class="news-row__head">
          <a class="news-row__sym" href="#/${esc(it.industry_slug)}/${esc(it.symbol)}">${esc(it.symbol)}</a>
          <a class="news-row__ind" href="#/${esc(it.industry_slug)}">${esc(indName)}</a>
        </div>
        <div class="news-row__desc">${esc(it.desc || "Announcement")}</div>
        ${it.sm_name && it.sm_name !== it.name ? `<div class="news-row__company muted">${esc(it.sm_name)}</div>` : ""}
      </div>
      <div class="news-row__action">
        ${it.attchmntFile
          ? `<a class="news-row__pdf" href="${esc(safeUrl(it.attchmntFile))}" target="_blank" rel="noopener noreferrer">Open PDF →</a>`
          : `<span class="muted">no attachment</span>`}
      </div>
    </li>`;
}

function renderLiveTicker() {
  const ticker = document.getElementById("liveTicker");
  const list = document.getElementById("liveTickerList");
  const timeEl = document.getElementById("liveTickerTime");
  if (!ticker || !list) return;
  const items = state.live.items.slice(0, LIVE_TICKER_MAX);
  if (items.length === 0) {
    ticker.hidden = true;
    return;
  }
  ticker.hidden = false;
  ticker.dataset.market = currentMarketStateIST();
  list.innerHTML = items.map((it) => {
    const tm = (it.sort_date || "").slice(11, 16);   // HH:MM
    return `
      <li class="live-ticker__item">
        <a href="#/${esc(it.industry_slug)}/${esc(it.symbol)}" title="${esc(it.desc || "")}">
          <span class="live-ticker__time-cell">${esc(tm)}</span>
          <span class="live-ticker__sym">${esc(it.symbol)}</span>
          <span class="live-ticker__desc">${esc(it.desc || "Announcement")}</span>
        </a>
        ${it.attchmntFile
          ? `<a class="live-ticker__pdf" href="${esc(safeUrl(it.attchmntFile))}" target="_blank" rel="noopener noreferrer">PDF</a>`
          : ""}
      </li>`;
  }).join("");
  if (timeEl && state.live.polledAt) {
    timeEl.textContent = "polled " + state.live.polledAt.slice(11, 16);
  }
}

function mergeLiveIntoStockDetail(newSeqs) {
  // If the user is currently viewing a stock detail, prepend matching live
  // items to its news-feed with a fading NEW pill. We rebuild the feed from
  // the cached stock + live items rather than touching DOM in-place — it's
  // simpler and the list is short.
  const sym = state.live.currentSymbol;
  if (!sym) return;
  const liveForSym = state.live.items.filter((i) => i.symbol === sym);
  if (liveForSym.length === 0) return;

  const feedEl = document.querySelector(".stock-right .news-feed");
  if (!feedEl) return;

  // Merge: live items first (with seq_ids known), then existing
  const slug = liveForSym[0].industry_slug;
  const key = `${slug}/${sym}`;
  const stock = state.stockCache.get(key);
  if (!stock) return;

  const existingByDate = new Set((stock.news || []).map((n) => n.sort_date));
  const merged = [
    ...liveForSym
      .filter((l) => !existingByDate.has(l.sort_date))
      .map((l) => ({ ...l, _isLive: true, _isNew: newSeqs.includes(l.seq_id) })),
    ...(stock.news || []),
  ];

  feedEl.outerHTML = renderNewsFeed(merged);

  // Schedule NEW-pill removal
  setTimeout(() => {
    document.querySelectorAll(".news-feed li.news--new").forEach((el) =>
      el.classList.remove("news--new"));
  }, LIVE_NEW_FADE_MS);

  // Also bump the count chip in the header
  const countEl = document.querySelector(".stock-right .news-count");
  if (countEl && stock.window_days) {
    countEl.textContent = `${merged.length} (incl. live)`;
  }
}

async function fetchJSON(path) {
  const r = await fetch(path, { cache: "no-store" });
  if (!r.ok) throw new Error(`fetch ${path}: ${r.status}`);
  return r.json();
}

function initTheme() {
  let saved;
  try {
    saved = localStorage.getItem("sb-theme") || "light";
  } catch { saved = "light"; }
  document.documentElement.dataset.theme = saved;
  const btn = document.getElementById("themeToggle");
  if (!btn) return;
  btn.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("sb-theme", next); } catch { }
  });
}

function initNavToggle() {
  const toggle = document.getElementById("navToggle");
  const nav = document.getElementById("topNav");
  const backdrop = document.getElementById("navBackdrop");
  if (!toggle || !nav) return;

  const setOpen = (open) => {
    toggle.classList.toggle("is-open", open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    toggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    if (backdrop) backdrop.hidden = !open;
  };

  toggle.addEventListener("click", () => {
    setOpen(!toggle.classList.contains("is-open"));
  });
  if (backdrop) backdrop.addEventListener("click", () => setOpen(false));
  // Close after picking a destination — even hash navigation feels intentional this way.
  nav.addEventListener("click", (e) => {
    if (e.target.closest("a")) setOpen(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && toggle.classList.contains("is-open")) setOpen(false);
  });
}

function bindToolbar() {
  const search = document.getElementById("indSearch");
  search.addEventListener("input", (e) => {
    const v = e.target.value.trim();
    const onNews = location.hash === "#/news" || location.hash.startsWith("#/news");
    if (onNews) {
      state.newsFilter.q = v;
      renderNewsPage();
      return;
    }
    state.search = v;
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
    state.live.currentSymbol = null;
    document.getElementById("indSearch").placeholder =
      "Search industries or stocks (e.g. steel, RELIANCE, banks)…";
    renderGrid();
  } else if (parts[0] === "news") {
    state.live.currentSymbol = null;
    document.getElementById("indSearch").value = state.newsFilter.q || "";
    document.getElementById("indSearch").placeholder =
      "Filter announcements by symbol or text (e.g. dividend, RELIANCE)…";
    renderNewsPage();
  } else if (parts[0] === "after-market") {
    state.live.currentSymbol = null;
    document.getElementById("indSearch").value = "";
    state.search = "";
    document.getElementById("indSearch").placeholder =
      "After-market filings, grouped by sector…";
    renderAfterMarketPage();
  } else if (parts[0] === "yesterday") {
    state.live.currentSymbol = null;
    document.getElementById("indSearch").value = "";
    state.search = "";
    document.getElementById("indSearch").placeholder =
      "Yesterday's news, grouped by day and sector…";
    renderYesterdayPage();
  } else if (parts.length === 1) {
    state.live.currentSymbol = null;
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
  state.live.currentSymbol = symbol;
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
        ${renderNewsFeed(buildMergedNews(stock))}
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

function buildMergedNews(stock) {
  if (!stock) return [];
  const liveForSym = state.live.items.filter((i) => i.symbol === stock.symbol);
  if (liveForSym.length === 0) return stock.news || [];
  const existingByDate = new Set((stock.news || []).map((n) => n.sort_date));
  const live = liveForSym
    .filter((l) => !existingByDate.has(l.sort_date))
    .map((l) => ({ ...l, _isLive: true }));
  return [...live, ...(stock.news || [])];
}

function renderNewsFeed(news) {
  if (!news || news.length === 0) {
    return `<ul class="news-feed"><li class="news-feed__empty">No announcements in this window.</li></ul>`;
  }
  return `
    <ul class="news-feed">
      ${news.map((n) => {
        const cls = [
          n._isLive ? "news--live" : "",
          n._isNew  ? "news--new"  : "",
        ].filter(Boolean).join(" ");
        const dateLabel = n._isLive
          ? (n.sort_date || "").slice(11, 16)
          : (n.sort_date || "").slice(0, 10);
        return `
        <li class="${cls}">
          <div class="news-feed__row">
            <span class="news-date">${esc(dateLabel)}</span>
            ${n._isNew ? `<span class="news-pill">New</span>` : ""}
            <span class="news-desc">${esc(n.desc || "Announcement")}</span>
          </div>
          ${n.attchmntFile
            ? `<a class="news-link" href="${esc(safeUrl(n.attchmntFile))}" target="_blank" rel="noopener noreferrer">Open PDF →</a>`
            : `<span class="muted">no attachment</span>`}
        </li>`;
      }).join("")}
    </ul>
  `;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function safeUrl(u) {
  try {
    const url = new URL(u, location.href);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : "#";
  } catch { return "#"; }
}

init();
