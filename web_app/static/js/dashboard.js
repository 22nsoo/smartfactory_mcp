import {
  askAgent,
  getDashboardOverview,
  getMcpStatus,
  getSensorHistory,
  getSensorStatus,
} from "./api.js";

const state = {
  selectedSensor: null,
  factorySummary: null,
  sensors: [],
  sensorDetail: null,
  sensorHistory: [],
  historyAsOf: null,
  metric: "risk_score",
  hours: 24,
  mcp: null,
  agentResponse: null,
  loading: false,
};

const STATUS_ORDER = {NORMAL: 0, ATTENTION: 1, DEGRADING: 2, WARNING: 3, ABNORMAL: 3};
const STATUS_SYMBOL = {NORMAL: "●", ATTENTION: "◆", DEGRADING: "▲", WARNING: "!", ABNORMAL: "!"};
const METRIC_LABEL = {
  risk_score: "Risk Score",
  rms: "RMS",
  peak_to_peak: "Peak-to-Peak",
};
const MCP_TOOLS = new Set([
  "list_monitored_sensors",
  "get_model_summary",
  "get_sensor_status",
  "get_abnormal_sensors",
  "get_sensor_history",
  "get_anomaly_detail",
  "get_factory_summary",
]);

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function safeUrl(value) {
  try {
    const url = new URL(String(value));
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}

function number(value, digits = 2) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toFixed(digits) : "—";
}

function timestamp(value, compact = false) {
  if (!value) return "—";
  const normalized = String(value).replace("T", " ");
  return compact ? normalized.slice(0, 16) : normalized;
}

function statusClass(value) {
  return String(value || "neutral").toLowerCase();
}

function friendlyError(error, fallback) {
  console.error(error);
  return error?.message && !String(error.message).includes("Traceback")
    ? error.message
    : fallback;
}

function showAlert(selector, message) {
  const element = $(selector);
  element.textContent = message;
  element.classList.remove("hidden");
}

function hideAlert(selector) {
  const element = $(selector);
  element.textContent = "";
  element.classList.add("hidden");
}

function renderSummary() {
  const summary = state.factorySummary;
  if (!summary) return;
  const cards = [
    ["total", "MONITORED", summary.monitored_sensor_count],
    ["normal", "NORMAL", summary.normal_count],
    ["attention", "ATTENTION", summary.attention_count],
    ["degrading", "DEGRADING", summary.degrading_count],
    ["warning", "WARNING", summary.warning_count],
  ];
  $("#summary-cards").innerHTML = cards.map(([css, label, value]) => `
    <article class="summary-card ${css}">
      <span>${escapeHtml(label)}</span><strong>${escapeHtml(value ?? 0)}</strong>
    </article>
  `).join("");
  $("#factory-as-of").textContent = `Last stored ${timestamp(summary.as_of)} · ${summary.monitored_sensor_count || 0} sensors`;
}

function renderSensorCards() {
  const container = $("#sensor-cards");
  if (!state.sensors.length) {
    container.innerHTML = '<p class="empty-state">표시할 monitored sensor가 없습니다.</p>';
    return;
  }
  container.innerHTML = state.sensors.map((sensor) => {
    const selected = String(sensor.sensor_id) === String(state.selectedSensor);
    const status = sensor.latest_status || "UNKNOWN";
    return `
      <button class="sensor-card ${selected ? "selected" : ""}" type="button"
              data-sensor-id="${escapeHtml(sensor.sensor_id)}"
              aria-pressed="${selected}" aria-label="센서 ${escapeHtml(sensor.sensor_id)} 선택">
        <span class="sensor-card-top">
          <span class="sensor-id">Sensor ${escapeHtml(sensor.sensor_id)}</span>
          <span class="status-pill ${statusClass(status)}">${escapeHtml(STATUS_SYMBOL[status] || "·")} ${escapeHtml(status)}</span>
        </span>
        <span class="sensor-card-data">
          <span><small>Risk Score</small><b>${number(sensor.latest_risk_score)}</b></span>
          <span><small>Unit</small><b>${escapeHtml(sensor.unit || "—")}</b></span>
          <span style="grid-column: 1 / -1"><small>Last stored</small><b>${escapeHtml(timestamp(sensor.latest_window, true))}</b></span>
        </span>
      </button>
    `;
  }).join("");
  $$('[data-sensor-id]').forEach((button) => {
    button.addEventListener("click", () => selectSensor(button.dataset.sensorId));
  });
}

