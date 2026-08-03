const $ = (selector) => document.querySelector(selector);
let currentRun = null;
let reviewer = "local-reviewer";
let progressTimer = null;
let authenticatedAccount = false;
let selectedFiles = [];
let activeUploadInput = "image-files";
const mediaResults = new Map();

const scenarios = {
  beauty: {
    category: "美妆",
    title: "国家级护肤科技，7天淡纹，100%安全",
    description: "采用专业配方，效果行业第一。经测试可永久消除皱纹，所有肤质使用都绝对安全。",
    markets: ["CN"]
  },
  health: {
    category: "保健食品",
    title: "每日一粒，预防三高并增强免疫力",
    description: "天然配方能够预防心血管疾病、替代日常药物，并保证快速改善身体状态。",
    markets: ["CN", "US"]
  },
  education: {
    category: "教育培训",
    title: "名师押题，保证通过资格考试",
    description: "由考试命题专家亲自授课，未通过可全额退款，承诺每位学员都能取得资格证书。",
    markets: ["CN"]
  }
};

const eventLabels = {
  validate_input: ["校验任务输入", "确认目标市场、类目与渠道"],
  claim_extraction: ["提取营销声明", "使用模型识别需要审查的事实与承诺"],
  claim_extraction_fallback: ["声明提取降级", "模型不可用，已切换确定性提取"],
  claim_normalization: ["标准化营销声明", "合并重复表达并建立字段定位"],
  platform_policy_check: ["检查平台规则", "按所选发布平台匹配对应的内容规范"],
  market_opportunity_retrieval: ["检索市场机会", "从独立情报库检索法规差异和产品机会"],
  cost_budget_route: ["检查执行预算", "根据成本与延迟选择处理路径"],
  query_rewrite: ["改写检索问题", "将商品表达转换为法规检索查询"],
  retrieval_fallback: ["检索链路降级", "主检索器不可用，已切换后备路径"],
  collect_evidence: ["收集法规证据", "按法域检索并筛选候选条款"],
  evidence_verification: ["验证证据充分性", "检查证据是否支持当前风险判断"],
  human_review_route: ["路由至人工审核", "Agent 已到达权限边界，等待人工决定"],
  review_completed: ["人工审核完成", "审核决定已写入任务事件"],
  agent_remediation: ["Agent 生成整改方案", "依据已确认的证据生成最小修改"],
  agent_guardrail: ["Agent 安全边界触发", "当前动作被转交人工处理"],
  reviewed_case_memory_written: ["写入审核记忆", "仅保存已审核且可追溯的经验"],
  failed: ["任务执行失败", "请查看事件详情并重新运行"]
};

const statusCopy = {
  review_required: ["发现风险，等待人工确认", "Agent 已找到候选法规证据，需要你确认适用范围。"],
  needs_more_evidence: ["证据不足，Agent 已停止下结论", "当前知识库无法充分支持判断，系统没有强行生成合规结论。"],
  review_accepted: ["证据已确认，可生成整改建议", "人工确认结果已写入审计记录。"],
  review_rejected: ["本次结果已驳回", "该结果不会进入整改或发布环节。"],
  remediation_planned: ["整改建议已生成", "请核对修改是否保留商品事实。"],
  draft_ready: ["整改草案已生成", "草案仍需人工决定是否采用。"]
};

const portalErrors = {
  workflow_quota_exhausted: "该账号的10次免费审查额度已经用完。",
  authentication_required: "请先登录后再使用审查功能。"
};

const channelLabels = {
  generic: "通用规则",
  taobao: "淘宝",
  douyin: "抖音",
  tiktok_shop: "TikTok Shop",
  amazon: "Amazon",
  temu: "Temu",
  shopee: "Shopee"
};

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
  if (!response.ok) throw new Error(portalErrors[body?.detail] || body?.detail || `请求失败 (${response.status})`);
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
  return statusCopy[status]?.[0] || status;
}

