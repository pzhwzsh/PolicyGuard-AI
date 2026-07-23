const $ = (selector) => document.querySelector(selector);
let currentRunId = null;
let currentTablePreview = null;
let currentCleaningReportUrl = null;

const canonicalFieldLabels = {
  ignore: "忽略此列",
  external_id: "商品标识 / SKU",
  category: "商品类目",
  title: "商品标题",
  description: "商品描述",
  markets: "目标市场"
};

const cleaningErrorLabels = {
  table_cleaning_mapping_invalid: "字段映射存在冲突，请确保每个目标字段只对应一列",
  table_cleaning_has_blocking_errors: "仍有异常行；修正数据或勾选“仅提交有效行”",
  table_cleaning_has_no_valid_rows: "没有可提交的有效数据行",
  table_cleaning_revision_conflict: "预览已更新，请重新上传后确认",
  table_cleaning_already_confirmed: "这份清洗结果已经确认，请勿重复提交"
};

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

async function pollBatch(jobId, attempts = 0) {
  const job = await api(`/api/v1/jobs/${jobId}`);
  $("#batch-status").hidden = false;
  $("#batch-status").innerHTML = `<strong>批量任务</strong><span>${escapeHtml(job.status)} · 已尝试 ${escapeHtml(job.attempts)}/${escapeHtml(job.max_attempts)}</span>${currentCleaningReportUrl ? `<a class="button-link secondary" href="${escapeHtml(currentCleaningReportUrl)}">下载清洗报告</a>` : ""}`;
  if (job.status === "completed") {
    $("#batch-status").innerHTML += `<a class="button-link" href="/api/v1/batches/${jobId}/result">下载检查结果</a>`;
    return;
  }
  if (job.status === "failed" || attempts >= 120) return;
  window.setTimeout(() => pollBatch(jobId, attempts + 1).catch(() => {}), 1000);
}

function mappingOptions(selected) {
  return Object.entries(canonicalFieldLabels).map(([value, label]) =>
    `<option value="${value}"${value === (selected || "ignore") ? " selected" : ""}>${label}</option>`
  ).join("");
}

function validateMappingSelections() {
  const badge = $("#cleaning-quality-badge");
  const button = $("#confirm-cleaning-button");
  if (!badge || !button || !currentTablePreview) return;
  let mappingProblems = 0;
  document.querySelectorAll(".mapping-sheet").forEach((sheet) => {
    const selected = [...sheet.querySelectorAll("select[data-column]")]
      .map((select) => select.value)
      .filter((value) => value !== "ignore");
    mappingProblems += selected.length - new Set(selected).size;
    mappingProblems += ["title", "markets"].filter((field) => !selected.includes(field)).length;
  });
  button.disabled = mappingProblems > 0;
  badge.className = `quality-badge ${mappingProblems || currentTablePreview.summary.invalid_row_count ? "warning" : "ready"}`;
  badge.textContent = mappingProblems
    ? `${mappingProblems} 个映射问题`
    : (currentTablePreview.summary.invalid_row_count
      ? `${currentTablePreview.summary.invalid_row_count} 行待处理`
      : "可以提交");
}

function renderCleaningPreview(preview) {
  currentTablePreview = preview;
  const container = $("#batch-cleaning-preview");
  const summary = preview.summary;
  const hasBlockingIssues = summary.invalid_row_count || summary.mapping_error_count;
  container.hidden = false;
  container.innerHTML = `
    <div class="cleaning-hero">
      <div><p>清洗预览</p><h3>确认字段映射后再启动 AI 审核</h3></div>
      <span id="cleaning-quality-badge" class="quality-badge ${hasBlockingIssues ? "warning" : "ready"}">
        ${summary.mapping_error_count ? `${summary.mapping_error_count} 个映射冲突` : (summary.invalid_row_count ? `${summary.invalid_row_count} 行待处理` : "可以提交")}
      </span>
    </div>
    <div class="cleaning-metrics">
      <article><span>有效 Sheet</span><strong>${escapeHtml(summary.sheet_count)}</strong></article>
      <article><span>总数据行</span><strong>${escapeHtml(summary.row_count)}</strong></article>
      <article><span>有效行</span><strong>${escapeHtml(summary.valid_row_count)}</strong></article>
      <article><span>异常行</span><strong>${escapeHtml(summary.invalid_row_count)}</strong></article>
      <article><span>模型调用</span><strong>${escapeHtml(summary.model_call_count)}</strong><small>规则清洗，零模型费用</small></article>
    </div>
    ${preview.skipped_sheets.length ? `<p class="cleaning-note">已跳过无商品表头的 Sheet：${preview.skipped_sheets.map((item) => escapeHtml(item.name)).join("、")}</p>` : ""}
    <div class="mapping-grid">
      ${preview.sheets.map((sheet) => `
        <article class="mapping-sheet" data-sheet="${escapeHtml(sheet.name)}">
          <div class="mapping-title"><strong>${escapeHtml(sheet.name)}</strong><span>表头第 ${escapeHtml(sheet.header_row)} 行 · ${escapeHtml(sheet.row_count)} 条</span></div>
          ${sheet.columns.map((column) => `
            <label>${escapeHtml(column)}
              <select data-column="${escapeHtml(column)}">${mappingOptions(sheet.mapping[column])}</select>
            </label>`).join("")}
        </article>`).join("")}
    </div>
    <div class="cleaning-table-wrap">
      <table class="cleaning-table">
        <thead><tr><th>来源</th><th>行号</th><th>SKU</th><th>清洗后标题</th><th>市场</th><th>状态</th></tr></thead>
        <tbody>${preview.rows.slice(0, 12).map((item) => `
          <tr class="${item.valid ? "" : "invalid"}">
            <td>${escapeHtml(item.source_sheet)}</td><td>${escapeHtml(item.source_row)}</td>
            <td>${escapeHtml(item.cleaned.external_id)}</td><td>${escapeHtml(item.cleaned.title)}</td>
            <td>${escapeHtml(item.cleaned.markets)}</td>
            <td>${item.valid ? "有效" : escapeHtml(item.issues.map((issue) => issue.code).join(", "))}</td>
          </tr>`).join("")}</tbody>
      </table>
    </div>
    <div class="cleaning-actions">
      <label><input id="allow-partial-cleaning" type="checkbox"> 仅提交有效行，异常行保留在清洗报告</label>
      <button id="confirm-cleaning-button" type="button">确认清洗并启动审核</button>
    </div>`;
  validateMappingSelections();
}

