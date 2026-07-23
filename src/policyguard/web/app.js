const $ = (selector) => document.querySelector(selector);
let currentRunId = null;
let latestRuntime = {};

const formatRuntimeMs = (value) => Number.isFinite(Number(value))
  ? `${Number(value).toFixed(1)} ms` : "暂无数据";

function renderRuntimeWindow(windowKey) {
  const item = latestRuntime?.windows?.[windowKey] || {};
  $("#live-requests").textContent = item.request_count ?? 0;
  $("#live-rpm").textContent = `${Number(item.requests_per_minute || 0).toFixed(2)} req/min`;
  $("#live-p50").textContent = formatRuntimeMs(item.p50_latency_ms);
  $("#live-p95").textContent = formatRuntimeMs(item.p95_latency_ms);
  $("#live-p99").textContent = formatRuntimeMs(item.p99_latency_ms);
  $("#live-success").textContent = Number.isFinite(Number(item.success_rate))
    ? `${(Number(item.success_rate) * 100).toFixed(1)}%` : "暂无数据";
  $("#live-tokens").textContent = Number(item.total_tokens || 0).toLocaleString();
  const cacheTotal = Number(item.cache_hits || 0) + Number(item.cache_misses || 0);
  $("#live-cache").textContent = cacheTotal
    ? `缓存命中 ${((Number(item.cache_hits || 0) / cacheTotal) * 100).toFixed(1)}%`
    : "暂无改写缓存请求";
}

$("#runtime-window").addEventListener("change", (event) => {
  renderRuntimeWindow(event.target.value);
});

function selectAdminPanel(panel) {
  document.querySelectorAll("[data-admin-panel]").forEach((section) => {
    section.hidden = section.dataset.adminPanel !== panel
      || (section.id === "result-section" && !currentRunId);
  });
  document.querySelectorAll(".admin-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.panel === panel);
  });
}

document.querySelectorAll(".admin-tab").forEach((button) => {
  button.addEventListener("click", () => selectAdminPanel(button.dataset.panel));
});

const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
}[char]));

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
  return body;
}

async function loadStatus() {
  const [stats, retrievers] = await Promise.all([api("/api/v1/knowledge/stats"), api("/api/v1/knowledge/retrievers")]);
  $("#document-count").textContent = stats.documents;
  $("#chunk-count").textContent = stats.chunks;
  $("#dense-status").textContent = retrievers.dense_available ? "可用" : "未配置";
  $("#model-name").textContent = retrievers.dense_model || "BM25";
  $("#system-status").textContent = "系统正常 · 本地运行";
}

function eventDetail(event) {
  if (event.step === "query_rewrite") {
    return `改写市场 ${event.detail.rewritten_market_count} · 缓存 ${event.detail.cache_hits}/${event.detail.cache_hits + event.detail.cache_misses} · ${event.detail.total_tokens} token`;
  }
  if (event.step === "query_rewrite_drift") return `已拒绝新增约束：${event.detail.added_constraints.join("、")}`;
  if (event.step === "query_rewrite_fallback") return "模型不可用，已使用原查询";
  return event.detail.market ? `市场 ${event.detail.market}` : "";
}

function renderRun(run) {
  currentRunId = run.id;
  selectAdminPanel("content");
  $("#result-section").hidden = false;
  $("#workflow-status").textContent = run.status;
  $("#notice").textContent = run.result_payload.note || "证据需要人工复核。";
  $("#market-results").innerHTML = (run.result_payload.markets || []).map((market) => `
    <article class="market">
      <div class="market-header"><strong>${escapeHtml(market.market)}</strong><span>${market.candidate_evidence.length} 条候选证据</span></div>
      ${(market.candidate_evidence || []).map((item) => `
        <div class="evidence">
          <a href="${escapeHtml(item.source_url)}" target="_blank" rel="noreferrer">${escapeHtml(item.section_id)} · ${escapeHtml(item.heading)}</a>
          <p>${escapeHtml(item.text)}</p>
        </div>`).join("") || "<p>当前知识库未找到证据。</p>"}
    </article>`).join("");
  $("#event-list").innerHTML = run.events.map((event) => `<li><strong>${escapeHtml(event.step)}</strong>${escapeHtml(event.status)}<small>${escapeHtml(eventDetail(event))}</small></li>`).join("");
  $("#accept-button").disabled = !["review_required", "needs_more_evidence"].includes(run.status);
  $("#reject-button").disabled = $("#accept-button").disabled;
  $("#download-json").href = `/api/v1/workflows/compliance/${run.id}/report?format=json`;
  $("#download-markdown").href = `/api/v1/workflows/compliance/${run.id}/report?format=markdown`;
  $("#download-pdf").href = `/api/v1/workflows/compliance/${run.id}/report?format=pdf`;
  renderRemediation(run);
  $("#result-section").scrollIntoView({ behavior: "smooth" });
}