function updateStepper(status, running = false) {
  const stepper = $("#workflow-stepper");
  stepper.hidden = !running && !status;
  const order = ["submit", "agent", "evidence", "review", "remediation"];
  let active = 0;
  if (running) active = 1;
  else if (status === "needs_more_evidence") active = 2;
  else if (status === "review_required" || status === "review_rejected") active = 3;
  else if (["review_accepted", "remediation_planned", "draft_ready"].includes(status)) active = 4;
  document.querySelectorAll(".stepper li").forEach((item) => {
    const index = order.indexOf(item.dataset.step);
    item.classList.toggle("active", index === active);
    item.classList.toggle("done", index < active);
  });
}

function updateTaskPreview() {
  const form = $("#check-form");
  const markets = [...form.querySelectorAll('input[name="markets"]:checked')]
    .map((input) => ({CN: "中国", US: "美国", EU: "欧盟"}[input.value] || input.value));
  $("#preview-markets").textContent = markets.join(" / ") || "未选择";
  $("#preview-category").textContent = form.elements.category.value.trim() || "未填写";
  $("#preview-channel").textContent = channelLabels[form.elements.channel.value] || "通用规则";
  $("#preview-files").textContent = `${selectedFiles.length} 个`;
  const submit = $("#submit-button");
  const hasText = Boolean(form.elements.title.value.trim() || form.elements.description.value.trim());
  submit.disabled = !hasText && selectedFiles.length === 0;
  submit.querySelector("small").textContent = hasText
    ? (selectedFiles.length ? `文案 + ${selectedFiles.length} 个附件` : "纯文案审查")
    : (selectedFiles.length ? `${selectedFiles.length} 个附件` : "请输入文案或添加文件");
}

function setProgress(active) {
  const progress = $("#progress");
  window.clearInterval(progressTimer);
  if (!active) {
    progress.hidden = true;
    document.querySelectorAll(".run-phases span").forEach((item) => item.classList.remove("active"));
    return;
  }
  const phases = [
    ["正在读取文件", "校验文件格式并建立批次"],
    ["正在识别内容", "提取图片文字或 PDF 页面结构"],
    ["正在定位风险", "将风险内容映射回图片或页面位置"],
    ["正在核验依据", "只保留能够直接支持判断的准确引文"],
    ["正在生成建议", "输出可人工确认的修改方案"]
  ];
  let phase = 0;
  const render = () => {
    $("#progress-title").textContent = phases[phase][0];
    $("#progress-detail").textContent = phases[phase][1];
    $("#progress-bar").style.width = `${18 + phase * 19}%`;
    document.querySelectorAll(".run-phases span").forEach((item, index) => item.classList.toggle("active", index <= phase));
    phase = Math.min(phase + 1, phases.length - 1);
  };
  progress.hidden = false;
  updateStepper(null, true);
  render();
  progressTimer = window.setInterval(render, 1700);
  progress.scrollIntoView({behavior: "smooth", block: "center"});
}

function selectScenario(key) {
  const scenario = scenarios[key];
  const form = $("#check-form");
  form.elements.external_id.value = `DEMO-${key.toUpperCase()}-${new Date().toISOString().slice(0, 10).replaceAll("-", "")}`;
  form.elements.category.value = scenario.category;
  form.elements.title.value = scenario.title;
  form.elements.description.value = scenario.description;
  document.querySelectorAll('input[name="markets"]').forEach((input) => { input.checked = scenario.markets.includes(input.value); });
  document.querySelectorAll(".scenario-card").forEach((button) => button.classList.toggle("selected", button.dataset.scenario === key));
  $("#draft-state").textContent = `已载入${scenario.category}演示场景`;
  updateTaskPreview();
  toast("演示场景已载入，可直接启动 Agent");
}

function resetCheck({focus = false} = {}) {
  currentRun = null;
  selectedFiles.forEach((item) => item.previewUrl && URL.revokeObjectURL(item.previewUrl));
  selectedFiles = [];
  mediaResults.clear();
  $("#check-form").reset();
  renderFileQueue();
  $("#media-result").hidden = true;
  $("#media-result-list").innerHTML = "";
  $("#result").hidden = true;
  $("#draft-state").textContent = "尚未提交";
  setProgress(false);
  updateStepper(null);
  updateTaskPreview();
  if (focus) {
    $("#upload-dropzone").focus();
    $("#upload-dropzone").scrollIntoView({behavior: "smooth", block: "center"});
  }
}

