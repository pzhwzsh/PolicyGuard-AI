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
  if (!response.ok) {
    const detail = typeof body.detail === "string"
      ? body.detail
      : body.detail?.issues?.join("、") || JSON.stringify(body.detail || body);
    throw new Error(detail || `HTTP ${response.status}`);
  }
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
    $("#service-status").classList.toggle("ready", response.ok);
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

let currentCreativeProject = null;

async function pollCreativeScene(jobId, attempts = 0) {
  const job = await api(`/api/v1/jobs/${jobId}`);
  const status = $("#creative-scene-status");
  if (status) status.textContent = `场景图任务：${job.status} · 尝试 ${job.attempts}/${job.max_attempts}`;
  if (job.status === "completed") {
    const project = await api(`/api/v1/creatives/${currentCreativeProject.project_id}`);
    renderCreativeProject(project);
    return;
  }
  if (job.status === "failed" || attempts >= 180) {
    if (status) status.textContent += ` · ${job.error || "处理超时"}`;
    return;
  }
  window.setTimeout(() => pollCreativeScene(jobId, attempts + 1).catch(() => {}), 1000);
}

function renderCreativeProject(project) {
  currentCreativeProject = project;
  const target = $("#creative-project");
  target.hidden = false;
  const copies = (project.copy_candidates || []).map((item, index) => `
    <label class="creative-copy">
      <input type="checkbox" data-copy-index="${index}" ${item.status === "passed" ? "checked" : "disabled"}>
      <span><p>${escapeHtml(item.text)}</p><small>${escapeHtml(item.status)} · ${escapeHtml(item.generation)}${item.issues?.length ? ` · ${escapeHtml(item.issues.join("、"))}` : ""}</small></span>
    </label>`).join("");
  const assets = (project.assets || []).map((asset) => `
    <article><img src="/api/v1/creatives/${escapeHtml(project.project_id)}/assets/${escapeHtml(asset.filename)}" alt="${escapeHtml(asset.type)}"><small>${escapeHtml(asset.filename)} · ${asset.width}×${asset.height}</small></article>`).join("");
  const evidenceCount = project.payload?.policy_evidence?.length || 0;
  const hasSceneCandidate = (project.assets || []).some(
    (asset) => asset.type === "scene_candidate"
  );
  target.innerHTML = `
    <div class="cleaning-hero"><div><p>创意项目 ${escapeHtml(project.project_id)}</p><h3>${escapeHtml(project.payload.product_name)}</h3></div><span class="quality-badge ${project.status === "approved" ? "ready" : "warning"}">${escapeHtml(project.status)}</span></div>
    <p class="cleaning-note">平台 ${escapeHtml(project.payload.platform)} · ${project.platform_spec.width}×${project.platform_spec.height} · 法规候选证据 ${evidenceCount} 条。所有文案和图片仍需人工确认。</p>
    <div class="creative-copy-list">${copies}</div>
    ${project.assets?.length ? `<div class="creative-assets">${assets}</div>` : ""}
    ${project.status === "awaiting_source_image" ? `<div class="batch-upload-row"><label>上传真实商品原图<input id="creative-source-image" type="file" accept=".png,.jpg,.jpeg"></label><button id="upload-creative-source" type="button">生成主图和 SKU 图</button></div>` : ""}
    ${project.status === "review_required" ? `<div class="cleaning-actions"><span>请核对商品身份、包装文字、SKU 属性和广告语。</span><button id="run-creative-preflight" class="secondary" type="button">发布前总检</button>${hasSceneCandidate ? "" : `<button id="generate-creative-scene" type="button">生成场景图（可选）</button>`}<button id="approve-creative-project" type="button">批准素材</button></div><p id="creative-scene-status" class="cleaning-note">${hasSceneCandidate ? "场景图待人工核对商品一致性，不会自动发布。" : "场景图需要配置兼容的图片编辑模型；未配置时会明确提示。"}</p>` : ""}
    ${project.status === "approved" ? `<div class="cleaning-actions"><span>已通过人工审批，可导出带审计清单的素材包。</span><a class="button-link" href="/api/v1/creatives/${escapeHtml(project.project_id)}/package">下载素材包</a></div>` : ""}`;
}