function renderRemediation(run) {
  const plan = run.result_payload.remediation_plan;
  const draft = run.result_payload.draft;
  const agentMeta = run.result_payload.agent_run?.context_metadata;
  $("#create-plan-button").disabled = run.status !== "review_accepted";
  $("#create-draft-button").disabled = run.status !== "remediation_planned";
  const operations = plan?.operations || [];
  $("#remediation-result").innerHTML = operations.map((item) => `
    <article class="remediation-item">
      <div class="market-header"><strong>${escapeHtml(item.field)}</strong><span>${escapeHtml(item.meaning_preservation?.strategy || "review")}</span></div>
      <div class="diff-grid"><div><small>修改前</small><p>${escapeHtml(item.before)}</p></div><div><small>修改后</small><p>${escapeHtml(item.after)}</p></div></div>
      <pre>${escapeHtml(item.diff || "")}</pre>
      ${(item.legal_basis || []).map((basis) => `<a href="${escapeHtml(basis.source_url)}" target="_blank" rel="noreferrer">${escapeHtml(basis.jurisdiction)} · ${escapeHtml(basis.section_id)} · ${escapeHtml(basis.heading)}</a>`).join("")}
    </article>`).join("") + (draft ? `<p class="notice">复检：${escapeHtml(draft.post_check?.status)} · 剩余风险操作 ${escapeHtml(draft.post_check?.remaining_risky_operation_count)} · 未执行外部发布</p>` : "") + (agentMeta ? `<p>召回记忆：${escapeHtml((agentMeta.recalled_memory_ids || []).join("、") || "无")}</p>` : "");
}

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
      method: "POST", body: JSON.stringify({execution_id: crypto.randomUUID(), approved_by: $("#reviewer-alias").value})
    }));
  } catch (error) { alert(error.message); }
});

async function loadReviewQueue() {
  const [queue, memories] = await Promise.all([api("/api/v1/review-queue"), api("/api/v1/agent-memories?limit=30")]);
  $("#review-queue-summary").innerHTML = [
    ["待核对样本", queue.evaluation.pending], ["待审法规", queue.legal_sources.pending],
    ["失效记忆", queue.agent_memories.invalidated], ["自动批准", queue.automatic_approval ? "开启" : "关闭"]
  ].map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
  $("#evaluation-review-list").innerHTML = queue.evaluation.items.slice(0, 12).map((item) => `
    <article class="review-row"><div><strong>${escapeHtml(item.sample_id)} · ${escapeHtml(item.jurisdiction)}</strong><p>${escapeHtml(item.query)}</p><small>${escapeHtml(item.proposed_section_id || "无答案")}</small></div>${item.review_status === "pending_human_review" ? `<button class="accept-evaluation" data-sample-id="${escapeHtml(item.sample_id)}">确认</button>` : `<span>${escapeHtml(item.decision)}</span>`}</article>`).join("");
  $("#memory-review-list").innerHTML = memories.map((item) => `<article class="review-row"><div><strong>${escapeHtml(item.category)} · ${escapeHtml(item.jurisdictions.join("/"))}</strong><p>${escapeHtml(item.summary)}</p><small>${escapeHtml(item.review_status)} · ${escapeHtml(item.run_id)}</small></div></article>`).join("") || "<p>暂无已确认经验。</p>";
}

$("#evaluation-review-list").addEventListener("click", async (event) => {
  const button = event.target.closest(".accept-evaluation");
  if (!button) return;
  try {
    await api(`/api/v1/evaluations/cross-language/reviews/${button.dataset.sampleId}`, {
      method: "POST", body: JSON.stringify({reviewer: $("#reviewer-alias").value, decision: "accept", comment: "Reviewed in local console"})
    });
    await loadReviewQueue();
  } catch (error) { alert(error.message); }
});