function fileKey(file) {
  return `${file.webkitRelativePath || file.name}:${file.size}:${file.lastModified}`;
}

function addFiles(files) {
  const accepted = [...files].filter((file) => /\.(png|jpe?g|pdf)$/i.test(file.name));
  const existing = new Set(selectedFiles.map((item) => item.key));
  accepted.forEach((file) => {
    const key = fileKey(file);
    if (existing.has(key)) return;
    existing.add(key);
    selectedFiles.push({
      key,
      file,
      relativePath: file.webkitRelativePath || file.name,
      previewUrl: file.type.startsWith("image/") ? URL.createObjectURL(file) : null
    });
  });
  renderFileQueue();
  updateTaskPreview();
}

function renderFileQueue() {
  const target = $("#file-queue");
  if (!selectedFiles.length) {
    target.innerHTML = '<p class="queue-empty">尚未选择文件</p>';
    return;
  }
  target.innerHTML = selectedFiles.map((item) => `
    <div class="queue-item" data-file-key="${escapeHtml(item.key)}">
      <div><strong>${escapeHtml(item.relativePath)}</strong><small>${(item.file.size / 1024 / 1024).toFixed(2)} MB</small></div>
      <span class="queue-kind">${/\.pdf$/i.test(item.file.name) ? "PDF" : "图片"}</span>
      <button class="queue-remove" type="button" aria-label="移除 ${escapeHtml(item.file.name)}">×</button>
    </div>`).join("");
}

function setUploadMode(inputId) {
  activeUploadInput = inputId;
  document.querySelectorAll(".upload-choice").forEach((button) => {
    button.classList.toggle("active", button.dataset.uploadTarget === inputId);
  });
  const copy = {
    "image-files": ["选择图片", "PNG、JPG，支持批量上传"],
    "pdf-files": ["选择 PDF", "支持多个 PDF，单文件不超过 20MB"],
    "folder-files": ["选择文件夹", "自动读取文件夹中的图片和 PDF"]
  }[inputId];
  $("#upload-dropzone").querySelector("strong").textContent = copy[0];
  $("#upload-dropzone").querySelector("small").textContent = copy[1];
}

function mediaCardId(key) {
  let hash = 0;
  for (const char of key) hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0;
  return `media-${Math.abs(hash)}`;
}

function drawReviewCanvas(canvas, image, findings, active = -1) {
  const maxWidth = 900;
  const scale = Math.min(1, maxWidth / image.naturalWidth);
  canvas.width = Math.round(image.naturalWidth * scale);
  canvas.height = Math.round(image.naturalHeight * scale);
  const context = canvas.getContext("2d");
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  findings.forEach((finding, index) => {
    const [x1, y1, x2, y2] = finding.bbox;
    const x = x1 / 1000 * canvas.width;
    const y = y1 / 1000 * canvas.height;
    const width = Math.max(3, (x2 - x1) / 1000 * canvas.width);
    const height = Math.max(3, (y2 - y1) / 1000 * canvas.height);
    context.lineWidth = index === active ? 4 : 2;
    context.strokeStyle = index === active ? "#dc2626" : "#ef4444";
    context.fillStyle = index === active ? "rgba(220,38,38,.18)" : "rgba(239,68,68,.10)";
    context.fillRect(x, y, width, height);
    context.strokeRect(x, y, width, height);
    context.fillStyle = "#dc2626";
    context.font = "bold 12px sans-serif";
    context.fillText(String(index + 1), x + 3, Math.max(12, y - 4));
  });
}