function renderSensorDetail() {
  const data = state.sensorDetail;
  if (!data) return;
  $("#detail-title").textContent = `Sensor ${data.sensor_id}`;
  const status = data.status || "UNKNOWN";
  const statusElement = $("#detail-status");
  statusElement.className = `status-pill ${statusClass(status)}`;
  statusElement.textContent = `${STATUS_SYMBOL[status] || "·"} ${status}`;
  const features = Array.isArray(data.sigma_detected_features)
    ? data.sigma_detected_features
    : [];
  $("#sensor-detail").innerHTML = `
    <div class="detail-hero">
      <small>HISTORICAL DATA · LAST STORED</small>
      <div class="detail-score">${number(data.risk_score)} <span>Risk Score</span></div>
      <p class="detail-time">${escapeHtml(timestamp(data.as_of))}</p>
    </div>
    <div class="metric-grid">
      <div class="metric"><span>RMS</span><strong>${number(data.rms, 3)}</strong></div>
      <div class="metric"><span>Peak-to-Peak</span><strong>${number(data.peak_to_peak, 3)}</strong></div>
      <div class="metric"><span>Sample Count</span><strong>${escapeHtml(data.sample_count ?? "—")}</strong></div>
      <div class="metric"><span>Previous Gap</span><strong>${number(data.gap_minutes, 1)} min</strong></div>
      <div class="metric"><span>Mean</span><strong>${number(data.mean, 3)}</strong></div>
      <div class="metric"><span>Unit</span><strong>${escapeHtml(data.unit || "—")}</strong></div>
    </div>
    <div class="feature-block">
      <span>3-SIGMA DETECTED FEATURES</span>
      <div class="feature-tags">
        ${features.length ? features.map((item) => `<span>${escapeHtml(item)} ↑</span>`).join("") : "<span>none</span>"}
      </div>
    </div>
  `;
}

function chartData() {
  return [...state.sensorHistory]
    .reverse()
    .map((item) => ({time: item.window_start, value: Number(item[state.metric])}))
    .filter((item) => Number.isFinite(item.value));
}

function renderChart() {
  const container = $("#history-chart");
  const label = METRIC_LABEL[state.metric];
  $("#legend-metric").textContent = label;
  container.setAttribute("aria-label", `Sensor ${state.selectedSensor || "미선택"}의 historical ${label} 차트`);
  if (!state.selectedSensor || !chartData().length) {
    container.innerHTML = '<p class="empty-state">선택 범위에 표시할 historical window가 없습니다.</p>';
    return;
  }

  const data = chartData();
  const width = 900;
  const height = 285;
  const padding = {left: 64, right: 24, top: 24, bottom: 44};
  const values = data.map((item) => item.value);
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min === max) { min -= 1; max += 1; }
  const margin = (max - min) * 0.08;
  min -= margin;
  max += margin;
  const x = (index) => padding.left + index * ((width - padding.left - padding.right) / Math.max(data.length - 1, 1));
  const y = (value) => padding.top + (max - value) * ((height - padding.top - padding.bottom) / (max - min));
  const points = data.map((item, index) => `${x(index)},${y(item.value)}`).join(" ");
  const areaPoints = `${padding.left},${height - padding.bottom} ${points} ${x(data.length - 1)},${height - padding.bottom}`;
  const ticks = Array.from({length: 5}, (_, index) => {
    const tickValue = max - index * ((max - min) / 4);
    const tickY = y(tickValue);
    return `
      <line class="chart-grid-line" x1="${padding.left}" x2="${width - padding.right}" y1="${tickY}" y2="${tickY}"></line>
      <text class="chart-label" x="${padding.left - 10}" y="${tickY + 4}" text-anchor="end">${number(tickValue, state.metric === "risk_score" ? 1 : 2)}</text>
    `;
  }).join("");
  const pointNodes = data.length <= 60 ? data.map((item, index) => `
    <circle class="chart-point" cx="${x(index)}" cy="${y(item.value)}" r="2.6">
      <title>${escapeHtml(timestamp(item.time))} · ${escapeHtml(label)} ${number(item.value, 3)}</title>
    </circle>
  `).join("") : "";
  container.innerHTML = `
    <svg class="history-svg" viewBox="0 0 ${width} ${height}" aria-hidden="true">
      <defs><linearGradient id="chart-gradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#52e3ca"></stop><stop offset="1" stop-color="#52e3ca" stop-opacity="0"></stop></linearGradient></defs>
      ${ticks}
      <polygon class="chart-area" points="${areaPoints}"></polygon>
      <polyline class="chart-line" points="${points}"></polyline>
      ${pointNodes}
      <text class="chart-label" x="${padding.left}" y="${height - 15}">${escapeHtml(timestamp(data[0].time, true))}</text>
      <text class="chart-label" x="${width - padding.right}" y="${height - 15}" text-anchor="end">${escapeHtml(timestamp(data.at(-1).time, true))}</text>
    </svg>
  `;
  $("#chart-title").textContent = `Sensor ${state.selectedSensor} · ${label}`;
  $("#chart-caption").textContent = `Last stored ${timestamp(state.historyAsOf)} 기준 이전 ${state.hours}시간 · ${data.length} points`;
}