$("#create-creative-button").addEventListener("click", async () => {
  const button = $("#create-creative-button");
  button.disabled = true;
  try {
    const project = await api("/api/v1/creatives", {
      method: "POST",
      body: JSON.stringify({
        external_id: $("#creative-external-id").value.trim(),
        product_name: $("#creative-product-name").value.trim(),
        category: $("#creative-category").value.trim(),
        brand: $("#creative-brand").value.trim(),
        platform: $("#creative-platform").value,
        market: $("#creative-market").value,
        verified_facts: [{
          name: $("#creative-fact-name").value.trim(),
          value: $("#creative-fact-value").value.trim(),
          evidence_reference: $("#creative-fact-evidence").value.trim() || null
        }],
        skus: [{
          sku_id: $("#creative-sku-id").value.trim(),
          label: $("#creative-sku-label").value.trim(),
          attributes: {specification: $("#creative-fact-value").value.trim()}
        }],
        copy_count: 3
      })
    });
    renderCreativeProject(project);
  } catch (error) { alert(error.message); }
  finally { button.disabled = false; }
});

$("#creative-project").addEventListener("click", async (event) => {
  const upload = event.target.closest("#upload-creative-source");
  if (upload && currentCreativeProject) {
    const file = $("#creative-source-image").files[0];
    if (!file) { alert("请先选择真实商品原图"); return; }
    const form = new FormData();
    form.append("file", file);
    upload.disabled = true;
    try {
      const url = `/api/v1/creatives/${currentCreativeProject.project_id}/source-image?expected_revision=${currentCreativeProject.revision}&reviewer=content-owner`;
      const response = await fetch(url, {method: "POST", body: form});
      const project = await response.json();
      if (!response.ok) throw new Error(project.detail || `HTTP ${response.status}`);
      renderCreativeProject(project);
    } catch (error) { alert(error.message); }
    finally { upload.disabled = false; }
    return;
  }
  const generateScene = event.target.closest("#generate-creative-scene");
  if (generateScene && currentCreativeProject) {
    generateScene.disabled = true;
    try {
      const job = await api(
        `/api/v1/creatives/${currentCreativeProject.project_id}/scene?expected_revision=${currentCreativeProject.revision}`,
        {method: "POST"}
      );
      await pollCreativeScene(job.id);
    } catch (error) {
      alert(error.message === "image_generation_provider_not_configured"
        ? "尚未配置图片编辑模型，主图和 SKU 图仍可正常使用。"
        : error.message);
      generateScene.disabled = false;
    }
    return;
  }
  const preflight = event.target.closest("#run-creative-preflight");
  if (preflight && currentCreativeProject) {
    preflight.disabled = true;
    try { renderPreflight(await creativePreflight(currentCreativeProject)); }
    catch (error) { showToast(error.message, "error"); }
    finally { preflight.disabled = false; }
    return;
  }
  const approve = event.target.closest("#approve-creative-project");
  if (!approve || !currentCreativeProject) return;
  const approvedIndexes = Array.from(
    $("#creative-project").querySelectorAll("input[data-copy-index]:checked")
  ).map((item) => Number(item.dataset.copyIndex));
  approve.disabled = true;
  try {
    const project = await api(`/api/v1/creatives/${currentCreativeProject.project_id}/review`, {
      method: "POST",
      body: JSON.stringify({
        expected_revision: currentCreativeProject.revision,
        reviewer: "content-owner",
        decision: "approve",
        approved_copy_indexes: approvedIndexes,
        comment: "商品身份、SKU 属性、图片和广告语已人工核对"
      })
    });
    renderCreativeProject(project);
  } catch (error) { alert(error.message); }
  finally { approve.disabled = false; }
});

const experienceErrorLabels = {
  product_revision_conflict: "商品档案已被其他操作更新，请刷新后再保存。",
  product_expected_revision_required: "这个商品已存在，请先从右侧商品列表打开再编辑。",
  product_prompt_injection_detected: "商品资料包含疑似提示注入内容，已阻止保存。",
  tenant_active_job_limit_reached: "进行中的任务已达到容量上限，请等待、取消任务或稍后重试。",
  job_not_cancellable: "任务已经开始或结束，当前不能取消。",
  image_generation_provider_not_configured: "尚未配置场景图片模型，主图与 SKU 图仍可使用。"
};