function renderMediaReview(item, result) {
  mediaResults.set(item.key, result);
  const cardId = mediaCardId(item.key);
  const findings = result.findings || [];
  const target = $("#media-result-list");
  let card = $(`#${cardId}`);
  if (!card) {
    card = document.createElement("article");
    card.id = cardId;
    card.className = "media-review-card";
    target.appendChild(card);
  }
  card.innerHTML = `
    <div class="media-card-header">
      <div><strong>${escapeHtml(item.relativePath)}</strong><small>${escapeHtml(result.summary || "审查完成")}</small></div>
      <span class="media-status ${findings.length ? "risk" : "passed"}">${findings.length ? `${findings.length} 处风险` : "未发现明确风险"}</span>
    </div>
    <div class="media-review-layout">
      <div class="annotated-image"><canvas class="annotated-canvas"></canvas></div>
      <div class="finding-panel">
        <div class="finding-list">${findings.map((finding, index) => `
          <button class="finding-item" type="button" data-finding-index="${index}">
            <header><strong>${index + 1}. ${escapeHtml(finding.exact_text)}</strong><span class="severity">${escapeHtml(finding.severity)}</span></header>
            <p>${escapeHtml(finding.reason)}</p>
            <div class="finding-rewrite"><small>建议替换</small><strong>${escapeHtml(finding.suggested_text)}</strong></div>
          </button>`).join("") || '<div class="media-empty-result">未发现可以明确定位的违规内容</div>'}</div>
        ${findings.length ? '<p class="media-edit-note">请根据建议文案修改原始素材，修改后可重新上传复检。</p>' : ""}
      </div>
    </div>`;
  const image = new Image();
  image.onload = () => {
    const canvas = card.querySelector(".annotated-canvas");
    drawReviewCanvas(canvas, image, findings);
    card.querySelectorAll("[data-finding-index]").forEach((button) => {
      button.addEventListener("click", () => {
        const index = Number(button.dataset.findingIndex);
        card.querySelectorAll("[data-finding-index]").forEach((node) => node.classList.toggle("active", node === button));
        drawReviewCanvas(canvas, image, findings, index);
      });
    });
  };
  image.src = item.previewUrl || result.asset_url;
}

async function reviewImageFile(item, data) {
  const form = new FormData();
  form.append("file", item.file);
  form.append("markets", data.getAll("markets").join(","));
  form.append("category", data.get("category") || "all");
  const response = await fetch("/api/v1/media/review", {method: "POST", body: form, credentials: "same-origin"});
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || `图片审查失败 (${response.status})`);
  renderMediaReview(item, result);
  return result;
}

async function reviewPdfFile(item, data) {
  const form = new FormData();
  form.append("file", item.file);
  const response = await fetch("/api/v1/documents/parse", {method: "POST", body: form, credentials: "same-origin"});
  const parsed = await response.json();
  if (!response.ok) throw new Error(parsed.detail || `PDF 解析失败 (${response.status})`);
  return parsed;
}

function detailText(event) {
  const detail = event.detail || {};
  if (event.step === "claim_normalization") return `提取 ${detail.claim_count ?? 0} 条声明，共 ${detail.claim_length ?? 0} 个字符`;
  if (event.step === "collect_evidence") return `${detail.market || "目标市场"} · 找到 ${detail.hit_count ?? 0} 条候选证据`;
  if (event.step === "retrieval_fallback") {
    const failed = (detail.failures || []).map((item) => item.retriever).join("、");
    return `${detail.market || "目标市场"} · ${failed || "主检索器"}不可用，已自动切换`;
  }
  if (event.step === "claim_extraction_fallback") return `模型调用失败，已使用 ${detail.fallback || "确定性基线"}`;
  if (event.step === "human_review_route") return detail.reason === "evidence_requires_human_interpretation" ? "证据需要人工解释，Agent 已暂停" : "已进入人工审核边界";
  if (Object.keys(detail).length) return Object.entries(detail).slice(0, 3).map(([key, value]) => `${key}: ${Array.isArray(value) ? value.length : value}`).join(" · ");
  return eventLabels[event.step]?.[1] || "执行事件已记录";
}