$("#batch-upload-button").textContent = "清洗并预览";
$("#batch-upload-button").addEventListener("click", async () => {
  const file = $("#batch-file").files[0];
  if (!file) { alert("请先选择 CSV 或 XLSX 文件"); return; }
  const button = $("#batch-upload-button");
  const form = new FormData();
  form.append("file", file);
  button.disabled = true;
  button.textContent = "正在清洗";
  currentCleaningReportUrl = null;
  currentTablePreview = null;
  $("#batch-cleaning-preview").hidden = true;
  try {
    const response = await fetch("/api/v1/batches/clean-preview", {method: "POST", body: form});
    const preview = await response.json();
    if (!response.ok) throw new Error(preview.detail || `HTTP ${response.status}`);
    renderCleaningPreview(preview);
  } catch (error) { alert(error.message); }
  finally { button.disabled = false; button.textContent = "清洗并预览"; }
});

$("#batch-cleaning-preview").addEventListener("click", async (event) => {
  const button = event.target.closest("#confirm-cleaning-button");
  if (!button || !currentTablePreview) return;
  const fieldMappings = {};
  document.querySelectorAll(".mapping-sheet").forEach((sheet) => {
    const mapping = {};
    sheet.querySelectorAll("select[data-column]").forEach((select) => {
      mapping[select.dataset.column] = select.value;
    });
    fieldMappings[sheet.dataset.sheet] = mapping;
  });
  button.disabled = true;
  button.textContent = "正在确认";
  try {
    const confirmed = await api(`/api/v1/batches/cleaning/${currentTablePreview.table_id}/confirm`, {
      method: "POST",
      body: JSON.stringify({
        expected_revision: currentTablePreview.revision,
        reviewer: "content-owner",
        field_mappings: fieldMappings,
        allow_partial: $("#allow-partial-cleaning").checked
      })
    });
    currentCleaningReportUrl = confirmed.report_url;
    $("#batch-status").hidden = false;
    $("#batch-status").innerHTML = `<strong>清洗已确认</strong><span>${escapeHtml(confirmed.summary.valid_row_count)} 行已进入审核队列</span><a class="button-link secondary" href="${escapeHtml(confirmed.report_url)}">下载清洗报告</a>`;
    await pollBatch(confirmed.job.id);
  } catch (error) { alert(cleaningErrorLabels[error.message] || error.message); }
  finally { button.disabled = false; button.textContent = "确认清洗并启动审核"; }
});

$("#batch-cleaning-preview").addEventListener("change", (event) => {
  if (event.target.matches("select[data-column]")) validateMappingSelections();
});

async function pollMedia(jobId, attempts = 0) {
  const job = await api(`/api/v1/jobs/${jobId}`);
  const target = $("#media-status");
  target.hidden = false;
  target.innerHTML = `<strong>媒体 OCR</strong><span>${escapeHtml(job.status)} · 已尝试 ${escapeHtml(job.attempts)}/${escapeHtml(job.max_attempts)}</span>`;
  if (job.status === "completed") {
    target.innerHTML += `<span>${escapeHtml(job.result.frame_count || 0)} 个采样帧 · 等待人工复核</span>`;
    return;
  }
  if (job.status === "failed" || attempts >= 180) {
    target.innerHTML += `<span>${escapeHtml(job.error || "处理超时")}</span>`;
    return;
  }
  window.setTimeout(() => pollMedia(jobId, attempts + 1).catch(() => {}), 1000);
}

$("#media-upload-button").addEventListener("click", async () => {
  const file = $("#media-file").files[0];
  if (!file) { alert("请先选择图片或视频文件"); return; }
  const button = $("#media-upload-button");
  const form = new FormData();
  form.append("file", file);
  button.disabled = true;
  try {
    const response = await fetch("/api/v1/media/claims", {method: "POST", body: form});
    const job = await response.json();
    if (!response.ok) throw new Error(job.detail || `HTTP ${response.status}`);
    await pollMedia(job.id);
  } catch (error) { alert(error.message); }
  finally { button.disabled = false; }
});
