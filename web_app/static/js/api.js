async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  let payload = {};
  try {
    payload = await response.json();
  } catch {
    payload = {};
  }
  if (!response.ok) {
    const error = new Error(payload.message || "요청을 처리하지 못했습니다.");
    error.status = response.status;
    throw error;
  }
  return payload;
}

export function getDashboardOverview() {
  return requestJson("/api/dashboard");
}

export function getSensorStatus(sensorId) {
  return requestJson(`/api/sensors/${encodeURIComponent(sensorId)}/status`);
}

export function getSensorHistory(sensorId, hours = 24, limit = 200) {
  const params = new URLSearchParams({hours: String(hours), limit: String(limit)});
  return requestJson(`/api/sensors/${encodeURIComponent(sensorId)}/history?${params}`);
}

export function getMcpStatus() {
  return requestJson("/api/system/mcp");
}

export function askAgent(question) {
  return requestJson("/api/ask", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({question}),
  });
}