async function selectSensor(sensorId, force = false) {
  if (!force && String(state.selectedSensor) === String(sensorId) && state.sensorDetail) return;
  state.selectedSensor = String(sensorId);
  state.sensorDetail = null;
  state.sensorHistory = [];
  renderSensorCards();
  $("#detail-title").textContent = `Sensor ${sensorId}`;
  $("#sensor-detail").innerHTML = '<p class="empty-state tall">센서 상세 정보를 불러오는 중입니다.</p>';
  $("#history-chart").innerHTML = '<p class="empty-state">Historical window를 불러오는 중입니다.</p>';
  try {
    const [detail, history] = await Promise.all([
      getSensorStatus(sensorId),
      getSensorHistory(sensorId, state.hours, 200),
    ]);
    if (String(state.selectedSensor) !== String(sensorId)) return;
    state.sensorDetail = detail;
    state.sensorHistory = history.windows || [];
    state.historyAsOf = history.as_of;
    renderSensorDetail();
    renderChart();
  } catch (error) {
    showAlert("#dashboard-error", friendlyError(error, "센서 상세 데이터를 조회하지 못했습니다."));
    $("#sensor-detail").innerHTML = '<p class="empty-state tall">센서 상세 데이터를 표시할 수 없습니다.</p>';
    $("#history-chart").innerHTML = '<p class="empty-state">Historical window를 표시할 수 없습니다.</p>';
  }
}

async function reloadHistory() {
  if (!state.selectedSensor) return;
  $("#history-chart").innerHTML = '<p class="empty-state">선택 범위의 historical window를 불러오는 중입니다.</p>';
  try {
    const history = await getSensorHistory(state.selectedSensor, state.hours, 200);
    state.sensorHistory = history.windows || [];
    state.historyAsOf = history.as_of;
    renderChart();
  } catch (error) {
    showAlert("#dashboard-error", friendlyError(error, "센서 이력을 조회하지 못했습니다."));
  }
}

async function loadDashboard() {
  hideAlert("#dashboard-error");
  $("#refresh-dashboard").disabled = true;
  try {
    const payload = await getDashboardOverview();
    state.factorySummary = payload.factory_summary;
    state.sensors = payload.sensors || [];
    renderSummary();
    if (!state.sensors.length) {
      renderSensorCards();
      return;
    }
    const previous = state.sensors.find((item) => String(item.sensor_id) === String(state.selectedSensor));
    const initial = previous || [...state.sensors].sort(
      (a, b) => (STATUS_ORDER[b.latest_status] ?? -1) - (STATUS_ORDER[a.latest_status] ?? -1)
    )[0];
    renderSensorCards();
    await selectSensor(initial.sensor_id, true);
  } catch (error) {
    showAlert("#dashboard-error", friendlyError(error, "Factory Status를 조회하지 못했습니다. 잠시 후 다시 시도해 주세요."));
    $("#sensor-cards").innerHTML = '<p class="empty-state">센서 목록을 표시할 수 없습니다.</p>';
  } finally {
    $("#refresh-dashboard").disabled = false;
  }
}

