import Chart from "chart.js/auto";

const fmt = (n) => Number(n).toLocaleString("en-CA");
const LINE_LABELS = {
  LINE_1_YONGE_UNIVERSITY: "Line 1 · Yonge–University",
  LINE_2_BLOOR_DANFORTH: "Line 2 · Bloor–Danforth",
  LINE_4_SHEPPARD: "Line 4 · Sheppard",
  LINE_3_SCARBOROUGH_RT: "Line 3 · Scarborough RT",
  MULTI_LINE_OR_NETWORK: "Multi-line / network",
  UNKNOWN: "Unknown (unmapped raw value)",
};
const lineLabel = (v) => LINE_LABELS[v] || v;

function card(value, label) {
  return `<div class="card"><div class="value">${value}</div><div class="label">${label}</div></div>`;
}
function table(el, headers, rows) {
  const body = rows.length
    ? rows.map((r) => "<tr>" + r.map((c, i) => `<td class="${headers[i].num ? "num" : ""}">${c}</td>`).join("") + "</tr>").join("")
    : `<tr><td class="empty-state" colspan="${headers.length}">No data matches this selection.</td></tr>`;
  el.innerHTML =
    "<thead><tr>" + headers.map((h) => `<th class="${h.num ? "num" : ""}">${h.t}</th>`).join("") + "</tr></thead>" +
    "<tbody>" + body + "</tbody>";
}
// Sum rows into groups keyed by keyFn, adding the numeric fields listed.
function sumBy(rows, keyFn, fields) {
  const out = new Map();
  for (const r of rows) {
    const k = keyFn(r);
    if (!out.has(k)) out.set(k, { ...r });
    else for (const f of fields) out.get(k)[f] += r[f];
  }
  return [...out.values()];
}

