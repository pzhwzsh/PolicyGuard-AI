const $ = (selector) => document.querySelector(selector);
let currentRun = null;
let reviewer = "local-reviewer";
let progressTimer = null;

const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
}[char]));

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: {"Content-Type": "application/json", ...(options.headers || {})},
    ...options
  });
  const body = response.status === 204 ? null : await response.json();
  if (!response.ok) throw new Error(body?.detail || `请求失败 (${response.status})`);
  return body;
}

function toast(message) {
  const target = $("#toast");
  target.textContent = message;
  target.hidden = false;
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => { target.hidden = true; }, 3500);
}

function statusText(status) {
  return ({
    review_required: "待人工确认", needs_more_evidence: "证据不足",
    review_accepted: "依据已确认", review_rejected: "结果已驳回",
    remediation_planned: "整改建议已生成", draft_ready: "整改草案已生成"
  })[status] || status;
}

function updateStepper(status) {
  const order = ["submit", "evidence", "review", "remediation"];
  const active = status == null ? 0 : ["review_required", "needs_more_evidence"].includes(status) ? 2
    : status === "review_rejected" ? 2 : 3;
  document.querySelectorAll(".stepper li").forEach((item) => {
    const index = order.indexOf(item.dataset.step);
    item.classList.toggle("active", index === active);
    item.classList.toggle("done", index < active);
  });
}

function setProgress(active) {
  const progress = $("#progress");
  window.clearInterval(progressTimer);
  if (!active) {
    progress.hidden = true;
    return;
  }
  const phases = [
    ["正在检索政策证据", "正在匹配市场、类目与宣传表述"],
    ["正在核对适用范围", "筛选可追溯的候选条款"],
    ["正在整理审核任务", "结果将进入人工确认，不会自动发布"]
  ];
  let phase = 0;
  const render = () => {
    $("#progress-title").textContent = phases[phase][0];
    $("#progress-detail").textContent = phases[phase][1];
    phase = (phase + 1) % phases.length;
  };
  progress.hidden = false;
  render();
  progressTimer = window.setInterval(render, 1400);
}

function resetCheck({focus = false} = {}) {
  currentRun = null;
  $("#check-form").reset();
  $("#result").hidden = true;
  $("#draft-state").textContent = "尚未填写";
  setProgress(false);
  updateStepper(null);
  if (focus) {
    const input = $('#check-form input[name="external_id"]');
    input.focus();
    input.scrollIntoView({behavior: "smooth", block: "center"});
  }
}

function renderRun(run) {
  currentRun = run;
  $("#result").hidden = false;
  $("#result-title").textContent = statusText(run.status);
  const note = run.result_payload.note;
  $("#result-note").textContent = note === "Candidate evidence only; no legal conclusion or automatic mutation."
    ? "以下仅为候选证据，不构成法律结论，也不会自动修改或发布内容。"
    : note || "请核对证据来源、适用范围和生效时间。";
  $("#report-link").href = `/api/v1/workflows/compliance/${run.id}/report?format=pdf`;
  const markets = run.result_payload.markets || [];
  const evidenceCount = markets.reduce((total, market) => total + (market.candidate_evidence || []).length, 0);
  $("#summary-status").textContent = statusText(run.status);
  $("#summary-evidence").textContent = `${evidenceCount} 条`;
  $("#summary-markets").textContent = markets.map((market) => market.market).join(" / ") || "-";
  $("#evidence-list").innerHTML = markets.map((market) => {
    const evidence = market.candidate_evidence || [];
    return `<article class="market-row"><div class="market-title"><strong>${escapeHtml(market.market)}</strong><span>${evidence.length ? `${evidence.length} 条候选依据` : "未找到足够依据"}</span></div>${evidence.map((item, index) => `<details class="evidence-item"${index === 0 ? " open" : ""}><summary><span class="evidence-index">${index + 1}</span><span>${escapeHtml(item.heading)}</span></summary><div class="evidence-body"><p>${escapeHtml(item.text)}</p><a href="${escapeHtml(item.source_url)}" target="_blank" rel="noreferrer">打开官方来源</a></div></details>`).join("") || '<p class="empty-evidence">当前资料不足，系统不据此作出合规结论。</p>'}</article>`;
  }).join("");
  const reviewable = ["review_required", "needs_more_evidence"].includes(run.status);
  const acceptable = run.status === "review_required";
  $("#accept-button").disabled = !acceptable;
  $("#reject-button").disabled = !reviewable;
  if (run.status === "needs_more_evidence") {
    $("#review-title").textContent = "证据不足，不能确认依据";
    $("#review-note").textContent = "当前结果只能标记为不可采用，补充可靠证据后需重新检查。";
  } else if (reviewable) {
    $("#review-title").textContent = "请人工核对证据";
    $("#review-note").textContent = "确认只代表允许进入整改阶段，不会自动发布内容。";
  } else {
    $("#review-title").textContent = run.status === "review_rejected" ? "本次结果已驳回" : "人工确认已完成";
    $("#review-note").textContent = run.status === "review_rejected" ? "该结果不会进入整改环节。" : "后续草案仍需人工决定是否采用。";
  }
  $("#remediation").hidden = !["review_accepted", "remediation_planned", "draft_ready"].includes(run.status);
  $("#plan-button").hidden = run.status !== "review_accepted";
  renderRemediation(run.result_payload.remediation_plan);
  updateStepper(run.status);
  $("#result").scrollIntoView({behavior: "smooth", block: "start"});
}