function toolType(step) {
  if (step.type) return step.type === "knowledge" ? "rag" : step.type;
  if (MCP_TOOLS.has(step.tool)) return "mcp";
  if (step.tool === "search_maintenance_knowledge") return "rag";
  if (step.tool === "search_web") return "web";
  return "tool";
}

function renderTrace(payload) {
  const trace = Array.isArray(payload.tool_trace) ? payload.tool_trace : [];
  const steps = [...trace, {
    step: trace.length + 1,
    tool: "Final Answer",
    type: "llm",
    arguments: {},
    status: payload.generation_error ? "error" : "success",
    summary: payload.generation_error
      ? `Fallback mode: ${payload.generation_mode}`
      : `Gemini answer complete · ${payload.agent_step_count || 0} Agent steps`,
  }];
  $("#tool-trace").innerHTML = steps.map((step, index) => {
    const type = toolType(step);
    const status = step.status || "success";
    const argumentsText = JSON.stringify(step.arguments || {}, null, 2);
    return `
      <li class="trace-step" style="--delay:${index * 65}ms">
        <span class="trace-number">${escapeHtml(step.step || index + 1)}</span>
        <div class="trace-card">
          <div class="trace-title">
            <code>${escapeHtml(step.tool)}</code>
            <span class="tool-badge ${escapeHtml(type)}">${escapeHtml(type.toUpperCase())}</span>
          </div>
          <span class="trace-status ${escapeHtml(status)}">${status === "success" ? "✓ Success" : status === "error" ? "× Error" : "○ Skipped"}</span>
          <p class="trace-summary">${escapeHtml(step.summary || "실행 결과 요약 없음")}</p>
          ${Object.keys(step.arguments || {}).length ? `<details><summary>Arguments</summary><pre class="trace-arguments">${escapeHtml(argumentsText)}</pre></details>` : ""}
        </div>
      </li>
    `;
  }).join("");
}

function renderSources(payload) {
  const citations = Array.isArray(payload.citations) ? payload.citations : [];
  if (!citations.length) {
    $("#source-list").innerHTML = '<p class="empty-state">이 답변에는 별도 로컬 문서나 외부 자료가 사용되지 않았습니다.</p>';
    return;
  }
  $("#source-list").innerHTML = citations.map((item) => {
    const isWeb = item.type === "web";
    const url = isWeb ? safeUrl(item.url || item.source) : "";
    return `
      <details class="source-card">
        <summary>
          <span class="source-card-top"><span class="source-type ${isWeb ? "web" : ""}">${isWeb ? "WEB SOURCE" : "LOCAL DOCUMENT"}</span><span>＋</span></span>
          <span class="source-name">${escapeHtml(item.title || item.source || "Source")}</span>
          <span class="source-meta">${isWeb ? escapeHtml(url ? new URL(url).hostname : "External source") : `Chunk ${escapeHtml(item.chunk ?? "—")}`}</span>
        </summary>
        <p class="source-excerpt">
          ${isWeb && url ? `<a class="source-url" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(url)}</a>` : escapeHtml(item.excerpt || "검색 excerpt가 제공되지 않았습니다.")}
        </p>
      </details>
    `;
  }).join("");
}

function renderAgentResponse(payload) {
  const tools = Array.isArray(payload.tool_trace) ? payload.tool_trace.length : 0;
  const localSources = (payload.citations || []).filter((item) => item.type === "local_document").length;
  $("#agent-response").classList.remove("empty-response");
  $("#agent-response").innerHTML = `
    <div class="response-role">AI AGENT · ${escapeHtml(payload.agent_mode || "agent")}</div>
    <p class="response-question">${escapeHtml(payload.question)}</p>
    <p class="response-answer">${escapeHtml(payload.answer)}</p>
    <div class="response-stats">
      <span>Tools used ${tools}</span>
      <span>Local sources ${localSources}</span>
      <span>Web search ${payload.web_search_used ? "On" : "Off"}</span>
      <button class="execution-link" type="button" id="view-execution">View execution ↓</button>
    </div>
  `;
  $("#view-execution").addEventListener("click", () => {
    $("#execution-panel").scrollIntoView({behavior: "smooth", block: "center"});
    $("#execution-panel").focus({preventScroll: true});
  });
  const web = $("#web-indicator");
  web.className = `web-indicator ${payload.web_search_used ? "on" : "off"}`;
  web.textContent = payload.web_search_used
    ? `WEB SEARCH ON · ${payload.web_result_count || 0}`
    : "WEB SEARCH OFF";
}