$("#refresh-review-queue").addEventListener("click", () => loadReviewQueue().catch((error) => alert(error.message)));
loadReviewQueue().catch(() => {});

async function loadDocumentWorkspace(documentId) {
  const workspace = await api(`/api/v1/documents/${documentId}`);
  $("#correction-document-id").value = documentId;
  $("#correction-revision").value = workspace.manifest.revision || 0;
  $("#warning-list").innerHTML = (workspace.document.warnings || []).map((warning) => `
    <label><input type="checkbox" name="resolved_warning" value="${escapeHtml(warning)}"> 标记已人工解决：${escapeHtml(warning)}</label>
  `).join("") || "<p>没有待处理解析警告。</p>";
  $("#block-editor").innerHTML = (workspace.document.blocks || []).map((block) => `
    <label class="block-item">
      <span>第 ${block.page} 页 · ${escapeHtml(block.block_type)} · ${escapeHtml(block.block_id)}</span>
      <textarea data-block-id="${escapeHtml(block.block_id)}" rows="4">${escapeHtml(block.text)}</textarea>
    </label>
  `).join("");
  $("#pdf-preview").src = `/api/v1/documents/${documentId}/original#page=1`;
  $("#correction-form").hidden = false;
}

$("#review-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#run-button");
  button.disabled = true;
  button.textContent = "审查中";
  const data = new FormData(event.currentTarget);
  try {
    renderRun(await api("/api/v1/workflows/compliance", {
      method: "POST",
      body: JSON.stringify({
        product: { external_id: data.get("external_id"), title: data.get("title"), description: data.get("description"), category: data.get("category"), attributes: {} },
        markets: data.getAll("markets"), category: data.get("category"), channel: "all"
      })
    }));
  } catch (error) { alert(error.message); }
  finally { button.disabled = false; button.textContent = "开始审查"; }
});

async function review(decision) {
  if (!currentRunId) return;
  try {
    renderRun(await api(`/api/v1/workflows/compliance/${currentRunId}/review`, {
      method: "POST",
      body: JSON.stringify({ decision_id: crypto.randomUUID(), decision, reviewer: "local-reviewer", comment: "Reviewed in local console" })
    }));
  } catch (error) { alert(error.message); }
}

$("#accept-button").addEventListener("click", () => review("accept"));
$("#reject-button").addEventListener("click", () => review("reject"));

$("#parse-pdf-button").addEventListener("click", async () => {
  const file = $("#pdf-file").files[0];
  if (!file) { alert("请先选择 PDF 文件"); return; }
  const button = $("#parse-pdf-button");
  const form = new FormData();
  form.append("file", file);
  button.disabled = true;
  button.textContent = "解析中";
  try {
    const response = await fetch("/api/v1/documents/parse", { method: "POST", body: form });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
    $("#pdf-result").hidden = false;
    $("#pdf-result").textContent = [
      `文档：${body.filename}`,
      `状态：${body.status} / ${body.activation_status}`,
      `路由：${body.parser_route} · ${body.parser} · 平均置信度 ${body.mean_block_confidence}`,
      `页数：${body.page_count}，布局块：${body.block_count}，Chunk：${body.chunk_count}`,
      `警告：${body.warnings.join("；") || "无"}`, "", body.markdown_preview
    ].join("\n");
    $("#approval-document-id").value = body.document_id;
    await loadDocumentWorkspace(body.document_id);
    $("#approval-form").elements.title.value = file.name.replace(/\.pdf$/i, "");
    $("#approval-form").hidden = body.status !== "parsed" || body.chunk_count === 0;
  } catch (error) { alert(error.message); }
  finally { button.disabled = false; button.textContent = "解析 PDF"; }
});