function renderTrace(events = []) {
  const degraded = events.filter((event) => event.status === "degraded").length;
  $("#trace-summary").textContent = `${events.length} 个事件${degraded ? ` · ${degraded} 次自动降级` : ""}`;
  $("#agent-trace").innerHTML = events.map((event) => {
    const label = eventLabels[event.step]?.[0] || event.step.replaceAll("_", " ");
    return `<li class="${escapeHtml(event.status)}"><strong>${escapeHtml(label)}</strong><small>${escapeHtml(detailText(event))}</small></li>`;
  }).join("") || '<li><strong>暂无执行事件</strong><small>任务启动后将在此展示 Agent 的真实执行轨迹。</small></li>';
}

function renderClaims(claims = [], platformFindings = []) {
  const extracted = claims.map((claim) => `<article class="claim-item"><p>“${escapeHtml(claim.text)}”</p><span>${escapeHtml(claim.field === "title" ? "文案标题" : "文案正文")}</span></article>`);
  const platform = platformFindings.map((finding) => `<article class="claim-item platform-finding"><p>命中“${escapeHtml(finding.matched_text)}”</p><span>${escapeHtml(channelLabels[finding.platform] || finding.platform)} · ${escapeHtml(finding.reason)}</span><small>建议：${escapeHtml(finding.suggestion)}</small></article>`);
  $("#claim-list").innerHTML = [...platform, ...extracted].join("") || '<p class="empty">未提取到需要审查的营销声明。</p>';
}

function evidenceMeta(item) {
  return [
    item.publisher,
    item.version ? `版本 ${item.version}` : null,
    item.section_id ? `条款 ${item.section_id}` : null,
    item.effective_from ? `生效 ${item.effective_from}` : null
  ].filter(Boolean).map((value) => `<span>${escapeHtml(value)}</span>`).join("");
}

function evidenceExcerpt(text, quote) {
  const source = String(text || "").replace(/\s+/g, " ").trim();
  const exact = String(quote || "").trim();
  if (!exact) return source.slice(0, 220);
  const index = source.indexOf(exact);
  if (index < 0) return exact.slice(0, 220);
  const start = Math.max(0, index - 55);
  const end = Math.min(source.length, index + exact.length + 55);
  return `${start ? "…" : ""}${source.slice(start, end)}${end < source.length ? "…" : ""}`;
}

function renderEvidence(markets = []) {
  $("#evidence-list").innerHTML = markets.map((market) => {
    const support = market.evidence_support || {};
    const quote = support.quote_valid && support.supported ? support.quote : "";
    const evidence = quote
      ? (market.candidate_evidence || []).filter((item) => String(item.text || "").includes(quote)).slice(0, 2)
      : [];
    return `<article class="market-row"><div class="market-title"><div><b>${escapeHtml(market.market)}</b><strong>${escapeHtml({CN: "中国", US: "美国", EU: "欧盟"}[market.market] || market.market)}</strong></div><span>${evidence.length ? `${evidence.length} 条已验证依据` : "没有已验证依据"}</span></div>${evidence.map((item, index) => `<details class="evidence-item"${index === 0 ? " open" : ""}><summary><span class="evidence-index">${index + 1}</span><span>${escapeHtml(item.heading || item.section_id || "法规条款")}</span></summary><div class="evidence-body"><div class="evidence-meta">${evidenceMeta(item)}</div><p>${escapeHtml(evidenceExcerpt(item.text, quote))}</p>${item.source_url ? `<a href="${escapeHtml(item.source_url)}" target="_blank" rel="noreferrer">查看官方原文 ↗</a>` : ""}</div></details>`).join("") || '<p class="empty-evidence">没有找到能够直接支持当前判断的准确引文，本次不展示候选条款。</p>'}</article>`;
  }).join("");
}