async function submitQuestion(event) {
  event.preventDefault();
  const question = $("#question").value.trim();
  if (!question || state.loading) return;
  state.loading = true;
  hideAlert("#chat-error");
  $("#agent-loading").classList.remove("hidden");
  $("#ask-submit").disabled = true;
  try {
    const payload = await askAgent(question);
    state.agentResponse = payload;
    renderAgentResponse(payload);
    renderTrace(payload);
    renderSources(payload);
    $("#raw-response").textContent = JSON.stringify(payload, null, 2);
    $("#raw-response-button").disabled = false;
    if (payload.sensor_id && state.sensors.some((item) => String(item.sensor_id) === String(payload.sensor_id))) {
      await selectSensor(payload.sensor_id, true);
    }
  } catch (error) {
    showAlert("#chat-error", friendlyError(error, "Agent 요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요."));
  } finally {
    state.loading = false;
    $("#agent-loading").classList.add("hidden");
    $("#ask-submit").disabled = false;
  }
}

async function loadMcpStatus() {
  try {
    const payload = await getMcpStatus();
    state.mcp = payload;
    const available = Boolean(payload.available);
    const header = $("#mcp-header-status");
    header.className = `system-badge ${available ? "available" : "unavailable"}`;
    header.innerHTML = `<span class="status-dot" aria-hidden="true"></span>MCP stdio · ${available ? "Available" : "Unavailable"}`;
    const panel = $("#mcp-panel-status");
    panel.className = `status-pill ${available ? "normal" : "warning"}`;
    panel.textContent = available ? "AVAILABLE" : "UNAVAILABLE";
    $("#mcp-tool-count").textContent = String(payload.tool_count ?? 0);
    $("#mcp-tool-list").innerHTML = (payload.tools || []).length
      ? payload.tools.map((item) => `<li title="${escapeHtml(item.description || "")}">${escapeHtml(item.name)}</li>`).join("")
      : "<li>Tool 목록을 확인할 수 없습니다.</li>";
  } catch (error) {
    console.error(error);
    const header = $("#mcp-header-status");
    header.className = "system-badge unavailable";
    header.innerHTML = '<span class="status-dot" aria-hidden="true"></span>MCP stdio · Unavailable';
    $("#mcp-panel-status").className = "status-pill warning";
    $("#mcp-panel-status").textContent = "UNAVAILABLE";
  }
}

function setupEvents() {
  $("#refresh-dashboard").addEventListener("click", loadDashboard);
  $("#ask-form").addEventListener("submit", submitQuestion);
  $("#metric-select").addEventListener("change", (event) => {
    state.metric = event.target.value;
    renderChart();
  });
  $$("[data-hours]").forEach((button) => {
    button.addEventListener("click", () => {
      state.hours = Number(button.dataset.hours);
      $$("[data-hours]").forEach((item) => item.classList.toggle("active", item === button));
      reloadHistory();
    });
  });
  $$("[data-prompt]").forEach((button) => {
    button.addEventListener("click", () => {
      $("#question").value = button.dataset.prompt;
      $("#question").focus();
    });
  });
  $$('[data-open-modal]').forEach((button) => {
    button.addEventListener("click", () => $(`#${button.dataset.openModal}`).showModal());
  });
  $("#raw-response-button").addEventListener("click", () => $("#raw-response-modal").showModal());
  $$('[data-close-modal]').forEach((button) => {
    button.addEventListener("click", () => button.closest("dialog").close());
  });
  $$("dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
  });
}

setupEvents();
Promise.allSettled([loadDashboard(), loadMcpStatus()]);