async function fetchJson(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} returned ${r.status}`);
  return r.json();
}
let core, tables, daily, quarter;
try {
  [core, tables, daily, quarter] = await Promise.all([
    fetchJson("/data/dashboard.json"),
    fetchJson("/data/tables_by_line.json"),
    fetchJson("/data/daily.json"),
    fetchJson("/data/quarter.json"),
  ]);
} catch (err) {
  const cards = document.getElementById("headline-cards");
  cards.innerHTML = "";
  cards.removeAttribute("aria-busy");
  const panel = document.getElementById("load-error");
  panel.hidden = false;
  document.getElementById("load-error-detail").textContent =
    `The dashboard data files could not be loaded (${err.message}). Check your connection and try again.`;
  document.getElementById("retry-load").addEventListener("click", () => location.reload());
  throw err;
}
document.getElementById("headline-cards").removeAttribute("aria-busy");
const data = { ...core, ...tables };
// By-line rows carry a short line code in `line`; restore canonical names.
for (const key of ["totals_by_year_line", "hour_by_year_line", "monthly_by_line", "stations_by_year_line", "codes_by_year_line"])
  for (const r of data[key]) r.line_canonical = data.line_codes[r.line];
// Daily series ship as compact arrays (layout documented in daily.json).
const isoDay = (epochDay) => new Date(epochDay * 86400000).toISOString().slice(0, 10);
data.daily_summary = daily.daily_summary.map(([d, incidents, delay]) =>
  ({ event_date: isoDay(d), incidents, total_delay_minutes: delay }));
data.daily_by_line = daily.daily_by_line.map(([d, code, delay]) =>
  ({ event_date: isoDay(d), line_canonical: data.line_codes[code], total_delay_minutes: delay }));
// Quarter-grain breakdowns ship as arrays plus lookups (see quarter.json).
data.totals_by_quarter_line = quarter.totals_by_quarter_line.map(([event_year, event_quarter, code, incidents, total_delay_minutes, stations]) =>
  ({ event_year, event_quarter, line_canonical: data.line_codes[code], incidents, total_delay_minutes, stations }));
data.hour_by_quarter_line = quarter.hour_by_quarter_line.map(([event_year, event_quarter, code, event_hour, incidents, total_delay_minutes]) =>
  ({ event_year, event_quarter, line_canonical: data.line_codes[code], event_hour, incidents, total_delay_minutes }));
data.stations_by_quarter_line = quarter.stations_by_quarter_line.map(([event_year, event_quarter, code, si, incidents, total_delay_minutes, avg]) =>
  ({ event_year, event_quarter, line_canonical: data.line_codes[code], station: quarter.station_names[si], incidents, total_delay_minutes, avg_delay_minutes_when_delayed: avg }));
data.codes_by_quarter_line = quarter.codes_by_quarter_line.map(([event_year, event_quarter, code, codeName, incidents, total_delay_minutes]) =>
  ({ event_year, event_quarter, line_canonical: data.line_codes[code], code: codeName, description: quarter.code_descriptions[codeName], incidents, total_delay_minutes }));
const h = data.headline;
const report = data.run_report;

const state = { line: "ALL", period: "ALL", stationQuery: "" };
// state.period is "ALL", a year ("2025"), or a quarter ("2025-Q3").
const periodYear = () => (state.period === "ALL" ? null : Number(state.period.slice(0, 4)));
const periodQuarter = () => (state.period.includes("-Q") ? Number(state.period.slice(6)) : null);
const periodLabel = () => (periodQuarter() === null ? `${periodYear()}` : `${periodYear()} Q${periodQuarter()}`);
const inYear = (yearVal) => periodYear() === null || Number(yearVal) === periodYear();
const inGrain = (r) => inYear(r.event_year) && (periodQuarter() === null || r.event_quarter === periodQuarter());
const inPeriodYM = (ym) => { // "2025-03"
  if (!inYear(ym.slice(0, 4))) return false;
  return periodQuarter() === null || Math.floor((Number(ym.slice(5, 7)) - 1) / 3) + 1 === periodQuarter();
};
// Year-grain table, or the quarter-grain one when a quarter is selected.
const grainRows = (yearKey, quarterKey) => data[periodQuarter() === null ? yearKey : quarterKey];
const inLine = (r) => state.line === "ALL" || r.line_canonical === state.line;

// --- Filter controls -------------------------------------------------------
const lineSelect = document.getElementById("filter-line");
lineSelect.innerHTML =
  `<option value="ALL">All lines</option>` +
  data.delays_by_line.map((r) => `<option value="${r.line_canonical}">${lineLabel(r.line_canonical)}</option>`).join("");
const periodSelect = document.getElementById("filter-year");
const years = [...new Set(data.totals_by_year_line.map((r) => r.event_year))].sort();
const quartersByYear = {};
for (const r of data.totals_by_quarter_line) (quartersByYear[r.event_year] ??= new Set()).add(r.event_quarter);
periodSelect.innerHTML =
  `<option value="ALL">All time (${h.min_date.slice(0, 4)}–${h.max_date.slice(0, 4)})</option>` +
  years.map((y) =>
    `<option value="${y}">${y}</option>` +
    [...(quartersByYear[y] || [])].sort().map((q) => `<option value="${y}-Q${q}">${y} Q${q}</option>`).join("")
  ).join("");
lineSelect.addEventListener("change", () => { state.line = lineSelect.value; render(); });
periodSelect.addEventListener("change", () => { state.period = periodSelect.value; render(); });
document.getElementById("station-search").addEventListener("input", (e) => {
  state.stationQuery = e.target.value.trim().toLowerCase();
  renderStations();
});

// --- Charts (created once, data swapped on filter change) -------------------
function makeChart(id, config) { return new Chart(document.getElementById(id), config); }
const monthlyChart = makeChart("chart-monthly", {
  data: { labels: [], datasets: [
    { type: "bar", label: "Incidents", data: [], backgroundColor: "#c8102e", yAxisID: "y" },
    { type: "line", label: "Delay minutes", data: [], borderColor: "#16181d", backgroundColor: "#16181d", yAxisID: "y1", tension: 0.25 },
  ] },
  options: { scales: { y: { position: "left" }, y1: { position: "right", grid: { drawOnChartArea: false } } } },
});
const dailyChart = makeChart("chart-daily", {
  data: { labels: [], datasets: [
    { type: "bar", label: "Delay minutes per day", data: [], backgroundColor: "#c8102e" },
    { type: "line", label: "7-day rolling average", data: [], borderColor: "#16181d", backgroundColor: "#16181d", pointRadius: 0, tension: 0.25 },
  ] },
  options: { scales: { x: { ticks: { maxTicksLimit: 12 } } }, plugins: { legend: { display: true } } },
});
const hourChart = makeChart("chart-hour", {
  type: "bar",
  data: { labels: [], datasets: [{ label: "Delay minutes", data: [], backgroundColor: "#16181d" }] },
  options: { plugins: { legend: { display: false } } },
});
const lineChart = makeChart("chart-line", {
  type: "bar",
  data: { labels: [], datasets: [{ label: "Delay minutes", data: [], backgroundColor: "#c8102e" }] },
  options: { indexAxis: "y", plugins: { legend: { display: false } } },
});
function setChart(chart, labels, series) {
  chart.data.labels = labels;
  series.forEach((s, i) => { chart.data.datasets[i].data = s; });
  chart.update();
}

// --- Renderers ---------------------------------------------------------------
function renderHeadline() {
  const rows = grainRows("totals_by_year_line", "totals_by_quarter_line").filter((r) => inGrain(r) && inLine(r));
  const incidents = rows.reduce((a, r) => a + r.incidents, 0);
  const delay = rows.reduce((a, r) => a + r.total_delay_minutes, 0);
  const range = state.period === "ALL" ? `${h.min_date} to ${h.max_date}` : `in ${periodLabel()}`;
  const scope = state.line === "ALL" ? `incidents, ${range}` : `incidents on ${lineLabel(state.line)}, ${range}`;
  document.getElementById("headline-cards").innerHTML = [
    card(fmt(incidents), scope),
    card(fmt(delay), "total recorded delay minutes (same selection)"),
    card(fmt(h.stations), "distinct station values as published, all time (free-text field, see Data quality)"),
    card(`${report.checks_passed}/${report.checks_total}`, "data-quality checks passed on the latest run"),
  ].join("");
  document.getElementById("filter-note").textContent =
    state.line === "ALL" && state.period === "ALL"
      ? "Showing all lines, all time."
      : "Filters apply to the cards, charts, and tables on this page. Station and code tables show the top entries for the current selection.";
}

function renderMonthly() {
  const rows = state.line === "ALL"
    ? data.monthly_trend.filter((r) => inPeriodYM(r.event_year_month))
    : data.monthly_by_line.filter((r) => inLine(r) && inPeriodYM(r.event_year_month));
  setChart(monthlyChart, rows.map((r) => r.event_year_month),
    [rows.map((r) => r.incidents), rows.map((r) => r.total_delay_minutes)]);
}

function renderDaily() {
  let rows = state.line === "ALL"
    ? data.daily_summary.map((r) => ({ date: String(r.event_date).slice(0, 10), delay: r.total_delay_minutes }))
    : data.daily_by_line.filter(inLine).map((r) => ({ date: String(r.event_date).slice(0, 10), delay: r.total_delay_minutes }));
  rows = rows.filter((r) => inPeriodYM(r.date.slice(0, 7))).sort((a, b) => a.date.localeCompare(b.date));
  const rolling = rows.map((r, i) => {
    const win = rows.slice(Math.max(0, i - 6), i + 1);
    return Math.round(win.reduce((a, w) => a + w.delay, 0) / win.length);
  });
  setChart(dailyChart, rows.map((r) => r.date), [rows.map((r) => r.delay), rolling]);
}

function renderHour() {
  let rows;
  if (state.line === "ALL" && state.period === "ALL") {
    rows = data.delays_by_hour;
  } else {
    const filtered = grainRows("hour_by_year_line", "hour_by_quarter_line").filter((r) => inLine(r) && inGrain(r));
    rows = sumBy(filtered, (r) => r.event_hour, ["incidents", "total_delay_minutes"])
      .sort((a, b) => a.event_hour - b.event_hour);
  }
  setChart(hourChart, rows.map((r) => `${r.event_hour}:00`), [rows.map((r) => r.total_delay_minutes)]);
}

function renderLineChart() {
  // Per-line totals for the selected period: the comparison stays visible
  // even when one line is selected, so the selection keeps its context.
  const rows = sumBy(
    grainRows("totals_by_year_line", "totals_by_quarter_line").filter((r) => inGrain(r)),
    (r) => r.line_canonical, ["incidents", "total_delay_minutes"])
    .sort((a, b) => b.total_delay_minutes - a.total_delay_minutes);
  setChart(lineChart, rows.map((r) => lineLabel(r.line_canonical)), [rows.map((r) => r.total_delay_minutes)]);
  table(document.getElementById("table-lines"),
    [{ t: "Line" }, { t: "Incidents", num: 1 }, { t: "Delay minutes", num: 1 }],
    rows.map((r) => [lineLabel(r.line_canonical), fmt(r.incidents), fmt(r.total_delay_minutes)]));
}

function stationRows() {
  let rows;
  if (state.line === "ALL" && state.period === "ALL") {
    rows = data.delays_by_station; // top 50 exported
  } else {
    const filtered = grainRows("stations_by_year_line", "stations_by_quarter_line").filter((r) => inLine(r) && inGrain(r));
    rows = state.period === "ALL"
      ? sumBy(filtered, (r) => r.station, ["incidents", "total_delay_minutes"])
          .map((r) => ({ ...r, avg_delay_minutes_when_delayed: null }))
      : filtered;
    rows = rows.sort((a, b) => b.total_delay_minutes - a.total_delay_minutes);
  }
  if (state.stationQuery) rows = rows.filter((r) => r.station.toLowerCase().includes(state.stationQuery));
  return rows.slice(0, 15);
}
function renderStations() {
  table(document.getElementById("table-stations"),
    [{ t: "Station" }, { t: "Incidents", num: 1 }, { t: "Delay minutes", num: 1 }, { t: "Avg min when delayed", num: 1 }],
    stationRows().map((r) => [r.station, fmt(r.incidents), fmt(r.total_delay_minutes), r.avg_delay_minutes_when_delayed ?? "—"]));
}

function renderCodes() {
  let rows;
  if (state.line === "ALL" && state.period === "ALL") {
    rows = data.delays_by_code;
  } else {
    const filtered = grainRows("codes_by_year_line", "codes_by_quarter_line").filter((r) => inLine(r) && inGrain(r));
    rows = sumBy(filtered, (r) => r.code, ["incidents", "total_delay_minutes"])
      .sort((a, b) => b.total_delay_minutes - a.total_delay_minutes);
  }
  table(document.getElementById("table-codes"),
    [{ t: "Code" }, { t: "Description" }, { t: "Incidents", num: 1 }, { t: "Delay minutes", num: 1 }],
    rows.slice(0, 12).map((r) => [r.code, r.description, fmt(r.incidents), fmt(r.total_delay_minutes)]));
}

function render() { renderHeadline(); renderMonthly(); renderDaily(); renderHour(); renderLineChart(); renderStations(); renderCodes(); }
render();

// --- Data quality view (unchanged, always global) ----------------------------
document.getElementById("quality-cards").innerHTML = [
  card(new Date(report.run_started_utc).toLocaleString("en-CA"), "latest pipeline run (local time)"),
  card(`${report.checks_passed}/${report.checks_total}`, "checks passed"),
  card(fmt(report.row_counts.bronze), "bronze rows"),
  card(fmt(report.row_counts.silver), "silver rows (after cleaning + dedupe)"),
].join("");

table(document.getElementById("table-checks"),
  [{ t: "Check" }, { t: "Layer" }, { t: "Result" }, { t: "Measured" }, { t: "Threshold" }],
  report.checks.map((c) => [c.name, c.layer, `<span class="${c.passed ? "pass" : "fail"}">${c.passed ? "PASS" : "FAIL"}</span>`, c.measured, c.threshold]));

table(document.getElementById("table-manifest"),
  [{ t: "File" }, { t: "Bytes", num: 1 }, { t: "SHA-256 (first 16)" }, { t: "Ingest status" }],
  report.manifest_files.map((f) => [f.filename, fmt(f.bytes), f.sha256.slice(0, 16) + "…", f.status]));

const overview = document.getElementById("view-overview");
const quality = document.getElementById("view-quality");
for (const [btn, showQuality] of [["nav-overview", false], ["nav-quality", true]]) {
  document.getElementById(btn).addEventListener("click", (e) => {
    overview.hidden = showQuality;
    quality.hidden = !showQuality;
    document.getElementById("nav-overview").classList.toggle("active", !showQuality);
    document.getElementById("nav-quality").classList.toggle("active", showQuality);
    e.currentTarget.blur();
  });
}
