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
  el.innerHTML =
    "<thead><tr>" + headers.map((h) => `<th class="${h.num ? "num" : ""}">${h.t}</th>`).join("") + "</tr></thead>" +
    "<tbody>" + rows.map((r) => "<tr>" + r.map((c, i) => `<td class="${headers[i].num ? "num" : ""}">${c}</td>`).join("") + "</tr>").join("") + "</tbody>";
}

const data = await (await fetch("/data/dashboard.json")).json();
const h = data.headline;
const report = data.run_report;

document.getElementById("headline-cards").innerHTML = [
  card(fmt(h.incidents), `incidents, ${h.min_date} to ${h.max_date}`),
  card(fmt(h.total_delay_minutes), "total recorded delay minutes"),
  card(fmt(h.stations), "distinct station values as published (free-text field, see Data quality)"),
  card(`${report.checks_passed}/${report.checks_total}`, "data-quality checks passed on the latest run"),
].join("");

new Chart(document.getElementById("chart-monthly"), {
  data: {
    labels: data.monthly_trend.map((r) => r.event_year_month),
    datasets: [
      { type: "bar", label: "Incidents", data: data.monthly_trend.map((r) => r.incidents), backgroundColor: "#c8102e", yAxisID: "y" },
      { type: "line", label: "Delay minutes", data: data.monthly_trend.map((r) => r.total_delay_minutes), borderColor: "#16181d", backgroundColor: "#16181d", yAxisID: "y1", tension: 0.25 },
    ],
  },
  options: { scales: { y: { position: "left" }, y1: { position: "right", grid: { drawOnChartArea: false } } } },
});

new Chart(document.getElementById("chart-hour"), {
  type: "bar",
  data: {
    labels: data.delays_by_hour.map((r) => `${r.event_hour}:00`),
    datasets: [{ label: "Delay minutes", data: data.delays_by_hour.map((r) => r.total_delay_minutes), backgroundColor: "#16181d" }],
  },
  options: { plugins: { legend: { display: false } } },
});

new Chart(document.getElementById("chart-line"), {
  type: "bar",
  data: {
    labels: data.delays_by_line.map((r) => lineLabel(r.line_canonical)),
    datasets: [{ label: "Delay minutes", data: data.delays_by_line.map((r) => r.total_delay_minutes), backgroundColor: "#c8102e" }],
  },
  options: { indexAxis: "y", plugins: { legend: { display: false } } },
});

table(document.getElementById("table-stations"),
  [{ t: "Station" }, { t: "Incidents", num: 1 }, { t: "Delay minutes", num: 1 }, { t: "Avg min when delayed", num: 1 }],
  data.delays_by_station.slice(0, 15).map((r) => [r.station, fmt(r.incidents), fmt(r.total_delay_minutes), r.avg_delay_minutes_when_delayed ?? "—"]));

table(document.getElementById("table-codes"),
  [{ t: "Code" }, { t: "Description" }, { t: "Incidents", num: 1 }, { t: "Delay minutes", num: 1 }],
  data.delays_by_code.slice(0, 12).map((r) => [r.code, r.description, fmt(r.incidents), fmt(r.total_delay_minutes)]));

table(document.getElementById("table-lines"),
  [{ t: "Line" }, { t: "Incidents", num: 1 }, { t: "Delay minutes", num: 1 }, { t: "Avg min when delayed", num: 1 }, { t: "% incidents with delay", num: 1 }],
  data.delays_by_line.map((r) => [lineLabel(r.line_canonical), fmt(r.incidents), fmt(r.total_delay_minutes), r.avg_delay_minutes_when_delayed ?? "—", r.pct_incidents_with_delay]));

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