function showToast(message, type = "success") {
  const normalized = experienceErrorLabels[message] || message;
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.textContent = normalized;
  $("#toast-region").appendChild(toast);
  window.setTimeout(() => toast.remove(), 4200);
}

function productPayload() {
  return {
    external_id: $("#product-external-id").value.trim(),
    name: $("#product-name").value.trim(),
    category: $("#product-category").value.trim(),
    brand: $("#product-brand").value.trim(),
    markets: [$("#product-market").value],
    platforms: [$("#product-platform").value],
    verified_facts: [{
      name: $("#product-fact-name").value.trim(),
      value: $("#product-fact-value").value.trim(),
      evidence_reference: $("#product-fact-evidence").value.trim() || null
    }],
    skus: [{
      sku_id: $("#product-sku-id").value.trim(),
      label: $("#product-sku-label").value.trim(),
      attributes: {specification: $("#product-fact-value").value.trim()}
    }],
    notes: $("#product-notes").value.trim(),
    expected_revision: $("#product-revision").value
      ? Number($("#product-revision").value) : null
  };
}

function applyProduct(product) {
  const fact = product.verified_facts?.[0] || {};
  const sku = product.skus?.[0] || {};
  $("#product-external-id").value = product.external_id;
  $("#product-name").value = product.name;
  $("#product-category").value = product.category;
  $("#product-brand").value = product.brand || "";
  $("#product-market").value = product.markets?.[0] || "CN";
  $("#product-platform").value = product.platforms?.[0] || "generic";
  $("#product-fact-name").value = fact.name || "";
  $("#product-fact-value").value = fact.value || "";
  $("#product-fact-evidence").value = fact.evidence_reference || "";
  $("#product-sku-id").value = sku.sku_id || "";
  $("#product-sku-label").value = sku.label || "";
  $("#product-notes").value = product.notes || "";
  $("#product-revision").value = product.revision || "";
  $("#product-save-state").textContent = `版本 ${product.revision} · 已载入`;
  document.querySelector('[name="external_id"]').value = product.external_id;
  document.querySelector('[name="category"]').value = product.category;
  document.querySelector('[name="title"]').value = product.name;
  $("#creative-external-id").value = product.external_id;
  $("#creative-product-name").value = product.name;
  $("#creative-category").value = product.category;
  $("#creative-brand").value = product.brand || "";
  $("#creative-market").value = product.markets?.[0] || "CN";
  $("#creative-platform").value = product.platforms?.[0] || "generic";
  $("#creative-fact-name").value = fact.name || "";
  $("#creative-fact-value").value = fact.value || "";
  $("#creative-fact-evidence").value = fact.evidence_reference || "";
  $("#creative-sku-id").value = sku.sku_id || "";
  $("#creative-sku-label").value = sku.label || "";
  showToast("商品事实已同步到内容审查和创意工作室");
}

async function loadProducts() {
  const products = await api("/api/v1/products");
  $("#product-list").innerHTML = products.length ? products.map((product) => `
    <article class="product-item" data-product-id="${escapeHtml(product.external_id)}">
      <b>${escapeHtml((product.name || "P").slice(0, 1))}</b>
      <div><strong>${escapeHtml(product.name)}</strong><small>${escapeHtml(product.external_id)} · ${escapeHtml(product.brand || "未填写品牌")} · v${product.revision}</small></div>
      <span>›</span>
    </article>`).join("") : `<div class="empty-state"><b>◇</b><p>还没有商品档案<br>从左侧保存第一件商品</p></div>`;
  $("#summary-products").textContent = products.length;
}

$("#product-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#save-product-button");
  button.disabled = true;
  try {
    const payload = productPayload();
    const safety = await api("/api/v1/safety/text-scan", {
      method: "POST", body: JSON.stringify({content: JSON.stringify(payload)})
    });
    if (Object.keys(safety.pii || {}).length) {
      throw new Error("发现手机号、邮箱或证件号，请脱敏后再保存。");
    }
    if (safety.prompt_injection?.length) throw new Error("发现疑似提示注入内容，已阻止保存。");
    const product = await api(`/api/v1/products/${encodeURIComponent(payload.external_id)}`, {
      method: "PUT", body: JSON.stringify(payload)
    });
    applyProduct(product);
    $("#product-save-state").textContent = `版本 ${product.revision} · 刚刚保存`;
    await loadProducts();
  } catch (error) { showToast(error.message, "error"); }
  finally { button.disabled = false; }
});

