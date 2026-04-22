/* DataPilot AI frontend.
 *
 * Handles file upload, asking questions, and rendering the analyst response
 * returned by /api/ask into KPIs, a Chart.js chart, a data table, and text
 * insights. No build step; plain ES2020.
 */

const state = {
  sessionId: null,
  chart: null,
};

document.addEventListener("DOMContentLoaded", () => {
  bindForms();
  refreshMode();
});

function bindForms() {
  document.getElementById("upload-form").addEventListener("submit", onUpload);
  document.getElementById("ask-form").addEventListener("submit", onAsk);
  document.querySelectorAll("#example-chips button[data-q]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.getElementById("question").value = btn.dataset.q;
    });
  });
}

async function refreshMode() {
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    const pill = document.getElementById("mode-indicator");
    if (data.llm_mode) {
      pill.textContent = "LLM mode";
      pill.classList.add("llm");
    } else {
      pill.textContent = "Mock analyst";
      pill.classList.add("mock");
    }
  } catch (err) {
    document.getElementById("mode-indicator").textContent = "offline";
  }
}

async function onUpload(event) {
  event.preventDefault();
  const sales = document.getElementById("sales-file").files[0];
  const purchase = document.getElementById("purchase-file").files[0];
  const status = document.getElementById("upload-status");
  status.className = "status";

  if (!sales && !purchase) {
    status.textContent = "Pick at least one file.";
    status.classList.add("error");
    return;
  }

  const formData = new FormData();
  if (sales) formData.append("sales", sales);
  if (purchase) formData.append("purchase", purchase);
  if (state.sessionId) formData.append("session_id", state.sessionId);

  const btn = document.getElementById("upload-btn");
  btn.disabled = true;
  status.textContent = "Uploading…";

  try {
    const res = await fetch("/api/upload", { method: "POST", body: formData });
    if (!res.ok) throw new Error((await res.json()).error || "Upload failed");
    const data = await res.json();
    state.sessionId = data.session_id;
    renderSchema(data.schema);
    status.textContent = `Uploaded: ${data.tables.join(", ")}. Session ${data.session_id.slice(0, 8)}…`;
    status.classList.add("success");
  } catch (err) {
    status.textContent = err.message;
    status.classList.add("error");
  } finally {
    btn.disabled = false;
  }
}

function renderSchema(schema) {
  const panel = document.getElementById("schema-panel");
  const body = document.getElementById("schema-content");
  body.innerHTML = "";
  for (const [name, info] of Object.entries(schema)) {
    const box = document.createElement("div");
    box.className = "schema-table";
    const title = document.createElement("h4");
    title.textContent = `${name} — ${info.row_count.toLocaleString()} rows`;
    box.appendChild(title);
    const cols = document.createElement("div");
    cols.className = "schema-cols";
    info.columns.forEach((c) => {
      const chip = document.createElement("span");
      chip.className = "schema-col";
      chip.title = c.dtype;
      chip.textContent = c.name;
      cols.appendChild(chip);
    });
    box.appendChild(cols);
    body.appendChild(box);
  }
  panel.hidden = false;
}

async function onAsk(event) {
  event.preventDefault();
  const question = document.getElementById("question").value.trim();
  const status = document.getElementById("ask-status");
  status.className = "status";

  if (!state.sessionId) {
    status.textContent = "Upload at least one file first.";
    status.classList.add("error");
    return;
  }
  if (!question) return;

  const btn = document.getElementById("ask-btn");
  btn.disabled = true;
  status.textContent = "Analysing…";

  try {
    const res = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.sessionId, question }),
    });
    if (!res.ok) throw new Error((await res.json()).error || "Analysis failed");
    const data = await res.json();
    renderResponse(data);
    status.textContent = "Done.";
    status.classList.add("success");
  } catch (err) {
    status.textContent = err.message;
    status.classList.add("error");
  } finally {
    btn.disabled = false;
  }
}

function renderResponse(data) {
  const errorCard = document.getElementById("error-card");
  const dashboard = document.getElementById("dashboard");

  if (data.error) {
    dashboard.hidden = true;
    errorCard.hidden = false;
    document.getElementById("error-message").textContent = data.error;
    return;
  }
  errorCard.hidden = true;
  dashboard.hidden = false;

  // KPIs
  for (const metric of ["total_revenue", "total_profit", "total_orders"]) {
    const el = document.querySelector(`.kpi[data-metric="${metric}"] .kpi-value`);
    const v = data.kpis ? data.kpis[metric] : null;
    el.textContent = formatKpi(metric, v);
  }

  // Explanation & query metadata
  document.getElementById("explanation").textContent = data.explanation || "—";
  document.getElementById("generated-query").textContent = data.query || "—";
  document.getElementById("intent-pill").textContent = `intent: ${data.intent || "—"}`;
  document.getElementById("dims-pill").textContent = `dims: ${(data.dimensions || []).join(", ") || "—"}`;
  document.getElementById("metrics-pill").textContent = `metrics: ${(data.metrics || []).join(", ") || "—"}`;

  // Insights
  const ul = document.getElementById("insights-list");
  ul.innerHTML = "";
  (data.insights || []).forEach((ins) => {
    const li = document.createElement("li");
    li.textContent = ins;
    ul.appendChild(li);
  });
  if (!ul.children.length) {
    const li = document.createElement("li");
    li.textContent = "No insights generated.";
    li.style.color = "var(--muted)";
    ul.appendChild(li);
  }

  // Chart + table
  renderChart(data);
  renderTable(data);
}