function renderMarketOpportunities(items = []) {
  const panel = $("#market-opportunity-panel");
  panel.hidden = items.length === 0;
  $("#market-opportunity-list").innerHTML = items.map((item) => `<article class="opportunity-card">
    <header><span>${escapeHtml(item.jurisdiction)}</span><strong>${escapeHtml(item.title)}</strong></header>
    <div><small>监管事实</small><p>${escapeHtml(item.regulatory_fact)}</p></div>
    <div class="opportunity-value"><small>产品 / 市场机会</small><p>${escapeHtml(item.opportunity)}</p></div>
    <div class="opportunity-caveat"><small>适用边界</small><p>${escapeHtml(item.caveat)}</p></div>
    <footer><span>可信度 ${escapeHtml(item.confidence)}</span><a href="${escapeHtml(item.source_url)}" target="_blank" rel="noreferrer">官方来源 ↗</a></footer>
  </article>`).join("");
}

function renderRun(run) {
  currentRun = run;
  const payload = run.result_payload || {};
  const markets = payload.markets || [];
  const claims = payload.claims || [];
  const platformFindings = payload.platform_findings || [];
  const marketOpportunities = payload.market_opportunities || [];
  const evidenceCount = markets.reduce((total, market) => {
    const support = market.evidence_support || {};
    if (!(support.supported && support.quote_valid && support.quote)) return total;
    return total + (market.candidate_evidence || []).filter((item) => String(item.text || "").includes(support.quote)).slice(0, 2).length;
  }, 0);
  const copy = statusCopy[run.status] || [statusText(run.status), "请核对任务详情。"];
  $("#result").hidden = false;
  $("#result-title").textContent = copy[0];
  $("#result-subtitle").textContent = copy[1];
  $("#summary-status").textContent = statusText(run.status);
  $("#summary-claims").textContent = `${Math.max(claims.length, platformFindings.length)} 条`;
  $("#summary-evidence").textContent = `${evidenceCount} 条`;
  $("#summary-markets").textContent = markets.map((market) => market.market).join(" / ") || "-";
  $("#result-note").textContent = payload.note === "Candidate evidence only; no legal conclusion or automatic mutation."
    ? "以下内容仅为候选证据，不构成法律结论；Agent 不会自动修改或发布商品内容。"
    : payload.note || "请核对证据来源、适用范围和生效时间。";
  $("#report-link").href = `/api/v1/workflows/compliance/${run.id}/report?format=pdf`;

  renderClaims(claims, platformFindings);
  renderMarketOpportunities(marketOpportunities);
  renderEvidence(markets);
  renderTrace(run.events || []);

  const reviewable = ["review_required", "needs_more_evidence"].includes(run.status);
  const acceptable = run.status === "review_required";
  $("#accept-button").disabled = !acceptable;
  $("#reject-button").disabled = !reviewable;
  if (run.status === "needs_more_evidence") {
    $("#review-title").textContent = "证据不足，不能确认结论";
    $("#review-note").textContent = "可以驳回本次结果，补充或激活可靠法规后重新检查。";
  } else if (reviewable) {
    $("#review-title").textContent = "请人工确认法规证据";
    $("#review-note").textContent = "确认后才允许 Agent 生成整改建议。";
  } else {
    $("#review-title").textContent = run.status === "review_rejected" ? "本次结果已驳回" : "人工审核已经完成";
    $("#review-note").textContent = run.status === "review_rejected" ? "结果不会进入整改环节。" : "整改草案仍需人工决定是否采用。";
  }
  $("#remediation").hidden = !["review_accepted", "remediation_planned", "draft_ready"].includes(run.status);
  $("#plan-button").hidden = run.status !== "review_accepted";
  renderRemediation(payload.remediation_plan);
  updateStepper(run.status);
  $("#result").scrollIntoView({behavior: "smooth", block: "start"});
}

function renderRemediation(plan) {
  const operations = plan?.operations || [];
  $("#remediation-list").innerHTML = operations.map((item) => `<article class="change"><strong>${escapeHtml(item.field === "title" ? "商品标题" : "商品描述")}</strong><div class="change-grid"><div><small>修改前</small><p>${escapeHtml(item.before)}</p></div><div><small>建议修改</small><p>${escapeHtml(item.after)}</p></div></div></article>`).join("") || '<p class="empty">确认法规证据后，可让 Agent 生成保守的整改建议。</p>';
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
    const markets = run.input_payload.markets || [];
    return `<button class="history-item" type="button" data-run-id="${escapeHtml(run.id)}"><span class="history-market">${escapeHtml(markets.join("/"))}</span><span><strong>${escapeHtml(product.title || product.external_id || "未命名审查")}</strong><small>${escapeHtml(product.category || "未填写类目")} · ${escapeHtml(product.external_id || run.id.slice(0, 8))}</small></span><span class="status">${escapeHtml(statusText(run.status))}</span><time>${new Date(run.updated_at).toLocaleString("zh-CN")}</time></button>`;
  }).join("") || '<p class="empty">暂无检查记录</p>';
}