$("#product-list").addEventListener("click", async (event) => {
  const item = event.target.closest("[data-product-id]");
  if (!item) return;
  try { applyProduct(await api(`/api/v1/products/${encodeURIComponent(item.dataset.productId)}`)); }
  catch (error) { showToast(error.message, "error"); }
});

$("#new-product-button").addEventListener("click", () => {
  $("#product-form").reset();
  $("#product-revision").value = "";
  $("#product-save-state").textContent = "新商品 · 尚未保存";
  $("#product-external-id").focus();
});
$("#refresh-products").addEventListener("click", () => loadProducts().catch((error) => showToast(error.message, "error")));

async function loadWorkspaceSummary() {
  const summary = await api("/api/v1/workspace/summary");
  $("#summary-products").textContent = summary.products;
  $("#summary-active-jobs").textContent = `${summary.active_jobs}/${summary.capacity.active_jobs_limit}`;
  $("#summary-failed-jobs").textContent = summary.failed_jobs;
}

const jobTypeLabels = {
  parse_document: "文档解析", batch_compliance_review: "批量合规审查",
  media_claim_extraction: "媒体声明提取", creative_scene_generation: "商品场景图",
  model_evaluation: "模型评测", reindex_embeddings: "知识库重建",
  source_monitor: "法规源监控"
};

async function loadTasks() {
  const jobs = await api("/api/v1/jobs?limit=30");
  $("#task-list").innerHTML = jobs.length ? jobs.map((job) => `
    <article class="task-row">
      <b>↻</b><div><strong>${escapeHtml(jobTypeLabels[job.job_type] || job.job_type)}</strong><small>${escapeHtml(job.id)}${job.error ? ` · ${escapeHtml(job.error)}` : ""}</small></div>
      <span class="task-status ${escapeHtml(job.status)}">${escapeHtml(job.status)}</span>
      <span>尝试 ${job.attempts}/${job.max_attempts}</span>
      ${["queued", "retry"].includes(job.status) ? `<button class="secondary" data-cancel-job="${escapeHtml(job.id)}">取消</button>` : job.status === "failed" ? `<button data-retry-job="${escapeHtml(job.id)}">重试</button>` : ""}
    </article>`).join("") : `<div class="empty-state"><b>✓</b><p>暂无后台任务</p></div>`;
  await loadWorkspaceSummary();
}

$("#task-list").addEventListener("click", async (event) => {
  const cancel = event.target.closest("[data-cancel-job]");
  const retry = event.target.closest("[data-retry-job]");
  if (!cancel && !retry) return;
  const button = cancel || retry;
  button.disabled = true;
  try {
    await api(`/api/v1/jobs/${cancel ? cancel.dataset.cancelJob : retry.dataset.retryJob}/${cancel ? "cancel" : "retry"}`, {method: "POST"});
    showToast(cancel ? "任务已取消" : "任务已重新进入队列");
    await loadTasks();
  } catch (error) { showToast(error.message, "error"); button.disabled = false; }
});
$("#refresh-tasks").addEventListener("click", () => loadTasks().catch((error) => showToast(error.message, "error")));

const readinessLabels = {
  database: "数据库", storage: "存储空间", llm: "语言模型", image_generation: "图片模型",
  ocr: "OCR 服务", authentication: "身份验证", worker_queue: "任务队列"
};

async function loadReadiness() {
  const readiness = await api("/api/v1/readiness");
  $("#summary-readiness").textContent = readiness.overall === "ready" ? "正常" : "需关注";
  $("#readiness-grid").innerHTML = readiness.checks.map((item) => `
    <article class="readiness-card"><header><strong>${escapeHtml(readinessLabels[item.component] || item.component)}</strong><i class="signal ${escapeHtml(item.status)}"></i></header><p>${escapeHtml(item.detail)}</p>${item.action ? `<p><b>建议：</b>${escapeHtml(item.action)}</p>` : ""}</article>`).join("");
}
$("#refresh-readiness").addEventListener("click", () => loadReadiness().catch((error) => showToast(error.message, "error")));