function formatKpi(metric, value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (metric === "total_orders") return Number(value).toLocaleString();
  return formatMoney(value);
}

function formatMoney(v) {
  const abs = Math.abs(v);
  if (abs >= 1e9) return (v / 1e9).toFixed(2) + "B";
  if (abs >= 1e6) return (v / 1e6).toFixed(2) + "M";
  if (abs >= 1e3) return (v / 1e3).toFixed(2) + "K";
  return Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function renderChart(data) {
  const canvas = document.getElementById("result-chart");
  const title = document.getElementById("chart-title");
  if (state.chart) {
    state.chart.destroy();
    state.chart = null;
  }
  const rows = Array.isArray(data.data) ? data.data : [];
  if (!rows.length) {
    title.textContent = "Chart (no rows returned)";
    return;
  }

  const columns = Object.keys(rows[0]);
  const dimCol = (data.dimensions && data.dimensions[0]) || columns[0];
  const metricCols = (data.metrics && data.metrics.length ? data.metrics : columns.slice(1))
    .filter((c) => columns.includes(c));

  title.textContent = `Chart · ${data.chart_hint || "table"}`;

  const labels = rows.map((r) => String(r[dimCol]));

  const palette = ["#2f81f7", "#3fb950", "#d29922", "#a371f7", "#f778ba", "#f85149"];

  const hint = (data.chart_hint || "table").toLowerCase();
  if (hint === "pie" && metricCols.length >= 1) {
    const metric = metricCols[0];
    const values = rows.map((r) => Number(r[metric] ?? 0));
    state.chart = new Chart(canvas, {
      type: "pie",
      data: {
        labels,
        datasets: [{ data: values, backgroundColor: palette }],
      },
      options: { ...commonChartOptions(), plugins: { legend: { position: "right", labels: { color: "#e6edf3" } } } },
    });
    return;
  }

  if (hint === "table" || metricCols.length === 0) {
    // Still draw a simple bar of first numeric column for context.
    const numericCol = columns.find((c) => typeof rows[0][c] === "number");
    if (!numericCol) {
      title.textContent = "Chart (non-numeric result)";
      return;
    }
    state.chart = new Chart(canvas, {
      type: "bar",
      data: {
        labels,
        datasets: [{ label: numericCol, data: rows.map((r) => r[numericCol]), backgroundColor: palette[0] }],
      },
      options: commonChartOptions(),
    });
    return;
  }

  const type = hint === "line" ? "line" : "bar";
  const datasets = metricCols.map((col, i) => ({
    label: col,
    data: rows.map((r) => Number(r[col] ?? 0)),
    borderColor: palette[i % palette.length],
    backgroundColor: palette[i % palette.length] + (type === "line" ? "33" : "cc"),
    fill: type === "line",
    tension: 0.2,
  }));

  state.chart = new Chart(canvas, {
    type,
    data: { labels, datasets },
    options: commonChartOptions(),
  });
}

function commonChartOptions() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    scales: {
      x: { ticks: { color: "#8b949e" }, grid: { color: "#30363d" } },
      y: { ticks: { color: "#8b949e" }, grid: { color: "#30363d" } },
    },
    plugins: { legend: { labels: { color: "#e6edf3" } } },
  };
}

function renderTable(data) {
  const rows = Array.isArray(data.data) ? data.data : [];
  const thead = document.querySelector("#result-table thead");
  const tbody = document.querySelector("#result-table tbody");
  thead.innerHTML = "";
  tbody.innerHTML = "";
  if (!rows.length) return;
  const cols = Object.keys(rows[0]);
  const tr = document.createElement("tr");
  cols.forEach((c) => {
    const th = document.createElement("th");
    th.textContent = c;
    tr.appendChild(th);
  });
  thead.appendChild(tr);
  rows.forEach((row) => {
    const r = document.createElement("tr");
    cols.forEach((c) => {
      const td = document.createElement("td");
      const v = row[c];
      td.textContent = typeof v === "number" ? Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 }) : String(v ?? "");
      r.appendChild(td);
    });
    tbody.appendChild(r);
  });
}