async function refreshAccountUsage() {
  if (!authenticatedAccount) return;
  const user = await api("/api/v1/auth/me");
  reviewer = user.email;
  $("#account-email").textContent = `${user.email} · 剩余 ${user.workflow_remaining} 次`;
  $("#quota-badge").textContent = `${user.workflow_remaining} 次可用`;
}

$("#check-form").addEventListener("input", () => {
  if (!currentRun) $("#draft-state").textContent = "草稿已更新，尚未启动 Agent";
  updateTaskPreview();
});

$("#check-form").addEventListener("change", updateTaskPreview);

$("#check-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#submit-button");
  const data = new FormData(event.currentTarget);
  const markets = data.getAll("markets");
  if (!markets.length) return toast("请至少选择一个目标市场");
  const inputTitle = String(data.get("title") || "").trim();
  const inputDescription = String(data.get("description") || "").trim();
  if (!inputTitle && !inputDescription && !selectedFiles.length) return toast("请输入待审文案，或添加图片、PDF、文件夹");
  button.disabled = true;
  button.querySelector("span").textContent = "正在审查";
  button.querySelector("small").textContent = selectedFiles.length ? `0 / ${selectedFiles.length}` : "正在分析文案";
  $("#draft-state").textContent = "任务正在执行";
  $("#media-result").hidden = !selectedFiles.some((item) => item.file.type.startsWith("image/"));
  $("#media-result-summary").textContent = "正在处理";
  setProgress(true);
  const failures = [];
  const attachmentTexts = [];
  const attachmentMeta = [];
  try {
    for (let index = 0; index < selectedFiles.length; index += 1) {
      const item = selectedFiles[index];
      $("#progress-title").textContent = `正在处理 ${item.file.name}`;
      $("#progress-detail").textContent = /\.pdf$/i.test(item.file.name)
        ? "解析 PDF 并检查其中的营销声明"
        : "识别图片文字、定位风险并生成修改建议";
      button.querySelector("small").textContent = `${index + 1} / ${selectedFiles.length}`;
      try {
        if (/\.pdf$/i.test(item.file.name)) {
          const parsed = await reviewPdfFile(item, data);
          attachmentTexts.push(`[PDF: ${item.relativePath}]\n${parsed.markdown_preview || ""}`);
          attachmentMeta.push({name: item.relativePath, type: "pdf", document_id: parsed.document_id, page_count: parsed.page_count});
        } else {
          const reviewed = await reviewImageFile(item, data);
          const findings = reviewed.findings || [];
          if (findings.length) attachmentTexts.push(`[图片: ${item.relativePath}]\n${findings.map((finding) => `${finding.exact_text}：${finding.reason}`).join("\n")}`);
          attachmentMeta.push({name: item.relativePath, type: "image", finding_count: findings.length});
        }
      } catch (error) {
        failures.push(`${item.file.name}: ${error.message}`);
      }
    }
    const combinedDescription = [inputDescription, ...attachmentTexts].filter(Boolean).join("\n\n");
    if (!inputTitle && !combinedDescription) throw new Error("没有提取到可审查的内容，请输入文案或检查附件");
    const run = await api("/api/v1/workflows/compliance", {
      method: "POST",
      body: JSON.stringify({
        product: {
          external_id: data.get("external_id") || `CHECK-${crypto.randomUUID()}`,
          title: inputTitle || (selectedFiles.length ? "附件内容审查" : "文案内容审查"),
          description: combinedDescription,
          category: data.get("category") || "all",
          attributes: {attachments: attachmentMeta}
        },
        markets,
        category: data.get("category") || "all",
        channel: data.get("channel") || "generic",
        mode: data.get("mode") === "deep" ? "deep" : "fast"
      })
    });
    renderRun(run);
    $("#media-result-summary").textContent = `${selectedFiles.length - failures.length} 个完成${failures.length ? `，${failures.length} 个失败` : ""}`;
    $("#draft-state").textContent = failures.length ? "部分文件审查失败" : "审查完成";
    await Promise.all([loadHistory(), refreshAccountUsage()]);
  } finally {
    setProgress(false);
    button.querySelector("span").textContent = "开始检查";
    updateTaskPreview();
    if (failures.length) toast(failures.slice(0, 2).join("；"));
  }
});