async function creativePreflight(project) {
  const selected = Array.from(document.querySelectorAll("input[data-copy-index]:checked"));
  const index = selected.length ? Number(selected[0].dataset.copyIndex) : 0;
  return api("/api/v1/publish-preflight", {
    method: "POST",
    body: JSON.stringify({
      ...project.payload,
      platform: project.payload.platform,
      copy: project.copy_candidates[index]?.text || "",
      assets: project.assets || []
    })
  });
}

function renderPreflight(report) {
  const panel = $("#preflight-panel");
  panel.hidden = false;
  const decision = {ready: "可以进入人工审批", human_review: "存在待人工确认项", blocked: "发现阻断项"}[report.decision];
  panel.innerHTML = `<div class="form-title"><div><span class="step-tag">Publish preflight</span><h3>${escapeHtml(decision)}</h3></div><span>${report.summary.red} 红 · ${report.summary.yellow} 黄 · ${report.summary.green} 绿</span></div><div class="preflight-grid">${report.checks.map((item) => `<article class="check-card"><header><strong>${escapeHtml(item.title)}</strong><i class="signal ${escapeHtml(item.status)}"></i></header><p>${escapeHtml(item.detail)}</p></article>`).join("")}</div><p class="cleaning-note">系统不会自动发布；通过总检后仍需人工确认商品身份、法规适用范围和平台规则。</p>`;
  panel.scrollIntoView({behavior: "smooth", block: "center"});
}

const draftControls = Array.from(document.querySelectorAll("input, textarea, select"))
  .filter((element) => element.id || element.name);
let draftTimer = null;
function saveDraft() {
  const data = {};
  draftControls.forEach((element) => {
    const key = element.id || element.name;
    if (element.type === "checkbox") data[key + ":" + element.value] = element.checked;
    else if (element.type !== "file" && element.type !== "hidden") data[key] = element.value;
  });
  localStorage.setItem("policyguard-workspace-draft-v2", JSON.stringify(data));
  $("#draft-state").textContent = "草稿刚刚自动保存";
}
function restoreDraft() {
  try {
    const data = JSON.parse(localStorage.getItem("policyguard-workspace-draft-v2") || "{}");
    draftControls.forEach((element) => {
      const key = element.id || element.name;
      if (element.type === "checkbox" && Object.hasOwn(data, key + ":" + element.value)) element.checked = data[key + ":" + element.value];
      else if (element.type !== "file" && element.type !== "hidden" && Object.hasOwn(data, key)) element.value = data[key];
    });
  } catch { localStorage.removeItem("policyguard-workspace-draft-v2"); }
}
draftControls.forEach((element) => element.addEventListener("input", () => {
  clearTimeout(draftTimer);
  $("#draft-state").textContent = "正在保存草稿…";
  draftTimer = window.setTimeout(saveDraft, 500);
}));

const reviewText = document.querySelector('[name="description"]');
let safetyTimer = null;
reviewText.addEventListener("input", () => {
  clearTimeout(safetyTimer);
  safetyTimer = window.setTimeout(async () => {
    if (!reviewText.value.trim()) return;
    try {
      const result = await api("/api/v1/safety/text-scan", {method: "POST", body: JSON.stringify({content: reviewText.value})});
      const box = $("#text-safety-state");
      box.classList.toggle("safe", result.safe_for_model);
      box.innerHTML = result.safe_for_model
        ? "<b>安全预检通过</b><p>未发现明显隐私信息或提示注入。</p>"
        : `<b>需要处理</b><p>${result.prompt_injection.length ? "疑似提示注入；" : ""}${Object.keys(result.pii).length ? "请先移除个人敏感信息。" : ""}</p>`;
    } catch { /* The submit endpoint remains the final enforcement point. */ }
  }, 450);
});

document.querySelectorAll(".product-nav a").forEach((link) => link.addEventListener("click", () => {
  document.querySelectorAll(".product-nav a").forEach((item) => item.classList.remove("active"));
  link.classList.add("active");
}));

restoreDraft();
Promise.all([loadProducts(), loadTasks(), loadReadiness()]).catch((error) => showToast(error.message, "error"));
window.setInterval(() => loadTasks().catch(() => {}), 15000);
