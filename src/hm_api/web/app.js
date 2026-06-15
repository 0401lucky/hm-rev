const statusPill = document.querySelector("#statusPill");
const accountName = document.querySelector("#accountName");
const endpointText = document.querySelector("#endpointText");
const keyStatus = document.querySelector("#keyStatus");
const credentialStatus = document.querySelector("#credentialStatus");
const portChip = document.querySelector("#portChip");
const totalCalls = document.querySelector("#totalCalls");
const totalTokens = document.querySelector("#totalTokens");
const successRate = document.querySelector("#successRate");
const streamCalls = document.querySelector("#streamCalls");
const statsUpdatedAt = document.querySelector("#statsUpdatedAt");
const dailyChart = document.querySelector("#dailyChart");
const modelList = document.querySelector("#modelList");
const recentCalls = document.querySelector("#recentCalls");
const proxyInput = document.querySelector("#proxyInput");
const manualAuthInput = document.querySelector("#manualAuthInput");
const callbackHint = document.querySelector("#callbackHint");
const callbackHintTitle = document.querySelector("#callbackHintTitle");
const callbackHintBody = document.querySelector("#callbackHintBody");
const bridgeCommand = document.querySelector("#bridgeCommand");
const loginButton = document.querySelector("#loginButton");
const pasteAuthButton = document.querySelector("#pasteAuthButton");
const importAuthButton = document.querySelector("#importAuthButton");
const copyBridgeButton = document.querySelector("#copyBridgeButton");
const refreshButton = document.querySelector("#refreshButton");
const copyEndpointButton = document.querySelector("#copyEndpointButton");
const toast = document.querySelector("#toast");

let pollTimer = null;
let toastTimer = null;

function renderIcons() {
  if (window.lucide) {
    window.lucide.createIcons();
  }
}

function showToast(message) {
  window.clearTimeout(toastTimer);
  toast.textContent = message;
  toast.classList.add("show");
  toastTimer = window.setTimeout(() => toast.classList.remove("show"), 2600);
}

function setPill(loggedIn) {
  statusPill.classList.toggle("ready", loggedIn);
  statusPill.classList.toggle("offline", !loggedIn);
  statusPill.classList.remove("pending");
  statusPill.innerHTML = `<span></span>${loggedIn ? "已授权" : "未授权"}`;
}

function endpointBase() {
  return `${window.location.origin}/v1`;
}

