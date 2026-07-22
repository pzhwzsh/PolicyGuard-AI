const $ = (selector) => document.querySelector(selector);
let currentRunId = null;

const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
}[char]));

async function api(path, options = {}) {
  const response = await fetch(path, {headers: {"Content-Type": "application/json"}, ...options});
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
  return body;
}

const statusLabel = (status) => ({
  review_required: "发现需要确认的风险",
  needs_more_evidence: "证据不足，需要进一步确认",
  review_accepted: "风险依据已确认",
  review_rejected: "结果已标记为有误",
  remediation_planned: "修改建议已生成",
  completed: "修改稿已生成"
}[status] || "检查已完成");

const marketLabel = (market) => ({CN: "中国", US: "美国", EU: "欧盟"}[market] || market);

function renderRemediation(run) {
  const plan = run.result_payload.remediation_plan;
  const draft = run.result_payload.draft;
  $("#create-plan-button").disabled = run.status !== "review_accepted";
  $("#create-draft-button").disabled = run.status !== "remediation_planned";
  const operations = plan?.operations || [];
  $("#remediation-result").innerHTML = operations.map((item) => `
    <article class="remediation-item">
      <strong>${escapeHtml(item.field === "title" ? "商品标题" : "商品描述")}</strong>
      <div class="diff-grid">
        <div><small>修改前</small><p>${escapeHtml(item.before)}</p></div>
        <div><small>建议修改</small><p>${escapeHtml(item.after)}</p></div>
      </div>
      ${(item.legal_basis || []).map((basis) => `
        <a href="${escapeHtml(basis.source_url)}" target="_blank" rel="noreferrer">
          ${escapeHtml(marketLabel(basis.jurisdiction))} · ${escapeHtml(basis.heading)}
        </a>`).join("")}
    </article>`).join("") + (draft ? `
      <p class="notice">修改稿已重新检查。仍需人工确认后再使用。</p>` : "");
}

function renderRun(run) {
  currentRunId = run.id;
  $("#result-section").hidden = false;
  $("#result-title").textContent = statusLabel(run.status);
  $("#notice").textContent = run.result_payload.note || "请核对风险依据和适用范围。";
  $("#market-results").innerHTML = (run.result_payload.markets || []).map((market) => `
    <article class="market">
      <div class="market-header">
        <strong>${escapeHtml(marketLabel(market.market))}</strong>
        <span>${market.candidate_evidence.length ? `找到 ${market.candidate_evidence.length} 条相关依据` : "未找到足够依据"}</span>
      </div>
      ${(market.candidate_evidence || []).map((item) => `
        <div class="evidence">
          <a href="${escapeHtml(item.source_url)}" target="_blank" rel="noreferrer">${escapeHtml(item.heading)}</a>
          <p>${escapeHtml(item.text)}</p>
        </div>`).join("") || "<p>当前资料不足，不能据此作出判断。</p>"}
    </article>`).join("");
  $("#accept-button").disabled = !["review_required", "needs_more_evidence"].includes(run.status);
  $("#reject-button").disabled = $("#accept-button").disabled;
  $("#download-report").href = `/api/v1/workflows/compliance/${run.id}/report?format=pdf`;
  renderRemediation(run);
  $("#result-section").scrollIntoView({behavior: "smooth"});
}

async function review(decision) {
  if (!currentRunId) return;
  renderRun(await api(`/api/v1/workflows/compliance/${currentRunId}/review`, {
    method: "POST",
    body: JSON.stringify({
      decision_id: crypto.randomUUID(), decision, reviewer: "content-owner", comment: "User decision"
    })
  }));
}

$("#review-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  if (!data.getAll("markets").length) {
    alert("请至少选择一个目标市场");
    return;
  }
  const button = $("#run-button");
  button.disabled = true;
  button.textContent = "检查中";
  try {
    renderRun(await api("/api/v1/workflows/compliance", {
      method: "POST",
      body: JSON.stringify({
        product: {
          external_id: data.get("external_id") || `WEB-${Date.now()}`, title: data.get("title"),
          description: data.get("description"), category: data.get("category"), attributes: {}
        },
        markets: data.getAll("markets"), category: data.get("category"), channel: "all"
      })
    }));
  } catch (error) {
    alert(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "开始检查";
  }
});

$("#accept-button").addEventListener("click", () => review("accept").catch((error) => alert(error.message)));
$("#reject-button").addEventListener("click", () => review("reject").catch((error) => alert(error.message)));

$("#create-plan-button").addEventListener("click", async () => {
  if (!currentRunId) return;
  try {
    renderRun(await api(`/api/v1/workflows/compliance/${currentRunId}/remediation-plan`, {
      method: "POST", body: JSON.stringify({plan_id: crypto.randomUUID(), mode: "pipeline"})
    }));
  } catch (error) { alert(error.message); }
});

$("#create-draft-button").addEventListener("click", async () => {
  if (!currentRunId) return;
  try {
    renderRun(await api(`/api/v1/workflows/compliance/${currentRunId}/draft`, {
      method: "POST",
      body: JSON.stringify({execution_id: crypto.randomUUID(), approved_by: "content-owner"})
    }));
  } catch (error) { alert(error.message); }
});

fetch("/health")
  .then((response) => {
    $("#service-status").textContent = response.ok ? "服务可用" : "服务异常";
  })
  .catch(() => { $("#service-status").textContent = "服务异常"; });