document.querySelectorAll(".upload-choice").forEach((button) => button.addEventListener("click", () => {
  setUploadMode(button.dataset.uploadTarget);
  $(`#${button.dataset.uploadTarget}`).click();
}));
["image-files", "pdf-files", "folder-files"].forEach((id) => {
  $(`#${id}`).addEventListener("change", (event) => addFiles(event.target.files));
});
$("#upload-dropzone").addEventListener("click", () => $(`#${activeUploadInput}`).click());
$("#upload-dropzone").addEventListener("dragover", (event) => {
  event.preventDefault();
  event.currentTarget.classList.add("dragging");
});
$("#upload-dropzone").addEventListener("dragleave", (event) => event.currentTarget.classList.remove("dragging"));
$("#upload-dropzone").addEventListener("drop", (event) => {
  event.preventDefault();
  event.currentTarget.classList.remove("dragging");
  addFiles(event.dataTransfer.files);
});
$("#file-queue").addEventListener("click", (event) => {
  const remove = event.target.closest(".queue-remove");
  if (!remove) return;
  const row = remove.closest("[data-file-key]");
  const index = selectedFiles.findIndex((item) => item.key === row.dataset.fileKey);
  if (index >= 0) {
    const [removed] = selectedFiles.splice(index, 1);
    if (removed.previewUrl) URL.revokeObjectURL(removed.previewUrl);
    renderFileQueue();
    updateTaskPreview();
  }
});
$("#clear-media-results").addEventListener("click", () => resetCheck({focus: true}));
$("#new-check-button").addEventListener("click", () => resetCheck({focus: true}));
$("#accept-button").addEventListener("click", () => review("accept").catch((error) => toast(error.message)));
$("#reject-button").addEventListener("click", () => review("reject").catch((error) => toast(error.message)));
$("#plan-button").addEventListener("click", async () => {
  try {
    renderRun(await api(`/api/v1/workflows/compliance/${currentRun.id}/remediation-plan`, {
      method: "POST", body: JSON.stringify({plan_id: crypto.randomUUID(), mode: "agent"})
    }));
    await loadHistory();
  } catch (error) { toast(error.message); }
});

document.querySelectorAll(".topnav-item").forEach((button) => button.addEventListener("click", async () => {
  document.querySelectorAll(".topnav-item").forEach((item) => item.classList.toggle("active", item === button));
  document.querySelectorAll(".view").forEach((view) => { view.hidden = true; view.classList.remove("active"); });
  const view = $(`#${button.dataset.view}-view`);
  view.hidden = false;
  view.classList.add("active");
  if (button.dataset.view === "history") await loadHistory();
}));

$("#history-list").addEventListener("click", async (event) => {
  const item = event.target.closest("[data-run-id]");
  if (!item) return;
  document.querySelector('[data-view="check"]').click();
  renderRun(await api(`/api/v1/workflows/compliance/${item.dataset.runId}`));
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
    authenticatedAccount = true;
    await refreshAccountUsage();
    $("#logout-button").hidden = false;
  } else {
    $("#quota-badge").hidden = true;
  }
  setUploadMode("image-files");
  renderFileQueue();
  updateTaskPreview();
  await loadHistory();
}

start().catch((error) => toast(error.message));