$("#correction-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#save-corrections-button");
  const documentId = $("#correction-document-id").value;
  const corrections = Array.from($("#block-editor").querySelectorAll("textarea")).map((item) => ({
    block_id: item.dataset.blockId, text: item.value, markdown: item.value
  }));
  const resolvedWarnings = Array.from($("#warning-list").querySelectorAll("input:checked")).map((item) => item.value);
  button.disabled = true;
  try {
    const workspace = await api(`/api/v1/documents/${documentId}`, {
      method: "PATCH",
      body: JSON.stringify({
        expected_revision: Number($("#correction-revision").value),
        reviewer: "local-reviewer", corrections, resolved_warnings: resolvedWarnings
      })
    });
    $("#correction-revision").value = workspace.manifest.revision;
    $("#pdf-result").textContent += `\n\n校正已保存：revision ${workspace.manifest.revision}，人工修订率 ${workspace.correction_rate}`;
    $("#approval-form").hidden = workspace.document.status !== "parsed";
    await loadDocumentWorkspace(documentId);
  } catch (error) { alert(error.message); }
  finally { button.disabled = false; }
});

async function loadSourceUpdates() {
  const updates = await api("/api/v1/source-updates");
  $("#source-update-list").innerHTML = updates.map((item) => `
    <article class="source-update">
      <div class="market-header"><strong>${escapeHtml(item.title)}</strong><span>${item.section_count} 个候选段落 · ${escapeHtml(item.status)}</span></div>
      <p>${escapeHtml(item.source_id)}${item.diff_available ? ` · <a href="/api/v1/source-updates/${escapeHtml(item.source_id)}/${escapeHtml(item.content_hash)}/diff" target="_blank">查看版本 Diff</a>` : ""} · 结构 ${escapeHtml(item.structural_review_status)} · 法律审核 ${escapeHtml(item.legal_review_status)}</p>
      ${item.preview.map((section) => `<details><summary>${escapeHtml(section.heading)}</summary><p>${escapeHtml(section.text)}</p></details>`).join("")}
      <div class="impact-result" data-impact-result></div>
      ${item.status === "staged" && item.structural_review_status !== "passed"
        && !(item.blocking_reasons || []).includes("not_a_legal_document") ? `
        <details class="source-correction">
          <summary>Review source structure</summary>
          <p>Revision ${escapeHtml(item.revision)} · blockers ${escapeHtml((item.blocking_reasons || []).join(", ") || "unknown")}</p>
          <label>Published date <input data-published-at type="date"></label>
          <label>Effective date <input data-effective-from type="date"></label>
          <label>Section heading map (JSON)
            <textarea data-heading-overrides rows="4" placeholder='{"section-id":"Article 1"}'></textarea>
          </label>
          <button class="correct-source secondary" data-source-id="${escapeHtml(item.source_id)}" data-content-hash="${escapeHtml(item.content_hash)}" data-revision="${escapeHtml(item.revision)}" type="button">Save correction and recheck</button>
        </details>
      ` : ""}
      <button class="analyze-impact secondary" data-source-id="${escapeHtml(item.source_id)}" data-content-hash="${escapeHtml(item.content_hash)}" type="button">分析历史影响</button>
      ${item.status === "staged" && item.eligible_for_activation && item.structural_review_status === "passed" ? `
        <label class="legal-confirm"><input type="checkbox"> 我已核对官方原文、适用范围和生效信息</label>
        <button class="approve-source" data-source-id="${escapeHtml(item.source_id)}" data-content-hash="${escapeHtml(item.content_hash)}" type="button">确认法律审核并激活</button>
      ` : "<p>该来源仅作目录或结构质检未通过，不能直接激活。</p>"}
    </article>
  `).join("") || "<p>当前没有 staged 来源更新。</p>";
}