function formatNumber(value) {
  return new Intl.NumberFormat("zh-CN").format(Number(value || 0));
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatPercent(success, total) {
  if (!total) {
    return "--";
  }
  return `${Math.round((success / total) * 100)}%`;
}

function formatTime(value) {
  if (!value) {
    return "--";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function compactDate(value) {
  const parts = String(value || "").split("-");
  return parts.length === 3 ? `${parts[1]}/${parts[2]}` : "--";
}

function renderEmpty(target, text) {
  target.innerHTML = `<div class="empty-state">${text}</div>`;
}

function bridgeCommandText() {
  return `uv run hm-api bridge --target ${window.location.origin} --port 8000`;
}

function setCallbackHint(title, body, active = false) {
  callbackHintTitle.textContent = title;
  callbackHintBody.textContent = body;
  callbackHint.classList.toggle("active", active);
}

async function refreshStatus() {
  try {
    const response = await fetch("/api/status");
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    const loggedIn = Boolean(data.logged_in);

    setPill(loggedIn);
    accountName.textContent = loggedIn
      ? data.user?.user_name || data.user?.user_id || "已授权账号"
      : "未授权";
    endpointText.textContent = endpointBase();
    keyStatus.textContent = data.api_key_enabled ? "已启用" : "未启用";
    credentialStatus.textContent = loggedIn ? "已写入 cred" : "等待授权";
    portChip.textContent = window.location.port || "80";

    if (loggedIn && pollTimer) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  } catch (error) {
    setPill(false);
    accountName.textContent = "服务不可用";
    credentialStatus.textContent = "检测失败";
    showToast("状态刷新失败");
  }
}

function renderDailyChart(rows) {
  if (!rows.length) {
    renderEmpty(dailyChart, "暂无调用数据");
    return;
  }

  const maxTokens = Math.max(...rows.map((row) => row.total_tokens || 0), 1);
  dailyChart.innerHTML = rows
    .map((row) => {
      const height = Math.max(Math.round(((row.total_tokens || 0) / maxTokens) * 100), 3);
      return `
        <div class="usage-bar" title="${compactDate(row.date)} · ${formatNumber(row.requests)} 次 · ${formatNumber(row.total_tokens)} tokens">
          <div class="usage-bar-track">
            <span class="usage-bar-fill" style="height:${height}%"></span>
          </div>
          <span class="usage-bar-value">${formatNumber(row.requests)}</span>
          <span class="usage-bar-label">${compactDate(row.date)}</span>
        </div>
      `;
    })
    .join("");
}

function renderModels(rows) {
  if (!rows.length) {
    renderEmpty(modelList, "暂无模型数据");
    return;
  }

  const maxTokens = Math.max(...rows.map((row) => row.total_tokens || 0), 1);
  modelList.innerHTML = rows
    .slice(0, 8)
    .map((row) => {
      const width = Math.max(Math.round(((row.total_tokens || 0) / maxTokens) * 100), 4);
      return `
        <div class="model-row">
          <div>
            <div class="model-name">${escapeHtml(row.model)}</div>
            <div class="model-meta">${formatNumber(row.requests)} 次 · ${formatNumber(row.total_tokens)} tokens</div>
          </div>
          <div class="model-meta">${formatPercent(row.success, row.requests)}</div>
          <div class="model-bar"><span style="width:${width}%"></span></div>
        </div>
      `;
    })
    .join("");
}

function renderRecent(rows) {
  if (!rows.length) {
    renderEmpty(recentCalls, "暂无请求记录");
    return;
  }

  recentCalls.innerHTML = rows
    .slice(0, 12)
    .map((row) => {
      const statusClass = row.success ? "ok" : "fail";
      const statusText = row.success ? "成功" : `失败 ${row.status_code || ""}`;
      const mode = row.stream ? "流式" : "非流式";
      return `
        <div class="recent-row">
          <div>
            <div class="recent-model">${escapeHtml(row.model || "unknown")}</div>
            <div class="recent-meta">${formatTime(row.time)} · ${mode}</div>
          </div>
          <div class="recent-status ${statusClass}">${statusText}</div>
          <div class="recent-meta">${formatNumber(row.total_tokens)} tokens</div>
          <div class="recent-meta">${formatNumber(row.duration_ms)} ms</div>
        </div>
      `;
    })
    .join("");
}

async function refreshStats() {
  try {
    const response = await fetch("/api/stats");
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    const totals = data.totals || {};
    totalCalls.textContent = formatNumber(totals.requests);
    totalTokens.textContent = formatNumber(totals.total_tokens);
    successRate.textContent = formatPercent(totals.success, totals.requests);
    streamCalls.textContent = formatNumber(totals.stream);
    statsUpdatedAt.textContent = data.updated_at ? `更新 ${formatTime(data.updated_at)}` : "未更新";
    renderDailyChart(data.daily || []);
    renderModels(data.models || []);
    renderRecent(data.recent || []);
  } catch (error) {
    statsUpdatedAt.textContent = "统计不可用";
    renderEmpty(dailyChart, "统计加载失败");
    renderEmpty(modelList, "统计加载失败");
    renderEmpty(recentCalls, "统计加载失败");
  }
}

async function startLogin() {
  loginButton.disabled = true;
  const proxy = proxyInput.value.trim();

  try {
    const response = await fetch("/api/auth/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(proxy ? { proxy } : {}),
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const data = await response.json();
    window.open(data.login_url, "_blank", "noopener,noreferrer");
    showToast("授权页已打开");
    setCallbackHint(
      "等待本机回调",
      "若新标签页显示 localhost 无法访问，请先运行桥接命令后重新授权。",
      true,
    );

    window.clearInterval(pollTimer);
    pollTimer = window.setInterval(refreshStatus, 3000);
  } catch (error) {
    showToast("授权启动失败");
  } finally {
    loginButton.disabled = false;
  }
}

async function pasteManualAuth() {
  try {
    const text = await navigator.clipboard.readText();
    if (!text.trim()) {
      showToast("剪贴板为空");
      return;
    }
    manualAuthInput.value = text.trim();
    manualAuthInput.focus();
    showToast("已读取剪贴板");
  } catch (error) {
    showToast("请手动粘贴回调内容");
  }
}

async function importManualAuth() {
  const callback = manualAuthInput.value.trim();
  if (!callback) {
    showToast("请粘贴回调 URL 或 tempToken");
    manualAuthInput.focus();
    return;
  }

  importAuthButton.disabled = true;
  const proxy = proxyInput.value.trim();

  try {
    const response = await fetch("/api/auth/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(proxy ? { callback, proxy } : { callback }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }

    manualAuthInput.value = "";
    window.clearInterval(pollTimer);
    pollTimer = null;
    await refreshStatus();
    await refreshStats();
    setCallbackHint("授权已导入", "云端凭据已经写入持久化目录。", false);
    showToast("授权已导入");
  } catch (error) {
    showToast(error.message || "导入失败");
  } finally {
    importAuthButton.disabled = false;
  }
}

async function copyBridgeCommand() {
  const value = bridgeCommandText();
  try {
    await navigator.clipboard.writeText(value);
    showToast("桥接命令已复制");
  } catch (error) {
    showToast("复制失败，请手动复制");
  }
}

async function copyEndpoint() {
  const value = endpointBase();
  try {
    await navigator.clipboard.writeText(value);
    showToast("API 地址已复制");
  } catch (error) {
    endpointText.textContent = value;
    showToast("复制失败，地址已显示");
  }
}

loginButton.addEventListener("click", startLogin);
pasteAuthButton.addEventListener("click", pasteManualAuth);
importAuthButton.addEventListener("click", importManualAuth);
copyBridgeButton.addEventListener("click", copyBridgeCommand);
refreshButton.addEventListener("click", refreshStatus);
copyEndpointButton.addEventListener("click", copyEndpoint);

window.addEventListener("focus", refreshStatus);
window.addEventListener("load", () => {
  bridgeCommand.textContent = bridgeCommandText();
  renderIcons();
  refreshStatus();
  refreshStats();
  window.setInterval(refreshStats, 15000);
});