function renderRemediation(plan) {
  const operations = plan?.operations || [];
  $("#remediation-list").innerHTML = operations.map((item) => `<article class="change"><strong>${escapeHtml(item.field === "title" ? "商品标题" : "商品描述")}</strong><div class="change-grid"><div><small>修改前</small><p>${escapeHtml(item.before)}</p></div><div><small>建议修改</small><p>${escapeHtml(item.after)}</p></div></div></article>`).join("") || '<p class="empty">确认依据后，可生成保守的整改建议。</p>';
}

async function review(decision) {
  const run = await api(`/api/v1/workflows/compliance/${currentRun.id}/review`, {
    method: "POST",
    body: JSON.stringify({
      decision_id: crypto.randomUUID(), decision, reviewer,
      comment: decision === "accept" ? "人工已核对候选证据" : "人工认为当前结果不可采用"
    })
  });
  renderRun(run);
  await loadHistory();
}

async function loadHistory() {
  const runs = await api("/api/v1/workflows/compliance?limit=50");
  $("#history-list").innerHTML = runs.map((run) => {
    const product = run.input_payload.product || {};
    return `<button class="history-item" type="button" data-run-id="${escapeHtml(run.id)}"><span><strong>${escapeHtml(product.title || product.external_id || "未命名检查")}</strong><small>${escapeHtml(product.external_id || run.id)}</small></span><span class="status">${escapeHtml(statusText(run.status))}</span><time>${new Date(run.updated_at).toLocaleString("zh-CN")}</time></button>`;
  }).join("") || '<p class="empty">暂无检查记录</p>';
}

$("#check-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#submit-button");
  const data = new FormData(event.currentTarget);
  const markets = data.getAll("markets");
  if (!markets.length) return toast("请至少选择一个目标市场");
  button.disabled = true;
  button.textContent = "检查中...";
  $("#draft-state").textContent = "正在处理";
  setProgress(true);
  try {
    const run = await api("/api/v1/workflows/compliance", {
      method: "POST",
      body: JSON.stringify({
        product: {
          external_id: data.get("external_id"), title: data.get("title"),
          description: data.get("description"), category: data.get("category"), attributes: {}
        },
        markets, category: data.get("category"), channel: "all"
      })
    });
    renderRun(run);
    $("#draft-state").textContent = "已生成检查记录";
    await loadHistory();
  } catch (error) {
    $("#draft-state").textContent = "检查失败，可重新提交";
    toast(error.message);
  }
  finally { setProgress(false); button.disabled = false; button.textContent = "开始检查"; }
});

$("#sample-button").addEventListener("click", () => {
  resetCheck();
  const form = $("#check-form");
  form.elements.external_id.value = `DEMO-${new Date().toISOString().slice(0, 10).replaceAll("-", "")}`;
  form.elements.category.value = "美妆";
  form.elements.title.value = "国家级护肤品 100%安全";
  form.elements.description.value = "采用专业配方，宣传为绝对安全、效果最佳。";
  $("#draft-state").textContent = "演示样例已载入";
  toast("演示样例已载入，可以开始检查");
});
$("#new-check-button").addEventListener("click", () => resetCheck({focus: true}));

$("#accept-button").addEventListener("click", () => review("accept").catch((error) => toast(error.message)));
$("#reject-button").addEventListener("click", () => review("reject").catch((error) => toast(error.message)));
$("#plan-button").addEventListener("click", async () => {
  try {
    renderRun(await api(`/api/v1/workflows/compliance/${currentRun.id}/remediation-plan`, {
      method: "POST", body: JSON.stringify({plan_id: crypto.randomUUID(), mode: "pipeline"})
    }));
    await loadHistory();
  } catch (error) { toast(error.message); }
});

document.querySelectorAll(".nav-item").forEach((button) => button.addEventListener("click", async () => {
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item === button));
  document.querySelectorAll(".view").forEach((view) => { view.hidden = true; view.classList.remove("active"); });
  const view = $(`#${button.dataset.view}-view`);
  view.hidden = false;
  view.classList.add("active");
  if (button.dataset.view === "history") await loadHistory();
}));

$("#history-list").addEventListener("click", async (event) => {
  const item = event.target.closest("[data-run-id]");
  if (!item) return;
  renderRun(await api(`/api/v1/workflows/compliance/${item.dataset.runId}`));
  document.querySelector('[data-view="check"]').click();
});
$("#refresh-history").addEventListener("click", () => loadHistory().catch((error) => toast(error.message)));
$("#logout-button").addEventListener("click", async () => {
  await api("/api/v1/auth/logout", {method: "POST"});
  location.assign("/login");
});

async function start() {
  const status = await api("/api/v1/auth/status");
  if (status.required && !status.authenticated) return location.replace("/login");
  if (status.authenticated) {
    const user = await api("/api/v1/auth/me");
    reviewer = user.email;
    $("#account-email").textContent = user.email;
    $("#logout-button").hidden = false;
  }
  await loadHistory();
}

start().catch((error) => toast(error.message));