async function loadOperations() {
  const [dashboard, jobs] = await Promise.all([
    api("/api/v1/operations/dashboard"), api("/api/v1/jobs?limit=20")
  ]);
  $("#operations-summary").innerHTML = [
    ["活动文档", dashboard.knowledge.documents],
    ["活动 Chunk", dashboard.knowledge.chunks],
    ["待法律审核", dashboard.sources.pending_legal_review],
    ["失败任务", dashboard.jobs.failed || 0]
  ].map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
  const rag = dashboard.evaluations.rag || {};
  const agent = dashboard.evaluations.agent || {};
  const pdf = dashboard.evaluations.pdf || {};
  $("#evaluation-cards").innerHTML = `
    <article class="metric-card"><strong>RAG</strong><p>Dense 困难集 MRR ${escapeHtml(rag.dense?.positive_metrics?.["positive-hard"]?.mean_reciprocal_rank ?? "-")}</p></article>
    <article class="metric-card"><strong>Agent / Pipeline</strong><p>${escapeHtml(agent.agent?.metrics?.success_rate ?? "-")} / ${escapeHtml(agent.pipeline?.metrics?.success_rate ?? "-")}</p></article>
    <article class="metric-card"><strong>PDF</strong><p>文本准确率 ${escapeHtml(pdf.aggregate?.text_accuracy ?? "-")} · ${escapeHtml(pdf.sample_count ?? 0)} 样本</p></article>`;
  $("#job-list").innerHTML = jobs.map((job) => `
    <article class="job-row"><span>${escapeHtml(job.job_type)}</span><strong>${escapeHtml(job.status)}</strong><small>尝试 ${job.attempts}/${job.max_attempts}</small>${job.status === "failed" ? `<button class="retry-job" data-job-id="${escapeHtml(job.id)}">重试</button>` : ""}</article>
  `).join("") || "<p>暂无后台任务。</p>";
  $("#report-history").innerHTML = dashboard.report_history.map((item) => `
    <article><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.status)}</span><a href="${escapeHtml(item.report_json)}">JSON</a><a href="${escapeHtml(item.report_pdf)}">PDF</a></article>
  `).join("") || "<p>暂无审查报告。</p>";

  const workflow = dashboard.performance?.workflow || {};
  const concurrent = dashboard.performance?.concurrency || {};
  const pdfScale = dashboard.performance?.pdf || {};
  latestRuntime = dashboard.performance?.runtime || {};
  renderRuntimeWindow($("#runtime-window").value);
  const formatMs = (value) => Number.isFinite(Number(value)) ? `${Number(value).toFixed(1)} ms` : "-";
  const formatRate = (value) => Number.isFinite(Number(value)) ? Number(value).toFixed(2) : "-";
  $("#perf-p50").textContent = formatMs(concurrent.p50_latency_ms);
  $("#perf-p95").textContent = formatMs(concurrent.p95_latency_ms);
  $("#perf-p99").textContent = formatMs(concurrent.p99_latency_ms);
  $("#perf-throughput").textContent = formatRate(concurrent.throughput_cases_per_second);
  $("#perf-success").textContent = Number.isFinite(Number(concurrent.success_rate))
    ? `${(Number(concurrent.success_rate) * 100).toFixed(0)}%` : "-";
  $("#performance-scope").textContent = `${concurrent.workers || "-"} worker · SQLite · ${concurrent.case_count || "-"} cases`;
  $("#serial-p95").textContent = formatMs(workflow.p95_latency_ms);
  $("#pdf-p95").textContent = formatMs(pdfScale.p95_document_latency_ms);
  $("#overview-knowledge").textContent = `${dashboard.knowledge.documents} / ${dashboard.knowledge.chunks}`;
  $("#overview-failed-jobs").textContent = dashboard.jobs.failed || 0;
  [["p50", concurrent.p50_latency_ms], ["p95", concurrent.p95_latency_ms], ["p99", concurrent.p99_latency_ms]].forEach(([key, value]) => {
    $("#chart-" + key).textContent = formatMs(value);
    const ceiling = Math.max(Number(concurrent.p99_latency_ms) || 1, 1);
    $("#bar-" + key).style.width = `${Math.max((Number(value) || 0) / ceiling * 100, 2)}%`;
  });
}

$("#job-list").addEventListener("click", async (event) => {
  const button = event.target.closest(".retry-job");
  if (!button) return;
  try {
    await api(`/api/v1/jobs/${button.dataset.jobId}/retry`, {method: "POST", body: "{}"});
    await loadOperations();
  } catch (error) { alert(error.message); }
});

$("#refresh-operations-button").addEventListener("click", () => loadOperations().catch((error) => alert(error.message)));

$("#source-update-list").addEventListener("click", async (event) => {
  const impactButton = event.target.closest(".analyze-impact");
  if (impactButton) {
    impactButton.disabled = true;
    try {
      const impact = await api(`/api/v1/source-updates/${impactButton.dataset.sourceId}/${impactButton.dataset.contentHash}/impact`, {method: "POST", body: "{}"});
      impactButton.parentElement.querySelector("[data-impact-result]").innerHTML = `
        <strong>影响分析</strong>
        <span>变更条款 ${escapeHtml(impact.changed_section_count)}</span>
        <span>历史报告 ${escapeHtml(impact.affected_workflow_count)}</span>
        <span>Agent 记忆 ${escapeHtml(impact.affected_memory_count)}</span>
        <span>复审任务 ${escapeHtml(impact.re_review_job_ids.length)}</span>`;
    } catch (error) { alert(error.message); }
    finally { impactButton.disabled = false; }
    return;
  }
  const correctionButton = event.target.closest(".correct-source");
  if (correctionButton) {
    const panel = correctionButton.closest(".source-correction");
    let headingOverrides = {};
    try {
      const raw = panel.querySelector("[data-heading-overrides]").value.trim();
      headingOverrides = raw ? JSON.parse(raw) : {};
      if (!headingOverrides || Array.isArray(headingOverrides) || typeof headingOverrides !== "object") {
        throw new Error("heading_overrides_must_be_an_object");
      }
    } catch (error) {
      alert(`Invalid section heading JSON: ${error.message}`);
      return;
    }
    const publishedAt = panel.querySelector("[data-published-at]").value || null;
    const effectiveFrom = panel.querySelector("[data-effective-from]").value || null;
    if (!publishedAt && !effectiveFrom && Object.keys(headingOverrides).length === 0) {
      alert("Enter at least one verified correction");
      return;
    }
    correctionButton.disabled = true;
    try {
      await api(`/api/v1/source-updates/${correctionButton.dataset.sourceId}/${correctionButton.dataset.contentHash}/structure`, {
        method: "PATCH",
        body: JSON.stringify({
          reviewer: "local-reviewer",
          expected_revision: Number(correctionButton.dataset.revision),
          published_at: publishedAt,
          effective_from: effectiveFrom,
          heading_overrides: headingOverrides
        })
      });
      await loadSourceUpdates();
    } catch (error) { alert(error.message); }
    finally { correctionButton.disabled = false; }
    return;
  }
  const button = event.target.closest(".approve-source");
  if (!button) return;
  const confirmed = button.parentElement.querySelector(".legal-confirm input")?.checked;
  if (!confirmed) { alert("请先确认已完成法律原文核对"); return; }
  button.disabled = true;
  try {
    await api(`/api/v1/source-updates/${button.dataset.sourceId}/${button.dataset.contentHash}/approve`, {
      method: "POST", body: JSON.stringify({
        reviewer: "local-reviewer", legal_review_confirmed: true
      })
    });
    await Promise.all([loadSourceUpdates(), loadStatus()]);
  } catch (error) { alert(error.message); }
  finally { button.disabled = false; }
});

$("#refresh-sources-button").addEventListener("click", () => loadSourceUpdates().catch((error) => alert(error.message)));

$("#check-sources-button").addEventListener("click", async () => {
  const button = $("#check-sources-button");
  button.disabled = true;
  try {
    const job = await api("/api/v1/source-updates/check", {method: "POST", body: "{}"});
    $("#source-check-status").textContent = `任务 ${job.status} · ${job.id.slice(0, 8)}`;
  } catch (error) { alert(error.message); }
  finally { button.disabled = false; }
});

$("#approval-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#approve-document-button");
  const data = new FormData(event.currentTarget);
  button.disabled = true;
  button.textContent = "激活中";
  try {
    const body = Object.fromEntries(data.entries());
    const result = await api(`/api/v1/documents/${$("#approval-document-id").value}/approve`, { method: "POST", body: JSON.stringify(body) });
    $("#pdf-result").textContent += `\n\n激活完成：${result.activated_chunks} 个 Chunk，审核人 ${result.reviewer}`;
    event.currentTarget.hidden = true;
    await loadStatus();
  } catch (error) { alert(error.message); }
  finally { button.disabled = false; button.textContent = "审批并激活"; }
});

Promise.all([loadStatus(), loadSourceUpdates(), loadOperations()]).catch(() => { $("#system-status").textContent = "系统状态读取失败"; });
