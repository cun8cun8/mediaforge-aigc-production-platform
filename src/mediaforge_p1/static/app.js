const state = {
  projectId: null,
  project: null,
  projects: [],
  cost: null,
  studioMetrics: null,
  jobs: [],
  audit: [],
  auditIntegrity: null,
  auditAnchors: null,
  operations: null,
  workflow: null,
  overview: null,
  assets: null,
  evaluation: null,
  llmops: null,
  editTimeline: null,
  benchmark: null,
  routes: null,
  compliance: null,
  continuity: null,
  distribution: null,
  trace: null,
  retrospective: null,
  providerStatus: null,
  providerDiagnostics: null,
  providerContract: null,
  lipsyncStatus: null,
  sourceOcrStatus: null,
  productionReadiness: null,
  operationsAlerts: null,
  enterpriseStatus: null,
  billingSummary: null,
  settlementSummary: null,
  billingPage: null,
  billingOffset: 0,
  billingGeneration: 0,
  identityGeneration: 0,
  projectGeneration: 0,
  planningGeneration: 0,
  planningReport: null,
  planningRunId: null,
  planningBusy: false,
  planningPermissions: {},
  narrativeEventEditingId: null,
  adaptationSceneEditingId: null,
  sourceChapterEditingId: null,
  speechStatus: null,
  memoryProjectId: null,
  memorySourceIds: [],
  memoryGeneration: 0,
  registry: null,
  quota: null,
  tenantCost: null,
  workers: null,
  collaboration: null,
  collaborationDocuments: null,
  collaborationSocket: null,
  collaborationSocketProjectId: null,
  collaborationSocketRetries: 0,
  collaborationSocketRetryTimer: null,
  collaborationRefreshTimer: null,
  selectedShotId: null,
  events: [],
  packageUrl: "",
  auditExportUrl: "",
  auditCsvUrl: "",
  auditIntegrityUrl: "",
  auditAnchorUrl: "",
  snapshotUrl: "",
  reportUrl: "",
  provenanceUrl: "",
  complianceUrl: "",
  continuityUrl: "",
  distributionUrl: "",
  acceptanceUrl: "",
  closeoutUrl: "",
  archivePackageUrl: "",
  archiveVerificationUrl: "",
  deliveryReceiptUrl: "",
  routeUrl: "",
  routeShotId: "",
  comparisonUrl: "",
  evaluationUrl: "",
  timelineExportUrl: "",
  benchmarkUrl: "",
  traceUrl: "",
  dataset: null,
  retrospectiveUrl: "",
  verificationUrl: "",
  activeTab: "inspector",
  shotQuery: "",
  shotStatus: "",
  jobStatusFilter: "",
  includeArchived: false,
  pendingReferenceFile: null,
  pendingSourceDocumentFile: null,
  pendingAudioFile: null,
  dialogueDraft: [],
  dialogueProjectId: null,
  dialogueDirty: false,
  eventStreamAbortController: null,
  eventStreamProjectId: null,
  eventStreamRunning: false,
  eventCursor: 0,
};

const $ = (id) => document.getElementById(id);

const STATUS_LABELS = {
  DRAFT: "草稿",
  PLANNED: "已规划",
  IN_PROGRESS: "进行中",
  EXPORTED: "已导出",
  ARCHIVED: "已归档",
  CLOSED: "已结项",
  SEALED: "已封存",
  VERIFIED: "已验证",
  RELEASED: "已发布",
  DELIVERED: "已分发",
  ACCEPTED: "已确认",
  REJECTED: "已拒绝",
  PENDING: "待审核",
  APPROVED: "已通过",
  CHANGES_REQUESTED: "需修改",
  ADOPTED: "已采纳",
  DISCARDED: "已舍弃",
  STALE: "已过期",
  VALIDATED: "已校验",
  QUEUED: "排队中",
  ADMITTED: "已接收",
  RUNNING: "执行中",
  SUCCEEDED: "已成功",
  FAILED: "失败",
  QUALITY_REJECTED: "质量未通过",
  RETRY_WAIT: "等待重试",
  CANCELED: "已取消",
  PASS: "通过",
  FAIL: "未通过",
  NEEDS_CHANGES: "需修改",
  UNSURE: "待确认",
  BLOCKED: "已拦截",
  SELECTED: "已选择",
  ELIGIBLE: "可用",
  "OVER BUDGET": "超出预算",
  UNSUPPORTED: "不支持",
  ON: "启用",
  OFF: "停用",
  INFO: "提示",
  WARNING: "警告",
  CRITICAL: "严重",
  CHECK: "待检查",
  ERROR: "错误",
  WAITING: "等待中",
};

const labelFor = (value) => STATUS_LABELS[String(value)] || String(value || "-");

const NEXT_ACTION_LABELS = {
  PLAN: "生成故事计划",
  PROCESS_QUEUE: "执行排队任务",
  RETRY_FAILED: "重试失败任务",
  RUN_REVISIONS: "执行返工任务",
  GENERATE_SHOTS: "生成待处理镜头",
  REVIEW_SHOTS: "审核生产结果",
  EXPORT: "导出最终样片",
  PACKAGE: "打包交付",
  VERIFY_PACKAGE: "验证交付包",
  CHECK_COMPLIANCE: "处理合规门禁",
  RELEASE: "发布项目",
  DISTRIBUTE: "记录分发",
  ACKNOWLEDGE_DELIVERY: "确认交付",
  CLOSEOUT: "关闭项目",
  REDISTRIBUTE: "重新记录分发",
  ARCHIVE_PACKAGE: "打包归档",
  VERIFY_ARCHIVE: "验证归档包",
  RESTORE: "恢复项目",
  CLOSED: "项目已结项",
  COMPLETE: "发布流程已完成",
  RECOVER_STALE: "恢复卡住任务",
};

const MOTION_LABELS = {
  static: "固定镜头",
  slow_push_in: "缓慢推进",
  push_in: "推进",
  pull_out: "拉远",
  pan_left: "左摇",
  pan_right: "右摇",
  tilt_up: "上摇",
  tilt_down: "下摇",
  tracking: "跟拍",
  handheld: "手持",
};

const ASSET_KIND_LABELS = {
  image: "图片",
  video: "视频",
  audio_track: "项目音频轨",
  final_mp4: "最终 MP4",
  subtitle_srt: "字幕 SRT",
  delivery_package: "交付包",
  lipsync_output: "口型同步输出",
  output: "输出",
  generated: "生成资产",
  reference: "参考资产",
};

const PROVIDER_MODE_LABELS = {
  mock: "模拟",
  comfyui: "ComfyUI",
  replicate: "Replicate",
  custom: "自定义",
  local: "本机 GPU",
  "local-command": "本机 GPU",
};

const PROVIDER_GRADE_LABELS = {
  READY: "生产就绪",
  SIMULATION: "模拟可用",
  DEGRADED: "连接异常",
  BLOCKED: "配置阻塞",
};

const PROVIDER_CAPABILITY_LABELS = {
  image_generation: "图像生成",
  image_to_video: "图生视频",
};

const PROVIDER_CHECK_LABELS = {
  configuration: "基础配置",
  connectivity: "服务连通性",
  capabilities: "生成能力",
  workflow: "工作流文件",
  credentials: "API 凭据",
  model_version: "模型版本",
  production_mode: "生产模式",
  provider_pool: "路由池",
  callback_authentication: "回调签名",
  replicate_webhook_authentication: "Replicate 回调签名",
  warmup: "模型预热",
};

const PROVIDER_ACTION_LABELS = {
  CONFIGURE_WORKFLOW: "配置 ComfyUI 工作流",
  CONFIGURE_CREDENTIALS: "配置 Replicate 凭据",
  CONFIGURE_MODEL_VERSION: "锁定 Replicate 模型版本",
  CONFIGURE_PROVIDER: "配置生成服务商",
  CHECK_CONNECTIVITY: "检查服务端与网络",
  START_GENERATION: "可以执行生成任务",
  SWITCH_REAL_PROVIDER: "交付前切换真实服务商",
  CONFIGURE_CALLBACK_SECRET: "配置回调密钥",
  CONFIGURE_REPLICATE_WEBHOOK_SECRET: "配置 Replicate 回调密钥",
  CONFIGURE_LOCAL_PROVIDER: "配置本机 GPU Provider",
};

const PROVIDER_DETAIL_LABELS = {
  base_url: "服务地址",
  api_base_url: "API 地址",
  workflow_path: "工作流文件",
  workflow_loaded: "工作流已加载",
  credential_configured: "API 凭据已配置",
  model_version_configured: "模型版本已配置",
  timeout_seconds: "超时时间",
  cancel_request_timeout_seconds: "取消请求超时",
  poll_interval_seconds: "轮询间隔",
  cancel_after_seconds: "远端取消期限",
  cancel_after_header: "远端取消请求头",
  webhook_url_template_configured: "Replicate 回调地址模板已配置",
  estimated_cost: "单次预估成本",
  execution: "执行方式",
  endpoint: "探测端点",
  device_count: "设备数量",
  command_configured: "执行命令已配置",
  health_command_configured: "健康命令已配置",
  warmup_command_configured: "预热命令已配置",
  capability_config: "能力配置",
};

const ACTION_LABELS = {
  "planning.approved": "规划审批",
  "narrative_event.created": "故事事件创建",
  "narrative_event.updated": "故事事件修订",
  "narrative_event.reviewed": "故事事件审核",
  "project.plan_invalidated": "计划失效",
  "project.created": "项目创建",
  "project.archived": "项目归档",
  "project.restored": "项目恢复",
  "project.cloned": "项目复制",
  "project.imported": "项目导入",
  "project.imported_package": "交付包导入",
  "project.imported_archive": "归档包导入",
  "project.snapshot_exported": "快照导出",
  "audio_track.registered": "音频轨应用",
  "audio_track.removed": "音频轨移除",
  "project.trace_exported": "追踪导出",
  "project.report_exported": "生产报告导出",
  "project.retrospective_exported": "复盘报告导出",
  "dataset.exported": "审核数据集导出",
  "project.provenance_exported": "来源报告导出",
  "project.compliance_exported": "合规报告导出",
  "project.continuity_exported": "连续性报告导出",
  "project.distribution_exported": "分发报告导出",
  "project.delivered": "项目分发",
  "project.delivery_accepted": "交付确认",
  "project.delivery_rejected": "交付拒绝",
  "project.acceptance_exported": "验收证书导出",
  "project.archive_packaged": "归档包生成",
  "project.archive_verified": "归档包验证",
  "project.closed": "项目结项",
  "project.planned": "项目规划",
  "policy.brief_checked": "简报策略检查",
  "policy.variant_blocked": "候选版本被拦截",
  "policy.shots_blocked": "镜头被拦截",
  "policy.shots_checked": "镜头策略检查",
  "policy.shot_blocked": "镜头被拦截",
  "shot.route_exported": "镜头路由导出",
  "shot.variant_submitted": "候选版本提交",
  "shot.variants_compared": "候选版本对比",
  "shot.comparison_exported": "对比报告导出",
  "shot.variant_promoted": "候选版本采用",
  "shot.submitted": "镜头提交",
  "job.process_started": "任务开始执行",
  "shot.retry_started": "镜头开始重试",
  "shot.quality_rejected": "镜头质量未通过",
  "shot.generated": "镜头生成",
  "shot.failed": "镜头生成失败",
  "shot.retry_requested": "镜头请求重试",
  "job.retry_exhausted": "任务重试次数耗尽",
  "job.retry_scheduled": "任务安排重试",
  "shot.retry_scheduled": "镜头安排重试",
  "shot.enqueued": "镜头加入队列",
  "project.batch_enqueued": "批量加入队列",
  "job.process_requested": "请求执行任务",
  "job.stale_recovered": "卡住任务恢复",
  "shot.retry_queued": "重试任务加入队列",
  "queue.drained": "队列执行完成",
  "project.batch_submitted": "批量提交镜头",
  "shot.reviewed": "镜头审核",
  "project.batch_approved": "批量通过镜头",
  "shot.revision_requested": "请求镜头返工",
  "project.exported": "样片导出",
  "workflow.stage_locked": "生产阶段锁定",
  "workflow.stage_invalidated": "生产阶段返工",
  "project.packaged": "交付包生成",
  "project.package_verified": "交付包验证",
  "audit.exported": "审计导出",
  "audit.csv_exported": "审计 CSV 导出",
  "audit.integrity_exported": "审计链校验报告导出",
  "audit.anchored": "审计链外部锚定",
  "project.evaluated": "项目评估",
  "provider.benchmarked": "Provider 基准测试",
  "project.released": "项目发布",
  "job.canceled": "任务取消",
};

const SPAN_NAME_LABELS = {
  "mediaforge.project.lifecycle": "项目生命周期",
  "mediaforge.generation.job": "媒体生成任务",
  "mediaforge.asset.created": "媒体资产生成",
};

const nextActionLabel = (action) => NEXT_ACTION_LABELS[action?.code] || action?.label || "审核生产结果";
const workflowStatusLabel = (value) => ({
  PENDING: "待开始",
  WAITING: "等待前序",
  IN_PROGRESS: "进行中",
  READY: "可锁定",
  LOCKED: "已锁定",
  REWORK: "需返工",
}[String(value)] || String(value || "待开始"));
const motionLabel = (value) => MOTION_LABELS[String(value)] || String(value || "-");
const assetKindLabel = (value) => ASSET_KIND_LABELS[String(value)] || String(value || "资产");
const actionLabel = (value) => ACTION_LABELS[String(value)] || String(value || "事件");
const spanNameLabel = (value) => SPAN_NAME_LABELS[String(value)] || actionLabel(value);

const MILESTONE_LABELS = {
  "project.created": "已创建",
  "project.planned": "已规划",
  "project.exported": "已导出",
  "project.packaged": "已打包",
  "project.package_verified": "交付包已验证",
  "project.released": "已发布",
  "project.delivered": "已分发",
  "project.delivery_accepted": "交付已确认",
  "project.closed": "已结项",
  "project.archive_packaged": "归档包已生成",
  "project.archive_verified": "归档已验证",
};

const milestoneLabel = (milestone) => MILESTONE_LABELS[milestone?.code] || String(milestone?.label || milestone?.code || "里程碑");

const CHECK_LABELS = {
  reference_asset_licenses: "参考资产使用已批准的许可证",
  audio_track_license: "音频轨具有已批准的许可证与来源",
  artifact_hashes: "生成资产具有不可变哈希",
  artifact_files: "生成资产文件完整存在",
  artifact_metadata: "生成资产保留执行元数据",
  generated_asset_providers: "生成资产保留服务商来源",
  provider_configuration: "生成服务商已配置",
  workflow_allowlist: "工作流模板已获批准",
  lora_allowlist: "LoRA 适配器已获批准",
  license_registry: "许可证台账已登记",
  asset_rights_registry: "资产权利记录有效",
  safety_policy: "安全策略门禁已通过",
  plan_complete: "故事计划已生成",
  generation_complete: "所有镜头均已生成",
  approvals_complete: "所有镜头均已通过审核",
  media_quality: "生成媒体通过质量检查",
  policy_clear: "策略门禁无阻塞",
  continuity_clear: "故事与时间轴连续",
  model_regression_baseline: "模型准入基线通过",
  shot_order: "镜头顺序连续",
  character_continuity: "角色连续性",
  scene_continuity: "场景字段完整",
  timeline_continuity: "时间轴连续",
  target_duration: "目标时长匹配",
  subtitle_coverage: "字幕覆盖完整",
  budget_guard: "支出保持在预算内",
  final_exported: "最终样片已导出",
  delivery_packaged: "交付包已生成",
  release_recorded: "发布记录已生成",
  asset_provenance: "资产来源信息完整",
};

const checkLabel = (check) => CHECK_LABELS[String(check?.name)] || String(check?.label || check?.name || "检查项");
const checkNameLabel = (value) => CHECK_LABELS[String(value)] || String(value || "检查项");

const POLICY_CATEGORY_LABELS = {
  prompt_injection: "提示词注入",
  governance_bypass: "绕过治理",
  policy_bypass: "绕过策略",
  budget_bypass: "绕过预算",
  graphic_content: "血腥内容",
  violence: "暴力内容",
  sexual_content: "性内容",
  self_harm: "自伤内容",
  likeness_rights: "肖像权",
  ip_rights: "知识产权",
};

const FIELD_LABELS = {
  title: "标题",
  premise: "故事梗概",
  genre: "题材",
  style: "视觉风格",
  characters: "角色",
  scene: "场景",
  description: "镜头描述",
  mood: "氛围",
  workflow: "工作流",
  camera_motion: "运镜",
};

const policyCategoryLabel = (value) => POLICY_CATEGORY_LABELS[String(value)] || String(value || "策略");
const fieldLabel = (value) => FIELD_LABELS[String(value)] || String(value || "字段");

const DELIVERY_FEEDBACK_LABELS = {
  category: {
    story: "剧情",
    visual: "画面",
    audio: "声音",
    continuity: "连续性",
    timing: "节奏",
    brand: "品牌",
    other: "其他",
  },
  severity: {
    LOW: "低",
    NORMAL: "一般",
    HIGH: "高",
    BLOCKER: "阻断",
  },
  verdict: {
    APPROVE: "认可",
    REQUEST_CHANGES: "需要修改",
    QUESTION: "待澄清",
  },
  status: {
    OPEN: "待处理",
    ACKNOWLEDGED: "已确认",
    RESOLVED: "已解决",
    DISMISSED: "不采纳",
  },
};

const deliveryFeedbackLabel = (type, value) => (
  DELIVERY_FEEDBACK_LABELS[type]?.[String(value)] || String(value || "-")
);

function requestErrorMessage(detail, status) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => {
      const field = Array.isArray(item?.loc) ? item.loc.slice(1).join(".") : "";
      return field ? `${field}：${item?.msg || "参数无效"}` : (item?.msg || "参数无效");
    }).filter(Boolean);
    if (messages.length) return messages.join("；");
  }
  return detail?.message || `请求失败：${status}`;
}

async function request(path, options = {}) {
  const token = window.localStorage.getItem("mediaforge.apiToken") || "";
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 429) {
      const retryAfter = Number(response.headers.get("Retry-After"));
      throw new Error(
        Number.isFinite(retryAfter) && retryAfter > 0
          ? `请求过于频繁，请在 ${retryAfter} 秒后重试。`
          : "请求过于频繁，请稍后重试。",
      );
    }
    const message = requestErrorMessage(payload.detail, response.status);
    throw new Error(localizeErrorMessage(message));
  }
  return payload;
}

async function loadAuthStatus() {
  try {
    const status = await request("/auth/status");
    const token = window.localStorage.getItem("mediaforge.apiToken") || "";
    $("authTokenInput").value = token;
    $("authTokenInput").placeholder = status.mode === "required" ? "必填" : "可选";
    $("authTokenButton").textContent = token
      ? "已连接"
      : (status.mode === "required" ? "登录" : "连接");
    $("authTokenButton").classList.toggle("button-approve", Boolean(token));
    const browserLogin = status.browser_login?.configured === true;
    $("authLoginButton").hidden = !browserLogin;
    let authenticated = Boolean(token);
    if (browserLogin && !authenticated) {
      try {
        await request("/auth/me");
        authenticated = true;
      } catch (_error) {
        authenticated = false;
      }
    }
    $("authLogoutButton").hidden = !browserLogin || !authenticated;
  } catch (error) {
    logEvent(error.message, "muted");
  }
}

async function loadQuotaStatus() {
  try {
    const [quota, cost] = await Promise.all([
      request("/tenants/me/quota"),
      request("/tenants/me/cost"),
    ]);
    state.quota = quota;
    state.tenantCost = cost;
    const remaining = quota.remaining || {};
    const projectLabel = remaining.projects === null ? "不限项目" : `余 ${remaining.projects} 个项目`;
    const budgetLabel = remaining.budget === null ? "不限预算" : `余 $${Number(remaining.budget || 0).toFixed(2)}`;
    const spent = Number(cost.summary?.spent || 0).toFixed(2);
    $("quotaState").textContent = `${quota.tenant_id} · ${projectLabel} · ${budgetLabel} · 已花 $${spent}`;
  } catch (_error) {
    $("quotaState").textContent = "租户配额不可用";
  }
}

function currentWorkerId() {
  return $("workerIdInput")?.value.trim()
    || window.localStorage.getItem("mediaforge.workerId")
    || "studio-worker";
}

function renderWorkers() {
  const report = state.workers || {};
  const workers = report.workers || [];
  const workerId = currentWorkerId();
  const current = workers.find((worker) => worker.worker_id === workerId);
  $("workerCount").textContent = String(report.online_count ?? workers.filter((worker) => worker.status === "ONLINE").length);
  $("workerLeaseCount").textContent = String(report.active_lease_count || 0);
  $("workerCurrentStatus").textContent = current
    ? `${current.status === "ONLINE" ? "在线" : current.status === "STALE" ? "心跳超时" : "离线"} · ${current.active_job_ids?.length || 0} 个租约`
    : "未注册";
  $("heartbeatWorkerButton").disabled = !current;
  $("claimWorkerButton").disabled = !current || Boolean(state.project?.archived);
  $("workerList").innerHTML = workers.length
    ? workers.map((worker) => `
      <div class="worker-row">
        <div>
          <strong>${escapeHtml(worker.worker_id)}</strong>
          <span>${escapeHtml((worker.capabilities || []).map((capability) => ({image_to_video: "图生视频", image_generation: "图像生成"}[capability] || capability)).join("、") || "全部能力")} · 并发 ${worker.concurrency} · 完成 ${worker.completed_jobs || 0} · 失败 ${worker.failed_jobs || 0}</span>
          <span>心跳：${worker.heartbeat_age_seconds == null ? "未知" : `${Math.round(worker.heartbeat_age_seconds)} 秒前`} · 活动任务：${worker.active_job_ids?.length || 0}</span>
          <span>${escapeHtml(worker.resources?.hostname || "未上报主机")} · ${escapeHtml(worker.resources?.os || "系统未知")} · CPU ${escapeHtml(worker.resources?.cpu_count ?? "未上报")}</span>
          <span>GPU：${worker.gpu?.available ? `${worker.gpu.gpu_count} 块 · 空闲 ${worker.gpu.memory_free_mib} MiB / 总计 ${worker.gpu.memory_total_mib} MiB` : (worker.gpu?.probe_message || "未探测到可用 GPU")}</span>
          <span>执行模式：${escapeHtml(worker.gpu?.execution_mode === "api-provider-dispatch" ? "API 服务商调度" : (worker.gpu?.execution_mode || "未声明"))}</span>
          ${(worker.active_job_ids || []).map((id) => `<span>${escapeHtml(id)}</span>`).join("")}
        </div>
        <span class="job-state ${worker.status === "ONLINE" ? "is-running" : "is-waiting"}">${escapeHtml(worker.status === "ONLINE" ? "在线" : worker.status === "STALE" ? "心跳超时" : "离线")}</span>
      </div>
    `).join("")
    : `<div class="project-empty">暂无已注册 Worker。</div>`;
}

async function loadWorkerStatus() {
  try {
    state.workers = await request("/workers");
    renderWorkers();
  } catch (error) {
    $("workerCurrentStatus").textContent = "不可用";
    logEvent(error.message, "muted");
  }
}

async function registerWorker() {
  const workerId = currentWorkerId();
  const button = $("registerWorkerButton");
  setBusy(button, true, "保存中");
  try {
    window.localStorage.setItem("mediaforge.workerId", workerId);
    await request("/workers/register", {
      method: "POST",
      body: JSON.stringify({
        worker_id: workerId,
        capabilities: $("workerCapabilitySelect").value ? [$("workerCapabilitySelect").value] : [],
        concurrency: Number($("workerConcurrencyInput").value || 1),
        resources: { execution_mode: "api-provider-dispatch" },
      }),
    });
    await loadWorkerStatus();
    logEvent(`Worker ${workerId} 已注册。`, "muted");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function heartbeatWorker() {
  const workerId = currentWorkerId();
  const button = $("heartbeatWorkerButton");
  setBusy(button, true, "发送中");
  try {
    const jobIds = (state.jobs || [])
      .filter((job) => job.worker_id === workerId)
      .map((job) => job.job_id);
    await request(`/workers/${encodeURIComponent(workerId)}/heartbeat`, {
      method: "POST",
      body: JSON.stringify({ job_ids: jobIds }),
    });
    await loadWorkerStatus();
    logEvent(`Worker ${workerId} 心跳已更新。`, "muted");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function claimWorkerJobs() {
  const workerId = currentWorkerId();
  const button = $("claimWorkerButton");
  setBusy(button, true, "领取中");
  try {
    const result = await request(`/workers/${encodeURIComponent(workerId)}/claim`, {
      method: "POST",
      body: JSON.stringify({
        limit: Number($("workerConcurrencyInput").value || 1),
        project_id: state.projectId || null,
        minimum_gpu_memory_mib: Number($("workerGpuMemoryInput").value || 0),
      }),
    });
    const admission = result.admission;
    logEvent(admission?.eligible === false
      ? `${workerId} 未通过 GPU 准入：${admission.reason}`
      : `${workerId} 已领取 ${result.claimed_count || 0} 个任务。`, "muted");
    await loadWorkerStatus();
    if (state.projectId) await loadProjectContext(state.projectId);
    setActiveTab("jobs");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function saveAuthToken() {
  state.identityGeneration += 1;
  state.projectGeneration += 1;
  resetPlanningPanel();
  state.billingGeneration += 1;
  state.billingPage = null;
  state.billingOffset = 0;
  $("enterpriseRuntime").replaceChildren();
  $("billingEvents").replaceChildren();
  $("billingTotals").replaceChildren();
  $("enterpriseProbeResult").replaceChildren();
  $("planningProbeResult").replaceChildren();
  $("memoryProbeResult").replaceChildren();
  $("memoryRuntime").replaceChildren();
  $("memoryBackend").replaceChildren();
  $("memoryRagflowSync").hidden = true;
  $("memoryRagflowSync").disabled = true;
  ["enterpriseProbe", "planningProbe", "memoryProbe", "billingDownload", "billingNext", "billingPrevious"].forEach((id) => $(id).disabled = true);
  state.memoryGeneration += 1;
  state.memoryProjectId = null;
  state.memorySourceIds = [];
  $("memoryResults").replaceChildren();
  $("memorySourceList").replaceChildren();
  $("memoryCitations").replaceChildren();
  $("memorySearchButton").disabled = true;
  $("memoryResultCount").textContent = "0 条";
  $("memoryStatus").textContent = "待重新选择项目";
  const token = $("authTokenInput").value.trim();
  if (token) window.localStorage.setItem("mediaforge.apiToken", token);
  else window.localStorage.removeItem("mediaforge.apiToken");
  await loadAuthStatus();
  await loadQuotaStatus();
  try {
    const me = await request("/auth/me");
    logEvent(`已连接：${me.subject} · ${me.role}`, "muted");
    await loadProjectList();
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  }
}

function beginBrowserLogin() {
  window.location.assign("/auth/login");
}

async function logoutBrowserSession() {
  try {
    await request("/auth/logout", {method: "POST"});
  } finally {
    window.localStorage.removeItem("mediaforge.apiToken");
    window.location.reload();
  }
}

async function loadProviderStatus() {
  try {
    const status = await request("/providers/status");
    state.providerStatus = status;
    state.providerDiagnostics = null;
    const stateLabel = status.mode === "mock"
      ? "模拟模式"
      : (status.configured ? "服务商已配置" : "服务商不可用");
    const label = `${stateLabel} · ${status.provider}`;
    $("connectionState").innerHTML = `<i></i> ${escapeHtml(label)}`;
    $("connectionState").classList.toggle("connection-warning", !status.configured);
    renderProviderCenter();
    logEvent(`服务商模式：${status.mode}`, "muted");
  } catch (error) {
    $("connectionState").innerHTML = `<i></i> API 状态未知`;
    $("connectionState").classList.add("connection-warning");
    $("providerMessage").textContent = "服务商配置读取失败。";
  }
}

async function loadPlannerStatus() {
  try {
    const status = await request("/llm/status");
    const configured = status.mode !== "deterministic" && status.configured;
    $("plannerStatus").textContent = configured ? "已启用" : "本地模式";
    $("plannerSummary").textContent = configured
      ? `${status.name || "LLM 规划器"} · ${status.model || "已配置"}`
      : "未配置外部 LLM，使用本地确定性规划器。";
  } catch (error) {
    $("plannerStatus").textContent = "不可用";
    $("plannerSummary").textContent = error.message;
  }
}

function providerNameLabel(value) {
  const labels = {
    "mock-provider": "模拟服务商",
    "replicate-video": "Replicate 视频",
    comfyui: "ComfyUI",
    "local-gpu-command": "本机 GPU Provider",
  };
  return labels[String(value)] || String(value || "-");
}

function providerGradeLabel(value) {
  return PROVIDER_GRADE_LABELS[String(value)] || "待诊断";
}

function localizeProviderDiagnosticMessage(value) {
  const text = String(value || "服务商状态未知。");
  const exact = {
    "Provider is configured.": "服务商已配置。",
    "Mock Provider is active.": "模拟服务商已启用。",
    "ComfyUI workflow is configured.": "ComfyUI 工作流已配置。",
    "Replicate video Provider is configured.": "Replicate 视频服务商已配置。",
    "Local GPU command Provider is configured.": "本机 GPU Provider 已配置。",
    "Local Provider command is configured; active health command is not set.": "本机 Provider 已配置，尚未设置主动健康检查命令。",
    "Local Provider is configured; no warmup command is set.": "本机 Provider 已配置，尚未设置模型预热命令。",
    "Provider has no explicit warmup hook.": "该 Provider 没有显式预热钩子。",
    "Provider configuration is complete.": "服务商基础配置完整。",
    "Provider configuration is incomplete.": "服务商基础配置不完整。",
    "1 generation capabilities are available.": "已有 1 项生成能力。",
    "2 generation capabilities are available.": "已有 2 项生成能力。",
    "No generation capability is available.": "没有可用的生成能力。",
    "ComfyUI workflow JSON is loaded.": "ComfyUI 工作流文件已加载。",
    "ComfyUI workflow JSON is not loaded.": "ComfyUI 工作流文件未加载。",
    "Replicate API credential is configured.": "Replicate API 凭据已配置。",
    "Replicate API credential is missing.": "Replicate API 凭据缺失。",
    "Replicate model version is pinned.": "Replicate 模型版本已锁定。",
    "Replicate model version is missing.": "Replicate 模型版本缺失。",
    "A real generation Provider is selected.": "当前已选择真实生成服务商。",
    "Mock mode is suitable for workflow validation only.": "模拟模式仅适合验证工作流。",
    "The Provider can accept generation jobs.": "服务商可以接收生成任务。",
    "Provide a valid COMFYUI_WORKFLOW_PATH and restart the service.": "提供有效的 COMFYUI_WORKFLOW_PATH，然后重启服务。",
    "Set REPLICATE_API_TOKEN and restart the service.": "设置 REPLICATE_API_TOKEN，然后重启服务。",
    "Set REPLICATE_MODEL_VERSION and restart the service.": "设置 REPLICATE_MODEL_VERSION，然后重启服务。",
    "Configure a supported generation Provider and restart the service.": "配置受支持的生成服务商，然后重启服务。",
    "Verify the Provider endpoint, network, and service availability.": "检查服务商地址、网络和服务是否可用。",
    "Switch to ComfyUI or Replicate before production delivery.": "正式交付前切换到 ComfyUI 或 Replicate。",
    "Provider callbacks require a valid HMAC-SHA256 signature.": "服务商回调已启用 HMAC-SHA256 签名校验。",
    "Configure MEDIAFORGE_CALLBACK_SECRET before using production callbacks.": "使用生产回调前请配置 MEDIAFORGE_CALLBACK_SECRET。",
    "Unsigned callbacks are allowed only in local Mock mode.": "仅本地模拟模式允许未签名回调。",
    "Set MEDIAFORGE_CALLBACK_SECRET and restart the service.": "设置 MEDIAFORGE_CALLBACK_SECRET，然后重启服务。",
    "Replicate native webhook verification is configured.": "Replicate 原生回调签名校验已配置。",
    "Configure REPLICATE_WEBHOOK_SIGNING_SECRET before using replicate-webhook execution.": "使用 Replicate 回调执行模式前请配置 REPLICATE_WEBHOOK_SIGNING_SECRET。",
    "Set REPLICATE_WEBHOOK_SIGNING_SECRET and restart the API before using replicate-webhook execution.": "使用 Replicate 回调执行模式前请设置 REPLICATE_WEBHOOK_SIGNING_SECRET 并重启 API。",
    "ComfyUI server is reachable.": "ComfyUI 服务在线。",
    "Replicate API is reachable.": "Replicate API 在线。",
    "Mock Provider is ready.": "模拟服务商已就绪。",
  };
  if (exact[text]) return exact[text];
  return text
    .replace(/^(\d+) generation capabilities are available\.$/i, "已有 $1 项生成能力。")
    .replace(/^missing (.+)$/i, "缺少配置：$1")
    .replace(/^Provider health check failed: (.+)$/i, "服务商健康检查失败：$1")
    .replace(/^Provider warmup failed: (.+)$/i, "服务商预热失败：$1")
    .replace(/^local Provider (.+)$/i, "本机 Provider $1")
    .replace(/^ComfyUI workflow file not found: (.+)$/i, "未找到 ComfyUI 工作流文件：$1")
    .replace(/^unsupported MEDIAFORGE_PROVIDER=(.+)$/i, "不支持的 MEDIAFORGE_PROVIDER：$1");
}

function providerDetailLabel(key) {
  return PROVIDER_DETAIL_LABELS[String(key)] || String(key).replaceAll("_", " ");
}

function providerDetailValue(key, value) {
  if (typeof value === "boolean") return value ? "是" : "否";
  if (["timeout_seconds", "poll_interval_seconds"].includes(key)) {
    return `${value} 秒`;
  }
  if (key === "estimated_cost") return formatCurrency(value);
  return formatObserved(value);
}

function renderProviderCenter() {
  const status = state.providerStatus;
  if (!status) return;
  const diagnostics = state.providerDiagnostics;
  const health = diagnostics?.health;
  const grade = diagnostics?.grade || (status.configured
    ? (status.mode === "mock" ? "SIMULATION" : null)
    : "BLOCKED");
  const statusDetails = { ...(status.details || {}) };
  if (!Object.keys(statusDetails).length && status.mode === "mock") {
    statusDetails.execution = "local-deterministic";
  }

  const readiness = $("providerReadiness");
  readiness.textContent = providerGradeLabel(grade);
  readiness.className = `activity-count provider-grade ${String(grade || "pending").toLowerCase()}`;
  $("providerCheckedAt").textContent = diagnostics?.checked_at
    ? `最近检查：${new Date(diagnostics.checked_at).toLocaleString()}`
    : "尚未运行诊断";
  $("providerSummary").innerHTML = `
    <div><span>模式</span><strong>${escapeHtml(PROVIDER_MODE_LABELS[status.mode] || status.mode || "-")}</strong></div>
    <div><span>服务商</span><strong>${escapeHtml(providerNameLabel(status.provider))}</strong></div>
    <div><span>配置</span><strong>${status.configured ? "已配置" : "未配置"}</strong></div>
    <div><span>连通性</span><strong>${health ? (health.healthy ? "在线" : "离线") : "待诊断"}</strong></div>
    <div><span>路由池</span><strong>${status.providers?.length || 1} 个 · ${health?.healthy_provider_count ?? (health ? (health.healthy ? 1 : 0) : 0)} 个在线</strong></div>
  `;
  $("providerMessage").textContent = localizeProviderDiagnosticMessage(
    health?.message || status.message,
  );

  const capabilities = status.capabilities || [];
  $("providerCapabilityCount").textContent = `${capabilities.length} 项`;
  $("providerCapabilities").innerHTML = capabilities.length
    ? capabilities.map((capability) => `<span class="provider-capability">${escapeHtml(PROVIDER_CAPABILITY_LABELS[capability] || capability)}</span>`).join("")
    : `<span class="provider-empty">暂无可用能力</span>`;

  const detailEntries = Object.entries(statusDetails);
  $("providerConfigList").innerHTML = detailEntries.length
    ? detailEntries.map(([key, value]) => `
      <div class="policy-row">
        <strong>${escapeHtml(providerDetailLabel(key))}</strong>
        <span>${escapeHtml(providerDetailValue(key, value))}</span>
      </div>
    `).join("")
    : `<div class="policy-row"><strong>内置配置</strong><span>本地确定性模拟</span></div>`;

  const checks = diagnostics?.checks || [];
  $("providerCheckCount").textContent = `${checks.length} 项`;
  $("providerCheckList").innerHTML = checks.length
    ? checks.map((check) => `
      <div class="policy-row ${check.passed ? "is-passed" : (check.blocking ? "is-blocked" : "is-warning")}">
        <strong><span class="policy-dot"></span>${escapeHtml(PROVIDER_CHECK_LABELS[check.code] || check.code)} · ${check.passed ? "通过" : "未通过"}</strong>
        <span>${escapeHtml(localizeProviderDiagnosticMessage(check.message))}</span>
      </div>
    `).join("")
    : `<div class="policy-row"><strong>尚未运行诊断</strong><span>运行一次诊断以检查服务连通性和生产条件。</span></div>`;

  const actions = diagnostics?.next_actions || [];
  $("providerActionCount").textContent = `${actions.length} 项`;
  $("providerActionList").innerHTML = actions.length
    ? actions.map((action) => `
      <div class="policy-row ${action.priority === "blocking" ? "is-blocked" : (action.priority === "ready" ? "is-passed" : "is-warning")}">
        <strong><span class="policy-dot"></span>${escapeHtml(PROVIDER_ACTION_LABELS[action.code] || action.code)}</strong>
        <span>${escapeHtml(localizeProviderDiagnosticMessage(action.message))}</span>
      </div>
    `).join("")
    : `<div class="policy-row"><strong>暂无处理建议</strong><span>当前没有额外的服务商操作。</span></div>`;
  renderProviderContract();
}

function renderProviderContract() {
  const report = state.providerContract;
  const rows = report?.providers || [];
  const routing = report?.routing || [];
  const summary = report?.summary || {};
  $("providerContractButton").disabled = !state.projectId || Boolean(state.project?.archived);
  $("providerContractStatus").textContent = !report
    ? "尚未验证"
    : summary.protocol_passed && !summary.unroutable_shot_count
      ? `通过 · ${summary.routable_shot_count || 0} 个镜头可路由`
      : `待处理 · ${summary.unroutable_shot_count || 0} 个镜头不可路由`;
  $("providerContractList").innerHTML = !report
    ? `<div class="policy-row"><strong>尚未验证</strong><span>检测不会提交任务或调用模型。</span></div>`
    : [
      ...rows.map((item) => `
        <div class="policy-row ${item.passed ? "is-passed" : "is-blocked"}">
          <strong><span class="policy-dot"></span>${escapeHtml(providerNameLabel(item.provider))} · ${item.passed ? "协议通过" : "协议失败"}</strong>
          <span>${escapeHtml((item.errors || []).join("；") || `${(item.capabilities || []).filter((capability) => capability.supported).length} 项能力可用`)}</span>
        </div>`),
      ...routing.filter((item) => !item.routable).map((item) => `
        <div class="policy-row is-blocked"><strong><span class="policy-dot"></span>${escapeHtml(item.shot_id)} 无法路由</strong><span>${escapeHtml(item.error || "没有可用服务商")}</span></div>`),
    ].join("") || `<div class="policy-row"><strong>未发现服务商</strong><span>请配置至少一个可用的服务商。</span></div>`;
}

function logEvent(message, tone = "normal") {
  state.events.unshift({ message, tone });
  state.events = state.events.slice(0, 8);
  $("activityCount").textContent = `${state.events.length} 条记录`;
  renderActivityLog("activityLog", state.events, "工作区已就绪。");
}

function renderActivityLog(targetId, events, emptyLabel) {
  const target = $(targetId);
  if (!events.length) {
    target.innerHTML = `<div class="activity-item activity-muted"><span class="activity-dot"></span><span>${escapeHtml(emptyLabel)}</span></div>`;
    return;
  }
  target.innerHTML = events.map((event) => `
    <div class="activity-item ${event.tone === "muted" ? "activity-muted" : ""}">
      <span class="activity-dot"></span><span>${escapeHtml(event.message)}</span>
    </div>
  `).join("");
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
  }[char]));
}

function setBusy(button, busy, busyLabel) {
  if (!button) return;
  if (busy) {
    button.dataset.originalText = button.innerHTML;
    button.innerHTML = `<span class="spinner"></span>${busyLabel}`;
    button.disabled = true;
  } else if (button.dataset.originalText) {
    button.innerHTML = button.dataset.originalText;
    button.disabled = false;
  }
}

function readBrief() {
  return {
    project_id: $("projectId").value.trim(),
    title: $("title").value.trim(),
    premise: $("premise").value.trim(),
    genre: $("genre").value.trim(),
    style: $("style").value.trim(),
    duration_seconds: Number($("duration").value),
    budget: Number($("budget").value),
    characters: [$("characterA").value.trim(), $("characterB").value.trim()],
  };
}

function formatCurrency(value) {
  return `$${Number(value || 0).toFixed(2)}`;
}

function formatRetryAt(value) {
  if (!value) return "";
  const target = new Date(value);
  if (Number.isNaN(target.getTime())) return "重试时间未知";
  const remaining = target.getTime() - Date.now();
  if (remaining <= 0) return "可以重试";
  const seconds = Math.ceil(remaining / 1000);
  if (seconds < 60) return `${seconds} 秒后重试`;
  if (seconds < 3600) return `${Math.ceil(seconds / 60)} 分钟后重试`;
  return `重试时间 ${target.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
}

function formatKindBreakdown(counts, total) {
  const image = Number(counts?.image || 0);
  const video = Number(counts?.video || 0);
  if (!image && !video) return String(total || 0);
  const parts = [];
  if (video) parts.push(`${video} 个视频`);
  if (image) parts.push(`${image} 张图片`);
  return `${total || image + video} · ${parts.join(" / ")}`;
}

function artifactUrl(projectId, uri) {
  const normalized = String(uri).replaceAll("\\", "/");
  const marker = `/${projectId}/`;
  const markerIndex = normalized.lastIndexOf(marker);
  if (markerIndex < 0) return "";
  return `/projects/${encodeURIComponent(projectId)}/media/${normalized.slice(markerIndex + marker.length)}`;
}

function isMockPreview(runtime) {
  return Boolean(runtime?.artifact && runtime.route?.provider === "mock-provider");
}

function normalizeProjectSummary(project) {
  return {
    project_id: project.project_id,
    title: project.title || project.project_id,
    status: project.status,
    archived: Boolean(project.archived),
    archived_at: project.archived_at,
    created_at: project.created_at,
    shot_count: project.shot_count || 0,
    approved_shots: project.approved_shots || 0,
    spent: project.spent || 0,
    budget: project.budget || 0,
    delivery_package: project.delivery_package,
    delivery_verified: Boolean(project.delivery_verified),
    delivery_verification_report: project.delivery_verification_report,
    provenance_report: project.provenance_report,
    compliance_passed: project.compliance_passed !== false,
    compliance_report: project.compliance_report,
    continuity_passed: project.continuity_passed !== false,
    continuity_report: project.continuity_report,
    delivery_count: project.delivery_count || 0,
    latest_delivery: project.latest_delivery,
    distribution_report: project.distribution_report,
    acceptance_report: project.acceptance_report,
    closeout_report: project.closeout_report,
    archive_package: project.archive_package,
    archive_verified: Boolean(project.archive_verified),
    archive_verification_report: project.archive_verification_report,
    final_mp4: project.final_mp4,
    subtitle_srt: project.subtitle_srt,
    audio_track: project.audio_track,
    released: Boolean(project.released || project.release),
  };
}

function renderProjectList() {
  const list = $("projectList");
  if (!state.projects.length) {
    list.innerHTML = `<div class="project-empty">暂无项目，请先填写上方 Brief 创建项目。</div>`;
    return;
  }
  list.innerHTML = state.projects.map((project) => `
    <button class="project-item ${project.project_id === state.projectId ? "is-selected" : ""}" type="button" data-project-id="${project.project_id}">
      <div class="project-item-head">
        <strong>${escapeHtml(project.title)}</strong>
        <span class="${project.archived ? "archive-label" : (project.latest_delivery?.status || project.delivery_count) ? "verified-label" : project.delivery_verified ? "verified-label" : ""}">
          ${project.archived ? (project.archive_verified ? "已封存" : project.acceptance_report || project.closeout_report ? "已结项" : "已归档") : project.latest_delivery?.status === "ACCEPTED" ? "已确认" : project.latest_delivery?.status === "REJECTED" ? "已拒绝" : project.latest_delivery?.status === "DELIVERED" || project.delivery_count ? "已分发" : project.delivery_verified ? "已验证" : escapeHtml(labelFor(project.status))}
        </span>
      </div>
      <div class="project-item-head">
        <span>${project.approved_shots || 0}/${project.shot_count || 0} 已通过</span>
        <span>${formatCurrency(project.spent)} / ${formatCurrency(project.budget)}</span>
      </div>
    </button>
  `).join("");
}

function renderOverview() {
  const overview = state.overview || {};
  const metrics = state.studioMetrics || {};
  $("overviewProjects").textContent = String(overview.project_count || 0);
  $("overviewActive").textContent = String(overview.active_projects || 0);
  $("overviewReleased").textContent = String(overview.released_projects || 0);
  $("overviewSpent").textContent = formatCurrency(overview.total_spent || 0);
  $("overviewApprovalRate").textContent = `${Math.round(Number(metrics.shots?.approval_rate || 0) * 100)}%`;
  $("overviewSuccessRate").textContent = `${Math.round(Number(metrics.jobs?.success_rate || 0) * 100)}%`;
  $("overviewQueueWait").textContent = `${Number(metrics.jobs?.average_queue_wait_seconds || 0).toFixed(1)} 秒`;
 $("overviewCostPerShot").textContent = formatCurrency(metrics.cost?.cost_per_approved_shot || 0);
  const readiness = state.productionReadiness || {};
  const operationalReady = Boolean(readiness.ready);
  const productionReady = Boolean(readiness.production_ready ?? readiness.ready);
  const readinessLabel = productionReady
    ? "生产可上线"
    : operationalReady ? "可运行，未达生产条件" : "未就绪";
  $("overviewReadiness").textContent = readinessLabel;
  $("overviewReadiness").style.color = productionReady
    ? "var(--mint)"
    : operationalReady ? "var(--yellow)" : "var(--coral)";
  $("overviewReadinessNote").textContent = productionReady
    ? (readiness.warnings?.length ? `有 ${readiness.warnings.length} 项建议` : "核心生产检查已通过")
    : operationalReady
      ? `当前可运行，${readiness.warnings?.length || 0} 项生产条件待处理`
      : `${readiness.blocking_failures?.length || 0} 项阻断问题待处理`;
  const alerts = state.operationsAlerts || {};
  const criticalAlerts = Number(alerts.critical_count || 0);
  const warningAlerts = Number(alerts.warning_count || 0);
  const alertGrade = alerts.grade || "HEALTHY";
  $("overviewAlerts").textContent = criticalAlerts
    ? `${criticalAlerts} 项严重`
    : warningAlerts ? `${warningAlerts} 项提醒` : "正常";
  $("overviewAlerts").style.color = criticalAlerts
    ? "var(--coral)" : warningAlerts ? "var(--yellow)" : "var(--mint)";
  const firstAlert = Array.isArray(alerts.alerts) ? alerts.alerts[0] : null;
  $("overviewAlertsNote").textContent = firstAlert
    ? `${firstAlert.code} · ${firstAlert.summary}`
    : alertGrade === "HEALTHY" ? "队列、预算、Provider 与 Worker 指标正常。" : "正在计算运营告警。";
  const enterprise = state.enterpriseStatus || {};
  const billing = state.billingSummary || {};
  $("overviewStateBackend").textContent = enterprise.database?.backend || "未知";
  $("overviewQueueBackend").textContent = enterprise.queue?.backend || "未知";
  $("overviewIdentityMode").textContent = enterprise.identity?.mode || "未知";
  $("overviewBillingEvents").textContent = String(billing.event_count || enterprise.billing?.event_count || 0);
 const archivedQuery = `&include_archived=${state.includeArchived ? "true" : "false"}`;
  $("overviewMetricsJsonLink").href = `/studio/metrics/export?format=json${archivedQuery}`;
  $("overviewMetricsCsvLink").href = `/studio/metrics/export?format=csv${archivedQuery}`;
  const openJobs = (overview.job_counts?.RUNNING || 0)
    + (overview.job_counts?.QUEUED || 0)
    + (overview.job_counts?.VALIDATED || 0)
    + (overview.job_counts?.ADMITTED || 0);
  $("overviewUpdated").textContent = openJobs ? `${openJobs} 个任务处理中` : "就绪";
}

function renderDetailPanels() {
  renderProductionWorkflow();
  $("budgetMetric").textContent = formatCurrency(state.cost?.budget ?? state.project?.brief?.budget ?? 0);
  $("spentMetric").textContent = formatCurrency(state.cost?.spent ?? 0);
  $("jobsMetric").textContent = String(state.jobs.length);
  $("auditMetric").textContent = String(state.audit.length);
  $("pendingMetric").textContent = String(state.operations?.pending_shots?.length || 0);
  $("failedMetric").textContent = String(state.operations?.failed_jobs?.length || 0);
  $("auditCount").textContent = `${state.audit.length} 条事件`;
  const integrity = state.auditIntegrity;
  const integrityLabels = {
    VERIFIED: "已验证",
    EMPTY: "暂无事件",
    LEGACY_UNSEALED: "历史未封存",
    INVALID: "校验失败",
  };
  $("auditIntegrityStatus").textContent = integrity
    ? `审计链：${integrityLabels[integrity.integrity_status] || integrity.integrity_status} · ${integrity.sealed_event_count || 0}/${integrity.event_count || 0} 条已封存`
    : "审计链：等待校验";
  const anchors = state.auditAnchors;
  const latestAnchor = anchors?.latest;
  $("auditAnchorStatus").textContent = !anchors
    ? "外部锚定：等待状态读取"
    : !anchors.configured
      ? "外部锚定：未配置"
      : !anchors.ready
        ? "外部锚定：配置待就绪"
        : latestAnchor
          ? `外部锚定：${anchors.verification?.verified ? "回执已验证" : "回执待复核"} · ${anchors.anchor_count || 0} 份回执`
          : "外部锚定：尚未提交链头";
  renderActivityLog("auditLog", state.audit.slice(0, 8).map((event) => ({
    message: auditEventText(event),
  })), "暂无审计事件。");
  const cost = state.cost || {};
  $("costBudget").textContent = formatCurrency(cost.budget);
  $("costSpent").textContent = formatCurrency(cost.spent);
  $("costRemaining").textContent = formatCurrency(cost.remaining);
  $("costRatio").textContent = `${Math.round(Number(cost.spent_ratio || 0) * 100)}%`;
  const breakdown = cost.shot_breakdown || [];
  $("costBreakdown").innerHTML = breakdown.length
    ? breakdown.map((shot) => `
      <div class="cost-row">
        <div>
          <strong>${escapeHtml(shot.shot_id)}</strong>
          <span>R${shot.revision} · ${shot.attempts} 次尝试</span>
        </div>
        <strong>${formatCurrency(shot.spent)}</strong>
      </div>
    `).join("")
    : `<div class="project-empty">生成镜头后，这里会显示成本台账。</div>`;
  const operations = state.operations;
  $("operationsStrip").hidden = !state.project;
  if (operations) {
    const nextAction = operations.next_action || {};
    $("nextActionLabel").textContent = nextActionLabel(nextAction);
    const actionShots = nextAction.shot_ids || [];
    const retry = operations.retry || {};
    $("nextActionMeta").textContent = actionShots.length
      ? `${actionShots.length} 个镜头 · ${actionShots.slice(0, 3).join(", ")}${actionShots.length > 3 ? "…" : ""}`
      : retry.next_retry_at
        ? `${retry.scheduled_jobs} 个任务等待重试 · ${formatRetryAt(retry.next_retry_at)}`
      : `${labelFor(operations.project_status || "DRAFT")} · 已生成 ${operations.generated_shots || 0} 个镜头`;
  }
  const planningRun = activePlanningRun();
  if (planningRun || state.planningBusy) {
    $("nextActionLabel").textContent = state.planningBusy ? "规划执行中" : `分步规划 · ${planningStatusLabel(planningRun.status)}`;
    $("nextActionMeta").textContent = `${planningRun?.shots.length || 0} 个草稿镜头 · 尚未应用`;
  }
  renderJobs();
  renderWorkers();
  renderPolicy();
  renderContinuity();
  renderAssets();
  renderEvaluation();
  renderLlmops();
  renderRoute();
  renderAbTest();
  renderTrace();
  renderRetrospective();
  renderCollaboration();
  renderProviderContract();
}

function renderProductionWorkflow() {
  const workflow = state.workflow || state.operations?.workflow || state.project?.production_workflow;
  const strip = $("workflowStageStrip");
  strip.hidden = !state.project || !workflow;
  if (!state.project || !workflow) return;
  const stages = workflow.stages || [];
  $("workflowStageSummary").textContent = `${workflow.locked_count || 0} / ${workflow.stage_count || stages.length} 阶段已锁定`;
  $("workflowStageMode").textContent = workflow.locking_required ? "强制门禁" : "建议门禁";
  $("workflowStageList").innerHTML = stages.map((stage) => {
    const status = String(stage.status || "PENDING");
    const gateCount = (stage.gates || []).filter((gate) => gate.passed).length;
    const gateTitle = (stage.gates || []).map((gate) => `${gate.passed ? "通过" : "待完成"}：${gate.label}`).join("\n");
    const actions = [
      stage.can_lock ? `<button class="button button-approve" type="button" data-action="workflow-lock" data-stage-key="${escapeHtml(stage.key)}">${status === "REWORK" ? "重新锁定" : "锁定"}</button>` : "",
      stage.can_return ? `<button class="button button-quiet" type="button" data-action="workflow-return" data-stage-key="${escapeHtml(stage.key)}">退回</button>` : "",
    ].join("");
    return `
      <article class="workflow-stage is-${escapeHtml(status.toLowerCase())}">
        <span class="workflow-stage-order">${String(stage.index || 0).padStart(2, "0")}</span>
        <div class="workflow-stage-body">
          <button class="workflow-stage-open" type="button" data-action="workflow-open-stage" data-stage-tab="${escapeHtml(stage.workspace_tab || "inspector")}">
            <strong>${escapeHtml(stage.label)}</strong>
            <span>${escapeHtml(workflowStatusLabel(status))}</span>
          </button>
        </div>
        <span class="workflow-gate-count" title="${escapeHtml(gateTitle)}">${gateCount}/${(stage.gates || []).length} 门禁</span>
        ${actions ? `<div class="workflow-stage-actions">${actions}</div>` : ""}
      </article>
    `;
  }).join("");
  const latest = workflow.latest_rework;
  const impact = $("workflowImpactStrip");
  impact.hidden = !latest;
  if (latest) {
    const impacted = (latest.affected_stages || []).map((key) => stages.find((stage) => stage.key === key)?.label || key);
    impact.textContent = `最近返工：${stages.find((stage) => stage.key === latest.from_stage)?.label || latest.from_stage} · 影响 ${impacted.join("、")} · ${latest.reason || "需要重新确认"}`;
  }
}

async function lockProductionStage(stageKey) {
  if (!state.projectId || !stageKey) return;
  try {
    await request(`/projects/${encodeURIComponent(state.projectId)}/workflow/stages/${encodeURIComponent(stageKey)}/lock`, {
      method: "POST",
      body: JSON.stringify({ actor: "studio-editor" }),
    });
    logEvent(`${stageKey} 阶段已锁定。`, "normal");
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  }
}

async function returnProductionStage(stageKey) {
  if (!state.projectId || !stageKey) return;
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/workflow/stages/${encodeURIComponent(stageKey)}/return`, {
      method: "POST",
      body: JSON.stringify({ actor: "studio-editor", reason: "工作台请求阶段返工" }),
    });
    const affected = result.impact?.affected_stages || [];
    logEvent(`已退回 ${stageKey}，影响 ${affected.length} 个阶段。`, "muted");
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  }
}

function renderCollaboration() {
  const collaboration = state.collaboration || {};
  const documentResponse = state.collaborationDocuments || {};
  const documents = documentResponse.documents || [];
  const members = collaboration.members || [];
  const comments = collaboration.comments || [];
  const presence = collaboration.presence || [];
  const locks = collaboration.edit_locks || [];
  $("collaborationMembers").textContent = String(members.length);
  $("collaborationComments").textContent = String(comments.length);
  $("collaborationPresence").textContent = String(presence.length);
  $("collaborationLocks").textContent = String(locks.length);
  $("collaborationCount").textContent = `${comments.length} 条评论 · ${documents.length} 份笔记`;
  const shotSelect = $("collaborationShotSelect");
  const currentValue = shotSelect.value;
  shotSelect.innerHTML = `<option value="">项目级评论</option>${(state.project?.shots || []).map((runtime) => `<option value="${escapeHtml(runtime.shot.shot_id)}">${escapeHtml(runtime.shot.shot_id)} · ${escapeHtml(runtime.shot.scene)}</option>`).join("")}`;
  if ([...shotSelect.options].some((option) => option.value === currentValue)) shotSelect.value = currentValue;
  const collaborationDisabled = !state.projectId || Boolean(state.project?.archived);
  $("collaborationCommentButton").disabled = collaborationDisabled;
  $("collaborationMemberButton").disabled = collaborationDisabled;
  $("collaborationPresenceStatus").disabled = collaborationDisabled;
  $("collaborationPresenceSection").disabled = collaborationDisabled;
  $("collaborationPresenceButton").disabled = collaborationDisabled;
  $("collaborationLockTarget").disabled = collaborationDisabled;
  $("collaborationLockButton").disabled = collaborationDisabled;
  $("collaborationDocumentInput").disabled = collaborationDisabled;
  $("collaborationDocumentId").disabled = collaborationDisabled;
  $("collaborationDocumentSaveButton").disabled = collaborationDisabled;
  const documentIdInput = $("collaborationDocumentId");
  const activeDocumentId = documentIdInput.value.trim().toLowerCase() || "production-notes";
  const activeDocument = documents.find((document) => document.document_id === activeDocumentId);
  const documentInput = $("collaborationDocumentInput");
  if (document.activeElement !== documentInput) {
    documentInput.value = activeDocument?.text || "";
  }
  $("collaborationDocumentList").innerHTML = documents.length
    ? documents.map((document) => `
      <div class="asset-row">
        <div><strong>${escapeHtml(document.document_id)}</strong><span>${document.operation_count} 次操作 · ${document.text.length} 个字符</span></div>
        <button class="button button-quiet" data-action="select-collaboration-document" data-document-id="${escapeHtml(document.document_id)}">打开</button>
      </div>
    `).join("")
    : `<div class="project-empty">创建第一份共享制作笔记。</div>`;
  $("collaborationPresenceList").innerHTML = presence.length
    ? presence.map((item) => `<div class="asset-row"><div><strong>${escapeHtml(item.subject)}</strong><span>${escapeHtml({ACTIVE: "在线", AWAY: "暂离", BUSY: "忙碌"}[item.status] || item.status)}${item.section ? ` · ${escapeHtml(item.section)}` : ""}</span></div><span class="mini-label">${escapeHtml(formatTimestamp(item.updated_at))}</span></div>`).join("")
    : `<div class="project-empty">当前没有活跃协作者。</div>`;
  $("collaborationLockList").innerHTML = locks.length
    ? locks.map((item) => `<div class="asset-row"><div><strong>${escapeHtml(item.target_type)} · ${escapeHtml(item.target_id)}</strong><span>${escapeHtml(item.subject)} · 截止 ${escapeHtml(formatTimestamp(item.expires_at))}</span></div><button class="button button-quiet" data-action="release-edit-lock" data-lock-id="${escapeHtml(item.lock_id)}">释放</button></div>`).join("")
    : `<div class="project-empty">没有正在占用的编辑锁。</div>`;
  $("collaborationMemberList").innerHTML = members.length
    ? members.map((member) => `
      <div class="asset-row">
        <div><strong>${escapeHtml(member.subject)}</strong><span>${escapeHtml(member.role)} · ${escapeHtml(formatTimestamp(member.updated_at || member.added_at))}</span></div>
        ${member.role === "owner" ? `<span class="job-state">所有者</span>` : `<button class="button button-quiet" data-action="remove-member" data-subject="${escapeHtml(member.subject)}">移除</button>`}
      </div>
    `).join("")
    : `<div class="project-empty">暂无项目成员。</div>`;
  $("collaborationCommentList").innerHTML = comments.length
    ? comments.slice(0, 30).map((comment) => `
      <div class="activity-item collaboration-comment">
        <span class="activity-dot"></span>
        <span><strong>${escapeHtml(comment.author)}${comment.shot_id ? ` · ${escapeHtml(comment.shot_id)}` : ""}</strong><br />${escapeHtml(comment.body)}<br /><small>${escapeHtml(formatTimestamp(comment.created_at))}</small></span>
        <button class="button button-quiet" data-action="remove-comment" data-comment-id="${escapeHtml(comment.comment_id)}">删除</button>
      </div>
    `).join("")
    : `<div class="activity-item activity-muted"><span class="activity-dot"></span><span>暂无评论。</span></div>`;
}

function renderTrace() {
  const trace = state.trace || {};
  const summary = trace.summary || {};
  const spans = trace.spans || [];
  const failed = Number(summary.failed_job_count || 0);
  $("traceCount").textContent = `${summary.span_count || spans.length || 0} 个追踪节点`;
  $("traceId").textContent = trace.trace_id || state.project?.trace_id || "-";
  $("traceDuration").textContent = formatDuration(summary.duration_ms);
  $("traceCost").textContent = formatCurrency(summary.estimated_cost);
  $("traceFailed").textContent = String(failed);
  const exportVisible = Boolean(state.traceUrl);
  $("traceLink").href = exportVisible ? state.traceUrl : "#";
  $("traceLink").hidden = !exportVisible;
  if (!spans.length) {
    $("traceList").innerHTML = `<div class="project-empty">完成首次项目操作后，这里会显示调用链。</div>`;
    return;
  }
  $("traceList").innerHTML = spans.slice().reverse().slice(0, 18).map((span) => {
    const statusClass = span.status === "ERROR" ? "is-failed" : (
      span.status === "WAITING" ? "is-waiting" : ""
    );
    const target = span.shot_id || span.job_id || span.artifact_id || "project";
    return `
      <div class="trace-row ${statusClass}">
        <div>
          <strong>${escapeHtml(spanNameLabel(span.name || span.kind))}</strong>
          <span>${escapeHtml(span.kind === "job" ? "任务" : span.kind === "artifact" ? "资产" : span.kind === "audit" ? "审计" : "项目")} · ${escapeHtml(target)}</span>
          ${span.attributes?.message ? `<span>${escapeHtml(localizeAuditMessage(span.attributes.message))}</span>` : ""}
        </div>
        <div class="trace-meta">
          <span class="job-state">${escapeHtml(labelFor(span.status || "PASS"))}</span>
          <span>${formatDuration(span.duration_ms)}</span>
        </div>
      </div>
    `;
  }).join("");
}

function renderRetrospective() {
  const report = state.retrospective || {};
  const production = report.metrics?.production || {};
  const cost = report.metrics?.cost || {};
  const insights = report.insights || [];
  const nextActions = report.next_actions || [];
  const milestones = report.timeline?.milestones || [];
  const maturity = Number(report.maturity_percent || 0);
  const approval = Number(production.approval_rate || 0) * 100;
  const quality = Number(production.quality_pass_rate || 0) * 100;
  $("retrospectiveMaturity").textContent = `${Math.round(maturity)}%`;
  $("retrospectiveApproval").textContent = `${Math.round(approval)}%`;
  $("retrospectiveQuality").textContent = `${Math.round(quality)}%`;
  $("retrospectiveRevisions").textContent = String(production.revision_count || 0);
  $("retrospectiveCostPerShot").textContent = formatCurrency(cost.cost_per_approved_shot);
  $("retrospectiveActionCount").textContent = `${nextActions.length} 项待处理`;
  $("retrospectiveInsightCount").textContent = `${insights.length} 条发现`;
  $("retrospectiveElapsed").textContent = formatDuration(Number(report.timeline?.elapsed_seconds || 0) * 1000);

  const severityClass = (severity) => severity === "critical"
    ? "is-failed"
    : severity === "warning" ? "is-waiting" : "";
  $("retrospectiveNextActions").innerHTML = nextActions.length
    ? nextActions.map((action) => `
      <div class="policy-row retrospective-action ${severityClass("warning")}">
        <span class="policy-dot"></span>
        <div><strong>待处理</strong><span>${escapeHtml(localizeRetrospectiveText(action))}</span></div>
      </div>
    `).join("")
    : `<div class="project-empty">暂无阻塞项，本项目可以作为下一次生产的基线。</div>`;
  $("retrospectiveInsights").innerHTML = insights.length
    ? insights.map((insight) => `
      <div class="policy-row ${severityClass(insight.severity)}">
        <span class="policy-dot"></span>
        <div>
          <strong>${escapeHtml(areaLabel(insight.area))} · ${escapeHtml(labelFor(String(insight.severity || "info").toUpperCase()))}</strong>
          <span>${escapeHtml(localizeRetrospectiveText(insight.finding || "暂无发现"))}</span>
          <span>建议：${escapeHtml(localizeRetrospectiveText(insight.recommendation || "暂无建议"))}</span>
        </div>
      </div>
    `).join("")
    : `<div class="project-empty">项目进入生产流程后，这里会显示复盘洞察。</div>`;
  $("retrospectiveMilestones").innerHTML = milestones.length
    ? milestones.map((milestone) => `
      <div class="activity-item">
        <span class="activity-dot"></span>
        <span><strong>${escapeHtml(milestoneLabel(milestone))}</strong> · ${escapeHtml(milestone.actor || "系统")} · ${escapeHtml(formatTimestamp(milestone.at))}</span>
      </div>
    `).join("")
    : `<div class="activity-item activity-muted"><span class="activity-dot"></span><span>暂无项目里程碑。</span></div>`;
  const exportVisible = Boolean(state.retrospectiveUrl);
  $("retrospectiveLink").href = exportVisible ? state.retrospectiveUrl : "#";
  $("retrospectiveLink").hidden = !exportVisible;
  const dataset = state.dataset || {};
  $("datasetStatus").textContent = dataset.ready
    ? `已导出 ${dataset.record_count || 0} 条记录`
    : `可导出 ${dataset.eligible_record_count || 0} 条审核通过记录`;
  $("datasetExportButton").disabled = !state.projectId || Boolean(state.project?.archived);
}

async function exportTrainingDataset() {
  if (!state.projectId) return;
  const button = $("datasetExportButton");
  setBusy(button, true, "导出中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/training-dataset/export`, {method: "POST", body: JSON.stringify({actor: "dataset-ops"})});
    state.dataset = result;
    $("datasetStatus").textContent = `已导出 ${result.record_count || 0} 条记录`;
    logEvent($("datasetStatus").textContent, "muted");
    await loadProjectContext(state.projectId);
    setActiveTab("retrospective");
  } catch (error) {
    alert(error.message);
  } finally {
    setBusy(button, false);
    renderRetrospective();
  }
}

async function loadOverview() {
  const suffix = state.includeArchived ? "?include_archived=true" : "";
  [state.overview, state.studioMetrics, state.productionReadiness, state.operationsAlerts, state.enterpriseStatus, state.billingSummary] = await Promise.all([
    request(`/studio/overview${suffix}`),
    request(`/studio/metrics${suffix}`),
    request("/ops/readiness"),
    request("/ops/alerts"),
    request("/enterprise/status"),
    request("/billing/summary"),
  ]);
  renderOverview();
}

async function loadProjectList() {
  const suffix = state.includeArchived ? "?include_archived=true" : "";
  const response = await request(`/projects${suffix}`);
  state.projects = (response.projects || []).map(normalizeProjectSummary);
  renderProjectList();
  await loadOverview();
}

function waitFor(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function handleProjectEvent(projectId, id, eventName, data) {
  if (state.eventStreamProjectId !== projectId) return;
  if (id) state.eventCursor = Math.max(state.eventCursor, Number(id) || state.eventCursor);
  if (eventName !== "audit" || !data) return;
  try {
    const payload = JSON.parse(data);
    if (payload.action) logEvent(auditEventText(payload), "muted");
    loadProjectContext(projectId).catch((error) => logEvent(error.message, "muted"));
  } catch (error) {
    logEvent(`实时事件解析失败：${error.message}`, "muted");
  }
}

function consumeSseBlock(projectId, block) {
  const fields = {};
  block.split(/\r?\n/).forEach((line) => {
    if (!line || line.startsWith(":")) return;
    const separator = line.indexOf(":");
    const key = separator < 0 ? line : line.slice(0, separator);
    const value = separator < 0 ? "" : line.slice(separator + 1).trimStart();
    fields[key] = fields[key] ? `${fields[key]}\n${value}` : value;
  });
  handleProjectEvent(projectId, fields.id, fields.event || "message", fields.data || "");
  return fields.event === "close";
}

async function runProjectEventStream(projectId, controller) {
  while (
    state.eventStreamAbortController === controller
    && state.eventStreamProjectId === projectId
    && !controller.signal.aborted
  ) {
    try {
      const token = window.localStorage.getItem("mediaforge.apiToken") || "";
      const response = await fetch(
        `/projects/${encodeURIComponent(projectId)}/events?after=${state.eventCursor}&timeout_seconds=25`,
        {
          headers: {
            Accept: "text/event-stream",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          signal: controller.signal,
        }
      );
      if (!response.ok || !response.body) {
        throw new Error(`实时事件请求失败（${response.status}）`);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let shouldReconnect = true;
      while (!controller.signal.aborted) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const blocks = buffer.split(/\r?\n\r?\n/);
        buffer = blocks.pop() || "";
        for (const block of blocks) {
          if (consumeSseBlock(projectId, block)) shouldReconnect = true;
        }
        if (done) {
          if (buffer.trim()) consumeSseBlock(projectId, buffer);
          break;
        }
      }
      if (!shouldReconnect || controller.signal.aborted) break;
    } catch (error) {
      if (controller.signal.aborted) break;
      logEvent(`实时事件连接中断：${error.message}`, "muted");
    }
    if (!controller.signal.aborted) await waitFor(1000);
  }
  if (state.eventStreamAbortController === controller) {
    state.eventStreamRunning = false;
  }
}

function connectProjectEvents(projectId, initialCursor = 0) {
  if (state.eventStreamRunning && state.eventStreamProjectId === projectId) return;
  if (state.eventStreamAbortController) state.eventStreamAbortController.abort();
  state.eventStreamAbortController = null;
  state.eventStreamProjectId = projectId;
  state.eventCursor = initialCursor;
  if (!projectId || !window.fetch || !window.ReadableStream) return;
  const controller = new AbortController();
  state.eventStreamAbortController = controller;
  state.eventStreamRunning = true;
  runProjectEventStream(projectId, controller).catch((error) => {
    if (!controller.signal.aborted) logEvent(`实时事件读取失败：${error.message}`, "muted");
  });
}

function setCollaborationRealtimeStatus(text) {
  const node = $("collaborationRealtimeStatus");
  if (node) node.textContent = text;
}

function disconnectCollaborationEvents() {
  if (state.collaborationSocketRetryTimer) {
    window.clearTimeout(state.collaborationSocketRetryTimer);
    state.collaborationSocketRetryTimer = null;
  }
  const socket = state.collaborationSocket;
  state.collaborationSocket = null;
  state.collaborationSocketProjectId = null;
  if (socket && socket.readyState === WebSocket.OPEN) socket.close(1000, "project changed");
  else if (socket && socket.readyState === WebSocket.CONNECTING) socket.close();
}

async function refreshCollaborationData(projectId) {
  if (!projectId || state.projectId !== projectId) return;
  const [collaboration, collaborationDocuments] = await Promise.all([
    request(`/projects/${encodeURIComponent(projectId)}/collaboration`),
    request(`/projects/${encodeURIComponent(projectId)}/collaboration/documents`),
  ]);
  if (state.projectId !== projectId) return;
  state.collaboration = collaboration;
  state.collaborationDocuments = collaborationDocuments;
  renderCollaboration();
}

function scheduleCollaborationRefresh(projectId) {
  if (state.collaborationRefreshTimer) return;
  state.collaborationRefreshTimer = window.setTimeout(() => {
    state.collaborationRefreshTimer = null;
    refreshCollaborationData(projectId).catch((error) => {
      setCollaborationRealtimeStatus("同步暂不可用");
      logEvent(`协作同步失败：${error.message}`, "muted");
    });
  }, 120);
}

function connectCollaborationEvents(projectId) {
  if (!projectId || !window.WebSocket) {
    setCollaborationRealtimeStatus("轮询同步");
    return;
  }
  if (
    state.collaborationSocketProjectId === projectId
    && state.collaborationSocket
    && [WebSocket.OPEN, WebSocket.CONNECTING].includes(state.collaborationSocket.readyState)
  ) return;
  disconnectCollaborationEvents();
  const cursor = Number(state.collaborationDocuments?.event_cursor || state.collaboration?.event_cursor || 0);
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(
    `${protocol}://${window.location.host}/projects/${encodeURIComponent(projectId)}/collaboration/events/ws?after=${Math.max(0, cursor)}`
  );
  state.collaborationSocket = socket;
  state.collaborationSocketProjectId = projectId;
  setCollaborationRealtimeStatus("正在连接");
  socket.addEventListener("open", () => {
    if (state.collaborationSocket !== socket) return;
    state.collaborationSocketRetries = 0;
    const token = window.localStorage.getItem("mediaforge.apiToken") || "";
    if (token) socket.send(JSON.stringify({ type: "authenticate", token }));
    setCollaborationRealtimeStatus("实时同步已连接");
  });
  socket.addEventListener("message", (event) => {
    if (state.collaborationSocket !== socket || state.projectId !== projectId) return;
    try {
      const message = JSON.parse(event.data);
      if (message.type === "snapshot") {
        state.collaboration = message.data?.collaboration || state.collaboration;
        state.collaborationDocuments = message.data?.documents || state.collaborationDocuments;
        renderCollaboration();
      } else if (message.type === "event") {
        scheduleCollaborationRefresh(projectId);
      }
    } catch (error) {
      logEvent(`协作实时消息解析失败：${error.message}`, "muted");
    }
  });
  socket.addEventListener("close", (event) => {
    if (state.collaborationSocket !== socket) return;
    state.collaborationSocket = null;
    setCollaborationRealtimeStatus("实时连接已断开");
    const retriable = ![1000, 4401, 4403, 4404].includes(event.code);
    if (retriable && state.projectId === projectId && state.collaborationSocketRetries < 3) {
      state.collaborationSocketRetries += 1;
      state.collaborationSocketRetryTimer = window.setTimeout(
        () => connectCollaborationEvents(projectId),
        900 * state.collaborationSocketRetries,
      );
    }
  });
  socket.addEventListener("error", () => setCollaborationRealtimeStatus("实时连接异常"));
}

async function loadProjectContext(projectId) {
  if (!projectId) return;
  const generation = ++state.projectGeneration;
  const identityGeneration = state.identityGeneration;
  const switchingProject = state.projectId !== projectId;
  if (switchingProject) {
    state.narrativeEventEditingId = null;
    state.sourceChapterEditingId = null;
    state.providerContract = null;
  }
  const [project, cost, jobs, audit, operations, assets, evaluation, llmops, editTimeline, benchmark, routes, compliance, continuity, distribution, trace, retrospective, collaboration, collaborationDocuments, dataset, sourceOcrStatus] = await Promise.all([
    request(`/projects/${encodeURIComponent(projectId)}`),
    request(`/projects/${encodeURIComponent(projectId)}/cost`),
    request(`/projects/${encodeURIComponent(projectId)}/jobs`),
    request(`/projects/${encodeURIComponent(projectId)}/audit`),
    request(`/projects/${encodeURIComponent(projectId)}/operations`),
    request(`/projects/${encodeURIComponent(projectId)}/assets`),
    request(`/projects/${encodeURIComponent(projectId)}/evaluations/latest`),
    request(`/projects/${encodeURIComponent(projectId)}/llmops`),
    request(`/projects/${encodeURIComponent(projectId)}/timeline`),
    request(`/projects/${encodeURIComponent(projectId)}/providers/benchmark`),
    request(`/projects/${encodeURIComponent(projectId)}/routes`),
    request(`/projects/${encodeURIComponent(projectId)}/compliance`),
    request(`/projects/${encodeURIComponent(projectId)}/continuity`),
    request(`/projects/${encodeURIComponent(projectId)}/distribution`),
    request(`/projects/${encodeURIComponent(projectId)}/trace`),
    request(`/projects/${encodeURIComponent(projectId)}/retrospective`),
    request(`/projects/${encodeURIComponent(projectId)}/collaboration`),
    request(`/projects/${encodeURIComponent(projectId)}/collaboration/documents`),
    request(`/projects/${encodeURIComponent(projectId)}/training-dataset`),
    request("/source-ingest/status"),
  ]);
  if (generation !== state.projectGeneration || identityGeneration !== state.identityGeneration) return;
  state.project = project;
  state.cost = cost;
  state.jobs = jobs.jobs || [];
  state.audit = audit.events || [];
  state.auditIntegrity = audit.integrity || null;
  state.auditAnchors = audit.anchors || null;
  state.operations = operations;
  state.workflow = operations.workflow || project.production_workflow || null;
  state.assets = assets;
  state.evaluation = evaluation;
  state.llmops = llmops;
  state.editTimeline = editTimeline;
  state.benchmark = benchmark;
  state.routes = routes;
  state.compliance = compliance;
  state.continuity = continuity;
  state.distribution = distribution;
  state.trace = trace;
  state.retrospective = retrospective;
  state.collaboration = collaboration;
  state.collaborationDocuments = collaborationDocuments;
  state.dataset = dataset;
  state.sourceOcrStatus = sourceOcrStatus;
  state.projectId = projectId;
  syncDialogueDraft(project, switchingProject || !state.dialogueDirty);
  connectProjectEvents(projectId, state.audit.length);
  connectCollaborationEvents(projectId);
  if (switchingProject) {
    resetPlanningPanel();
    state.memoryProjectId = null;
    state.memorySourceIds = [];
    state.memoryGeneration += 1;
    $("memoryResults").replaceChildren();
    $("memorySourceList").replaceChildren();
    $("memoryCitations").replaceChildren();
    $("memoryResultCount").textContent = "0 条";
    state.pendingAudioFile = null;
    $("audioFileInput").value = "";
    state.pendingSourceDocumentFile = null;
    $("sourceDocumentFileInput").value = "";
    state.auditExportUrl = "";
    state.auditCsvUrl = "";
    state.auditIntegrityUrl = "";
    state.auditAnchorUrl = "";
    state.snapshotUrl = "";
    state.reportUrl = "";
    state.provenanceUrl = "";
    state.complianceUrl = "";
    state.continuityUrl = "";
    state.distributionUrl = "";
    state.traceUrl = "";
    state.retrospectiveUrl = "";
    state.acceptanceUrl = "";
    state.closeoutUrl = "";
    state.archivePackageUrl = "";
    state.archiveVerificationUrl = "";
    state.deliveryReceiptUrl = "";
    state.routeUrl = "";
    state.routeShotId = "";
    state.comparisonUrl = "";
    state.evaluationUrl = "";
    state.timelineExportUrl = "";
    state.benchmarkUrl = "";
    state.verificationUrl = "";
  }
  state.selectedShotId = (state.project.shots || []).some(
    (shot) => shot.shot.shot_id === state.selectedShotId
  ) ? state.selectedShotId : null;
  fillBriefFromProject(project);
  const summary = normalizeProjectSummary({
    project_id: project.project_id,
    title: project.brief.title,
    status: project.status,
    archived: project.archived,
    archived_at: project.archived_at,
    created_at: project.created_at,
    shot_count: project.shots?.length || 0,
    approved_shots: (project.shots || []).filter(
      (shot) => shot.review_status === "APPROVED"
    ).length,
    spent: cost.spent,
    budget: cost.budget,
    final_mp4: project.final_mp4,
    subtitle_srt: project.subtitle_srt,
    audio_track: project.audio_track,
    delivery_package: project.delivery_package,
    delivery_verified: project.delivery_verified,
    delivery_verification_report: project.delivery_verification_report,
    provenance_report: project.provenance_report,
    compliance_passed: project.compliance_passed,
    compliance_report: project.compliance_report,
    continuity_passed: project.continuity_passed,
    continuity_report: project.continuity_report,
    delivery_count: project.delivery_count,
    latest_delivery: project.latest_delivery,
    distribution_report: project.distribution_report,
    acceptance_report: project.acceptance_report,
    closeout_report: project.closeout_report,
    archive_package: project.archive_package,
    archive_verified: project.archive_verified,
    archive_verification_report: project.archive_verification_report,
    released: Boolean(project.release),
  });
  state.projects = [
    summary,
    ...state.projects.filter((item) => item.project_id !== projectId),
  ].sort((left, right) => String(right.created_at).localeCompare(String(left.created_at)));
  renderProjectList();
  renderProject();
  renderDetailPanels();
  if (state.activeTab === "memory") await loadMemorySources();
  await loadPlanningPanel();
}

function setActiveTab(tabName) {
  state.activeTab = tabName;
  document.querySelectorAll("[data-panel-tab]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.panelTab === tabName);
  });
  const views = {
    inspector: "inspectorTabView",
    jobs: "jobsTabView",
    assets: "assetsTabView",
    policy: "policyTabView",
    continuity: "continuityTabView",
    eval: "evalTabView",
    route: "routeTabView",
    ab: "abTabView",
    cost: "costTabView",
    audit: "auditTabView",
    trace: "traceTabView",
  retrospective: "retrospectiveTabView",
    collaboration: "collaborationTabView",
    providers: "providersTabView",
    memory: "memoryTabView",
    source: "sourceTabView",
    canvas: "canvasTabView",
    narrative: "narrativeTabView",
    script: "scriptTabView",
    planning: "planningTabView",
    enterprise: "enterpriseTabView",
  };
  Object.entries(views).forEach(([name, id]) => {
    $(id).hidden = name !== tabName;
  });
  if (tabName === "memory") loadMemorySources();
  if (tabName === "enterprise") loadEnterprisePanel();
  if (tabName === "planning") loadPlanningPanel();
}

const planningStatusLabel = (value) => ({RUNNING: "执行中", INTERRUPTED: "执行中断", FAILED: "阶段失败", AWAITING_REVIEW: "等待人工确认", READY_TO_APPLY: "待应用", APPROVED: "已应用", REJECTED: "已退回", CANCELED: "已取消"}[value] || value);
const planningStageLabel = (value) => ({retrieve: "故事检索", story: "故事与角色", storyboard: "分镜", compile: "参数与策略校验", review: "人工确认"}[value] || value);

function resetPlanningPanel() {
  state.planningGeneration += 1;
  state.planningReport = null;
  state.planningRunId = null;
  state.planningBusy = false;
  state.planningPermissions = {};
  ["planningDraft", "planningTrace", "planningStatus", "planningRunSelect", "planningError"].forEach((id) => $(id).replaceChildren());
  $("planningComment").value = "";
  $("planningMode").textContent = "未加载";
  $("planningError").hidden = true;
  renderPlanningControls();
}

function selectedPlanningRun() {
  return state.planningReport?.runs.find((run) => run.run_id === state.planningRunId);
}

function activePlanningRun() {
  return state.planningReport?.runs.find((run) => !["APPROVED", "REJECTED", "CANCELED"].includes(run.status));
}

function renderPlanningControls() {
  const run = selectedPlanningRun();
  const settings = state.planningReport?.settings || {};
  const active = (state.planningReport?.runs || []).some((item) => !["APPROVED", "REJECTED", "CANCELED"].includes(item.status));
  const available = settings.configured && !state.planningBusy;
  $("planningStart").disabled = !available || !state.planningPermissions.edit || active || !!state.project?.archived || !!state.project?.release || state.jobs.length > 0;
  $("planningApprove").disabled = !available || !state.planningPermissions.review || !["AWAITING_REVIEW", "READY_TO_APPLY"].includes(run?.status);
  const revisable = run?.status === "AWAITING_REVIEW" || (run?.status === "FAILED" && run?.story_bible);
  $("planningRevise").disabled = !available || !state.planningPermissions.review || settings.model_mode !== "external" || !revisable || run?.revision >= 3 || !$("planningComment").value.trim();
  $("planningReject").disabled = !available || !state.planningPermissions.review || run?.status !== "AWAITING_REVIEW";
  $("planningResume").disabled = !available || !state.planningPermissions.edit || !["FAILED", "INTERRUPTED"].includes(run?.status);
  $("planningMigrate").disabled = !available || !state.planningPermissions.edit || !run?.migration_required;
  $("planningCancel").disabled = !available || !state.planningPermissions.edit || !run || ["RUNNING", "APPROVED", "REJECTED", "CANCELED"].includes(run.status);
  $("planningRunSelect").disabled = state.planningBusy || !(state.planningReport?.runs.length);
}

function renderPlanningRun() {
  const run = selectedPlanningRun();
  $("planningStatus").textContent = run ? `${planningStatusLabel(run.status)} · 修订 ${run.revision}${run.migration_required ? " · 需要迁移" : ""}` : "暂无规划记录";
  $("planningError").hidden = !run?.error;
  $("planningError").textContent = run?.error ? localizeErrorMessage(run.error) : "";
  $("planningTrace").innerHTML = (run?.trace || []).map((event) => runtimeRow(planningStageLabel(event.stage), event.decision ? ({approve: "通过", revise: "修订", reject: "退回"}[event.decision] || event.decision) : "已完成", `修订 ${event.revision} · ${event.duration_ms} 毫秒${event.comment ? ` · ${event.comment}` : ""}`)).join("");
  const bible = run?.story_bible;
  $("planningDraft").innerHTML = (bible ? `<div class="provider-block"><strong>${escapeHtml(bible.theme)}</strong><p>${escapeHtml(bible.logline)}</p>${bible.characters.map((character) => runtimeRow(character.name, character.role)).join("")}</div>` : "")
    + (run?.shots || []).map((shot, index) => `<div class="policy-row"><strong>${index + 1}. ${escapeHtml(shot.scene)} · ${shot.duration_seconds} 秒</strong><p>${escapeHtml(shot.description)}</p>${run.specs[index] ? `<details><summary>生成参数</summary><pre>${escapeHtml(JSON.stringify(run.specs[index], null, 2))}</pre></details>` : ""}</div>`).join("");
  renderPlanningControls();
}

async function loadPlanningPanel() {
  const projectId = state.projectId;
  if (!projectId) return;
  const generation = ++state.planningGeneration;
  const identityGeneration = state.identityGeneration;
  try {
    const [report, me] = await Promise.all([request(`/projects/${encodeURIComponent(projectId)}/planning`), request("/auth/me")]);
    if (projectId !== state.projectId || generation !== state.planningGeneration || identityGeneration !== state.identityGeneration) return;
    state.planningReport = report;
    const member = state.collaboration?.members?.find((item) => item.subject === me.subject);
    state.planningPermissions = {
      edit: me.role === "admin" || (["editor", "publisher"].includes(me.role) && ["editor", "publisher", "owner"].includes(member?.role)),
      review: me.role === "admin" || (["reviewer", "editor", "publisher"].includes(me.role) && ["reviewer", "editor", "publisher", "owner"].includes(member?.role)),
    };
    if (!report.runs.some((run) => run.run_id === state.planningRunId)) state.planningRunId = report.runs[0]?.run_id || null;
    $("planningMode").textContent = report.settings.configured ? `LangGraph · ${report.settings.model_mode === "external" ? "模型分阶段规划" : "本地确定性规划"}` : "分步规划未启用";
    $("planningRunSelect").innerHTML = report.runs.map((run) => `<option value="${escapeHtml(run.run_id)}" ${run.run_id === state.planningRunId ? "selected" : ""}>${new Date(run.created_at * 1000).toLocaleString("zh-CN")} · ${escapeHtml(planningStatusLabel(run.status))}${run.migration_required ? " · 需要迁移" : ""}</option>`).join("");
    renderPlanningRun();
    renderProject();
    renderDetailPanels();
  } catch (error) {
    if (generation !== state.planningGeneration || identityGeneration !== state.identityGeneration) return;
    $("planningError").textContent = error.message;
    $("planningError").hidden = false;
    state.planningReport = null;
    renderPlanningControls();
  }
}

async function performPlanningAction(action) {
  const projectId = state.projectId;
  const runId = state.planningRunId;
  const identityGeneration = state.identityGeneration;
  if (!projectId || state.planningBusy) return;
  state.planningBusy = true;
  renderPlanningControls();
  renderProject();
  renderDetailPanels();
  $("planningStatus").textContent = "执行中";
  try {
    const prefix = `/projects/${encodeURIComponent(projectId)}/planning`;
    let url = prefix;
    let body = {memory_project_ids: state.memoryProjectId === projectId ? state.memorySourceIds : null};
    if (action !== "start") {
      if (!runId) return;
      const reviewing = ["approve", "revise", "reject"].includes(action);
      url += `/${encodeURIComponent(runId)}/${reviewing ? "review" : action}`;
      body = reviewing ? {decision: action, comment: $("planningComment").value.trim()} : {};
    }
    const run = await request(url, {method: "POST", body: JSON.stringify(body)});
    if (projectId !== state.projectId || identityGeneration !== state.identityGeneration) return;
    state.planningRunId = run.run_id;
    $("planningComment").value = "";
    if (run.status === "APPROVED") await loadProjectContext(projectId);
    await loadPlanningPanel();
  } catch (error) {
    if (projectId !== state.projectId || identityGeneration !== state.identityGeneration) return;
    await loadPlanningPanel();
    $("planningError").textContent = error.message;
    $("planningError").hidden = false;
  } finally {
    if (projectId === state.projectId && identityGeneration === state.identityGeneration) {
      state.planningBusy = false;
      renderPlanningControls();
      renderProject();
      renderDetailPanels();
    }
  }
}

function runtimeRow(title, value, detail = "") {
  return `<div class="runtime-row"><strong>${escapeHtml(title)} · ${escapeHtml(value)}</strong><span>${escapeHtml(detail)}</span></div>`;
}

async function loadEnterprisePanel() {
  const identityGeneration = state.identityGeneration;
  $("enterpriseRuntime").textContent = "读取中";
  $("enterpriseProbe").disabled = true;
  $("planningProbe").disabled = true;
  $("memoryProbe").disabled = true;
  try {
    const [runtime, summary, settlements, me, quality, planning, stripeSettlement] = await Promise.all([
      request("/enterprise/status"), request("/billing/summary"), request("/billing/settlements/summary"), request("/auth/me"), request("/quality/status"), request("/planning/status"), request("/billing/settlements/stripe/status"),
    ]);
    if (identityGeneration !== state.identityGeneration) return;
    state.enterpriseStatus = runtime;
    state.billingSummary = summary;
    state.settlementSummary = settlements;
    $("enterpriseProbe").disabled = me.role !== "admin";
    $("planningProbe").disabled = me.role !== "admin" || !planning.enabled;
    $("memoryProbe").disabled = me.role !== "admin";
    $("memoryRuntime").innerHTML = memoryRuntimeRows(runtime.story_memory || {});
    const labels = {identity: "身份认证", database: "状态存储", queue: "任务索引", storage: "交付归档", rate_limit: "请求限流"};
    const modes = {disabled: "未启用", required: "令牌认证", oidc: "企业身份", filesystem: "本地文件", local: "本地", json: "JSON 文件", sqlite: "SQLite", postgres: "PostgreSQL", redis: "Redis", s3: "S3 对象存储"};
    const planningBackend = planning.checkpoint_backend === "postgres" ? "共享 PostgreSQL" : "本地 SQLite";
    const siem = runtime.siem || {};
    const auditAnchor = runtime.audit_anchor || {};
    const planningDetail = !planning.enabled
      ? "分步规划未启用"
      : planning.checkpoint_backend === "postgres"
        ? (planning.checkpoint_connectivity_verified ? "已通过显式连接检查" : "等待管理员检查")
        : "本地持久化";
    $("enterpriseRuntime").innerHTML = Object.entries(labels).map(([key, label]) => {
      const adapter = runtime[key] || {};
      const mode = adapter.backend || adapter.mode || "未知";
      return runtimeRow(label, modes[mode] || mode, adapter.configured === false ? "未配置" : "配置已读取");
    }).join("") + runtimeRow("规划 Checkpoint", planningBackend, planningDetail)
      + runtimeRow("部署拓扑", "单活动控制面", "远程 Worker 通过 API 调度服务商")
      + runtimeRow("本地画面检测", quality.visual_gate ? "阻断门禁" : "辅助审核", quality.local_visual || "未配置")
      + runtimeRow("外部视觉评估", quality.enabled ? "已启用" : "未启用", quality.fail_open ? "故障策略：保留本地结果" : "故障策略：阻断")
      + runtimeRow("Stripe 结算回调", stripeSettlement.configured ? "已启用" : "未启用", stripeSettlement.configured ? `事件：${(stripeSettlement.allowed_event_types || []).join("、")}` : "需要端点密钥和显式事件白名单")
      + runtimeRow("SIEM 审计投递", siem.configured ? "已启用" : "未启用", siem.configured ? `${siem.endpoint_count || 0} 个端点 · 待投递 ${siem.pending || 0}` : "需要 HTTPS 地址与显式域名白名单")
      + runtimeRow("审计链外部锚定", auditAnchor.configured ? (auditAnchor.ready ? "配置可用" : "待配置") : "未启用", auditAnchor.configured ? `${auditAnchor.mode || "未知"} · 保留 ${auditAnchor.retention_days || 0} 天` : "可接入 Object Lock 或签名 HTTP 公证服务");
    $("billingTotals").innerHTML = Object.entries(summary.by_currency || {}).map(([currency, total]) =>
      runtimeRow(currency, Number(total.amount).toFixed(6), Object.entries(total.by_category || {}).map(([key, value]) => `${billingCategoryLabel(key)}: ${Number(value).toFixed(6)}`).join(" · "))
    ).join("") || `<div class="project-empty">暂无用量记录</div>`;
    $("settlementTotals").innerHTML = Object.entries(settlements.by_currency || {}).map(([currency, total]) =>
      runtimeRow(currency, Number(total.amount).toFixed(2), Object.entries(total.by_status || {}).map(([key, value]) => `${settlementStatusLabel(key)}：${Number(value).toFixed(2)}`).join(" · "))
    ).join("") || `<div class="project-empty">暂无结算记录</div>`;
    await loadSettlements();
  } catch (error) {
    if (identityGeneration !== state.identityGeneration) return;
    $("enterpriseRuntime").textContent = error.message;
    $("billingTotals").replaceChildren();
    $("settlementTotals").replaceChildren();
    $("settlementEvents").replaceChildren();
  }
  await loadBillingEvents();
}

async function loadSettlements() {
  try {
    const result = await request("/billing/settlements?limit=20");
    $("settlementEvents").innerHTML = result.settlements.map((settlement) => runtimeRow(
      settlementStatusLabel(settlement.status), `${settlement.currency} ${Number(settlement.amount).toFixed(2)}`,
      `${settlement.provider} · ${settlement.external_id || "无外部流水号"} · ${new Date(settlement.occurred_at * 1000).toLocaleString("zh-CN")}`,
    )).join("") || `<div class="project-empty">暂无结算记录</div>`;
  } catch (error) {
    $("settlementEvents").textContent = error.message;
  }
}

function settlementStatusLabel(status) {
  return {pending: "待结算", paid: "已支付", failed: "失败", refunded: "已退款", void: "已作废"}[status] || status;
}

async function probeEnterprise() {
  const button = $("enterpriseProbe");
  setBusy(button, true, "检查中");
  $("enterpriseProbeResult").textContent = "连接检查中";
  try {
    const result = await request("/enterprise/probe", {method: "POST"});
    const names = {storage: "交付归档", database: "状态存储", queue: "任务索引"};
    $("enterpriseProbeResult").textContent = (result.checks || []).map((check) => `${names[check.name] || check.name}：${check.passed ? "连接正常" : "连接失败"}`).join(" · ");
  } catch (error) {
    $("enterpriseProbeResult").textContent = error.message;
  } finally {
    setBusy(button, false);
  }
}

async function probePlanning() {
  const button = $("planningProbe");
  setBusy(button, true, "检查中");
  $("planningProbeResult").textContent = "检查中";
  try {
    const result = await request("/planning/probe", {method: "POST"});
    $("planningProbeResult").textContent = result.reachable
      ? "规划存储连接正常"
      : "规划存储连接失败，请检查数据库、角色和 Checkpoint 表权限";
    await loadEnterprisePanel();
  } catch (error) {
    $("planningProbeResult").textContent = error.message;
  } finally {
    setBusy(button, false);
  }
}

function memoryRuntimeRows(settings) {
  if (settings.backend === "ragflow-retrieval-api") {
    return runtimeRow("检索引擎", "RAGFlow 检索 API", `${settings.dataset_count || 0} 个受控数据集`)
      + runtimeRow("文本发送授权", settings.allow_data_export ? "已授权" : "未授权")
      + runtimeRow("批准快照同步", settings.allow_write_sync ? "可手动同步" : "只读")
      + runtimeRow("最近连接结果", settings.connectivity_verified ? "已验证" : "未验证");
  }
  if (settings.backend !== "postgres-pgvector") return runtimeRow("检索引擎", "SQLite 关键词", "FTS5 / BM25");
  return runtimeRow("检索引擎", "PostgreSQL 语义检索", `精确余弦 · 阈值 ${settings.min_similarity}`)
    + runtimeRow("嵌入模型", settings.embedding_model, `${settings.embedding_dimensions} 维 · ${settings.embedding_revision}`)
    + runtimeRow("文本发送授权", settings.allow_data_export ? "已授权" : "未授权")
    + runtimeRow("最近连接结果", settings.connectivity_verified && settings.embedding_verified ? "已验证" : "未验证");
}

function setRagflowSyncControl(settings) {
  const button = $("memoryRagflowSync");
  const available = Boolean(
    state.projectId
    && settings?.backend === "ragflow-retrieval-api"
    && settings.allow_write_sync,
  );
  button.hidden = !available;
  button.disabled = !available;
}

async function probeMemory() {
  const generation = state.identityGeneration;
  const button = $("memoryProbe");
  setBusy(button, true, "检查中");
  $("memoryProbeResult").textContent = "检查中";
  try {
    const result = await request("/enterprise/memory/probe", {method: "POST"});
    if (generation !== state.identityGeneration) return;
    $("memoryRuntime").innerHTML = memoryRuntimeRows(result.runtime || {});
    $("memoryProbeResult").textContent = result.reachable ? "检索连接正常" : `检索连接失败：${localizeErrorMessage(result.error || "服务不可用")}`;
  } catch (error) {
    if (generation === state.identityGeneration) $("memoryProbeResult").textContent = error.message;
  } finally {
    if (generation === state.identityGeneration) setBusy(button, false);
  }
}

async function loadBillingEvents() {
  const generation = ++state.billingGeneration;
  state.billingPage = null;
  ["billingPrevious", "billingNext", "billingDownload"].forEach((id) => $(id).disabled = true);
  $("billingEvents").textContent = "查询中";
  const query = new URLSearchParams({limit: "20", offset: String(state.billingOffset)});
  for (const [id, parameter] of [["billingProject", "project_id"], ["billingCategory", "category"], ["billingCurrency", "currency"]]) {
    const value = $(id).value.trim();
    if (value) query.set(parameter, value);
  }
  for (const [id, parameter] of [["billingSince", "since"], ["billingUntil", "until"]]) {
    if ($(id).value) query.set(parameter, String(new Date($(id).value).getTime() / 1000));
  }
  try {
    const result = await request(`/billing/events?${query}`);
    if (generation !== state.billingGeneration) return;
    state.billingPage = result;
    $("billingCount").textContent = `${result.total} 条 · 本页 ${result.events.length} 条`;
    $("billingPage").textContent = `第 ${Math.floor(result.offset / result.limit) + 1} 页`;
    $("billingEvents").innerHTML = result.events.map((event) => runtimeRow(
      billingCategoryLabel(event.category), `${event.currency} ${Number(event.amount).toFixed(6)}`,
      `${event.project_id || "租户级"} · ${new Date(event.occurred_at * 1000).toLocaleString("zh-CN")} · ${event.quantity} × ${event.unit_price} · ${event.event_id}`,
    )).join("") || `<div class="project-empty">无匹配记录</div>`;
    $("billingPrevious").disabled = result.offset === 0;
    $("billingNext").disabled = !result.has_more;
    $("billingDownload").disabled = !result.events.length;
  } catch (error) {
    if (generation !== state.billingGeneration) return;
    $("billingEvents").textContent = error.message;
    $("billingCount").textContent = "查询失败";
    $("billingPrevious").disabled = state.billingOffset === 0;
  }
}

function downloadBillingPage() {
  if (!state.billingPage) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(state.billingPage, null, 2)], {type: "application/json"}));
  const link = document.createElement("a");
  link.href = url;
  link.download = `mediaforge-usage-page-${Math.floor(state.billingPage.offset / state.billingPage.limit) + 1}.json`;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function billingCategoryLabel(category) {
  return {provider_generation: "媒体生成", storage: "存储", generation: "生成用量", speech: "文本配音"}[category] || category;
}

async function loadMemorySources() {
  const projectId = state.projectId;
  $("memorySearchButton").disabled = !projectId;
  if (!projectId) {
    $("memoryStatus").textContent = "暂无项目";
    $("memoryBackend").replaceChildren();
    setRagflowSyncControl(null);
    return;
  }
  const generation = ++state.memoryGeneration;
  $("memoryStatus").textContent = "读取中";
  try {
    const report = await request(`/projects/${encodeURIComponent(projectId)}/memory/sources`);
    if (state.projectId !== projectId || generation !== state.memoryGeneration) return;
    const selected = state.memoryProjectId === projectId ? state.memorySourceIds : [projectId];
    state.memoryProjectId = projectId;
    state.memorySourceIds = selected.filter((id) => report.sources.some((source) => source.project_id === id));
    $("memorySourceList").innerHTML = report.sources.map((source) => `
      <label class="memory-source"><input type="checkbox" value="${escapeHtml(source.project_id)}" ${state.memorySourceIds.includes(source.project_id) ? "checked" : ""} />
        <span>${escapeHtml(source.title)}<br /><small>${escapeHtml(source.project_id)}</small></span>
      </label>`).join("");
    $("memoryStatus").textContent = !report.settings.enabled ? "规划检索已停用"
      : report.settings.planner_supports_context ? "检索增强已启用" : "本地规划";
    $("memoryBackend").innerHTML = memoryRuntimeRows(report.settings);
    setRagflowSyncControl(report.settings);
    renderMemoryCitations();
  } catch (error) {
    if (state.projectId === projectId && generation === state.memoryGeneration) {
      $("memoryStatus").textContent = "读取失败";
      $("memorySourceList").textContent = error.message;
      $("memorySearchButton").disabled = true;
      setRagflowSyncControl(null);
    }
  }
}

async function syncStoryMemoryToRagflow() {
  const projectId = state.projectId;
  if (!projectId) return;
  const confirmed = window.confirm(
    "将已批准的故事设定、通过策略检查的镜头卡和参考资产元数据导出为 RAGFlow 快照？原始章节、媒体文件和未批准的规划不会上传。",
  );
  if (!confirmed) return;

  const button = $("memoryRagflowSync");
  setBusy(button, true, "同步中");
  $("memoryStatus").textContent = "正在同步已批准快照";
  try {
    const result = await request(`/projects/${encodeURIComponent(projectId)}/memory/ragflow-sync`, {
      method: "POST",
      body: JSON.stringify({confirm_data_export: true}),
    });
    if (state.projectId !== projectId) return;
    $("memoryStatus").textContent = `快照已同步：新增 ${result.uploaded_count}，已存在 ${result.existing_count}`;
    logEvent(`RAGFlow 已同步批准快照：新增 ${result.uploaded_count}，已存在 ${result.existing_count}。`);
  } catch (error) {
    if (state.projectId === projectId) {
      $("memoryStatus").textContent = "快照同步失败";
      alert(error.message);
    }
  } finally {
    if (state.projectId === projectId) setBusy(button, false);
  }
}

function renderMemoryCitations() {
  const provenance = state.project?.story_bible?.memory_retrieval;
  const sources = provenance?.sources || [];
  $("memoryCitations").innerHTML = sources.length ? sources.map((source) => `
    <div class="policy-row"><strong>${escapeHtml(source.metadata?.title || source.project_id)}</strong>
      <span>${escapeHtml(source.metadata?.shot_id || memoryKindLabel(source.kind))}</span>
      ${source.embedding_fingerprint ? `<span>索引版本：${escapeHtml(source.embedding_fingerprint.slice(0, 16))}</span>` : ""}
      <span>SHA-256: ${escapeHtml(source.sha256)}</span></div>`).join("")
    : '<span class="mini-label">当前计划未引用记忆</span>';
}

function memoryKindLabel(kind) {
  return { story_bible: "故事设定", shot_card: "镜头卡", reference_asset: "参考资产" }[kind] || kind;
}

async function searchStoryMemory(event) {
  event.preventDefault();
  const projectId = state.projectId;
  const query = $("memoryQuery").value.trim();
  if (!projectId || !query) return;
  const generation = ++state.memoryGeneration;
  const button = $("memorySearchButton");
  setBusy(button, true, "检索中");
  $("memoryResults").replaceChildren();
  $("memoryResultCount").textContent = "检索中";
  try {
    const params = new URLSearchParams({ query });
    (state.memorySourceIds.length ? state.memorySourceIds : [projectId])
      .forEach((id) => params.append("source_project_ids", id));
    const report = await request(`/projects/${encodeURIComponent(projectId)}/memory?${params}`);
    if (state.projectId !== projectId || generation !== state.memoryGeneration) return;
    $("memoryResultCount").textContent = `${report.results.length} 条`;
    $("memoryBackend").innerHTML = memoryRuntimeRows(report.settings);
    $("memoryResults").innerHTML = report.results.length ? report.results.map((hit) => `
      <div class="policy-row"><strong>${escapeHtml(hit.metadata?.title || hit.project_id)} · ${escapeHtml(memoryKindLabel(hit.kind))}</strong>
        <span>${escapeHtml(hit.metadata?.shot_id || hit.project_id)}</span>
        ${hit.score_kind === "cosine_similarity" ? `<span>余弦相似度：${Number(hit.score).toFixed(4)}</span>` : ""}
        <p class="memory-excerpt">${escapeHtml(hit.content)}</p>
        <span>SHA-256: ${escapeHtml(hit.sha256)}</span>
      </div>`).join("") : '<span class="mini-label">没有匹配的记忆</span>';
  } catch (error) {
    if (state.projectId === projectId && generation === state.memoryGeneration) {
      $("memoryResults").textContent = error.message;
      $("memoryResultCount").textContent = "检索失败";
    }
  } finally {
    setBusy(button, false);
  }
}

function fillBriefFromProject(project) {
  const brief = project.brief;
  $("projectId").value = brief.project_id;
  $("title").value = brief.title;
  $("premise").value = brief.premise;
  $("genre").value = brief.genre;
  $("style").value = brief.style;
  $("duration").value = String(brief.duration_seconds);
  $("budget").value = Number(brief.budget).toFixed(2);
  $("characterA").value = brief.characters[0] || "";
  $("characterB").value = brief.characters[1] || "";
  $("saveProjectButton").disabled = Boolean(project.archived);
}

function fillShotEditor(runtime) {
  if (!runtime) {
    $("saveShotButton").disabled = true;
    return;
  }
  const shot = runtime.shot;
  $("shotEditScene").value = shot.scene || "";
  $("shotEditDescription").value = shot.description || "";
  $("shotEditMood").value = shot.mood || "";
  $("shotEditDuration").value = String(shot.duration_seconds || 1);
  $("shotEditSubtitle").value = shot.subtitle_text || "";
  const selected = new Set(shot.characters || []);
  $("shotEditCharacters").innerHTML = (state.project?.brief?.characters || [])
    .map((character) => `<option value="${escapeHtml(character)}" ${selected.has(character) ? "selected" : ""}>${escapeHtml(character)}</option>`)
    .join("");
  $("saveShotButton").disabled = Boolean(state.project?.archived);
}

const narrativeImportanceLabel = (value) => ({
  MAINLINE: "主线",
  SUPPORTING: "支线",
  TRANSITION: "过渡",
}[String(value)] || String(value || "主线"));

const parseNarrativeTags = (value) => String(value || "")
  .split(/[，、,;；\n\s]+/)
  .map((item) => item.trim())
  .filter(Boolean);

function narrativeEventsLocked(project) {
  return Boolean(
    !project
    || project.archived
    || project.release
    || project.final_mp4
    || (state.jobs || []).length
    || (project.shots || []).some((runtime) => runtime.artifact),
  );
}

function sourceChaptersLocked(project) {
  return narrativeEventsLocked(project);
}

function renderSourceChapterControls() {
  const project = state.project;
  const editing = (project?.source_chapters || []).find(
    (chapter) => chapter.chapter_id === state.sourceChapterEditingId,
  );
  const disabled = sourceChaptersLocked(project);
  [
    "sourceChapterSourceName",
    "sourceChapterNumber",
    "sourceChapterTitle",
    "sourceChapterRights",
    "sourceChapterContent",
    "sourceChapterExternalConsent",
  ].forEach((id) => {
    $(id).disabled = disabled;
  });
  $("saveSourceChapterButton").disabled = disabled;
  $("saveSourceChapterButton").textContent = editing ? "保存修订" : "导入章节";
  $("cancelSourceChapterButton").hidden = !editing;
  $("sourceDocumentChooseButton").disabled = disabled;
  $("sourceDocumentImportButton").disabled = disabled || !state.pendingSourceDocumentFile;
  $("sourceDocumentFileName").textContent = state.pendingSourceDocumentFile
    ? state.pendingSourceDocumentFile.name
    : "尚未选择文件";
}

function resetSourceChapterForm() {
  state.sourceChapterEditingId = null;
  $("sourceChapterForm").reset();
  $("sourceChapterSourceName").value = state.project?.brief?.title || "";
  const chapterNumbers = (state.project?.source_chapters || []).map(
    (chapter) => Number(chapter.chapter_number || 0),
  );
  $("sourceChapterNumber").value = String(Math.max(0, ...chapterNumbers) + 1);
  $("sourceChapterRights").value = "project-owned";
  renderSourceChapterControls();
}

function beginSourceChapterEdit(chapterId) {
  const chapter = (state.project?.source_chapters || []).find(
    (item) => item.chapter_id === chapterId,
  );
  if (!chapter || sourceChaptersLocked(state.project)) return;
  state.sourceChapterEditingId = chapter.chapter_id;
  $("sourceChapterSourceName").value = chapter.source_name || "";
  $("sourceChapterNumber").value = String(chapter.chapter_number || 1);
  $("sourceChapterTitle").value = chapter.title || "";
  $("sourceChapterRights").value = chapter.rights_basis || "project-owned";
  $("sourceChapterContent").value = chapter.content || "";
  $("sourceChapterExternalConsent").checked = Boolean(chapter.allow_external_processing);
  renderSourceChapterControls();
}

function renderSourceChapters() {
  const project = state.project;
  const chapters = project?.source_chapters || [];
  const documents = project?.source_documents || [];
  const candidates = project?.narrative_event_candidates || [];
  const pending = candidates.filter((item) => item.status === "PENDING");
  const locked = sourceChaptersLocked(project);
  const ocr = state.sourceOcrStatus;
  $("sourceOcrStatus").textContent = !ocr ? "扫描件 OCR 状态未知"
    : ocr.configured ? `扫描件 OCR 已配置（${ocr.mode === "http" ? "受控服务" : "本地命令"}）`
      : ocr.configuration_error ? "扫描件 OCR 配置无效"
        : "扫描件 OCR 未配置";
  $("sourceChapterStatus").textContent = `${chapters.length} 章 · ${pending.length} 条待采纳`;
  $("sourceChapterCount").textContent = `${chapters.length} 章`;
  $("sourceDocumentCount").textContent = `${documents.length} 个`;
  $("narrativeCandidateCount").textContent = `${pending.length} 条待采纳`;
  $("sourceChapterList").innerHTML = chapters.length
    ? chapters.map((chapter) => {
      const external = chapter.allow_external_processing ? "已授权外部提取" : "仅本地提取";
      const preview = String(chapter.content || "").replace(/\s+/g, " ").slice(0, 360);
      return `
        <article class="narrative-event-row">
          <strong>第 ${Number(chapter.chapter_number)} 章 · ${escapeHtml(chapter.title)} · r${Number(chapter.revision || 1)}</strong>
          <span>${escapeHtml(chapter.source_name)} · ${escapeHtml(chapter.rights_basis)} · ${external}</span>
          <p>${escapeHtml(preview)}${String(chapter.content || "").length > preview.length ? "..." : ""}</p>
          <span>原文哈希：${escapeHtml(String(chapter.content_sha256 || "").slice(0, 12))}</span>
          <div class="narrative-event-actions">
            ${!locked ? `<button class="button button-secondary" type="button" data-action="extract-narrative-candidates" data-chapter-id="${escapeHtml(chapter.chapter_id)}">生成候选</button>` : ""}
            ${!locked ? `<button class="button button-quiet" type="button" data-action="edit-source-chapter" data-chapter-id="${escapeHtml(chapter.chapter_id)}">编辑</button>` : ""}
          </div>
        </article>
      `;
    }).join("")
    : `<div class="project-empty">尚未导入原著章节。章节原文默认只在本地结构化提取，不会自动发送到外部模型。</div>`;
  $("sourceDocumentList").innerHTML = documents.length
    ? documents.map((document) => `
      <article class="narrative-event-row">
        <strong>${escapeHtml(document.name)} · ${escapeHtml(String(document.format || "").toUpperCase())}</strong>
        <span>${escapeHtml(document.source_name)} · ${Number(document.chapter_ids?.length || 0)} 章 · ${formatBytes(document.size_bytes || 0)}</span>
        <span>哈希：${escapeHtml(String(document.sha256 || "").slice(0, 12))} · ${document.allow_external_processing ? "已授权外部提取" : "仅本地提取"} · ${document.extraction_method === "ocr_http" ? "受控 OCR" : document.extraction_method === "ocr_command" ? "本地 OCR" : "内嵌文本"}</span>
        <div class="narrative-event-actions">
          <a class="button button-quiet" href="/projects/${encodeURIComponent(project?.project_id || "")}/source-documents/${encodeURIComponent(document.document_id)}/download" download>下载原文件</a>
        </div>
      </article>
    `).join("")
    : `<div class="project-empty">导入 TXT、Markdown、DOCX 或 PDF 后会保留可校验的原文件记录。</div>`;
  $("narrativeCandidateList").innerHTML = candidates.length
    ? candidates.map((candidate) => {
      const proposal = candidate.proposal || {};
      const isPending = candidate.status === "PENDING" && !locked;
      const suggestedCharacters = (proposal.characters || []).join("、");
      return `
        <article class="narrative-event-row">
          <strong>${Number(candidate.sequence)}. ${escapeHtml(proposal.title || "未命名候选")} · ${escapeHtml(labelFor(candidate.status))}</strong>
          <span>${escapeHtml(proposal.scene || "未标注场景")} · ${escapeHtml(narrativeImportanceLabel(proposal.importance))} · ${escapeHtml(candidate.extractor || "未知提取器")}</span>
          <p>${escapeHtml(proposal.summary || "")}</p>
          <span>来源：${escapeHtml(proposal.source_locator || candidate.source_chapter_id)} · 章节 r${Number(candidate.source_chapter_revision || 1)}</span>
          ${isPending ? `
            <label class="narrative-candidate-characters">涉及角色
              <input type="text" data-candidate-characters="${escapeHtml(candidate.candidate_id)}" maxlength="500" value="${escapeHtml(suggestedCharacters)}" placeholder="候选未识别角色时，请填写项目角色" />
            </label>
            <div class="narrative-event-actions">
              <button class="button button-approve" type="button" data-action="adopt-narrative-candidate" data-candidate-id="${escapeHtml(candidate.candidate_id)}">采纳为事件</button>
              <button class="button button-quiet" type="button" data-action="discard-narrative-candidate" data-candidate-id="${escapeHtml(candidate.candidate_id)}">舍弃</button>
            </div>
          ` : ""}
          ${candidate.status === "ADOPTED" ? `<span>已转为事件：${escapeHtml(candidate.adopted_event_id || "")}</span>` : ""}
        </article>
      `;
    }).join("")
    : `<div class="project-empty">导入章节后生成候选事件。候选必须被采纳并在“故事事件”中审核后，才会进入分镜规划。</div>`;
  renderSourceChapterControls();
}

function renderAdaptationCanvas() {
  const project = state.project;
  const documents = project?.source_documents || [];
  const chapters = project?.source_chapters || [];
  const candidates = project?.narrative_event_candidates || [];
  const events = project?.narrative_events || [];
  const scenes = project?.adaptation_scenes || [];
  const shots = project?.shots || [];
  const total = documents.length + chapters.length + candidates.length + events.length + scenes.length + shots.length;
  $("adaptationCanvasStatus").textContent = total
    ? `${documents.length} 文件 · ${chapters.length} 章节 · ${events.length} 事件 · ${scenes.length} 场次`
    : "等待原著";
  if (!total) {
    $("adaptationCanvas").innerHTML = `<div class="adaptation-canvas-empty">导入原著章节后，这里会展示从文件、章节、候选到事件和镜头的改编链路。</div>`;
    return;
  }
  const node = (kind, title, meta, action, attributes = "") => `
    <button class="adaptation-node ${kind}" type="button" data-action="${action}" ${attributes}>
      <strong>${escapeHtml(title)}</strong><span>${escapeHtml(meta)}</span>
    </button>
  `;
  const lanes = [
    {
      title: "原著文件",
      body: documents.length
        ? documents.map((document) => node(
          "document",
          document.name,
          `${String(document.format || "").toUpperCase()} · ${Number(document.chapter_ids?.length || 0)} 章`,
          "canvas-open-source",
        )).join("")
        : `<div class="adaptation-canvas-empty">手工章节</div>`,
    },
    {
      title: "章节",
      body: chapters.length
        ? chapters.map((chapter) => node(
          "chapter",
          `第 ${chapter.chapter_number} 章 · ${chapter.title}`,
          `${chapter.source_name} · r${chapter.revision}`,
          "canvas-open-source",
          `data-chapter-id="${escapeHtml(chapter.chapter_id)}"`,
        )).join("")
        : `<div class="adaptation-canvas-empty">尚未导入</div>`,
    },
    {
      title: "候选",
      body: candidates.length
        ? candidates.map((candidate) => node(
          "candidate",
          candidate.proposal?.title || "未命名候选",
          `${labelFor(candidate.status)} · ${candidate.source_chapter_id}`,
          "canvas-open-source",
          `data-candidate-id="${escapeHtml(candidate.candidate_id)}"`,
        )).join("")
        : `<div class="adaptation-canvas-empty">尚未提取</div>`,
    },
    {
      title: "审核事件",
      body: events.length
        ? events.map((event) => node(
          "event",
          event.title,
          `${labelFor(event.review_status)} · 第 ${event.chapter_number} 章`,
          "canvas-open-event",
          `data-event-id="${escapeHtml(event.event_id)}"`,
        )).join("")
        : `<div class="adaptation-canvas-empty">尚未采纳</div>`,
    },
    {
      title: "剧本场次",
      body: scenes.length
        ? scenes.map((scene) => node(
          "script",
          scene.heading,
          `${labelFor(scene.review_status)} · ${scene.source_event_ids?.length || 0} 个事件 · r${scene.revision}`,
          "canvas-open-script",
          `data-scene-id="${escapeHtml(scene.scene_id)}"`,
        )).join("")
        : `<div class="adaptation-canvas-empty">确认事件后生成</div>`,
    },
    {
      title: "视觉镜头",
      body: shots.length
        ? shots.map((runtime) => node(
          "shot",
          runtime.shot?.scene || runtime.shot?.shot_id || "镜头",
          `${labelFor(runtime.review_status)} · ${runtime.shot?.duration_seconds || 0} 秒`,
          "select",
          `data-shot-id="${escapeHtml(runtime.shot?.shot_id || "")}"`,
        )).join("")
        : `<div class="adaptation-canvas-empty">等待规划</div>`,
    },
  ];
  $("adaptationCanvas").innerHTML = `<div class="adaptation-canvas-track">${lanes.map((lane) => `
    <section class="adaptation-canvas-lane"><h3>${escapeHtml(lane.title)}</h3>${lane.body}</section>
  `).join("")}</div>`;
}

async function saveSourceChapter(event) {
  event.preventDefault();
  const projectId = state.projectId;
  if (!projectId || sourceChaptersLocked(state.project) || !$("sourceChapterForm").reportValidity()) return;
  const current = (state.project?.source_chapters || []).find(
    (item) => item.chapter_id === state.sourceChapterEditingId,
  );
  const payload = {
    source_name: $("sourceChapterSourceName").value.trim(),
    rights_basis: $("sourceChapterRights").value.trim(),
    allow_external_processing: $("sourceChapterExternalConsent").checked,
    chapter_number: Number($("sourceChapterNumber").value),
    title: $("sourceChapterTitle").value.trim(),
    content: $("sourceChapterContent").value.trim(),
  };
  const button = $("saveSourceChapterButton");
  setBusy(button, true, current ? "保存中" : "导入中");
  try {
    const url = current
      ? `/projects/${encodeURIComponent(projectId)}/source-chapters/${encodeURIComponent(current.chapter_id)}`
      : `/projects/${encodeURIComponent(projectId)}/source-chapters`;
    const result = await request(url, {
      method: current ? "PATCH" : "POST",
      body: JSON.stringify(current ? {...payload, expected_revision: current.revision} : payload),
    });
    logEvent(result.plan_invalidated ? "章节已修订，关联事件和旧计划已退回。" : "原著章节已保存。", "muted");
    await loadProjectContext(projectId);
    resetSourceChapterForm();
    setActiveTab("source");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function importSourceDocument() {
  const projectId = state.projectId;
  const file = state.pendingSourceDocumentFile;
  if (!projectId || !file || sourceChaptersLocked(state.project)) return;
  if (file.size > 20 * 1024 * 1024) {
    alert("原著文件不能超过 20 MB");
    return;
  }
  const button = $("sourceDocumentImportButton");
  setBusy(button, true, "解析中");
  try {
    const result = await request(`/projects/${encodeURIComponent(projectId)}/source-documents/import`, {
      method: "POST",
      body: JSON.stringify({
        name: file.name,
        content_b64: arrayBufferToBase64(await file.arrayBuffer()),
        source_name: $("sourceChapterSourceName").value.trim(),
        rights_basis: $("sourceChapterRights").value.trim(),
        allow_external_processing: $("sourceChapterExternalConsent").checked,
        chapter_number_start: Number($("sourceChapterNumber").value),
      }),
    });
    state.pendingSourceDocumentFile = null;
    $("sourceDocumentFileInput").value = "";
    logEvent(`已导入 ${result.document.name}，自动创建 ${result.chapters.length} 个章节。`, "muted");
    await loadProjectContext(projectId);
    resetSourceChapterForm();
    setActiveTab("source");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
    renderSourceChapterControls();
  }
}

async function extractNarrativeCandidates(chapterId) {
  const projectId = state.projectId;
  if (!projectId || sourceChaptersLocked(state.project)) return;
  try {
    const result = await request(`/projects/${encodeURIComponent(projectId)}/source-chapters/${encodeURIComponent(chapterId)}/event-candidates`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    const method = result.extractor === "external-structured-v1" ? "外部模型" : "本地结构化";
    logEvent(`已使用${method}提取 ${result.candidates.length} 条候选事件。`, "muted");
    await loadProjectContext(projectId);
    setActiveTab("source");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  }
}

async function adoptNarrativeCandidate(candidateId) {
  const projectId = state.projectId;
  const candidate = (state.project?.narrative_event_candidates || []).find(
    (item) => item.candidate_id === candidateId,
  );
  if (!projectId || !candidate || sourceChaptersLocked(state.project)) return;
  const field = document.querySelector(`[data-candidate-characters="${candidateId}"]`);
  const characters = parseNarrativeTags(field?.value || "");
  try {
    const result = await request(`/projects/${encodeURIComponent(projectId)}/narrative-candidates/${encodeURIComponent(candidateId)}/adopt`, {
      method: "POST",
      body: JSON.stringify({
        characters: characters.length ? characters : undefined,
        expected_revision: candidate.revision,
      }),
    });
    logEvent(result.plan_invalidated ? "候选已采纳，旧分镜计划已失效。" : "候选已采纳为待审核故事事件。", "muted");
    await loadProjectContext(projectId);
    setActiveTab("narrative");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  }
}

async function discardNarrativeCandidate(candidateId) {
  const projectId = state.projectId;
  const candidate = (state.project?.narrative_event_candidates || []).find(
    (item) => item.candidate_id === candidateId,
  );
  if (!projectId || !candidate || sourceChaptersLocked(state.project)) return;
  try {
    await request(`/projects/${encodeURIComponent(projectId)}/narrative-candidates/${encodeURIComponent(candidateId)}/discard`, {
      method: "POST",
      body: JSON.stringify({reason: "工作台未采纳", expected_revision: candidate.revision}),
    });
    logEvent("候选事件已舍弃。", "muted");
    await loadProjectContext(projectId);
    setActiveTab("source");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  }
}

function renderNarrativeEventControls() {
  const project = state.project;
  const editing = (project?.narrative_events || []).find(
    (event) => event.event_id === state.narrativeEventEditingId,
  );
  const disabled = narrativeEventsLocked(project);
  [
    "narrativeChapter",
    "narrativeSequence",
    "narrativeTitle",
    "narrativeScene",
    "narrativeSummary",
    "narrativeCharacters",
    "narrativeEmotions",
    "narrativeImportance",
    "narrativeDuration",
    "narrativeLocator",
    "narrativeExcerpt",
  ].forEach((id) => {
    $(id).disabled = disabled;
  });
  $("saveNarrativeEventButton").disabled = disabled;
  $("saveNarrativeEventButton").textContent = editing ? "保存修订" : "添加事件";
  $("cancelNarrativeEventButton").hidden = !editing;
}

function resetNarrativeEventForm() {
  state.narrativeEventEditingId = null;
  $("narrativeEventForm").reset();
  $("narrativeChapter").value = "1";
  $("narrativeSequence").value = String((state.project?.narrative_events || []).length + 1);
  $("narrativeDuration").value = "5";
  $("narrativeImportance").value = "MAINLINE";
  renderNarrativeEventControls();
}

function beginNarrativeEventEdit(eventId) {
  const item = (state.project?.narrative_events || []).find(
    (event) => event.event_id === eventId,
  );
  if (!item || narrativeEventsLocked(state.project)) return;
  state.narrativeEventEditingId = item.event_id;
  $("narrativeChapter").value = String(item.chapter_number);
  $("narrativeSequence").value = String(item.sequence);
  $("narrativeTitle").value = item.title || "";
  $("narrativeScene").value = item.scene || "";
  $("narrativeSummary").value = item.summary || "";
  $("narrativeCharacters").value = (item.characters || []).join("、");
  $("narrativeEmotions").value = (item.emotions || []).join("、");
  $("narrativeImportance").value = item.importance || "MAINLINE";
  $("narrativeDuration").value = String(item.estimated_duration_seconds || 5);
  $("narrativeLocator").value = item.source_locator || "";
  $("narrativeExcerpt").value = item.source_excerpt || "";
  renderNarrativeEventControls();
}

function renderNarrativeEvents() {
  const project = state.project;
  const events = project?.narrative_events || [];
  const approved = events.filter((event) => event.review_status === "APPROVED").length;
  $("narrativeEventStatus").textContent = `${approved} / ${events.length} 个已确认`;
  $("narrativeEventCount").textContent = `${events.length} 条`;
  const locked = narrativeEventsLocked(project);
  $("narrativeEventList").innerHTML = events.length
    ? events.map((item) => {
      const review = labelFor(item.review_status);
      const source = item.source_locator
        ? `来源：${item.source_locator}`
        : `来源摘要：${String(item.source_sha256 || "").slice(0, 12) || "未记录"}`;
      const editable = !locked;
      return `
        <article class="narrative-event-row">
          <strong>第 ${item.chapter_number} 章 · ${item.sequence}. ${escapeHtml(item.title)} · ${escapeHtml(review)}</strong>
          <span>${escapeHtml(item.scene)} · ${escapeHtml(narrativeImportanceLabel(item.importance))} · ${Number(item.estimated_duration_seconds || 0)} 秒 · r${Number(item.revision || 1)}</span>
          <p>${escapeHtml(item.summary)}</p>
          <span>${escapeHtml((item.characters || []).join("、"))}${item.emotions?.length ? ` · ${escapeHtml(item.emotions.join("、"))}` : ""}</span>
          <span>${escapeHtml(source)}</span>
          <div class="narrative-event-actions">
            ${editable ? `<button class="button button-quiet" type="button" data-action="edit-narrative-event" data-event-id="${escapeHtml(item.event_id)}">编辑</button>` : ""}
            ${editable && item.review_status !== "APPROVED" ? `<button class="button button-approve" type="button" data-action="approve-narrative-event" data-event-id="${escapeHtml(item.event_id)}">确认</button>` : ""}
            ${editable && item.review_status !== "CHANGES_REQUESTED" ? `<button class="button button-secondary" type="button" data-action="request-narrative-changes" data-event-id="${escapeHtml(item.event_id)}">退回</button>` : ""}
          </div>
        </article>
      `;
    }).join("")
    : `<div class="project-empty">尚未录入事件。通过审核的事件将成为下一次分镜规划的明确输入。</div>`;
  renderNarrativeEventControls();
}

async function saveNarrativeEvent(event) {
  event.preventDefault();
  const projectId = state.projectId;
  if (!projectId || narrativeEventsLocked(state.project) || !$("narrativeEventForm").reportValidity()) return;
  const button = $("saveNarrativeEventButton");
  const current = (state.project?.narrative_events || []).find(
    (item) => item.event_id === state.narrativeEventEditingId,
  );
  const payload = {
    chapter_number: Number($("narrativeChapter").value),
    sequence: Number($("narrativeSequence").value),
    title: $("narrativeTitle").value.trim(),
    scene: $("narrativeScene").value.trim(),
    summary: $("narrativeSummary").value.trim(),
    characters: parseNarrativeTags($("narrativeCharacters").value),
    emotions: parseNarrativeTags($("narrativeEmotions").value),
    importance: $("narrativeImportance").value,
    estimated_duration_seconds: Number($("narrativeDuration").value),
    source_locator: $("narrativeLocator").value.trim() || null,
    source_excerpt: $("narrativeExcerpt").value.trim() || null,
  };
  setBusy(button, true, current ? "保存中" : "添加中");
  try {
    const url = current
      ? `/projects/${encodeURIComponent(projectId)}/narrative-events/${encodeURIComponent(current.event_id)}`
      : `/projects/${encodeURIComponent(projectId)}/narrative-events`;
    const result = await request(url, {
      method: current ? "PATCH" : "POST",
      body: JSON.stringify(current ? {...payload, expected_revision: current.revision} : payload),
    });
    logEvent(result.plan_invalidated ? "事件已保存，旧分镜计划已失效。" : "故事事件已保存。", "muted");
    await loadProjectContext(projectId);
    resetNarrativeEventForm();
    setActiveTab("narrative");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function reviewNarrativeEvent(eventId, status) {
  const projectId = state.projectId;
  const current = (state.project?.narrative_events || []).find(
    (item) => item.event_id === eventId,
  );
  if (!projectId || !current || narrativeEventsLocked(state.project)) return;
  try {
    const result = await request(`/projects/${encodeURIComponent(projectId)}/narrative-events/${encodeURIComponent(eventId)}/review`, {
      method: "POST",
      body: JSON.stringify({status, expected_revision: current.revision}),
    });
    logEvent(result.plan_invalidated ? "事件审核已更新，旧分镜计划已失效。" : "事件审核已更新。", "muted");
    await loadProjectContext(projectId);
    setActiveTab("narrative");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  }
}

function adaptationScenesLocked(project) {
  return narrativeEventsLocked(project);
}

function splitSceneBeats(value) {
  return String(value || "")
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, 12);
}

function renderAdaptationSceneControls() {
  const project = state.project;
  const editing = (project?.adaptation_scenes || []).find(
    (scene) => scene.scene_id === state.adaptationSceneEditingId,
  );
  const disabled = adaptationScenesLocked(project);
  [
    "adaptationSceneSequence",
    "adaptationSceneDuration",
    "adaptationSceneHeading",
    "adaptationSceneSynopsis",
    "adaptationSceneBeats",
    "adaptationSceneDialogue",
    "adaptationSceneCharacters",
    "adaptationSceneMood",
    "adaptationSceneSourceEvents",
  ].forEach((id) => {
    $(id).disabled = disabled;
  });
  $("deriveAdaptationScenesButton").disabled = disabled
    || !(project?.narrative_events || []).some((item) => item.review_status === "APPROVED");
  $("saveAdaptationSceneButton").disabled = disabled;
  $("saveAdaptationSceneButton").textContent = editing ? "保存修订" : "添加场次";
  $("cancelAdaptationSceneButton").hidden = !editing;
}

function resetAdaptationSceneForm() {
  state.adaptationSceneEditingId = null;
  $("adaptationSceneForm").reset();
  $("adaptationSceneSequence").value = String((state.project?.adaptation_scenes || []).length + 1);
  $("adaptationSceneDuration").value = "5";
  renderAdaptationSceneControls();
}

function beginAdaptationSceneEdit(sceneId) {
  const item = (state.project?.adaptation_scenes || []).find(
    (scene) => scene.scene_id === sceneId,
  );
  if (!item || adaptationScenesLocked(state.project)) return;
  state.adaptationSceneEditingId = item.scene_id;
  $("adaptationSceneSequence").value = String(item.sequence);
  $("adaptationSceneDuration").value = String(item.estimated_duration_seconds || 5);
  $("adaptationSceneHeading").value = item.heading || "";
  $("adaptationSceneSynopsis").value = item.synopsis || "";
  $("adaptationSceneBeats").value = (item.beats || []).join("\n");
  $("adaptationSceneDialogue").value = item.dialogue_draft || "";
  $("adaptationSceneCharacters").value = (item.characters || []).join("、");
  $("adaptationSceneMood").value = item.mood || "";
  $("adaptationSceneSourceEvents").value = (item.source_event_ids || []).join("、");
  renderAdaptationSceneControls();
}

function renderAdaptationScenes() {
  const project = state.project;
  const scenes = project?.adaptation_scenes || [];
  const approved = scenes.filter((scene) => scene.review_status === "APPROVED").length;
  $("adaptationSceneStatus").textContent = `${approved} / ${scenes.length} 个已确认`;
  $("adaptationSceneCount").textContent = `${scenes.length} 条`;
  const locked = adaptationScenesLocked(project);
  $("adaptationSceneList").innerHTML = scenes.length
    ? scenes.map((item) => `
      <article class="narrative-event-row">
        <strong>${Number(item.sequence)}. ${escapeHtml(item.heading)} · ${escapeHtml(labelFor(item.review_status))}</strong>
        <span>${Number(item.estimated_duration_seconds || 0)} 秒 · ${escapeHtml(item.mood || "未标注氛围")} · r${Number(item.revision || 1)}</span>
        <p>${escapeHtml(item.synopsis)}</p>
        ${item.beats?.length ? `<span>节拍：${escapeHtml(item.beats.join(" / "))}</span>` : ""}
        ${item.dialogue_draft ? `<span>对白：${escapeHtml(item.dialogue_draft)}</span>` : ""}
        <span>来源事件：${escapeHtml((item.source_event_ids || []).join("、"))}</span>
        <div class="narrative-event-actions">
          ${!locked ? `<button class="button button-quiet" type="button" data-action="edit-adaptation-scene" data-scene-id="${escapeHtml(item.scene_id)}">编辑</button>` : ""}
          ${!locked && item.review_status !== "APPROVED" ? `<button class="button button-approve" type="button" data-action="approve-adaptation-scene" data-scene-id="${escapeHtml(item.scene_id)}">确认</button>` : ""}
          ${!locked && item.review_status !== "CHANGES_REQUESTED" ? `<button class="button button-secondary" type="button" data-action="request-adaptation-scene-changes" data-scene-id="${escapeHtml(item.scene_id)}">退回</button>` : ""}
        </div>
      </article>
    `).join("")
    : `<div class="project-empty">先确认故事事件，再自动生成可编辑的剧本场次。已确认场次将成为分镜规划的优先输入。</div>`;
  renderAdaptationSceneControls();
}

async function saveAdaptationScene(event) {
  event.preventDefault();
  const projectId = state.projectId;
  if (!projectId || adaptationScenesLocked(state.project) || !$("adaptationSceneForm").reportValidity()) return;
  const current = (state.project?.adaptation_scenes || []).find(
    (item) => item.scene_id === state.adaptationSceneEditingId,
  );
  const payload = {
    sequence: Number($("adaptationSceneSequence").value),
    heading: $("adaptationSceneHeading").value.trim(),
    synopsis: $("adaptationSceneSynopsis").value.trim(),
    beats: splitSceneBeats($("adaptationSceneBeats").value),
    dialogue_draft: $("adaptationSceneDialogue").value.trim(),
    characters: parseNarrativeTags($("adaptationSceneCharacters").value),
    mood: $("adaptationSceneMood").value.trim(),
    estimated_duration_seconds: Number($("adaptationSceneDuration").value),
    source_event_ids: parseNarrativeTags($("adaptationSceneSourceEvents").value),
  };
  const button = $("saveAdaptationSceneButton");
  setBusy(button, true, current ? "保存中" : "添加中");
  try {
    const result = await request(
      current
        ? `/projects/${encodeURIComponent(projectId)}/adaptation-scenes/${encodeURIComponent(current.scene_id)}`
        : `/projects/${encodeURIComponent(projectId)}/adaptation-scenes`,
      {
        method: current ? "PATCH" : "POST",
        body: JSON.stringify(current ? {...payload, expected_revision: current.revision} : payload),
      },
    );
    logEvent(result.plan_invalidated ? "场次已保存，旧分镜计划已失效。" : "剧本场次已保存。", "muted");
    await loadProjectContext(projectId);
    resetAdaptationSceneForm();
    setActiveTab("script");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function deriveAdaptationScenes() {
  const projectId = state.projectId;
  if (!projectId || adaptationScenesLocked(state.project)) return;
  const button = $("deriveAdaptationScenesButton");
  setBusy(button, true, "生成中");
  try {
    const result = await request(`/projects/${encodeURIComponent(projectId)}/adaptation-scenes/derive`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    logEvent(`已从已确认事件生成 ${result.scenes.length} 个待审核场次。`, "muted");
    await loadProjectContext(projectId);
    resetAdaptationSceneForm();
    setActiveTab("script");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function reviewAdaptationScene(sceneId, status) {
  const projectId = state.projectId;
  const current = (state.project?.adaptation_scenes || []).find(
    (item) => item.scene_id === sceneId,
  );
  if (!projectId || !current || adaptationScenesLocked(state.project)) return;
  try {
    const result = await request(`/projects/${encodeURIComponent(projectId)}/adaptation-scenes/${encodeURIComponent(sceneId)}/review`, {
      method: "POST",
      body: JSON.stringify({status, expected_revision: current.revision}),
    });
    logEvent(result.plan_invalidated ? "场次审核已更新，旧分镜计划已失效。" : "场次审核已更新。", "muted");
    await loadProjectContext(projectId);
    setActiveTab("script");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  }
}

function setStatus(status) {
  const element = $("projectStatus");
  element.textContent = labelFor(status);
  element.className = "status-chip";
  if (status === "PLANNED") element.classList.add("status-planned");
  if (status === "IN_PROGRESS") element.classList.add("status-progress");
  if (status === "EXPORTED") element.classList.add("status-exported");
}

function renderProject() {
  const project = state.project;
  if (!project) return;
  $("projectTitle").textContent = project.brief.title;
  $("projectPremise").textContent = project.brief.premise;
  $("projectKeyLabel").textContent = project.project_id;
  setStatus(project.status);
  const archived = Boolean(project.archived);
  const closed = archived && Boolean(project.acceptance_report || project.closeout_report);
  const archiveVerified = Boolean(project.archive_verified);
  $("projectStatus").textContent = archiveVerified ? "已封存" : closed ? "已结项" : archived ? "已归档" : labelFor(project.status);
  $("projectStatus").classList.toggle("status-archived", archived);
  $("archiveButton").disabled = false;
  $("archiveButton").textContent = archived ? "恢复" : "归档";
  $("cloneButton").disabled = !project.project_id;
  const planningRun = activePlanningRun();
  const planningBlocked = Boolean(planningRun || state.planningBusy);
  $("planButton").disabled = archived || planningBlocked;

  const shots = project.shots || [];
  const approved = shots.filter((shot) => shot.review_status === "APPROVED").length;
  const planned = shots.length > 0;
  const ungenerated = shots.filter((shot) => !shot.artifact || shot.review_status === "CHANGES_REQUESTED").length;
  const queuedStatuses = ["VALIDATED", "QUEUED", "ADMITTED"];
  const queuedCount = (state.operations?.queue?.queued_jobs || 0)
    + (state.operations?.queue?.retry_due_jobs || 0);
  const queueable = shots.filter((shot) => (
    !shot.artifact
    && shot.review_status !== "CHANGES_REQUESTED"
    && !queuedStatuses.includes(shot.job?.status)
    && !["FAILED", "QUALITY_REJECTED", "RETRY_WAIT"].includes(shot.job?.status)
  )).length;
  const ready = shots.filter((shot) => shot.artifact && shot.review_status === "PENDING").length;
  const percent = shots.length ? (approved / shots.length) * 100 : 0;
  const released = Boolean(project.release);
  const compliancePassed = state.compliance ? Boolean(state.compliance.passed) : project.compliance_passed !== false;
  const continuityPassed = state.continuity ? Boolean(state.continuity.passed) : project.continuity_passed !== false;
  const latestDeliveryStatus = project.latest_delivery?.status || "";
  const query = state.shotQuery.trim().toLowerCase();
  const visibleShots = shots.filter((runtime) => {
    const searchable = [
      runtime.shot.shot_id,
      runtime.shot.scene,
      runtime.shot.description,
      runtime.shot.mood,
    ].join(" ").toLowerCase();
    const matchesQuery = !query || searchable.includes(query);
    const matchesStatus = !state.shotStatus
      || (state.shotStatus === "PENDING"
        ? Boolean(runtime.artifact) && runtime.review_status === "PENDING"
        : runtime.review_status === state.shotStatus);
    return matchesQuery && matchesStatus;
  });
  $("progressValue").style.width = `${percent}%`;
  $("progressLabel").textContent = `${approved} / ${shots.length || 6} 个镜头已通过`;
  $("progressCost").textContent = `已花费 ${formatCurrency(state.cost?.spent ?? estimateCost(shots))}`;
  $("emptyState").hidden = shots.length > 0;
  $("emptyState").querySelector("h3").textContent = planningBlocked ? "分步规划草稿" : "暂无镜头";
  $("emptyState").querySelector("p").textContent = planningBlocked
    ? `${state.planningBusy ? "执行中" : planningStatusLabel(planningRun.status)} · ${planningRun?.shots.length || 0} 个草稿镜头`
    : "当前项目尚无镜头计划。";
  $("shotList").hidden = shots.length === 0;
  $("shotToolbar").hidden = shots.length === 0;
  $("shotFilterCount").textContent = `${visibleShots.length} / ${shots.length} 个镜头`;
  $("shotList").innerHTML = visibleShots.length
    ? visibleShots.map(renderShot).join("")
    : `<div class="project-empty">没有符合当前筛选条件的镜头。</div>`;
  $("generateAllButton").disabled = archived || planningBlocked || !planned || ungenerated === 0;
  $("queueAllButton").disabled = archived || planningBlocked || !planned || queueable === 0;
  $("drainQueueButton").disabled = archived || queuedCount === 0;
  $("recoverStaleButton").disabled = archived || !project.project_id;
  $("approveReadyButton").disabled = archived || ready === 0;
  $("exportButton").disabled = archived || shots.length === 0 || approved !== shots.length;
  $("packageButton").disabled = archived || project.status !== "EXPORTED";
  $("verifyPackageButton").disabled = archived || !project.delivery_package;
  $("releaseButton").disabled = archived || project.status !== "EXPORTED" || !project.delivery_verified || !compliancePassed || !continuityPassed || released;
  const awaitingDeliveryAck = latestDeliveryStatus === "DELIVERED";
  const deliveryAccepted = latestDeliveryStatus === "ACCEPTED";
  $("recordDeliveryButton").disabled = archived || !released || awaitingDeliveryAck || deliveryAccepted;
  $("acknowledgeDeliveryButton").disabled = archived || !awaitingDeliveryAck;
  $("closeoutButton").disabled = archived || !released || !deliveryAccepted || !project.delivery_verified || !compliancePassed;
  $("deliveryAcceptedToggle").disabled = archived || !awaitingDeliveryAck;
  $("deliveryAcceptedToggle").checked = latestDeliveryStatus !== "REJECTED";
  $("auditExportButton").disabled = archived || !project.project_id;
  $("auditCsvExportButton").disabled = archived || !project.project_id;
  $("auditIntegrityButton").disabled = !project.project_id;
  $("auditIntegrityExportButton").disabled = archived || !project.project_id;
  $("auditAnchorButton").disabled = archived
    || !project.project_id
    || !state.auditIntegrity?.verified
    || !state.auditAnchors?.configured
    || !state.auditAnchors?.ready;
  $("snapshotButton").disabled = !project.project_id;
  $("traceExportButton").disabled = !project.project_id;
  $("retrospectiveExportButton").disabled = !project.project_id;
  $("reportButton").disabled = !project.project_id;
  $("provenanceButton").disabled = archived || !project.project_id;
  $("complianceButton").disabled = archived || !project.project_id;
  $("continuityExportButton").disabled = archived || !project.project_id;
  $("distributionButton").disabled = archived || !project.project_id;
  $("acceptanceExportButton").disabled = !closed;
  $("archivePackageButton").disabled = !closed;
  $("verifyArchiveButton").disabled = !closed || !project.archive_package;
  $("evaluateButton").disabled = archived || shots.length === 0;
  $("benchmarkButton").disabled = archived || shots.length === 0;
  const selectedRuntime = shots.find((shot) => shot.shot.shot_id === state.selectedShotId);
  $("routeExportButton").disabled = archived || !selectedRuntime;
  $("compareButton").disabled = archived || !selectedRuntime;
  $("comparisonExportButton").disabled = archived || !selectedRuntime || !(selectedRuntime.variants || []).length;
  const hasProject = Boolean(state.projectId && state.project);
  $("audioChooseButton").disabled = !hasProject || archived || released;
  $("audioUploadButton").disabled = !hasProject || archived || released || !state.pendingAudioFile;
  $("audioRemoveButton").disabled = !hasProject || archived || released || !project.audio_track;
  $("audioLicense").disabled = !hasProject || archived || released;
  $("audioSource").disabled = !hasProject || archived || released;
  $("voiceoverText").disabled = !hasProject || archived || released;
  $("voiceoverVoice").disabled = !hasProject || archived || released;
  $("voiceoverButton").disabled = !hasProject || archived || released || !$("voiceoverText").value.trim();
  $("audioFileName").textContent = state.pendingAudioFile?.name
    || (project.audio_track
      ? String(project.audio_track).replaceAll("\\", "/").split("/").pop()
      : "未配置");
  let deliveryState = "等待审批";
  if (archiveVerified) {
    deliveryState = "归档已封存";
  } else if (closed) {
    deliveryState = project.archive_package ? "归档包待验证" : "已结项，等待归档";
  } else if (latestDeliveryStatus === "DELIVERED") {
    deliveryState = "等待确认交付";
  } else if (latestDeliveryStatus === "ACCEPTED") {
    deliveryState = "可以结项";
  } else if (latestDeliveryStatus === "REJECTED") {
    deliveryState = "交付被拒绝";
  } else if ((project.delivery_count || 0) > 0) {
    deliveryState = "已分发";
  } else if (released) {
    deliveryState = "已发布";
  } else if (project.delivery_verified && !compliancePassed) {
    deliveryState = "合规门禁拦截";
  } else if (project.delivery_verified) {
    deliveryState = "交付包已验证";
  } else if (project.status === "EXPORTED") {
    deliveryState = "样片已导出";
  } else if (shots.length > 0 && approved === shots.length) {
    deliveryState = "可以导出";
  }
  $("deliveryState").textContent = deliveryState;
  $("budgetMetric").textContent = formatCurrency(state.cost?.budget ?? project.brief.budget);
  $("spentMetric").textContent = formatCurrency(state.cost?.spent ?? estimateCost(shots));
  $("jobsMetric").textContent = String(state.jobs.length);
  $("auditMetric").textContent = String(state.audit.length);

  const finalUrl = project.final_mp4
    ? artifactUrl(project.project_id, project.final_mp4)
    : "";
  $("downloadLink").href = finalUrl || "#";
  $("downloadLink").hidden = !finalUrl;
  const subtitleUrl = project.subtitle_srt
    ? artifactUrl(project.project_id, project.subtitle_srt)
    : "";
  $("subtitleLink").href = subtitleUrl || "#";
  $("subtitleLink").hidden = !subtitleUrl;
  const audioUrl = project.audio_track
    ? artifactUrl(project.project_id, project.audio_track)
    : "";
  $("audioLink").href = audioUrl || "#";
  $("audioLink").hidden = !audioUrl;
  const packageUrl = project.delivery_package ? artifactUrl(project.project_id, project.delivery_package) : state.packageUrl;
  $("packageLink").href = packageUrl || "#";
  $("packageLink").hidden = !packageUrl;
  if (!state.verificationUrl && project.delivery_verification_report) {
    state.verificationUrl = artifactUrl(project.project_id, project.delivery_verification_report);
  }
  $("verificationLink").href = state.verificationUrl || "#";
  $("verificationLink").hidden = !state.verificationUrl;
  $("auditExportLink").href = state.auditExportUrl || "#";
  $("auditExportLink").hidden = !state.auditExportUrl;
  $("auditCsvLink").href = state.auditCsvUrl || "#";
  $("auditCsvLink").hidden = !state.auditCsvUrl;
  $("auditIntegrityLink").href = state.auditIntegrityUrl || "#";
  $("auditIntegrityLink").hidden = !state.auditIntegrityUrl;
  $("auditAnchorLink").href = state.auditAnchorUrl || "#";
  $("auditAnchorLink").hidden = !state.auditAnchorUrl;
  $("snapshotLink").href = state.snapshotUrl || "#";
  $("snapshotLink").hidden = !state.snapshotUrl;
  $("reportLink").href = state.reportUrl || "#";
  $("reportLink").hidden = !state.reportUrl;
  if (!state.provenanceUrl && project.provenance_report) {
    state.provenanceUrl = artifactUrl(project.project_id, project.provenance_report);
  }
  $("provenanceLink").href = state.provenanceUrl || "#";
  $("provenanceLink").hidden = !state.provenanceUrl;
  if (!state.complianceUrl && project.compliance_report) {
    state.complianceUrl = artifactUrl(project.project_id, project.compliance_report);
  }
  $("complianceLink").href = state.complianceUrl || "#";
  $("complianceLink").hidden = !state.complianceUrl;
  if (!state.continuityUrl && project.continuity_report) {
    state.continuityUrl = artifactUrl(project.project_id, project.continuity_report);
  }
  $("continuityLink").href = state.continuityUrl || "#";
  $("continuityLink").hidden = !state.continuityUrl;
  if (!state.distributionUrl && project.distribution_report) {
    state.distributionUrl = artifactUrl(project.project_id, project.distribution_report);
  }
  if (!state.acceptanceUrl && project.acceptance_report) {
    state.acceptanceUrl = artifactUrl(project.project_id, project.acceptance_report);
  }
  if (!state.closeoutUrl && project.closeout_report) {
    state.closeoutUrl = artifactUrl(project.project_id, project.closeout_report);
  }
  if (!state.traceUrl && project.trace_report) {
    state.traceUrl = artifactUrl(project.project_id, project.trace_report);
  }
  if (!state.archivePackageUrl && project.archive_package) {
    state.archivePackageUrl = artifactUrl(project.project_id, project.archive_package);
  }
  if (!state.archiveVerificationUrl && project.archive_verification_report) {
    state.archiveVerificationUrl = artifactUrl(project.project_id, project.archive_verification_report);
  }
  if (!state.deliveryReceiptUrl && project.latest_delivery?.receipt_path) {
    state.deliveryReceiptUrl = artifactUrl(project.project_id, project.latest_delivery.receipt_path);
  }
  $("distributionLink").href = state.distributionUrl || "#";
  $("distributionLink").hidden = !state.distributionUrl;
  $("acceptanceLink").href = state.acceptanceUrl || "#";
  $("acceptanceLink").hidden = !state.acceptanceUrl;
  $("closeoutLink").href = state.closeoutUrl || "#";
  $("closeoutLink").hidden = !state.closeoutUrl;
  $("archivePackageLink").href = state.archivePackageUrl || "#";
  $("archivePackageLink").hidden = !state.archivePackageUrl;
  $("archiveVerificationLink").href = state.archiveVerificationUrl || "#";
  $("archiveVerificationLink").hidden = !state.archiveVerificationUrl;
  $("deliveryReceiptLink").href = state.deliveryReceiptUrl || "#";
  $("deliveryReceiptLink").hidden = !state.deliveryReceiptUrl;
  renderDeliveryFeedback();
  const routeUrlVisible = state.routeUrl && state.routeShotId === state.selectedShotId;
  $("routeLink").href = routeUrlVisible ? state.routeUrl : "#";
  $("routeLink").hidden = !routeUrlVisible;
  $("comparisonLink").href = state.comparisonUrl || "#";
  $("comparisonLink").hidden = !state.comparisonUrl;
  if (!state.evaluationUrl && state.project.latest_evaluation?.report_path) {
    state.evaluationUrl = artifactUrl(project.project_id, state.project.latest_evaluation.report_path);
  }
  if (!state.benchmarkUrl && state.project.latest_provider_benchmark?.report_path) {
    state.benchmarkUrl = artifactUrl(project.project_id, state.project.latest_provider_benchmark.report_path);
  }
  $("evaluationLink").href = state.evaluationUrl || "#";
  $("evaluationLink").hidden = !state.evaluationUrl;
  $("benchmarkLink").href = state.benchmarkUrl || "#";
  $("benchmarkLink").hidden = !state.benchmarkUrl;
  renderSourceChapters();
  renderAdaptationCanvas();
  renderNarrativeEvents();
  renderAdaptationScenes();
  renderInspector();
  renderRoute();
  renderAbTest();
  renderActivityLog("auditLog", state.audit.slice(0, 8).map((event) => ({
    message: auditEventText(event),
  })), "暂无审计事件。");
}

function estimateCost(shots) {
  return shots.reduce((total, shot) => total + (shot.route?.estimated_cost || 0.02), 0);
}

function renderShot(runtime) {
  const shot = runtime.shot;
  const selected = state.selectedShotId === shot.shot_id;
  const status = runtime.review_status;
  const jobStatus = runtime.job?.status;
  const retryable = !runtime.artifact && ["FAILED", "QUALITY_REJECTED", "RETRY_WAIT"].includes(jobStatus);
  const queued = !runtime.artifact && ["VALIDATED", "QUEUED", "ADMITTED"].includes(jobStatus);
  const archived = Boolean(state.project?.archived);
  const variantCount = (runtime.variants || []).length;
  const simulated = isMockPreview(runtime);
  const statusLabel = status === "APPROVED" ? "已通过" : (
    status === "CHANGES_REQUESTED" ? "需修改" : (
      runtime.artifact ? "待审核" : (
        queued ? "排队中" : (
          jobStatus === "QUALITY_REJECTED" ? "质量未通过" : (
          jobStatus === "FAILED" ? (runtime.job?.retry_at ? "已安排重试" : "生成失败") : (
          jobStatus === "RETRY_WAIT" ? "等待重试" : "未生成"
          )
        )
        )
      )
    )
  );
  let action = "";
  if (archived) {
    action = `<button class="button button-quiet" data-action="select" data-shot-id="${shot.shot_id}">查看</button>`;
  } else if (queued) {
    action = `<button class="button button-secondary" data-action="process-job" data-job-id="${runtime.current_job_id}">执行</button>`;
  } else if (retryable) {
    const schedule = !runtime.job?.retry_at
      ? `<button class="button button-quiet" data-action="schedule-retry" data-shot-id="${shot.shot_id}">安排重试</button>`
      : "";
    action = `<button class="button button-revise" data-action="retry" data-shot-id="${shot.shot_id}">重试 <span aria-hidden="true">↻</span></button>${schedule}`;
  } else if (!runtime.artifact) {
    action = `<button class="button button-quiet" data-action="submit" data-shot-id="${shot.shot_id}">生成 <span aria-hidden="true">→</span></button>`;
  } else if (status === "PENDING") {
    action = `
      <button class="button button-approve" data-action="approve" data-shot-id="${shot.shot_id}">通过 <span aria-hidden="true">✓</span></button>
      <button class="button button-reject" data-action="request-changes" data-shot-id="${shot.shot_id}">要求修改</button>
    `;
  } else if (status === "CHANGES_REQUESTED") {
    action = `<button class="button button-revise" data-action="revise" data-shot-id="${shot.shot_id}">执行返工 <span aria-hidden="true">↻</span></button>`;
  } else {
    action = `<button class="button button-quiet" data-action="select" data-shot-id="${shot.shot_id}">查看</button>`;
  }
  return `
    <article class="shot-card ${selected ? "is-selected" : ""} ${status === "APPROVED" ? "is-ready" : ""} ${status === "CHANGES_REQUESTED" ? "is-changes" : ""}" data-shot-card="${shot.shot_id}">
      <div class="shot-index">${String(shot.shot_id).split("_").pop()}</div>
      <div class="shot-copy" data-action="select" data-shot-id="${shot.shot_id}">
        <div class="shot-title-row"><span class="shot-title">${escapeHtml(shot.scene)}</span><span class="revision-label">R${runtime.revision}${variantCount ? ` · ${variantCount}V` : ""}</span>${simulated ? '<span class="simulation-badge">模拟预览</span>' : ""}</div>
        <div class="shot-meta">${escapeHtml(shot.description)} · ${shot.duration_seconds}s</div>
        <div class="shot-status ${status === "APPROVED" ? "approved" : ""} ${status === "CHANGES_REQUESTED" ? "changes" : ""}">${statusLabel}</div>
      </div>
      <div class="shot-actions">${action}</div>
    </article>
  `;
}

function renderInspector() {
  const runtime = (state.project?.shots || []).find((shot) => shot.shot.shot_id === state.selectedShotId);
  if (!runtime) {
    $("inspectorEmpty").hidden = false;
    $("inspectorContent").hidden = true;
    $("inspectorTitle").textContent = "请选择镜头";
    $("inspectorRevision").textContent = "";
    fillShotEditor(null);
    return;
  }
  $("inspectorEmpty").hidden = true;
  $("inspectorContent").hidden = false;
  $("inspectorTitle").textContent = runtime.shot.shot_id;
  $("inspectorRevision").textContent = `R${runtime.revision}`;
  $("inspectorScene").textContent = runtime.shot.scene;
  $("inspectorMotion").textContent = motionLabel(runtime.spec.intent.camera_motion);
  const simulated = isMockPreview(runtime);
  $("inspectorProvider").textContent = simulated
    ? "模拟服务商（预览）"
    : (runtime.route?.provider || (runtime.artifact ? "未知" : "未执行"));
  $("inspectorQuality").textContent = runtime.quality
    ? (runtime.quality.passed ? "已通过" : "未通过")
    : "待检查";
  $("inspectorJob").textContent = labelFor(runtime.job?.status || "未创建");
  $("inspectorDescription").textContent = runtime.shot.description;
  renderQualityEvidence(runtime);
  fillShotEditor(runtime);
  const media = $("inspectorMedia");
  if (runtime.artifact) {
    const url = artifactUrl(state.project.project_id, runtime.artifact.uri);
    if (runtime.artifact.kind === "image") {
      media.innerHTML = `<img alt="${escapeHtml(runtime.shot.shot_id)}" loading="lazy" src="${url}">${simulated ? '<span class="media-simulation-badge">模拟预览</span>' : ""}`;
    } else {
      media.innerHTML = `<video controls preload="metadata" src="${url}"></video>${simulated ? '<span class="media-simulation-badge">模拟预览</span>' : ""}`;
    }
  } else {
    media.innerHTML = `<div class="media-placeholder">暂无媒体产物</div>`;
  }
  const actions = $("inspectorActions");
  const queued = !runtime.artifact && ["VALIDATED", "QUEUED", "ADMITTED"].includes(runtime.job?.status);
  const retryable = !runtime.artifact && ["FAILED", "QUALITY_REJECTED", "RETRY_WAIT"].includes(runtime.job?.status);
  const compareAction = state.project?.archived ? "" : `<button class="button button-quiet" data-action="compare-shot" data-shot-id="${runtime.shot.shot_id}">对比候选</button>`;
  if (state.project?.archived) {
    actions.innerHTML = `<span class="shot-status">项目已归档</span>`;
  } else if (queued) {
    actions.innerHTML = `<button class="button button-secondary" data-action="process-job" data-job-id="${runtime.current_job_id}">执行排队任务</button>${compareAction}`;
  } else if (retryable) {
    const schedule = !runtime.job?.retry_at
      ? `<button class="button button-quiet" data-action="schedule-retry" data-shot-id="${runtime.shot.shot_id}">安排重试</button>`
      : "";
    actions.innerHTML = `<button class="button button-revise" data-action="retry" data-shot-id="${runtime.shot.shot_id}">重试镜头 <span aria-hidden="true">↻</span></button>${schedule}${compareAction}`;
  } else if (!runtime.artifact) {
    actions.innerHTML = `<button class="button button-secondary" data-action="submit" data-shot-id="${runtime.shot.shot_id}">生成镜头</button>${compareAction}`;
  } else if (runtime.review_status === "PENDING") {
    actions.innerHTML = `
      <button class="button button-approve" data-action="approve" data-shot-id="${runtime.shot.shot_id}">通过 <span aria-hidden="true">✓</span></button>
      <button class="button button-reject" data-action="request-changes" data-shot-id="${runtime.shot.shot_id}">要求修改</button>
      ${compareAction}
    `;
  } else if (runtime.review_status === "CHANGES_REQUESTED") {
    actions.innerHTML = `<button class="button button-revise" data-action="revise" data-shot-id="${runtime.shot.shot_id}">执行返工 <span aria-hidden="true">↻</span></button>${compareAction}`;
  } else {
    actions.innerHTML = `<span class="shot-status approved">已通过，可交付 ✓</span>${compareAction}`;
  }
}

function renderQualityEvidence(runtime) {
  const quality = runtime.quality || {};
  const visual = quality.visual_evaluation;
  const external = quality.external_evaluation;
  const names = {decode: "媒体解码", duration: "时长", resolution: "分辨率", format: "格式", visual_exposure: "画面曝光", visual_contrast: "画面对比度", visual_decode: "画面解码", external_decision: "外部评估结论", artifact_integrity: "文件完整性"};
  const checks = [...(quality.checks || []), ...(visual && !visual.blocking ? visual.checks : [])];
  $("qualityEvidence").innerHTML = checks.map((check) => runtimeRow(names[check.name] || check.name,
    check.passed ? "通过" : "未通过", `实际：${JSON.stringify(check.observed)} · 期望：${JSON.stringify(check.expected)}`)).join("")
    + (visual ? runtimeRow("抽帧", `${visual.frames?.length || 0} 帧`, visual.blocking ? "阻断门禁" : "辅助审核") : "")
    + (external ? runtimeRow("外部评估", external.passed == null ? "未完成" : external.passed ? "通过" : "未通过", external.error || external.provider) : "")
    + (quality.artifact_sha256 ? runtimeRow("证据 SHA256", quality.artifact_sha256, quality.evaluated_at || "") : "");
  $("recheckQualityButton").disabled = !runtime.artifact || Boolean(state.project?.archived || state.project?.release);
}

async function recheckQuality() {
  const projectId = state.projectId;
  const shotId = state.selectedShotId;
  if (!projectId || !shotId) return;
  const button = $("recheckQualityButton");
  setBusy(button, true, "质检中");
  try {
    await request(`/projects/${encodeURIComponent(projectId)}/shots/${encodeURIComponent(shotId)}/review-quality`, {method: "POST", body: JSON.stringify({actor: "studio-reviewer"})});
    if (state.projectId === projectId) await loadProjectContext(projectId);
    logEvent("镜头质检已更新。", "muted");
  } catch (error) {
    logEvent(error.message, "muted");
    $("qualityEvidence").textContent = error.message;
  } finally {
    setBusy(button, false);
    renderInspector();
  }
}

async function checkProviderHealth() {
  const buttons = [$("providerHealthButton"), $("providerDiagnosticsButton")];
  buttons.forEach((button) => setBusy(button, true, "检查中"));
  setActiveTab("providers");
  try {
    const diagnostics = await request("/providers/diagnostics");
    const health = diagnostics.health;
    state.providerStatus = diagnostics.status;
    state.providerDiagnostics = diagnostics;
    renderProviderCenter();
    const label = health.healthy
      ? `${diagnostics.grade === "SIMULATION" ? "模拟服务商在线" : "服务商在线"} · ${health.provider}`
      : health.configured
        ? `服务商离线 · ${health.provider}`
        : `服务商不可用 · ${health.provider}`;
    $("connectionState").innerHTML = `<i></i> ${escapeHtml(label)}`;
    $("connectionState").classList.toggle("connection-warning", !health.healthy);
    logEvent(
      `${localizeProviderDiagnosticMessage(health.message)}${health.latency_ms != null ? ` · ${health.latency_ms} 毫秒` : ""}`,
      health.healthy ? "muted" : "muted",
    );
  } catch (error) {
    $("connectionState").innerHTML = `<i></i> 服务商健康状态未知`;
    $("connectionState").classList.add("connection-warning");
    logEvent(error.message, "muted");
  } finally {
    buttons.forEach((button) => setBusy(button, false));
  }
}

async function warmupProvider() {
  const button = $("providerWarmupButton");
  setBusy(button, true, "预热中");
  try {
    const result = await request("/providers/warmup", {method: "POST", body: JSON.stringify({})});
    const total = result.providers?.length || 0;
    const ready = result.providers?.filter((item) => item.ready).length || 0;
    $("providerWarmupResult").textContent = `${ready}/${total} 个 Provider 已完成预热`;
    logEvent($("providerWarmupResult").textContent, ready === total ? "muted" : "error");
    await checkProviderHealth();
  } catch (error) {
    $("providerWarmupResult").textContent = error.message;
  } finally {
    setBusy(button, false);
  }
}

async function validateProviderContract() {
  if (!state.projectId) return;
  const button = $("providerContractButton");
  setBusy(button, true, "验证中");
  try {
    const report = await request(`/projects/${encodeURIComponent(state.projectId)}/providers/contracts/validate`, {
      method: "POST",
      body: JSON.stringify({ actor: "studio-operations" }),
    });
    state.providerContract = report;
    renderProviderContract();
    logEvent(report.summary?.unroutable_shot_count ? "Provider 契约发现不可路由镜头。" : "Provider 接口契约和路由检查已通过。", "muted");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

function renderJobs() {
  const allJobs = state.jobs || [];
  const filteredJobs = state.jobStatusFilter
    ? allJobs.filter((job) => job.status === state.jobStatusFilter)
    : allJobs;
  $("jobCount").textContent = `${filteredJobs.length} / ${allJobs.length} 个任务`;
  $("recoverStaleButton").disabled = Boolean(state.project?.archived) || !state.projectId;
  const counts = state.operations?.job_counts || {};
  const retry = state.operations?.retry || {};
  $("jobSummary").innerHTML = `
    <div><span>排队中</span><strong>${(counts.VALIDATED || 0) + (counts.QUEUED || 0) + (counts.ADMITTED || 0)}</strong></div>
    <div><span>失败</span><strong>${(counts.FAILED || 0) + (counts.QUALITY_REJECTED || 0)}</strong></div>
    <div><span>等待重试</span><strong>${retry.scheduled_jobs || 0}</strong></div>
    <div><span>已成功</span><strong>${counts.SUCCEEDED || 0}</strong></div>
  `;
  if (!filteredJobs.length) {
    $("jobsLog").innerHTML = `<div class="project-empty">没有符合当前筛选条件的任务。</div>`;
    return;
  }
  $("jobsLog").innerHTML = filteredJobs.slice().reverse().map((job) => {
    const failed = ["FAILED", "QUALITY_REJECTED", "RETRY_WAIT"].includes(job.status);
    const processable = ["VALIDATED", "QUEUED", "ADMITTED"].includes(job.status);
    const terminal = ["SUCCEEDED", "FAILED", "QUALITY_REJECTED", "CANCELED"].includes(job.status)
      && !job.retry_at;
    const statusClass = failed
      ? (job.status === "RETRY_WAIT" ? "is-waiting" : "is-failed")
      : (job.status === "RUNNING" ? "is-running" : (processable ? "is-queued" : ""));
    const retry = failed
      ? `<button class="button button-revise" data-action="retry" data-shot-id="${escapeHtml(job.spec.shot_id)}">重试</button>`
      : "";
    const process = processable && !state.project?.archived
      ? `<button class="button button-secondary" data-action="process-job" data-job-id="${escapeHtml(job.job_id)}">执行</button>`
      : "";
    const cancel = !terminal && !state.project?.archived
      ? `<button class="button button-reject" data-action="cancel-job" data-job-id="${escapeHtml(job.job_id)}">取消</button>`
      : "";
    return `
      <div class="job-row">
        <div>
          <strong>${escapeHtml(job.spec.shot_id)}</strong>
          <span>${escapeHtml(job.job_id)} · ${job.attempts} 次尝试</span>
          ${job.worker_id ? `<span>Worker：${escapeHtml(job.worker_id)}${job.lease_expires_at ? ` · 租约至 ${escapeHtml(formatRetryAt(job.lease_expires_at))}` : ""}</span>` : ""}
          ${job.retry_at ? `<span>${escapeHtml(formatRetryAt(job.retry_at))}${job.last_error ? ` · ${escapeHtml(job.last_error)}` : ""}</span>` : ""}
          ${retry}
          ${process}
          ${cancel}
        </div>
        <span class="job-state ${statusClass}">${escapeHtml(labelFor(job.status))}</span>
      </div>
    `;
  }).join("");
}

function renderPolicy() {
  const policy = state.project?.policy || state.operations?.policy || {};
  const compliance = state.compliance || state.operations?.compliance || {};
  const registry = compliance.registry || {};
  const reports = policy.reports || [];
  const latestFindings = reports.flatMap((report) => (
    report.findings || []
  )).slice(-12).reverse();
  const blocked = policy.passed === false || compliance.passed === false;
  $("policyStatus").textContent = blocked ? "已拦截" : "通过";
  $("policyStatus").classList.toggle("connection-warning", blocked);
  $("policyReportCount").textContent = String(policy.count || reports.length || 0);
  $("policyWarningCount").textContent = String(policy.warning_count || 0);
  $("policyBlockedCount").textContent = String(
    (policy.blocked_count || 0) + (compliance.summary?.blocking_failures || 0)
  );
  const registryChecked = Number(registry.checked_count || 0);
  const registryRegistered = Number(registry.registered_count || 0);
  const registryUnregistered = Number(registry.unregistered_count || 0);
  $("registryStatus").textContent = compliance.registry
    ? (registry.passed ? "已通过" : "需处理")
    : "待检查";
  $("registryStatus").classList.toggle("connection-warning", registry.passed === false);
  $("registryRegistered").textContent = `${registryRegistered} / ${registryChecked}`;
  $("registryUnregistered").textContent = String(registryUnregistered);
  $("registrySource").textContent = registry.source ? `来源：${registry.source}` : "来源未知";
  const complianceRows = (compliance.checks || []).map((check) => `
    <div class="policy-row ${check.passed ? "is-passed" : "is-blocked"}">
      <strong>${check.passed ? "通过" : "已拦截"} · ${escapeHtml(checkLabel(check))}</strong>
      <span>${escapeHtml(checkNameLabel(check.name))} · ${check.blocking ? "阻断项" : "提示项"}</span>
      <span>${escapeHtml(check.passed ? "已满足发布门禁。" : "请在发布前处理。")}</span>
    </div>
  `);
  if (!latestFindings.length && !complianceRows.length) {
    $("policyFindings").innerHTML = `<div class="project-empty">当前项目暂无策略发现。</div>`;
    return;
  }
  const policyRows = latestFindings.map((finding) => {
    const blocked = finding.severity === "block";
    return `
      <div class="policy-row ${blocked ? "is-blocked" : "is-warning"}">
        <strong>${escapeHtml(finding.severity === "block" ? "拦截" : "警告")} · ${escapeHtml(policyCategoryLabel(finding.category))}</strong>
        <span>${escapeHtml(fieldLabel(finding.field))} 命中“${escapeHtml(finding.term)}”</span>
        <span>${escapeHtml(finding.message === "Text attempts to bypass governance or prompt boundaries." ? "文本试图绕过治理或提示词边界。" : finding.message === "Text should receive human attention in review." ? "该文本需要人工复核。" : finding.message)}</span>
      </div>
    `;
  });
  $("policyFindings").innerHTML = [...policyRows, ...complianceRows].join("");
}

function renderRegistryManagement() {
  const registry = state.registry || {};
  const management = registry.management || {};
  const sync = management.sync || {};
  $("registryExportLink").href = "/governance/license-registry/export";
  const updatedLabel = management.updated_at
    ? `最近更新：${management.updated_by || "系统"} · ${formatTimestamp(management.updated_at)}`
    : "当前使用启动配置";
  const syncLabel = sync.last_status
    ? `同步：${sync.last_status === "SYNCED" ? "已同步" : sync.last_status === "NOT_MODIFIED" ? "无变化" : "失败"}`
    : "同步未运行";
  $("registryManagement").textContent = `${updatedLabel} · ${syncLabel}`;
}

async function loadRegistry() {
  state.registry = await request("/governance/license-registry");
  renderRegistryManagement();
}

async function validateRegistry() {
  if (!state.registry?.records?.length) return;
  const button = $("registryValidateButton");
  setBusy(button, true, "校验中");
  try {
    const result = await request("/governance/license-registry/validate", {
      method: "POST",
      body: JSON.stringify({
        records: state.registry.records,
        source: "studio-current",
        actor: "studio-governance",
      }),
    });
    logEvent(`许可证台账校验通过，共 ${result.summary.record_count} 条记录。`, "muted");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function syncRegistry() {
  const button = $("registrySyncButton");
  setBusy(button, true, "同步中");
  try {
    const result = await request("/governance/license-registry/sync", {
      method: "POST",
      body: JSON.stringify({ actor: "studio-governance" }),
    });
    state.registry = result;
    renderRegistryManagement();
    const label = result.sync_result?.not_modified ? "已是最新版本" : "已从集中式台账同步";
    logEvent(`${label}，共 ${result.summary.record_count} 条记录。`);
    if (state.projectId) await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function importRegistryFile(event) {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file) return;
  try {
    const payload = JSON.parse(await file.text());
    const records = Array.isArray(payload) ? payload : payload.records;
    if (!Array.isArray(records) || !records.length) {
      throw new Error("台账文件必须包含 records 数组");
    }
    const result = await request("/governance/license-registry/import", {
      method: "POST",
      body: JSON.stringify({
        records,
        source: `studio-upload:${file.name}`,
        actor: "studio-governance",
      }),
    });
    state.registry = result;
    renderRegistryManagement();
    logEvent(`许可证台账已导入，共 ${result.summary.record_count} 条记录。`);
    if (state.projectId) await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  }
}

function renderContinuity() {
  const report = state.continuity || {};
  const summary = report.summary || {};
  const checks = report.checks || [];
  const timeline = report.timeline || [];
  const duration = Number(summary.duration_seconds || 0);
  const failed = checks.filter((check) => !check.passed).length;
  $("continuityStatus").textContent = report.passed === true ? "通过" : report.passed === false ? "需处理" : "待检查";
  $("continuityStatus").classList.toggle("connection-warning", report.passed === false);
  $("continuityShotCount").textContent = String(summary.shot_count || timeline.length || 0);
  $("continuityDuration").textContent = `${duration % 1 ? duration.toFixed(1) : duration} 秒`;
  $("continuitySceneCount").textContent = String(Object.keys(report.scene_counts || {}).filter((scene) => scene).length);
  $("continuityFailed").textContent = String(failed);
  const exportVisible = Boolean(state.continuityUrl);
  $("continuityLink").href = exportVisible ? state.continuityUrl : "#";
  $("continuityLink").hidden = !exportVisible;
  const editTimeline = state.editTimeline || {};
  const canExportTimeline = Boolean(state.projectId && (editTimeline.video_clips || []).length);
  $("timelineOtioExportButton").disabled = !canExportTimeline || Boolean(state.project?.archived);
  $("timelineEdlExportButton").disabled = !canExportTimeline || Boolean(state.project?.archived);
  $("timelineExportLink").href = state.timelineExportUrl || "#";
  $("timelineExportLink").hidden = !state.timelineExportUrl;
  $("continuityChecks").innerHTML = checks.length
    ? checks.map((check) => `
      <div class="policy-row ${check.passed ? "is-passed" : "is-blocked"}">
        <strong>${check.passed ? "通过" : "需处理"} · ${escapeHtml(checkLabel(check))}</strong>
        <span>${escapeHtml(check.name || "检查项")} · 期望 ${escapeHtml(formatObserved(check.expected))}</span>
        <span>实际：${escapeHtml(formatObserved(check.observed))}</span>
      </div>
    `).join("")
    : `<div class="project-empty">完成故事规划后，这里会显示连续性检查。</div>`;
  $("continuityTimeline").innerHTML = timeline.length
    ? timeline.map((item) => `
      <div class="trace-row">
        <div>
          <strong>${escapeHtml(item.shot_id || "镜头")}</strong>
          <span>${escapeHtml(item.scene || "未填写场景")} · ${escapeHtml((item.characters || []).join("、") || "未填写角色")}</span>
          <span>${item.subtitle ? `字幕：${escapeHtml(item.subtitle)}` : "暂无字幕"}</span>
        </div>
        <div class="trace-meta">
          <span>${Number(item.start_seconds || 0).toFixed(1)} - ${Number(item.end_seconds || 0).toFixed(1)} 秒</span>
          <span>${Number(item.duration_seconds || 0).toFixed(1)} 秒</span>
        </div>
      </div>
    `).join("")
    : `<div class="project-empty">暂无镜头时间轴。</div>`;
}

function dialogueShotWindows() {
  const windows = {};
  let cursor = 0;
  for (const runtime of state.project?.shots || []) {
    const duration = Number(runtime.shot.duration_seconds || 0);
    windows[runtime.shot.shot_id] = { start: cursor, end: cursor + duration };
    cursor += duration;
  }
  return windows;
}

function newDialogueLine() {
  const firstShot = state.project?.shots?.[0]?.shot;
  const firstCharacter = state.project?.brief?.characters?.[0] || "旁白";
  const start = firstShot ? 0 : 0;
  const end = firstShot ? Math.min(Number(firstShot.duration_seconds || 1), 3) : 1;
  return {
    line_id: `dlg_${timestampKey()}_${Math.random().toString(36).slice(2, 7)}`,
    shot_id: firstShot?.shot_id || "",
    speaker: firstCharacter,
    text: "",
    start_seconds: start,
    end_seconds: end,
    voice: "default",
    language: "zh-CN",
  };
}

function syncDialogueDraft(project, force = false) {
  if (!project) return;
  if (!force && state.dialogueProjectId === project.project_id) return;
  state.dialogueProjectId = project.project_id;
  state.dialogueDraft = (project.dialogue_timeline?.lines || []).map((line) => ({ ...line }));
  state.dialogueDirty = false;
}

function dialogueOption(value, label, selected) {
  return `<option value="${escapeHtml(value)}"${value === selected ? " selected" : ""}>${escapeHtml(label)}</option>`;
}

function renderDialogueTimeline() {
  const list = $("dialogueLineList");
  if (!list) return;
  const shots = state.project?.shots || [];
  const characters = state.project?.brief?.characters || [];
  const windows = dialogueShotWindows();
  if (!shots.length) {
    list.innerHTML = '<div class="project-empty">生成镜头计划后可编辑对白。</div>';
    return;
  }
  if (!state.dialogueDraft.length) {
    list.innerHTML = '<div class="project-empty">暂无台词。</div>';
    return;
  }
  list.innerHTML = state.dialogueDraft.map((line, index) => {
    const shotOptions = shots.map((runtime) => {
      const shot = runtime.shot;
      const window = windows[shot.shot_id];
      return dialogueOption(shot.shot_id, `${shot.shot_id} · ${window.start.toFixed(1)}-${window.end.toFixed(1)}s`, line.shot_id);
    }).join("");
    const speakerOptions = [...characters, "旁白"].filter((value, position, values) => values.indexOf(value) === position)
      .map((value) => dialogueOption(value, value, line.speaker)).join("");
    const disabled = state.project?.archived || Boolean(state.project?.release) ? " disabled" : "";
    return `
      <div class="dialogue-line" data-dialogue-index="${index}">
        <select data-dialogue-field="shot_id" aria-label="对白镜头"${disabled}>${shotOptions}</select>
        <select data-dialogue-field="speaker" aria-label="对白角色"${disabled}>${speakerOptions}</select>
        <input data-dialogue-field="start_seconds" type="number" min="0" step="0.1" value="${Number(line.start_seconds || 0).toFixed(1)}" aria-label="开始时间"${disabled} />
        <input data-dialogue-field="end_seconds" type="number" min="0.1" step="0.1" value="${Number(line.end_seconds || 0).toFixed(1)}" aria-label="结束时间"${disabled} />
        <input data-dialogue-field="voice" type="text" value="${escapeHtml(line.voice || "default")}" aria-label="音色" placeholder="音色"${disabled} />
        <button class="button button-quiet" type="button" data-dialogue-remove="${index}" title="删除台词"${disabled}>删除</button>
        <textarea data-dialogue-field="text" maxlength="1200" aria-label="台词内容" placeholder="输入台词"${disabled}>${escapeHtml(line.text || "")}</textarea>
      </div>`;
  }).join("");
}

function renderDialogueControls() {
  const hasProject = Boolean(state.projectId && state.project);
  const locked = !hasProject || Boolean(state.project?.archived || state.project?.release);
  const hasPlan = Boolean(state.project?.shots?.length);
  const ready = hasPlan && state.dialogueDraft.length > 0 && state.dialogueDraft.every((line) => (
    String(line.text || "").trim() && String(line.shot_id || "").trim() && Number(line.end_seconds) > Number(line.start_seconds)
  ));
  $("dialogueAddLineButton").disabled = locked || !hasPlan;
  $("dialogueLicense").disabled = locked || !hasPlan;
  $("dialogueSource").disabled = locked || !hasPlan;
  $("dialogueGenerateButton").disabled = locked || !ready;
  const timeline = state.project?.dialogue_timeline;
  $("dialogueStatus").textContent = !hasPlan
    ? "等待镜头计划"
    : timeline?.current && !state.dialogueDirty
      ? `${state.dialogueDraft.length} 句已合成`
      : `${state.dialogueDraft.length} 句待合成`;
}

function renderAssets() {
  const project = state.project || {};
  const inventory = state.assets || {};
  const summary = inventory.summary || {};
  const generated = inventory.generated_assets || [];
  const references = inventory.reference_assets || [];
  const outputs = inventory.outputs || [];
  const credentials = project.content_credentials?.credentials || [];
  const finalMediaCredential = project.content_credentials?.summary?.final_media || {};
  const credentialsByAsset = new Map(credentials.map((credential) => [credential.claim?.asset_id, credential]));
  const totalAssets = (summary.reference_assets || 0) + (summary.generated_assets || 0) + (summary.outputs || 0);
  $("assetCount").textContent = `${totalAssets} 个资产`;
  $("assetRefs").textContent = String(summary.reference_assets || 0);
  $("assetGenerated").textContent = formatKindBreakdown(summary.generated_by_kind, summary.generated_assets || 0);
  $("assetCurrent").textContent = formatKindBreakdown(summary.current_by_kind, summary.current_media_assets || summary.current_video_assets || 0);
  $("assetOutputs").textContent = String(summary.outputs || 0);
  const finalCredentialStatus = $("finalMediaCredentialStatus");
  const finalCredentialReady = Boolean(finalMediaCredential.ready);
  const finalMediaAvailable = Boolean(finalMediaCredential.available);
  finalCredentialStatus.classList.toggle("is-ready", finalCredentialReady);
  finalCredentialStatus.classList.toggle("is-blocked", finalMediaAvailable && !finalCredentialReady);
  finalCredentialStatus.textContent = !finalMediaAvailable
    ? "最终成片凭证：等待导出成片"
    : finalCredentialReady
      ? "最终成片凭证：C2PA 已签名并独立验证"
      : "最终成片凭证：需要为当前成片创建并验证 C2PA 凭证";
  const hasProject = Boolean(state.projectId && state.project);
  const archived = Boolean(state.project?.archived);
  const kind = $("referenceKind");
  const character = $("referenceCharacter");
  const selectedCharacter = character.value;
  const characters = state.project?.brief?.characters || [];
  character.innerHTML = [
    `<option value="">通用参考</option>`,
    ...characters.map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`),
  ].join("");
  character.value = characters.includes(selectedCharacter) ? selectedCharacter : (characters[0] || "");
  character.disabled = !hasProject || archived || kind.value !== "character_reference";
  kind.disabled = !hasProject || archived;
  $("referenceLicense").disabled = !hasProject || archived;
  $("referenceChooseButton").disabled = !hasProject || archived;
  $("referenceUploadButton").disabled = !hasProject || archived || !state.pendingReferenceFile;
  if (!state.pendingReferenceFile) $("referenceFileName").textContent = "未选择文件";
  const audioLocked = archived || Boolean(state.project?.release);
  $("audioChooseButton").disabled = !hasProject || audioLocked;
  $("audioUploadButton").disabled = !hasProject || audioLocked || !state.pendingAudioFile;
  $("audioRemoveButton").disabled = !hasProject || audioLocked || !state.project?.audio_track;
  $("audioLicense").disabled = !hasProject || audioLocked;
  $("audioSource").disabled = !hasProject || audioLocked;
  $("voiceoverText").disabled = !hasProject || audioLocked;
  ["voiceoverLanguage", "voiceoverLicense", "voiceoverSource"].forEach((id) => $(id).disabled = !hasProject || audioLocked);
  $("voiceoverStatus").textContent = state.speechStatus?.preview_only ? "测试音 · 非人声"
    : state.speechStatus?.configured ? (state.speechStatus.mode === "system" ? "系统配音" : "外部配音") : "配音未就绪";
  $("voiceoverButton").textContent = state.speechStatus?.preview_only ? "生成测试音" : "生成配音";
  $("voiceoverVoice").disabled = !hasProject || audioLocked;
  $("voiceoverButton").disabled = !hasProject || audioLocked || !$("voiceoverText").value.trim();
  $("audioFileName").textContent = state.pendingAudioFile?.name
    || (state.project?.audio_track
      ? String(state.project.audio_track).replaceAll("\\", "/").split("/").pop()
      : "未配置");

  const audioUrl = state.project?.audio_track
    ? artifactUrl(state.project.project_id, state.project.audio_track)
    : "";
  const audioPreview = $("audioPreview");
  audioPreview.src = audioUrl || "";
  audioPreview.hidden = !audioUrl;
  const lipConfigured = state.lipsyncStatus?.configured && state.lipsyncStatus?.mode !== "disabled";
  $("lipsyncStatus").textContent = !lipConfigured
    ? "适配器未启用"
    : state.project?.lipsync_artifact ? "已生成，可重新执行" : "已配置，等待执行";
  $("lipsyncButton").disabled = !hasProject || audioLocked || !project?.final_mp4 || !project?.audio_track || !lipConfigured;
  renderDialogueTimeline();
  renderDialogueControls();

  const rows = [
    ...outputs.map((asset) => ({
      assetId: asset.kind,
      title: assetKindLabel(asset.kind),
      meta: `${asset.exists ? "就绪" : "缺失"} · ${formatBytes(asset.size_bytes)}${asset.kind === "audio_track" && asset.license ? ` · ${asset.license} · ${asset.source || "来源未标注"}` : ""}`,
      uri: asset.uri,
      derivatives: asset.derivatives || [],
      kind: "output",
    })),
    ...generated.map((asset) => ({
      assetId: asset.asset_id,
      title: `${asset.shot_id} · R${asset.revision}${asset.variant_id ? ` · ${asset.variant_id}` : ""}`,
      meta: `${asset.provider || "服务商"} · ${assetKindLabel(asset.kind)}${asset.candidate ? " · 候选" : ""}${asset.promoted ? " · 已采用" : ""} · ${formatBytes(asset.size_bytes)}`,
      uri: asset.uri,
      metadataUri: asset.metadata_uri,
      derivatives: asset.derivatives || [],
      kind: "generated",
    })),
    ...references.map((asset) => ({
      assetId: asset.asset_id,
      title: asset.name,
      meta: `${asset.license || "未标注许可证"} · ${asset.version || "v1"} · 用于 ${(asset.used_by_shots || []).length} 个镜头`,
      uri: asset.uri,
      derivatives: asset.derivatives || [],
      kind: "reference",
    })),
  ];
  if (!rows.length) {
    $("assetList").innerHTML = `<div class="project-empty">暂无资产。</div>`;
    return;
  }
  $("assetList").innerHTML = rows.map((asset) => {
    const url = asset.assetId
      ? `/projects/${encodeURIComponent(state.projectId)}/assets/${encodeURIComponent(asset.assetId)}/download`
      : (asset.uri ? artifactUrl(state.projectId, asset.uri) : "");
    const metadataUrl = asset.metadataUri ? artifactUrl(state.projectId, asset.metadataUri) : "";
    const derivativeLinks = (asset.derivatives || []).filter((derivative) => derivative.available && derivative.derivative_id).map((derivative) => {
      const derivativeUrl = `/projects/${encodeURIComponent(state.projectId)}/assets/${encodeURIComponent(asset.assetId)}/derivatives/${encodeURIComponent(derivative.derivative_id)}/download`;
      return `<a href="${derivativeUrl}" target="_blank" rel="noreferrer">${escapeHtml({thumbnail: "缩略图", proxy: "代理片", contact_sheet: "联系表", waveform: "波形"}[derivative.kind] || derivative.kind)}</a>`;
    }).join("");
    const canDerive = Boolean(asset.assetId && asset.uri && !state.project?.archived);
    const credential = credentialsByAsset.get(asset.assetId);
    const credentialStatus = credential?.c2pa?.status;
    const verificationStatus = credential?.verification?.status;
    const credentialLabel = verificationStatus === "SIGNED_VERIFIED"
      ? "C2PA 已独立验证"
      : verificationStatus === "INTEGRITY_VERIFIED"
        ? "声明完整性已验证"
        : credentialStatus === "SIGNED_UNVERIFIED"
          ? "验证已签名凭证"
          : credentialStatus === "UNSIGNED"
            ? "验证内容声明"
            : "创建内容凭证";
    const credentialAction = credential ? "verify-content-credential" : "create-content-credential";
    const canCredential = Boolean(asset.assetId && asset.uri && !state.project?.archived);
    return `
      <div class="asset-row">
        <div>
          <strong>${escapeHtml(asset.title)}</strong>
          <span>${escapeHtml(assetKindLabel(asset.kind))} · ${escapeHtml(asset.meta)}</span>
          ${derivativeLinks || canDerive || canCredential ? `<div class="asset-derivatives">${derivativeLinks}${canDerive ? `<button class="button button-quiet" type="button" data-action="generate-derivatives" data-asset-id="${escapeHtml(asset.assetId)}">生成衍生文件</button>` : ""}${canCredential ? `<button class="button button-quiet" type="button" data-action="${credentialAction}" data-asset-id="${escapeHtml(asset.assetId)}"${credential ? ` data-credential-id="${escapeHtml(credential.credential_id)}"` : ""}>${escapeHtml(credentialLabel)}</button>` : ""}</div>` : ""}
        </div>
        ${url || metadataUrl ? `<div class="asset-actions">${url ? `<a href="${url}" target="_blank" rel="noreferrer">打开</a>` : ""}${metadataUrl ? `<a href="${metadataUrl}" target="_blank" rel="noreferrer">元数据</a>` : ""}</div>` : `<span class="job-state">参考</span>`}
      </div>
    `;
  }).join("");
}

async function uploadReferenceAsset() {
  if (!state.projectId || !state.pendingReferenceFile) return;
  const button = $("referenceUploadButton");
  setBusy(button, true, "登记中");
  try {
    const file = state.pendingReferenceFile;
    const content_b64 = arrayBufferToBase64(await file.arrayBuffer());
    const kind = $("referenceKind").value;
    const character = kind === "character_reference"
      ? $("referenceCharacter").value || null
      : null;
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/references`, {
      method: "POST",
      body: JSON.stringify({
        name: file.name,
        content_b64,
        license: $("referenceLicense").value,
        kind,
        character,
        actor: "studio-user",
      }),
    });
    state.pendingReferenceFile = null;
    $("referenceFileInput").value = "";
    logEvent(`参考图 ${result.asset?.name || file.name} 已登记，并已写入镜头规格。`);
    await loadProjectContext(state.projectId);
    setActiveTab("assets");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
    renderAssets();
  }
}

async function uploadAudioTrack() {
  if (!state.projectId || !state.pendingAudioFile) return;
  const button = $("audioUploadButton");
  setBusy(button, true, "应用中");
  try {
    const file = state.pendingAudioFile;
    await request(`/projects/${encodeURIComponent(state.projectId)}/audio`, {
      method: "POST",
      body: JSON.stringify({
        name: file.name,
        content_b64: arrayBufferToBase64(await file.arrayBuffer()),
        license: $("audioLicense").value,
        source: $("audioSource").value.trim() || "studio-upload",
        actor: "studio-postproduction",
      }),
    });
    state.pendingAudioFile = null;
    $("audioFileInput").value = "";
    logEvent(`音频轨 ${file.name} 已应用，旧导出产物已失效。`);
    await loadProjectContext(state.projectId);
    setActiveTab("assets");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
    renderAssets();
  }
}

async function generateVoiceover() {
  if (!state.projectId) return;
  const text = $("voiceoverText").value.trim();
  if (!text) return;
  const button = $("voiceoverButton");
  setBusy(button, true, "生成中");
  try {
    await request(`/projects/${encodeURIComponent(state.projectId)}/voiceover`, {
      method: "POST",
      body: JSON.stringify({
        text,
        voice: $("voiceoverVoice").value.trim() || "default",
        language: $("voiceoverLanguage").value,
        license: $("voiceoverLicense").value,
        source: $("voiceoverSource").value.trim(),
        actor: "studio-postproduction",
      }),
    });
    logEvent("音轨已生成，旧导出产物已失效。", "muted");
    await loadProjectContext(state.projectId);
    setActiveTab("assets");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
    renderAssets();
  }
}

async function generateLipSync() {
  if (!state.projectId) return;
  const button = $("lipsyncButton");
  setBusy(button, true, "生成中");
  try {
    await request(`/projects/${encodeURIComponent(state.projectId)}/lipsync`, {
      method: "POST",
      body: JSON.stringify({actor: "studio-postproduction"}),
    });
    logEvent("口型同步片已生成，并已成为当前项目主样片。", "muted");
    await loadProjectContext(state.projectId);
    setActiveTab("assets");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
    renderAssets();
  }
}

function updateDialogueDraft(index, field, value) {
  const line = state.dialogueDraft[index];
  if (!line) return;
  if (field === "start_seconds" || field === "end_seconds") {
    line[field] = Number(value);
  } else {
    line[field] = value;
  }
  if (field === "shot_id") {
    const window = dialogueShotWindows()[value];
    if (window) {
      line.start_seconds = window.start;
      line.end_seconds = Math.min(window.end, window.start + 3);
    }
  }
  state.dialogueDirty = true;
  renderDialogueControls();
}

async function generateDialogueTimeline() {
  if (!state.projectId || !state.dialogueDraft.length) return;
  const button = $("dialogueGenerateButton");
  const lines = state.dialogueDraft.map((line) => ({
    ...line,
    text: String(line.text || "").trim(),
    start_seconds: Number(line.start_seconds),
    end_seconds: Number(line.end_seconds),
    voice: String(line.voice || "default").trim() || "default",
  }));
  if (lines.some((line) => !line.text || !Number.isFinite(line.start_seconds) || !Number.isFinite(line.end_seconds))) {
    alert("请补全台词内容和时间");
    return;
  }
  setBusy(button, true, "合成中");
  try {
    await request(`/projects/${encodeURIComponent(state.projectId)}/dialogue`, {
      method: "POST",
      body: JSON.stringify({
        lines,
        license: $("dialogueLicense").value,
        source: $("dialogueSource").value.trim(),
        actor: "studio-postproduction",
      }),
    });
    state.dialogueDirty = false;
    state.dialogueProjectId = null;
    logEvent("多角色对白已合成，旧导出产物已失效。", "muted");
    await loadProjectContext(state.projectId);
    setActiveTab("assets");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
    renderAssets();
  }
}

async function removeAudioTrack() {
  if (!state.projectId || !state.project?.audio_track) return;
  const button = $("audioRemoveButton");
  setBusy(button, true, "移除中");
  try {
    await request(`/projects/${encodeURIComponent(state.projectId)}/audio`, {
      method: "DELETE",
      body: JSON.stringify({ actor: "studio-postproduction" }),
    });
    state.pendingAudioFile = null;
    $("audioFileInput").value = "";
    logEvent("项目音频轨已移除，旧导出产物已失效。");
    await loadProjectContext(state.projectId);
    setActiveTab("assets");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
    renderAssets();
  }
}

async function generateAssetDerivatives(assetId) {
  if (!state.projectId || !assetId) return;
  const button = document.querySelector(`[data-action="generate-derivatives"][data-asset-id="${CSS.escape(assetId)}"]`);
  setBusy(button, true, "生成中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/assets/${encodeURIComponent(assetId)}/derivatives`, {
      method: "POST",
      body: JSON.stringify({ actor: "studio-postproduction" }),
    });
    const available = (result.derivatives || []).filter((item) => item.available).length;
    logEvent(`${assetId} 已生成 ${available} 个衍生文件。`, "muted");
    await loadProjectContext(state.projectId);
    setActiveTab("assets");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function createContentCredential(assetId) {
  if (!state.projectId || !assetId) return;
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/content-credentials`, {
      method: "POST",
      body: JSON.stringify({ asset_id: assetId, actor: "studio-governance" }),
    });
    const status = result.credential?.c2pa?.status === "SIGNED_UNVERIFIED" ? "已由签名器产出，待独立验证" : "已生成待签名内容凭证";
    logEvent(`${assetId} ${status}。`, "muted");
    await loadProjectContext(state.projectId);
    setActiveTab("assets");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  }
}

async function verifyContentCredential(credentialId) {
  if (!state.projectId || !credentialId) return;
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/content-credentials/${encodeURIComponent(credentialId)}/verify`, {
      method: "POST",
      body: JSON.stringify({ actor: "studio-governance" }),
    });
    const status = result.verification?.status === "SIGNED_VERIFIED"
      ? "签名和内容均已验证"
      : result.verification?.status === "INTEGRITY_VERIFIED"
        ? "内容声明完整性已验证，未签名"
        : "验证未通过，请查看治理报告";
    logEvent(`${credentialId} ${status}。`, result.verification?.passed ? "muted" : "normal");
    await loadProjectContext(state.projectId);
    setActiveTab("assets");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  }
}

async function exportEditTimeline(format, buttonId) {
  if (!state.projectId) return;
  const button = $(buttonId);
  setBusy(button, true, "导出中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/timeline/export`, {
      method: "POST",
      body: JSON.stringify({ format, actor: "studio-editor" }),
    });
    state.editTimeline = result.timeline;
    state.timelineExportUrl = artifactUrl(state.projectId, result.path);
    logEvent(`剪辑清单已导出：${format === "otio_json" ? "OTIO" : "EDL"}。`, "muted");
    await loadProjectContext(state.projectId);
    setActiveTab("continuity");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function createPromptVersion() {
  if (!state.projectId) return;
  const template = $("promptTemplateInput").value.trim();
  if (!template) return;
  const button = $("promptCreateButton");
  setBusy(button, true, "创建中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/prompts`, {
      method: "POST",
      body: JSON.stringify({
        key: $("promptKeySelect").value,
        label: $("promptLabelInput").value.trim(),
        template,
        activate: true,
        actor: "studio-prompt-editor",
      }),
    });
    $("promptLabelInput").value = "";
    state.llmops = { ...(state.llmops || {}), prompt_registry: result.registry };
    logEvent(`提示词 ${result.prompt.key} V${result.prompt.version} 已启用。`, "muted");
    await loadProjectContext(state.projectId);
    setActiveTab("eval");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function activatePromptVersion(promptId) {
  if (!state.projectId || !promptId) return;
  const button = document.querySelector(`[data-action="activate-prompt"][data-prompt-id="${CSS.escape(promptId)}"]`);
  setBusy(button, true, "启用中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/prompts/${encodeURIComponent(promptId)}/activate`, {
      method: "POST",
      body: JSON.stringify({ actor: "studio-prompt-editor" }),
    });
    state.llmops = { ...(state.llmops || {}), prompt_registry: result.registry };
    logEvent(`提示词 ${result.prompt.key} V${result.prompt.version} 已启用。`, "muted");
    await loadProjectContext(state.projectId);
    setActiveTab("eval");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function recordProjectAnnotation() {
  if (!state.projectId) return;
  const button = $("annotationCreateButton");
  const rawRating = $("annotationRatingInput").value.trim();
  setBusy(button, true, "记录中");
  try {
    await request(`/projects/${encodeURIComponent(state.projectId)}/evaluations/annotations`, {
      method: "POST",
      body: JSON.stringify({
        target_type: "project",
        target_id: state.projectId,
        verdict: $("annotationVerdictSelect").value,
        rating: rawRating === "" ? null : Number(rawRating),
        note: $("annotationNoteInput").value.trim(),
        actor: "studio-reviewer",
      }),
    });
    $("annotationNoteInput").value = "";
    logEvent("人工评审结论已记录。", "muted");
    await loadProjectContext(state.projectId);
    setActiveTab("eval");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

function renderEvaluation() {
  const evaluationView = state.evaluation || {};
  const report = evaluationView.latest || evaluationView.current || {};
  const currentReport = evaluationView.current || report;
  const admissionReport = report.model_quality_score === undefined ? currentReport : report;
  const baselines = evaluationView.baselines || [];
  const regression = report.regression || currentReport.regression || evaluationView.regression || {};
  const gates = report.gates || [];
  const benchmarkView = state.benchmark || {};
  const benchmark = benchmarkView.latest || benchmarkView.current || {};
  const providers = benchmark.providers || [];
  const selectedCost = benchmark.selected_routes?.estimated_total_cost || 0;

  $("evalScore").textContent = `${report.score_percent || 0}/100`;
  $("evalScore").classList.toggle("connection-warning", report.passed === false);
  $("evalGateCount").textContent = String(report.gate_count || gates.length || 0);
  $("evalFailedCount").textContent = String(report.failed_gate_count || 0);
  $("benchmarkProvider").textContent = benchmark.recommended_provider || "-";
  $("benchmarkCost").textContent = formatCurrency(selectedCost);
  const baselineButton = $("evaluationBaselineButton");
  const baselineDisabled = !state.projectId || Boolean(state.project?.archived) || admissionReport.model_quality_score === undefined;
  baselineButton.disabled = baselineDisabled;
  $("evaluationBaselineName").disabled = baselineDisabled;
  $("evaluationBaselineScore").disabled = baselineDisabled;
  if (document.activeElement !== $("evaluationBaselineScore") && admissionReport.model_quality_score !== undefined) {
    $("evaluationBaselineScore").placeholder = `当前模型质量 ${(Number(admissionReport.model_quality_score) * 100).toFixed(0)}%`;
  }
  if (!baselines.length) {
    $("evaluationBaselineStatus").textContent = "尚未设置";
    $("evaluationBaselineList").innerHTML = `<div class="project-empty">评估通过后可固化当前服务商、工作流和提示词版本。</div>`;
  } else {
    const activeCount = baselines.filter((baseline) => baseline.active).length;
    $("evaluationBaselineStatus").textContent = regression.passed ? "准入通过" : "需要重新准入";
    $("evaluationBaselineList").innerHTML = baselines.slice().reverse().map((baseline) => {
      const result = (regression.results || []).find((item) => item.baseline_id === baseline.baseline_id);
      const stateLabel = baseline.active ? (result?.passed ? "通过" : "阻断") : "历史";
      return `<div class="asset-row"><div><strong>${escapeHtml(baseline.name)}</strong><span>最低模型质量 ${(Number(baseline.minimum_score || 0) * 100).toFixed(0)}% · ${escapeHtml(String(baseline.context?.fingerprint || "").slice(0, 12))}</span></div><span class="job-state ${result?.passed ? "" : "is-failed"}">${activeCount && baseline.active ? stateLabel : "历史"}</span></div>`;
    }).join("");
  }

  if (!gates.length) {
    $("evalGates").innerHTML = `<div class="project-empty">完成计划后运行评估，即可查看发布门禁。</div>`;
  } else {
    $("evalGates").innerHTML = gates.map((gate) => `
      <div class="policy-row ${gate.passed ? "is-passed" : "is-blocked"}">
        <strong>${escapeHtml(gate.passed ? "通过" : "未通过")} · ${escapeHtml(checkLabel(gate))}</strong>
        <span>${escapeHtml(checkNameLabel(gate.name))} · 权重 ${escapeHtml(gate.weight)}</span>
        <span>${escapeHtml(formatObserved(gate.observed))}</span>
      </div>
    `).join("");
  }

  if (!providers.length) {
    $("benchmarkList").innerHTML = `<div class="project-empty">完成镜头计划后，可比较服务商路由。</div>`;
    return;
  }
  $("benchmarkList").innerHTML = providers.map((provider) => `
    <div class="asset-row">
      <div>
        <strong>${escapeHtml(provider.name)}</strong>
        <span>${provider.supported_shots}/${benchmark.shot_count || 0} 个镜头支持 · ${provider.within_budget_shots}/${benchmark.shot_count || 0} 个镜头在预算内</span>
      </div>
      <span class="job-state">${formatCurrency(provider.estimated_total_cost)}</span>
    </div>
  `).join("");
}

function renderRoute() {
  const runtime = (state.project?.shots || []).find((shot) => shot.shot.shot_id === state.selectedShotId);
  const reports = state.routes?.reports || [];
  const route = reports.find((item) => item.shot_id === state.selectedShotId);
  if (!runtime || !route) {
    $("routeCount").textContent = `${reports.length || 0} 条路由`;
    $("routeSelected").textContent = "-";
    $("routeSelectedCost").textContent = formatCurrency(0);
    $("routeMatched").textContent = "0";
    $("routeList").innerHTML = `<div class="project-empty">请选择已规划的镜头，预览服务商路由。</div>`;
    $("routeLink").href = "#";
    $("routeLink").hidden = true;
    return;
  }

  const candidates = route.candidates || [];
  $("routeCount").textContent = `${reports.length} 条路由`;
  $("routeSelected").textContent = route.selected_provider || "-";
  $("routeSelectedCost").textContent = formatCurrency(route.selected_estimated_cost);
  $("routeMatched").textContent = `${route.within_budget_count || 0}/${route.candidate_count || 0}`;
  const routeUrlVisible = state.routeUrl && state.routeShotId === state.selectedShotId;
  $("routeLink").href = routeUrlVisible ? state.routeUrl : "#";
  $("routeLink").hidden = !routeUrlVisible;

  if (!candidates.length) {
    $("routeList").innerHTML = `<div class="project-empty">当前镜头暂无可用的服务商候选。</div>`;
    return;
  }
  $("routeList").innerHTML = candidates.map((candidate) => {
    const status = candidate.selected ? "SELECTED" : (
      candidate.within_budget ? "ELIGIBLE" : (
        candidate.supported ? "OVER BUDGET" : "UNSUPPORTED"
      )
    );
    return `
      <div class="asset-row ${candidate.selected ? "route-selected" : ""}">
        <div>
          <strong>${escapeHtml(candidate.provider)} · ${escapeHtml(labelFor(status))}</strong>
          <span>P${escapeHtml(candidate.priority)} · ${formatCurrency(candidate.estimated_cost)} · ${escapeHtml(localizeRouteReason(candidate.reason))}</span>
        </div>
        <span class="job-state ${candidate.selected ? "is-running" : ""}">${candidate.enabled ? "启用" : "停用"}</span>
      </div>
    `;
  }).join("");
}

function renderLlmops() {
  const llmops = state.llmops || {};
  const registry = llmops.prompt_registry || {};
  const prompts = registry.prompts || [];
  const active = registry.active || {};
  const annotations = llmops.annotations || [];
  const select = $("promptKeySelect");
  const selectedKey = select.value;
  const keys = [...new Set(prompts.map((prompt) => prompt.key).filter(Boolean))].sort();
  select.innerHTML = keys.length
    ? keys.map((key) => `<option value="${escapeHtml(key)}">${escapeHtml(key)}</option>`).join("")
    : `<option value="planning.story">planning.story</option>`;
  select.value = keys.includes(selectedKey) ? selectedKey : (keys[0] || "planning.story");
  const writable = Boolean(state.projectId && !state.project?.archived);
  $("promptActiveCount").textContent = `${registry.active_count || Object.keys(active).length} 个启用`;
  $("promptKeySelect").disabled = !writable;
  $("promptLabelInput").disabled = !writable;
  $("promptTemplateInput").disabled = !writable;
  $("promptCreateButton").disabled = !writable || !$("promptTemplateInput").value.trim();
  $("annotationVerdictSelect").disabled = !writable;
  $("annotationRatingInput").disabled = !writable;
  $("annotationNoteInput").disabled = !writable;
  $("annotationCreateButton").disabled = !writable;
  $("annotationCount").textContent = `${annotations.length} 条`;

  if (!$("promptTemplateInput").value.trim() && active[select.value]?.template) {
    $("promptTemplateInput").value = active[select.value].template;
  }
  $("promptVersionList").innerHTML = prompts.length
    ? prompts.map((prompt) => {
      const isActive = prompt.status === "ACTIVE";
      return `
        <div class="asset-row">
          <div>
            <strong>${escapeHtml(prompt.key)} · V${escapeHtml(prompt.version)}</strong>
            <span>${escapeHtml(prompt.label || "未命名")} · ${isActive ? "已启用" : prompt.status === "DRAFT" ? "草稿" : "已归档"} · ${escapeHtml(String(prompt.sha256 || "").slice(0, 12))}</span>
          </div>
          ${isActive ? `<span class="job-state is-running">启用</span>` : `<button class="button button-quiet" data-action="activate-prompt" data-prompt-id="${escapeHtml(prompt.prompt_id)}" ${writable ? "" : "disabled"}>启用</button>`}
        </div>
      `;
    }).join("")
    : `<div class="project-empty">暂无提示词版本。</div>`;
  $("annotationList").innerHTML = annotations.length
    ? annotations.slice().reverse().slice(0, 12).map((annotation) => `
      <div class="asset-row">
        <div>
          <strong>${escapeHtml(labelFor(annotation.verdict))} · ${escapeHtml(annotation.target_type)} · ${escapeHtml(annotation.target_id)}</strong>
          <span>${annotation.rating === null || annotation.rating === undefined ? "未评分" : `${Math.round(Number(annotation.rating) * 100)} 分`} · ${escapeHtml(annotation.actor || "reviewer")} · ${escapeHtml(annotation.note || "无备注")}</span>
        </div>
      </div>
    `).join("")
    : `<div class="project-empty">暂无人工结论。</div>`;
}

function renderAbTest() {
  const runtime = (state.project?.shots || []).find((shot) => shot.shot.shot_id === state.selectedShotId);
  if (!runtime) {
    $("abCount").textContent = "0 个候选";
    $("abBest").textContent = "-";
    $("abScore").textContent = "0%";
    $("abCost").textContent = formatCurrency(0);
    $("abVariantList").innerHTML = `<div class="project-empty">请选择镜头，比较已生成的候选版本。</div>`;
    $("comparisonLink").href = state.comparisonUrl || "#";
    $("comparisonLink").hidden = !state.comparisonUrl;
    return;
  }

  const variants = runtime.variants || [];
  const latest = state.project?.latest_comparison?.shot_id === runtime.shot.shot_id
    ? state.project.latest_comparison
    : null;
  const ranking = latest?.ranking || variants.map((variant) => ({
    source: "variant",
    variant_id: variant.variant_id,
    label: variant.label,
    shot_id: runtime.shot.shot_id,
    job_id: variant.job_id,
    artifact_id: variant.artifact?.artifact_id,
    artifact: variant.artifact,
    provider: variant.route?.provider,
    estimated_cost: variant.route?.estimated_cost,
    quality_passed: Boolean(variant.quality?.passed),
    score: variant.score || { total: 0, passed: false, breakdown: {} },
    promoted: Boolean(variant.promoted),
    selected: Boolean(variant.selected),
    error: variant.error,
  })).sort((left, right) => Number(right.score?.total || 0) - Number(left.score?.total || 0));
  const best = ranking[0];
  $("abCount").textContent = `${variants.length} 个候选`;
  $("abBest").textContent = best ? (best.label || best.variant_id) : "-";
  $("abScore").textContent = `${Math.round(Number(best?.score?.total || 0) * 100)}%`;
  $("abCost").textContent = formatCurrency(variants.reduce((total, variant) => (
    total + Number(variant.route?.estimated_cost || 0)
  ), 0));
  $("comparisonLink").href = state.comparisonUrl || "#";
  $("comparisonLink").hidden = !state.comparisonUrl;

  if (!ranking.length) {
    $("abVariantList").innerHTML = `<div class="project-empty">点击“比较当前镜头”，为该镜头创建 A/B 候选。</div>`;
    return;
  }
  $("abVariantList").innerHTML = ranking.map((candidate) => {
    const artifact = candidate.artifact;
    const url = artifact?.uri ? artifactUrl(state.projectId, artifact.uri) : "";
    const score = Math.round(Number(candidate.score?.total || 0) * 100);
    const canPromote = candidate.source === "variant"
      && artifact
      && candidate.quality_passed
      && !candidate.selected
      && !state.project?.archived;
    const status = candidate.error ? "FAILED" : (
      candidate.selected ? "SELECTED" : (
        candidate.quality_passed ? "PASS" : "CHECK"
      )
    );
    return `
      <div class="asset-row variant-row">
        <div>
          <strong>${escapeHtml(candidate.label || candidate.variant_id)} · ${score}%</strong>
          <span>${escapeHtml(candidate.provider || "服务商")} · ${formatCurrency(candidate.estimated_cost)} · ${escapeHtml(labelFor(status))}</span>
        </div>
        <div class="variant-actions">
          ${url ? `<a href="${url}" target="_blank" rel="noreferrer">打开</a>` : `<span class="job-state">暂无文件</span>`}
          ${canPromote ? `<button class="button button-approve" data-action="promote-variant" data-shot-id="${runtime.shot.shot_id}" data-variant-id="${candidate.variant_id}">采用</button>` : ""}
        </div>
      </div>
    `;
  }).join("");
}

function formatObserved(value) {
  if (value === null || value === undefined) return "-";
  if (typeof value === "object") return JSON.stringify(value);
  return value;
}

function localizeProviderMessage(value) {
  const messages = {
    "Provider is configured.": "服务商已配置。",
    "Mock Provider is active.": "模拟服务商已启用。",
    "ComfyUI workflow is configured.": "ComfyUI 工作流已配置。",
    "Replicate video Provider is configured.": "Replicate 视频服务商已配置。",
  };
  return messages[String(value)] || String(value || "服务商状态未知。");
}

function localizeRouteReason(value) {
  return String(value || "").replace(
    /^selected (.+) by priority=(\d+), estimated_cost=([\d.]+)$/,
    "按优先级 $2 选择 $1，预计成本 $3",
  );
}

function localizeErrorMessage(value) {
  const text = String(value || "未知错误");
  const exact = {
    "voiceover license source or evidence is required": "请填写配音授权来源或凭证",
    "TTS provider returned unreadable audio": "配音服务返回的音频无法解码，原音轨未更改",
    "TTS request failed or returned invalid audio data": "配音请求失败或返回无效音频，原音轨未更改",
    "system speech synthesis failed; check installed voice and language": "系统配音失败，请检查已安装的音色和语言",
    "shot quality must pass before approval": "镜头质检未通过，需返修或重新质检后再审核",
    "released project quality cannot be changed": "项目已发布，质检结果已锁定",
    "invalid billing date range": "账单查询时间范围无效",
    "snapshot must contain a project record": "快照中必须包含项目记录",
    "package_zip or package_zip_b64 is required": "必须提供交付包 ZIP",
    "package_zip_b64 is not valid base64": "交付包内容不是有效的 Base64",
    "delivery package is not a valid zip file": "交付包不是有效的 ZIP 文件",
    "archive_zip or archive_zip_b64 is required": "必须提供归档包 ZIP",
    "archive_zip_b64 is not valid base64": "归档包内容不是有效的 Base64",
    "archive package is not a valid zip file": "归档包不是有效的 ZIP 文件",
    "release project before recording delivery": "请先发布项目，再记录分发",
    "package delivery before recording delivery": "请先生成交付包，再记录分发",
    "delivery already accepted": "交付已确认",
    "delivery package verification failed": "交付包验证失败",
    "project compliance report is not deliverable": "项目未通过合规检查，无法交付",
    "release project before acknowledging delivery": "请先发布项目，再确认交付",
    "delivery is already finalized": "交付已经完成最终确认",
    "delivery receipt path is unavailable": "交付回执路径不可用",
    "final MP4 is missing": "缺少最终 MP4",
    "configured audio track is missing": "配置的音频轨文件不存在",
    "content_b64 is not valid base64": "文件内容不是有效的 Base64",
    "audio track is empty": "音频轨为空",
    "audio track exceeds the 50 MB limit": "音频轨超过 50 MB 限制",
    "audio track must be a readable audio file": "音频文件无法读取或格式不受支持",
    "released project audio cannot be changed": "项目已发布，音频轨已锁定；请复制项目后修改",
    "delivery package is missing": "缺少交付包",
    "archive package is missing": "缺少归档包",
    "generate a plan before comparing shot variants": "请先生成计划，再比较镜头候选版本",
    "generate a plan before submitting shots": "请先生成计划，再提交镜头",
    "generate a plan before enqueueing shots": "请先生成计划，再将镜头加入队列",
    "shot has no failed job to retry": "该镜头没有可重试的失败任务",
    "shot must be generated before review": "镜头生成后才能审核",
    "shot must have requested changes before revision": "镜头被要求修改后才能执行返工",
    "generate a plan before export": "请先生成计划，再导出",
    "export project before packaging delivery assets": "请先导出项目，再打包交付资产",
    "package delivery before verification": "请先生成交付包，再执行验证",
    "export project before release": "请先导出项目，再发布",
    "package delivery before release": "请先生成交付包，再发布",
    "project policy report is not releasable": "项目未通过策略检查，无法发布",
    "project is archived; restore it before editing": "项目已归档，请恢复后再编辑",
    "embedding data export is disabled": "未授权向嵌入服务发送项目文本",
    "embedding service failed or returned invalid vectors": "嵌入服务不可用或返回的向量无效",
    "RAG database operation failed": "故事检索数据库操作失败",
    "RAG database role must not be superuser or BYPASSRLS": "检索数据库必须使用不绕过行级权限的普通账号",
    "RAG schema and forced row security must be initialized": "检索数据库结构或强制行级权限尚未初始化",
    "project memory changed during indexing; retry current state": "索引更新时项目记忆已变化，请重试",
    "project memory exceeds 4000 chunks": "项目记忆超过 4000 个片段，请缩减检索来源",
    "planning run not found": "规划记录不存在",
    "planning operation is already running": "该项目正在执行规划或修改，请稍后重试",
    "an active planning draft already exists": "项目已有待处理的规划草稿",
    "approve or cancel the active planning draft first": "请先确认或取消当前规划草稿",
    "staged planning is disabled or planner stages are unavailable": "分步规划未启用，或模型不支持分阶段接口",
    "staged planning requires a project with no generation jobs; create a branch": "分步规划仅用于尚未生成媒体的项目，请使用新项目或空白分支",
    "project changed after planning started; cancel this draft and create a new run": "项目已修改，请取消旧草稿并重新规划",
    "planner configuration changed; cancel this draft and create a new run": "规划模型配置已变更，请重新创建草稿",
    "planning run requires migration before it can be resumed or reviewed": "该规划草稿来自旧图版本，请先迁移草稿",
    "planning run uses an unsupported graph version; create a new run": "该规划草稿版本不兼容，请创建新草稿",
    "planning checkpoint identity is invalid": "规划检查点标识无效，无法迁移",
    "planning checkpoint contains unsupported stages; create a new run": "规划检查点包含不兼容阶段，请创建新草稿",
    "only a failed or interrupted planning run can be resumed": "仅失败或中断的规划可以恢复",
    "planning run is not awaiting review": "当前规划不处于待确认状态",
    "revision requires feedback and at most three revision rounds": "修订需要审核意见，每个草稿最多修订三轮",
    "automatic draft revision requires an external staged planner": "自动修订需要接入分阶段规划模型",
    "planning policy or compiled parameters changed; start a new run": "策略或生成参数已变更，请重新规划",
    "planning commit failed; retry approval": "草稿保存失败，可重试确认应用",
    "planning commit failed": "草稿保存失败",
    "planning model stage failed": "模型规划阶段失败，可恢复执行",
    "planning retrieval stage failed": "检索阶段失败，可恢复执行",
    "planning validation stage failed": "参数校验或策略检查失败",
    "completed planning runs cannot be canceled": "已结束的规划不能取消",
    "an applied planning run cannot be canceled": "已应用的规划不能取消",
    "approved graph output can only be applied or canceled": "已确认草稿只能应用或取消",
    "a validated story is required before revising the storyboard": "故事设定通过校验后才能修订分镜",
    "narrative events are locked after media work begins; create a branch": "媒体生产已开始，故事事件已锁定；请创建分支后修改",
    "narrative event revision conflict; refresh before saving": "故事事件已被他人修订，请刷新后再保存",
    "narrative event revision conflict; refresh before reviewing": "故事事件已被他人修订，请刷新后再审核",
  };
  if (exact[text]) return exact[text];
  return text
    .replace(/^project already exists: (.+)$/i, "项目已存在：$1")
    .replace(/^unknown job: (.+)$/i, "未知任务：$1")
    .replace(/^delivery package not found: (.+)$/i, "未找到交付包：$1")
    .replace(/^archive package not found: (.+)$/i, "未找到归档包：$1")
    .replace(/^generation failed for (.+): (.+)$/i, "$1 生成失败：$2")
    .replace(/^quality gate failed for (.+)$/i, "$1 未通过质量门禁")
    .replace(/^audio track must use a supported extension: (.+)$/i, "音频扩展名不受支持，可使用：$1")
    .replace(/^unsupported provider: (.+)$/i, "不支持的服务商：$1");
}

const AREA_LABELS = {
  planning: "规划",
  production: "生产",
  review: "审核",
  cost: "成本",
  quality: "质量",
  governance: "治理",
  delivery: "交付",
};

const areaLabel = (value) => AREA_LABELS[String(value)] || String(value || "审核");

function localizeRetrospectiveText(value) {
  const text = String(value || "");
  const exact = {
    "No shot plan has been generated.": "尚未生成镜头计划。",
    "Generate a structured shot plan before spending on media jobs.": "请先生成结构化镜头计划，再投入媒体任务。",
    "Finish queued generation work before release review.": "请先完成排队中的生成任务，再进行发布审核。",
    "Close review decisions or request targeted revisions.": "请完成审核决策，或针对性地发起返工。",
    "Reuse the same plan-review-export cadence for the next episode.": "下一集可以复用本次计划、审核、导出的节奏。",
    "Tighten prompts or style references before generation to reduce rework.": "生成前收紧提示词和风格参考，减少返工。",
    "Run provider benchmark before revisions and cap expensive variants.": "返工前运行服务商基准，并限制高成本候选版本。",
    "There is budget headroom for optional A/B variants or polish passes.": "预算仍有余量，可用于可选 A/B 候选或润色。",
    "Address failed evaluation gates before stakeholder delivery.": "在交付给相关方前处理未通过的评估门禁。",
    "Preserve the evaluation gate as a required release step.": "将评估门禁保留为必需的发布步骤。",
    "Compliance checks have blocking failures.": "合规检查存在阻断性失败。",
    "Resolve compliance findings before release or archive packaging.": "发布或打包归档前，请先处理合规发现。",
    "Delivery and archive packages are both verified.": "交付包和归档包均已验证。",
    "Use the archive package as the canonical handoff and recovery artifact.": "将归档包作为正式交接和恢复产物。",
    "No release record exists yet.": "当前还没有发布记录。",
    "Release only after package verification and compliance pass.": "完成交付包验证且通过合规检查后再发布。",
    "Release exists but no accepted delivery is recorded.": "已有发布记录，但还没有已确认的交付记录。",
    "Capture delivery acknowledgement to close the business loop.": "记录交付确认，闭合业务流程。",
    "Accepted delivery has not been closed out.": "已确认的交付尚未结项。",
    "Run closeout and archive packaging after recipient acceptance.": "收件方确认后执行结项并打包归档。",
  };
  if (exact[text]) return exact[text];
  return text
    .replace(/(\d+) shot\(s\) are still missing media\./, "$1 个镜头仍缺少媒体产物。")
    .replace(/(\d+) generated shot\(s\) are not approved\./, "$1 个已生成镜头尚未通过审核。")
    .replace(/Revision volume reached (\d+) across (\d+) shot\(s\)\./, "已在 $2 个镜头中产生 $1 次返工。")
    .replace(/Spend used ([\d.]+%) of the budget\./, "已使用预算的 $1。")
    .replace(/Latest evaluation scored (\d+)\/100\./, "最近一次评估得分为 $1/100。")
    .replace(/All planned shots reached approval\./, "所有已规划镜头均已通过审核。");
}

function localizeAuditMessage(value) {
  const text = String(value || "");
  const exact = {
    "Staged planning draft adopted after human review.": "分步规划草稿已通过人工确认并应用。",
    "Project brief passed the local policy gate.": "项目简报已通过本地策略门禁。",
    "Project archived.": "项目已归档。",
    "Project restored.": "项目已恢复。",
    "Portable project snapshot exported.": "项目便携快照已导出。",
    "Project trace exported.": "项目追踪报告已导出。",
    "Production operations report exported.": "生产运营报告已导出。",
    "Project retrospective report exported.": "项目复盘报告已导出。",
    "Project provenance report exported.": "项目来源报告已导出。",
    "Project compliance report exported.": "项目合规报告已导出。",
    "Project distribution report exported.": "项目分发报告已导出。",
    "Acceptance certificate exported.": "验收证书已导出。",
    "Final archive package prepared.": "最终归档包已准备完成。",
    "Final archive package verification completed.": "最终归档包验证已完成。",
    "Project closed out and archived.": "项目已结项并归档。",
    "Project trace exported.": "项目追踪报告已导出。",
    "Delivery package prepared.": "交付包已准备完成。",
    "Delivery package verification completed.": "交付包验证已完成。",
    "Final MP4 sample exported.": "最终 MP4 样片已导出。",
    "Audit log exported.": "审计日志已导出。",
    "Audit log CSV exported.": "审计日志 CSV 已导出。",
    "Provider benchmark report generated.": "服务商基准报告已生成。",
    "Existing plan invalidated by a narrative event change.": "故事事件变更，现有分镜计划已失效。",
    "Existing plan invalidated by a narrative event review.": "故事事件审核变更，现有分镜计划已失效。",
  };
  if (exact[text]) return exact[text];
  return text
    .replace(/^Project (.+) created\.$/, "项目 $1 已创建。")
    .replace(/^Narrative event (.+) created\.$/, "故事事件 $1 已创建。")
    .replace(/^Narrative event (.+) revised to version (\d+)\.$/, "故事事件 $1 已修订为版本 $2。")
    .replace(/^Narrative event (.+) reviewed as (APPROVED|CHANGES_REQUESTED)\.$/, (_, id, status) => `故事事件 ${id} 审核结果：${labelFor(status)}。`)
    .replace(/^Project cloned from (.+)\.$/, "项目已从 $1 复制。")
    .replace(/^Project imported from portable snapshot\.$/, "项目已从便携快照导入。")
    .replace(/^Project imported from delivery package\.$/, "项目已从交付包导入。")
    .replace(/^Project imported from verified final archive package\.$/, "项目已从已验证的最终归档包导入。")
    .replace(/^Release delivered to (.+)\.$/, "发布内容已分发至 $1。")
    .replace(/^Delivery (.+) (accepted|rejected)\.$/, (_, id, status) => `交付 ${id} 状态：${status === "accepted" ? "已确认" : "已拒绝"}。`)
    .replace(/^(.+) route preview exported\.$/, "$1 路由预览已导出。")
    .replace(/^(.+) A\/B candidate failed policy checks\.$/, "$1 A/B 候选版本未通过策略检查。")
    .replace(/^(.+) A\/B comparison generated\.$/, "$1 A/B 对比已生成。")
    .replace(/^(.+) A\/B comparison exported\.$/, "$1 A/B 对比已导出。")
    .replace(/^(.+) promoted (.+) as the current version\.$/, "$1 已采用 $2 作为当前版本。")
    .replace(/^(.+) submitted to (.+)\.$/, "$1 已提交至 $2。")
    .replace(/^(.+) queued job started\.$/, "$1 排队任务已开始执行。")
    .replace(/^(.+) admitted job started\.$/, "$1 已接收任务开始执行。")
    .replace(/^(.+) retry started from (.+)\.$/, "$1 从 $2 状态开始重试。")
    .replace(/^(.+) failed the media quality gate\.$/, "$1 未通过媒体质量门禁。")
    .replace(/^(.+) generated and passed quality checks\.$/, "$1 已生成并通过质量检查。")
    .replace(/^(.+) generation failed\.$/, "$1 生成失败。")
    .replace(/^Retry requested for (.+)\.$/, "已请求重试 $1。")
    .replace(/^(.+) reached its retry limit\.$/, "$1 已达到重试上限。")
    .replace(/^(.+) will retry after backoff\.$/, "$1 将在退避后重试。")
    .replace(/^Retry scheduled for (.+)\.$/, "已为 $1 安排重试。")
    .replace(/^(.+) queued for (.+)\.$/, "$1 已加入 $2 队列。")
    .replace(/^(\d+) shot\(s\) (enqueued in batch|submitted in batch|approved in batch)\.$/, (_, count, action) => {
      const labels = { "enqueued in batch": "批量加入队列", "submitted in batch": "批量提交", "approved in batch": "批量通过" };
      return `${labels[action]} ${count} 个镜头。`;
    })
    .replace(/^(.+) queued job requested for processing\.$/, "$1 排队任务已请求执行。")
    .replace(/^(.+) retry is ready to run\.$/, "$1 重试任务已准备执行。")
    .replace(/^(\d+) queued job\(s\) processed\.$/, "已执行 $1 个排队任务。")
    .replace(/^(.+) reviewed as (APPROVED|CHANGES_REQUESTED)\.$/, (_, shot, status) => `${shot} 审核结果：${labelFor(status)}。`)
    .replace(/^(.+) revision (\d+) prepared\.$/, "$1 返工版本 $2 已准备完成。")
    .replace(/^(.+) job canceled\.$/, "$1 任务已取消。");
}

function auditEventText(event) {
  return `${actionLabel(event.action)} · ${localizeAuditMessage(event.message)}`;
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (bytes <= 0) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatDuration(value) {
  const milliseconds = Number(value || 0);
  if (milliseconds < 1000) return `${Math.round(milliseconds)} 毫秒`;
  const seconds = milliseconds / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)} 秒`;
  return `${Math.floor(seconds / 60)} 分 ${Math.round(seconds % 60)} 秒`;
}

function formatTimestamp(value) {
  if (!value) return "-";
  const target = new Date(value);
  if (Number.isNaN(target.getTime())) return "-";
  return target.toLocaleString([], {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function timestampKey() {
  return new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 14);
}

function isDefaultProjectId(value) {
  return /^drama_studio_\d{14}$/.test(String(value || ""));
}

function suggestImportProjectId(sourceProjectId) {
  const currentProjectId = $("projectId").value.trim();
  return currentProjectId
    && !isDefaultProjectId(currentProjectId)
    && currentProjectId !== state.projectId
    ? currentProjectId
    : `${sourceProjectId}_imported_${timestampKey()}`;
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunkSize = 0x8000;
  for (let index = 0; index < bytes.length; index += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
  }
  return btoa(binary);
}

async function refreshProject() {
  if (!state.projectId) return;
  await loadProjectContext(state.projectId);
}

async function saveProjectEdits() {
  if (!state.projectId) return;
  const button = $("saveProjectButton");
  setBusy(button, true, "保存中");
  try {
    const brief = readBrief();
    const planned = Boolean(state.project?.shots?.length);
    const payload = planned
      ? {
          title: brief.title,
          budget: brief.budget,
          actor: "studio-editor",
        }
      : {
          title: brief.title,
          premise: brief.premise,
          genre: brief.genre,
          style: brief.style,
          duration_seconds: brief.duration_seconds,
          budget: brief.budget,
          characters: brief.characters,
          actor: "studio-editor",
        };
    await request(`/projects/${encodeURIComponent(state.projectId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
    logEvent("项目简报已保存。", "normal");
    await loadProjectList();
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
    button.disabled = Boolean(state.project?.archived);
  }
}

async function saveShotEdits() {
  if (!state.projectId || !state.selectedShotId) return;
  const button = $("saveShotButton");
  setBusy(button, true, "保存中");
  try {
    const characters = [...$("shotEditCharacters").selectedOptions].map((option) => option.value);
    await request(`/projects/${encodeURIComponent(state.projectId)}/shots/${encodeURIComponent(state.selectedShotId)}`, {
      method: "PATCH",
      body: JSON.stringify({
        scene: $("shotEditScene").value.trim(),
        description: $("shotEditDescription").value.trim(),
        mood: $("shotEditMood").value.trim(),
        duration_seconds: Number($("shotEditDuration").value),
        characters,
        subtitle_text: $("shotEditSubtitle").value.trim() || null,
        actor: "studio-editor",
      }),
    });
    logEvent(`${state.selectedShotId} 镜头卡已保存。`, "normal");
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
    button.disabled = Boolean(state.project?.archived || !state.selectedShotId);
  }
}

async function createProject(event) {
  event.preventDefault();
  const button = $("createProjectButton");
  setBusy(button, true, "创建中");
  try {
    const brief = readBrief();
    state.project = await request("/projects", { method: "POST", body: JSON.stringify(brief) });
    state.projectId = brief.project_id;
    state.selectedShotId = null;
    state.events = [];
    state.packageUrl = "";
    state.verificationUrl = "";
    logEvent(`项目 ${brief.project_id} 已创建。`);
    await loadProjectList();
    await loadProjectContext(brief.project_id);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function planProject() {
  if (!state.projectId) return;
  const button = $("planButton");
  setBusy(button, true, "规划中");
  try {
    state.project = await request(`/projects/${encodeURIComponent(state.projectId)}/plan`, {
      method: "POST",
      body: JSON.stringify({ memory_project_ids: state.memoryProjectId === state.projectId ? state.memorySourceIds : null }),
    });
    logEvent("故事设定和镜头卡已创建。");
    await loadProjectContext(state.projectId);
    if (state.activeTab === "memory") await loadMemorySources();
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function generateAllShots() {
  if (!state.projectId) return;
  const button = $("generateAllButton");
  setBusy(button, true, "生成中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/shots/submit-all`, { method: "POST" });
    state.project = result.project;
    logEvent(`已生成 ${result.submitted} 个镜头。`);
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function queueAllShots() {
  if (!state.projectId) return;
  const button = $("queueAllButton");
  setBusy(button, true, "排队中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/shots/enqueue-all`, {
      method: "POST",
    });
    logEvent(`已将 ${result.queued} 个镜头加入队列。`);
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function drainQueue() {
  if (!state.projectId) return;
  const button = $("drainQueueButton");
  setBusy(button, true, "执行中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/queue/drain`, {
      method: "POST",
      body: JSON.stringify({ limit: 50, actor: "studio-worker" }),
    });
    logEvent(`已执行 ${result.processed} 个排队任务。`);
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function drainAllQueues() {
  const button = $("drainAllQueuesButton");
  setBusy(button, true, "执行中");
  try {
    const result = await request("/queue/drain-all", {
      method: "POST",
      body: JSON.stringify({
        limit: 50,
        actor: "studio-worker",
        include_archived: false,
      }),
    });
    logEvent(`全局队列已执行 ${result.processed} 个任务。`);
    await loadProjectList();
    if (state.projectId) {
      await loadProjectContext(state.projectId);
    }
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function recoverStaleJobs() {
  if (!state.projectId || state.project?.archived) return;
  const button = $("recoverStaleButton");
  setBusy(button, true, "恢复中");
  try {
    const result = await request(
      `/projects/${encodeURIComponent(state.projectId)}/jobs/recover-stale`,
      {
        method: "POST",
        body: JSON.stringify({ actor: "studio-operations" }),
      },
    );
    logEvent(
      result.recovered_count
        ? `已恢复 ${result.recovered_count} 个卡住任务，并安排重试。`
        : "当前没有超过租约的卡住任务。",
      result.recovered_count ? "normal" : "muted",
    );
    await loadProjectContext(state.projectId);
    setActiveTab("jobs");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function approveReadyShots() {
  if (!state.projectId) return;
  const button = $("approveReadyButton");
  setBusy(button, true, "审批中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/shots/approve-ready`, {
      method: "POST",
      body: JSON.stringify({
        comment: $("reviewComment").value.trim() || "已在工作室批量通过。",
        actor: "studio-reviewer",
      }),
    });
    state.project = result.project;
    logEvent(`已通过 ${result.approved.length} 个镜头。`);
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function handleShotAction(action, shotId) {
  state.selectedShotId = shotId;
  const encodedProject = encodeURIComponent(state.projectId);
  const encodedShot = encodeURIComponent(shotId);
  try {
    if (action === "select") {
      setActiveTab("inspector");
      renderProject();
      return;
    }
    if (action === "submit") {
      await request(`/projects/${encodedProject}/shots/${encodedShot}/submit`, { method: "POST" });
      logEvent(`${shotId} 已生成。`);
    } else if (action === "retry") {
      await request(`/projects/${encodedProject}/shots/${encodedShot}/retry`, { method: "POST" });
      logEvent(`${shotId} 已完成重试。`);
    } else if (action === "schedule-retry") {
      await request(`/projects/${encodedProject}/shots/${encodedShot}/retry/schedule`, {
        method: "POST",
        body: JSON.stringify({ actor: "studio-worker" }),
      });
      logEvent(`${shotId} 已安排重试。`);
    } else if (action === "approve") {
      await reviewShot("APPROVED");
      logEvent(`${shotId} 已通过。`, "normal");
    } else if (action === "request-changes") {
      await reviewShot("CHANGES_REQUESTED");
      logEvent(`${shotId} 已退回返工。`, "muted");
    } else if (action === "revise") {
      const result = await request(`/projects/${encodedProject}/shots/${encodedShot}/revise`, {
        method: "POST",
        body: JSON.stringify({ comment: $("reviewComment").value.trim() }),
      });
      logEvent(result.requires_stage_lock ? `${shotId} 返修已准备，请依次重新锁定分镜和资产阶段后生成。` : `${shotId} 返工版本已生成。`);
    }
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  }
}

async function reviewShot(status) {
  const comment = $("reviewComment").value.trim();
  const encodedProject = encodeURIComponent(state.projectId);
  const encodedShot = encodeURIComponent(state.selectedShotId);
  await request(`/projects/${encodedProject}/shots/${encodedShot}/review`, {
    method: "POST",
    body: JSON.stringify({ status, comment, actor: "studio-reviewer" }),
  });
}

async function exportProject() {
  const button = $("exportButton");
  setBusy(button, true, "导出中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/export`, { method: "POST" });
    logEvent("最终 MP4 已导出。");
    state.packageUrl = "";
    state.verificationUrl = "";
    await loadProjectContext(state.projectId);
    $("downloadLink").hidden = false;
    $("downloadLink").href = artifactUrl(state.projectId, result.final_mp4);
    $("subtitleLink").hidden = false;
    $("subtitleLink").href = artifactUrl(state.projectId, result.subtitle_srt);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function packageProject() {
  const button = $("packageButton");
  setBusy(button, true, "打包中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/package`, { method: "POST" });
    state.packageUrl = artifactUrl(state.projectId, result.package_zip);
    state.verificationUrl = artifactUrl(state.projectId, result.verification.report_path);
    logEvent(`已打包并验证 ${result.file_count} 个文件。`);
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function verifyPackage() {
  if (!state.projectId) return;
  const button = $("verifyPackageButton");
  setBusy(button, true, "验证中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/package/verify`, {
      method: "POST",
      body: JSON.stringify({ actor: "studio-delivery-qa" }),
    });
    state.verificationUrl = artifactUrl(state.projectId, result.verification_report);
    $("verificationLink").href = state.verificationUrl || "#";
    $("verificationLink").hidden = !state.verificationUrl;
    logEvent(
      result.verification.passed
        ? `已验证 ${result.verification.manifest_file_count} 个交付文件。`
        : "交付包验证失败。",
      result.verification.passed ? "normal" : "muted",
    );
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function releaseProject() {
  if (!state.projectId) return;
  const button = $("releaseButton");
  setBusy(button, true, "发布中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/release`, {
      method: "POST",
      body: JSON.stringify({
        channel: "studio-release",
        comment: $("reviewComment").value.trim(),
        actor: "studio-publisher",
      }),
    });
    state.packageUrl = artifactUrl(state.projectId, result.package.package_zip);
    state.verificationUrl = result.package.verification?.report_path
      ? artifactUrl(state.projectId, result.package.verification.report_path)
      : "";
    logEvent(`${result.release.release_id} 已发布。`);
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function recordDelivery() {
  if (!state.projectId) return;
  const button = $("recordDeliveryButton");
  setBusy(button, true, "分发中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/deliveries/dispatch`, {
      method: "POST",
      body: JSON.stringify({
        channel: "studio-release",
        recipient: "delivery-archive",
        note: $("reviewComment").value.trim(),
        actor: "delivery-ops",
      }),
    });
    state.deliveryReceiptUrl = artifactUrl(state.projectId, result.receipt_path);
    state.distributionUrl = artifactUrl(state.projectId, result.distribution_report);
    $("deliveryReceiptLink").href = state.deliveryReceiptUrl || "#";
    $("deliveryReceiptLink").hidden = !state.deliveryReceiptUrl;
    $("distributionLink").href = state.distributionUrl || "#";
    $("distributionLink").hidden = !state.distributionUrl;
    logEvent(`${result.delivery.delivery_id} 已分发至 ${result.delivery.channel}。`);
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function acknowledgeDelivery() {
  if (!state.projectId || !state.project?.latest_delivery?.delivery_id) return;
  const button = $("acknowledgeDeliveryButton");
  setBusy(button, true, "确认中");
  try {
    const deliveryId = state.project.latest_delivery.delivery_id;
    const result = await request(
      `/projects/${encodeURIComponent(state.projectId)}/deliveries/${encodeURIComponent(deliveryId)}/acknowledge`,
      {
        method: "POST",
        body: JSON.stringify({
          accepted: $("deliveryAcceptedToggle").checked,
          note: $("reviewComment").value.trim(),
          actor: "delivery-recipient",
        }),
      },
    );
    state.deliveryReceiptUrl = artifactUrl(state.projectId, result.receipt_path);
    state.distributionUrl = artifactUrl(state.projectId, result.distribution_report);
    $("deliveryReceiptLink").href = state.deliveryReceiptUrl || "#";
    $("deliveryReceiptLink").hidden = !state.deliveryReceiptUrl;
    $("distributionLink").href = state.distributionUrl || "#";
    $("distributionLink").hidden = !state.distributionUrl;
    logEvent(`${result.delivery.delivery_id} 状态：${labelFor(result.delivery.status)}。`);
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

function renderDeliveryFeedback() {
  const project = state.project || {};
  const feedbackView = state.distribution?.feedback || {};
  const summary = feedbackView.summary || project.delivery_feedback || {};
  const items = feedbackView.items || [];
  const latestDelivery = project.latest_delivery || {};
  const writable = Boolean(state.projectId && latestDelivery.delivery_id && !project.archived);
  const target = $("deliveryFeedbackTarget");
  const selectedTarget = target.value;
  const targets = [
    {value: `project:${project.project_id || ""}`, label: "项目整体"},
    ...(project.shots || []).map((runtime) => ({
      value: `shot:${runtime.shot.shot_id}`,
      label: `镜头 ${runtime.shot.shot_id}`,
    })),
  ];
  target.innerHTML = targets.length
    ? targets.map((item) => `<option value="${escapeHtml(item.value)}">${escapeHtml(item.label)}</option>`).join("")
    : `<option value="">项目整体</option>`;
  const preferredTarget = state.selectedShotId ? `shot:${state.selectedShotId}` : `project:${project.project_id || ""}`;
  target.value = targets.some((item) => item.value === selectedTarget)
    ? selectedTarget
    : (targets.some((item) => item.value === preferredTarget) ? preferredTarget : targets[0]?.value || "");
  $("deliveryFeedbackOpen").textContent = String(summary.unresolved_count || 0);
  $("deliveryFeedbackBlockers").textContent = String(summary.blocking_open_count || 0);
  $("deliveryFeedbackResolved").textContent = String(summary.resolved_count || 0);
  $("deliveryFeedbackStatus").textContent = latestDelivery.delivery_id
    ? (summary.unresolved_count ? `${summary.unresolved_count} 项待处理` : "反馈已闭环")
    : "需先记录分发";
  [
    "deliveryFeedbackTarget", "deliveryFeedbackCategory", "deliveryFeedbackSeverity",
    "deliveryFeedbackVerdict", "deliveryFeedbackRating", "deliveryFeedbackAssignee",
    "deliveryFeedbackComment", "deliveryFeedbackButton",
  ].forEach((id) => { $(id).disabled = !writable; });
  $("deliveryFeedbackList").innerHTML = items.length
    ? items.slice(0, 30).map((item) => {
      const closed = ["RESOLVED", "DISMISSED"].includes(item.status);
      const targetLabel = item.target_type === "shot" ? `镜头 ${item.target_id}` : "项目整体";
      const severityLabel = deliveryFeedbackLabel("severity", item.severity);
      const verdictLabel = deliveryFeedbackLabel("verdict", item.verdict);
      const categoryLabel = deliveryFeedbackLabel("category", item.category);
      const statusLabel = deliveryFeedbackLabel("status", item.status);
      const reporter = item.submitted_by === "delivery-recipient"
        ? "交付对象"
        : (item.submitted_by || "交付对象");
      return `<div class="delivery-feedback-item" data-feedback-id="${escapeHtml(item.feedback_id)}">
        <div><strong>${escapeHtml(severityLabel)} · ${escapeHtml(verdictLabel)} · ${escapeHtml(targetLabel)}</strong><span>${escapeHtml(categoryLabel)} · ${item.rating == null ? "未评分" : `${item.rating}/5`} · ${escapeHtml(reporter)}</span></div>
        <span>${escapeHtml(item.comment || "")}</span>
        ${item.assignee ? `<span>负责人：${escapeHtml(item.assignee)}</span>` : ""}
        ${item.resolution ? `<span>处理结论：${escapeHtml(item.resolution)}</span>` : ""}
        ${closed ? `<span>状态：${escapeHtml(statusLabel)}</span>` : `<div class="delivery-feedback-triage">
          <select data-feedback-triage-status aria-label="反馈处理状态"><option value="ACKNOWLEDGED">已确认</option><option value="RESOLVED">已解决</option><option value="DISMISSED">不采纳</option></select>
          <input data-feedback-resolution maxlength="4000" placeholder="处理结论" aria-label="反馈处理结论" />
          <button class="button button-quiet" type="button" data-action="triage-delivery-feedback" data-feedback-id="${escapeHtml(item.feedback_id)}" ${writable ? "" : "disabled"}>更新</button>
        </div>`}
      </div>`;
    }).join("")
    : `<div class="project-empty">尚未收到交付反馈。</div>`;
}

async function submitDeliveryFeedback() {
  if (!state.projectId || !state.project?.latest_delivery?.delivery_id) return;
  const comment = $("deliveryFeedbackComment").value.trim();
  if (!comment) return;
  const button = $("deliveryFeedbackButton");
  const [targetType, targetId] = $("deliveryFeedbackTarget").value.split(":", 2);
  const rawRating = $("deliveryFeedbackRating").value.trim();
  setBusy(button, true, "提交中");
  try {
    await request(`/projects/${encodeURIComponent(state.projectId)}/delivery-feedback`, {
      method: "POST",
      body: JSON.stringify({
        delivery_id: state.project.latest_delivery.delivery_id,
        target_type: targetType || "project",
        target_id: targetId || state.projectId,
        category: $("deliveryFeedbackCategory").value,
        severity: $("deliveryFeedbackSeverity").value,
        verdict: $("deliveryFeedbackVerdict").value,
        rating: rawRating === "" ? null : Number(rawRating),
        assignee: $("deliveryFeedbackAssignee").value.trim() || null,
        comment,
        actor: "delivery-recipient",
      }),
    });
    $("deliveryFeedbackComment").value = "";
    $("deliveryFeedbackRating").value = "";
    logEvent("交付反馈已记录并进入待处理队列。", "muted");
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function triageDeliveryFeedback(feedbackId, element) {
  if (!state.projectId || !feedbackId || !element) return;
  const row = element.closest("[data-feedback-id]");
  const status = row?.querySelector("[data-feedback-triage-status]")?.value;
  const resolution = row?.querySelector("[data-feedback-resolution]")?.value.trim() || "";
  setBusy(element, true, "更新中");
  try {
    await request(`/projects/${encodeURIComponent(state.projectId)}/delivery-feedback/${encodeURIComponent(feedbackId)}`, {
      method: "PATCH",
      body: JSON.stringify({status, resolution, actor: "delivery-owner"}),
    });
    logEvent("交付反馈处理状态已更新。", "muted");
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(element, false);
  }
}

async function closeoutProject() {
  if (!state.projectId) return;
  const button = $("closeoutButton");
  setBusy(button, true, "结项中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/closeout`, {
      method: "POST",
      body: JSON.stringify({ actor: "studio-publisher" }),
    });
    state.closeoutUrl = artifactUrl(state.projectId, result.closeout_report);
    state.acceptanceUrl = artifactUrl(state.projectId, result.acceptance_report);
    $("closeoutLink").href = state.closeoutUrl || "#";
    $("closeoutLink").hidden = !state.closeoutUrl;
    $("acceptanceLink").href = state.acceptanceUrl || "#";
    $("acceptanceLink").hidden = !state.acceptanceUrl;
    logEvent(`${result.project_id} 已结项。`);
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function buildArchivePackage() {
  if (!state.projectId) return;
  const button = $("archivePackageButton");
  setBusy(button, true, "归档中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/archive-package`, {
      method: "POST",
      body: JSON.stringify({ actor: "studio-archive" }),
    });
    state.archivePackageUrl = artifactUrl(state.projectId, result.archive_package);
    state.archiveVerificationUrl = artifactUrl(state.projectId, result.archive_verification_report);
    $("archivePackageLink").href = state.archivePackageUrl || "#";
    $("archivePackageLink").hidden = !state.archivePackageUrl;
    $("archiveVerificationLink").href = state.archiveVerificationUrl || "#";
    $("archiveVerificationLink").hidden = !state.archiveVerificationUrl;
    logEvent(`已打包并验证 ${result.file_count} 个归档文件。`);
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function verifyArchivePackage() {
  if (!state.projectId) return;
  const button = $("verifyArchiveButton");
  setBusy(button, true, "验证中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/archive-package/verify`, {
      method: "POST",
      body: JSON.stringify({ actor: "archive-qa" }),
    });
    state.archiveVerificationUrl = artifactUrl(state.projectId, result.archive_verification_report);
    $("archiveVerificationLink").href = state.archiveVerificationUrl || "#";
    $("archiveVerificationLink").hidden = !state.archiveVerificationUrl;
    logEvent(
      result.verification.passed
        ? `已验证 ${result.verification.manifest_file_count} 个归档文件。`
        : "归档包验证失败。",
      result.verification.passed ? "normal" : "muted",
    );
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function exportAudit() {
  if (!state.projectId) return;
  const button = $("auditExportButton");
  setBusy(button, true, "导出中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/audit/export`, {
      method: "POST",
    });
    state.auditExportUrl = artifactUrl(state.projectId, result.audit_log);
    $("auditExportLink").href = state.auditExportUrl || "#";
    $("auditExportLink").hidden = !state.auditExportUrl;
    logEvent(`已导出 ${result.event_count} 条审计事件。`, "muted");
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function verifyAuditIntegrity() {
  if (!state.projectId) return;
  const button = $("auditIntegrityButton");
  setBusy(button, true, "校验中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/audit/integrity`);
    state.auditIntegrity = result;
    renderDetailPanels();
    logEvent(result.verified ? "审计链校验通过。" : "审计链尚未通过校验。", result.verified ? "normal" : "muted");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function exportAuditIntegrity() {
  if (!state.projectId) return;
  const button = $("auditIntegrityExportButton");
  setBusy(button, true, "导出中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/audit/export-integrity`, {
      method: "POST",
    });
    state.auditIntegrity = result.integrity || null;
    state.auditIntegrityUrl = artifactUrl(state.projectId, result.audit_integrity_report);
    $("auditIntegrityLink").href = state.auditIntegrityUrl || "#";
    $("auditIntegrityLink").hidden = !state.auditIntegrityUrl;
    logEvent("审计链校验报告已导出。", "muted");
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function anchorAuditChain() {
  if (!state.projectId) return;
  const button = $("auditAnchorButton");
  setBusy(button, true, "锚定中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/audit/anchor`, {
      method: "POST",
      body: JSON.stringify({ actor: "studio-governance" }),
    });
    state.auditAnchors = result.anchors || null;
    state.auditAnchorUrl = artifactUrl(state.projectId, result.audit_anchors_report);
    $("auditAnchorLink").href = state.auditAnchorUrl || "#";
    $("auditAnchorLink").hidden = !state.auditAnchorUrl;
    logEvent("审计链头已写入外部锚定回执。", "normal");
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function exportTrace() {
  if (!state.projectId) return;
  const button = $("traceExportButton");
  setBusy(button, true, "导出中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/trace/export`, {
      method: "POST",
      body: JSON.stringify({ actor: "studio-observer" }),
    });
    state.traceUrl = artifactUrl(state.projectId, result.trace_report);
    $("traceLink").href = state.traceUrl || "#";
    $("traceLink").hidden = !state.traceUrl;
    logEvent(`已导出 ${result.span_count} 个追踪节点。`, "muted");
    await loadProjectContext(state.projectId);
    setActiveTab("trace");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function exportRetrospective() {
  if (!state.projectId) return;
  const button = $("retrospectiveExportButton");
  setBusy(button, true, "导出中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/retrospective/export`, {
      method: "POST",
      body: JSON.stringify({ actor: "studio-ops" }),
    });
    state.retrospectiveUrl = artifactUrl(state.projectId, result.retrospective_report);
    $("retrospectiveLink").href = state.retrospectiveUrl || "#";
    $("retrospectiveLink").hidden = !state.retrospectiveUrl;
    logEvent("复盘报告已导出。", "muted");
    await loadProjectContext(state.projectId);
    setActiveTab("retrospective");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function archiveProject() {
  if (!state.projectId || !state.project) return;
  const button = $("archiveButton");
  const archived = Boolean(state.project.archived);
  setBusy(button, true, archived ? "恢复中" : "归档中");
  try {
    const path = archived
      ? `/projects/${encodeURIComponent(state.projectId)}/restore`
      : `/projects/${encodeURIComponent(state.projectId)}/archive`;
    await request(path, {
      method: "POST",
    });
    logEvent(`${state.projectId} 已${archived ? "恢复" : "归档"}。`, "muted");
    await loadProjectList();
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function cloneProject() {
  if (!state.projectId || !state.project) return;
  const button = $("cloneButton");
  const targetProjectId = `${state.projectId}_branch_${timestampKey()}`;
  setBusy(button, true, "复制中");
  try {
    const project = await request(`/projects/${encodeURIComponent(state.projectId)}/clone`, {
      method: "POST",
      body: JSON.stringify({
        project_id: targetProjectId,
        title_suffix: "分支",
        actor: "studio-user",
      }),
    });
    logEvent(`${project.project_id} 已复制为新的分支。`);
    await loadProjectList();
    await loadProjectContext(project.project_id);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function runEvaluation() {
  if (!state.projectId) return;
  const button = $("evaluateButton");
  setBusy(button, true, "评估中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/evaluations/run`, {
      method: "POST",
      body: JSON.stringify({ actor: "studio-evaluator" }),
    });
    state.evaluationUrl = artifactUrl(state.projectId, result.evaluation_report);
    logEvent(`评估得分 ${result.report.score_percent}/100。`, result.report.passed ? "normal" : "muted");
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function createEvaluationBaseline() {
  if (!state.projectId) return;
  const name = $("evaluationBaselineName").value.trim();
  if (!name) {
    alert("请填写基线名称。");
    return;
  }
  const value = $("evaluationBaselineScore").value.trim();
  const minimumScore = value === "" ? null : Number(value);
  const button = $("evaluationBaselineButton");
  setBusy(button, true, "固化中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/evaluations/baselines`, {
      method: "POST",
      body: JSON.stringify({
        name,
        minimum_score: minimumScore,
        actor: "studio-quality-owner",
      }),
    });
    $("evaluationBaselineName").value = "";
    $("evaluationBaselineScore").value = "";
    logEvent(`模型准入基线 ${result.baseline.name} 已固化。`, "muted");
    await loadProjectContext(state.projectId);
    setActiveTab("eval");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function runBenchmark() {
  if (!state.projectId) return;
  const button = $("benchmarkButton");
  setBusy(button, true, "基准测试中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/providers/benchmark`, {
      method: "POST",
      body: JSON.stringify({ actor: "studio-ops" }),
    });
    state.benchmarkUrl = artifactUrl(state.projectId, result.benchmark_report);
    logEvent(`基准测试推荐 ${result.benchmark.recommended_provider || "暂无服务商"}。`, "muted");
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function compareSelectedShot() {
  if (!state.projectId || !state.selectedShotId) return;
  const button = $("compareButton");
  setBusy(button, true, "比较中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/shots/${encodeURIComponent(state.selectedShotId)}/variants/compare`, {
      method: "POST",
      body: JSON.stringify({ candidate_count: 2, actor: "studio-optimizer" }),
    });
    state.comparisonUrl = "";
    logEvent(`已生成 ${result.generated.length} 个 A/B 候选版本。`);
    await loadProjectContext(state.projectId);
    setActiveTab("ab");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function promoteVariant(shotId, variantId) {
  if (!state.projectId || !shotId || !variantId) return;
  try {
    await request(`/projects/${encodeURIComponent(state.projectId)}/shots/${encodeURIComponent(shotId)}/variants/${encodeURIComponent(variantId)}/promote`, {
      method: "POST",
      body: JSON.stringify({
        comment: $("reviewComment").value.trim(),
        actor: "studio-reviewer",
      }),
    });
    state.selectedShotId = shotId;
    state.packageUrl = "";
    state.verificationUrl = "";
    logEvent(`${variantId} 已用于 ${shotId}。`);
    await loadProjectContext(state.projectId);
    setActiveTab("ab");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  }
}

async function exportComparison() {
  if (!state.projectId || !state.selectedShotId) return;
  const button = $("comparisonExportButton");
  setBusy(button, true, "导出中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/shots/${encodeURIComponent(state.selectedShotId)}/variants/export`, {
      method: "POST",
      body: JSON.stringify({ actor: "studio-user" }),
    });
    state.comparisonUrl = artifactUrl(state.projectId, result.comparison_report);
    $("comparisonLink").href = state.comparisonUrl || "#";
    $("comparisonLink").hidden = !state.comparisonUrl;
    logEvent("A/B 对比报告已导出。", "muted");
    await loadProjectContext(state.projectId);
    setActiveTab("ab");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function exportAuditCsv() {
  if (!state.projectId) return;
  const button = $("auditCsvExportButton");
  setBusy(button, true, "导出中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/audit/export-csv`, {
      method: "POST",
      body: JSON.stringify({ actor: "studio-user" }),
    });
    state.auditCsvUrl = artifactUrl(state.projectId, result.audit_csv);
    $("auditCsvLink").href = state.auditCsvUrl || "#";
    $("auditCsvLink").hidden = !state.auditCsvUrl;
    logEvent(`已导出 ${result.event_count} 条审计记录。`, "muted");
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function exportSnapshot() {
  if (!state.projectId) return;
  const button = $("snapshotButton");
  setBusy(button, true, "保存中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/snapshot/export`, {
      method: "POST",
      body: JSON.stringify({ actor: "studio-user" }),
    });
    state.snapshotUrl = artifactUrl(state.projectId, result.snapshot);
    $("snapshotLink").href = state.snapshotUrl || "#";
    $("snapshotLink").hidden = !state.snapshotUrl;
    logEvent(`已导出包含 ${result.shot_count} 个镜头的项目快照。`, "muted");
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function importSnapshotFile(event) {
  const input = event.target;
  const file = input.files && input.files[0];
  if (!file) return;
  const button = $("importSnapshotButton");
  setBusy(button, true, "导入中");
  try {
    const snapshot = JSON.parse(await file.text());
    const sourceProjectId = snapshot?.project?.brief?.project_id
      || snapshot?.project_id
      || "imported_project";
    const targetProjectId = suggestImportProjectId(sourceProjectId);
    const result = await request("/projects/import", {
      method: "POST",
      body: JSON.stringify({
        snapshot,
        project_id: targetProjectId,
        actor: "studio-user",
      }),
    });
    logEvent(`${result.project_id} 已从快照导入。`);
    await loadProjectList();
    await loadProjectContext(result.project_id);
    setActiveTab("inspector");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    input.value = "";
    setBusy(button, false);
  }
}

async function importPackageFile(event) {
  const input = event.target;
  const file = input.files && input.files[0];
  if (!file) return;
  const button = $("importPackageButton");
  setBusy(button, true, "导入中");
  try {
    const packageZipB64 = arrayBufferToBase64(await file.arrayBuffer());
    const sourceProjectId = file.name.replace(/-delivery\.zip$/i, "").replace(/\.zip$/i, "") || "imported_package";
    const targetProjectId = suggestImportProjectId(sourceProjectId);
    const result = await request("/projects/import-package", {
      method: "POST",
      body: JSON.stringify({
        package_zip_b64: packageZipB64,
        project_id: targetProjectId,
        actor: "studio-user",
      }),
    });
    logEvent(`${result.project_id} 已从交付包导入。`);
    await loadProjectList();
    await loadProjectContext(result.project_id);
    setActiveTab("inspector");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    input.value = "";
    setBusy(button, false);
  }
}

async function importArchiveFile(event) {
  const input = event.target;
  const file = input.files && input.files[0];
  if (!file) return;
  const button = $("importArchiveButton");
  setBusy(button, true, "导入中");
  try {
    const archiveZipB64 = arrayBufferToBase64(await file.arrayBuffer());
    const sourceProjectId = file.name
      .replace(/-archive\.zip$/i, "")
      .replace(/\.zip$/i, "")
      || "imported_archive";
    const targetProjectId = suggestImportProjectId(sourceProjectId);
    const result = await request("/projects/import-archive", {
      method: "POST",
      body: JSON.stringify({
        archive_zip_b64: archiveZipB64,
        project_id: targetProjectId,
        actor: "studio-user",
      }),
    });
    logEvent(`${result.project_id} 已从已验证归档包导入。`);
    await loadProjectList();
    await loadProjectContext(result.project_id);
    setActiveTab("inspector");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    input.value = "";
    setBusy(button, false);
  }
}

async function exportReport() {
  if (!state.projectId) return;
  const button = $("reportButton");
  setBusy(button, true, "导出中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/reports/export`, {
      method: "POST",
      body: JSON.stringify({ actor: "studio-user" }),
    });
    state.reportUrl = artifactUrl(state.projectId, result.production_report);
    $("reportLink").href = state.reportUrl || "#";
    $("reportLink").hidden = !state.reportUrl;
    logEvent("生产报告已导出。", "muted");
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function exportAcceptance() {
  if (!state.projectId) return;
  const button = $("acceptanceExportButton");
  setBusy(button, true, "导出中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/acceptance/export`, {
      method: "POST",
      body: JSON.stringify({ actor: "studio-governance" }),
    });
    state.acceptanceUrl = artifactUrl(state.projectId, result.acceptance_report);
    $("acceptanceLink").href = state.acceptanceUrl || "#";
    $("acceptanceLink").hidden = !state.acceptanceUrl;
    logEvent("验收证书已导出。", "muted");
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function exportProvenance() {
  if (!state.projectId) return;
  const button = $("provenanceButton");
  setBusy(button, true, "导出中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/provenance/export`, {
      method: "POST",
      body: JSON.stringify({ actor: "studio-governance" }),
    });
    state.provenanceUrl = artifactUrl(state.projectId, result.provenance_report);
    $("provenanceLink").href = state.provenanceUrl || "#";
    $("provenanceLink").hidden = !state.provenanceUrl;
    logEvent(
      `来源报告已追踪 ${result.report.summary.artifact_count} 个产物。`,
      "muted",
    );
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function exportCompliance() {
  if (!state.projectId) return;
  const button = $("complianceButton");
  setBusy(button, true, "检查中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/compliance/export`, {
      method: "POST",
      body: JSON.stringify({ actor: "compliance-officer" }),
    });
    state.compliance = result.report;
    state.complianceUrl = artifactUrl(state.projectId, result.compliance_report);
    $("complianceLink").href = state.complianceUrl || "#";
    $("complianceLink").hidden = !state.complianceUrl;
    logEvent(
      `${result.report.summary.passed_checks}/${result.report.summary.check_count} 项合规检查已通过。`,
      "muted",
    );
    await loadProjectContext(state.projectId);
    setActiveTab("policy");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function exportContinuity() {
  if (!state.projectId) return;
  const button = $("continuityExportButton");
  setBusy(button, true, "检查中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/continuity/export`, {
      method: "POST",
      body: JSON.stringify({ actor: "continuity-agent" }),
    });
    state.continuity = result.report;
    state.continuityUrl = artifactUrl(state.projectId, result.continuity_report);
    $("continuityLink").href = state.continuityUrl || "#";
    $("continuityLink").hidden = !state.continuityUrl;
    logEvent(
      `连续性报告完成：${result.report.summary.passed_checks}/${result.report.summary.check_count} 项通过。`,
      result.report.passed ? "muted" : "error",
    );
    await loadProjectContext(state.projectId);
    setActiveTab("continuity");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function exportDistribution() {
  if (!state.projectId) return;
  const button = $("distributionButton");
  setBusy(button, true, "导出中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/distribution/export`, {
      method: "POST",
      body: JSON.stringify({ actor: "delivery-ops" }),
    });
    state.distribution = result.report;
    state.distributionUrl = artifactUrl(state.projectId, result.distribution_report);
    $("distributionLink").href = state.distributionUrl || "#";
    $("distributionLink").hidden = !state.distributionUrl;
    logEvent(`已导出 ${result.report.count} 条分发记录。`, "muted");
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function exportRoute() {
  if (!state.projectId || !state.selectedShotId) return;
  const button = $("routeExportButton");
  setBusy(button, true, "导出中");
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/shots/${encodeURIComponent(state.selectedShotId)}/route/export`, {
      method: "POST",
      body: JSON.stringify({ actor: "studio-user" }),
    });
    state.routeUrl = artifactUrl(state.projectId, result.route_report);
    state.routeShotId = state.selectedShotId;
    $("routeLink").href = state.routeUrl || "#";
    $("routeLink").hidden = !state.routeUrl;
    logEvent("路由预览已导出。", "muted");
    await loadProjectContext(state.projectId);
    setActiveTab("route");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function processJob(jobId) {
  if (!state.projectId || !jobId) return;
  try {
    const job = (state.jobs || []).find((item) => item.job_id === jobId);
    const workerId = job?.worker_id || "";
    const workerQuery = workerId ? "?worker_id=" + encodeURIComponent(workerId) : "";
    await request(`/projects/${encodeURIComponent(state.projectId)}/jobs/${encodeURIComponent(jobId)}/process${workerQuery}`, {
      method: "POST",
    });
    logEvent(`${jobId} 已执行。`);
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  }
}

async function cancelJob(jobId) {
  if (!state.projectId || !jobId) return;
  try {
    const result = await request(`/projects/${encodeURIComponent(state.projectId)}/jobs/${encodeURIComponent(jobId)}/cancel`, {
      method: "POST",
    });
    const remoteCancellation = result.remote_cancellation;
    const message = remoteCancellation?.attempted
      ? (remoteCancellation.requested
        ? `${jobId} 已取消，已向云端服务请求停止。`
        : `${jobId} 已取消；云端停止请求未确认，请查看审计记录。`)
      : `${jobId} 已取消。`;
    logEvent(message, "muted");
    await loadProjectContext(state.projectId);
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  }
}

function selectCollaborationDocument(documentId) {
  const document = (state.collaborationDocuments?.documents || []).find(
    (item) => item.document_id === documentId,
  );
  $("collaborationDocumentId").value = documentId;
  $("collaborationDocumentInput").value = document?.text || "";
  setActiveTab("collaboration");
}

async function saveCollaborationDocument() {
  if (!state.projectId) return;
  const idInput = $("collaborationDocumentId");
  const documentId = idInput.value.trim().toLowerCase();
  if (!documentId) {
    alert("请填写笔记标识。");
    return;
  }
  const button = $("collaborationDocumentSaveButton");
  setBusy(button, true, "保存中");
  try {
    const result = await request(
      `/projects/${encodeURIComponent(state.projectId)}/collaboration/documents/${encodeURIComponent(documentId)}`,
      {
        method: "PUT",
        body: JSON.stringify({ text: $("collaborationDocumentInput").value }),
      },
    );
    const existing = (state.collaborationDocuments?.documents || []).filter(
      (item) => item.document_id !== result.document.document_id,
    );
    state.collaborationDocuments = {
      ...(state.collaborationDocuments || {}),
      documents: [...existing, result.document].sort((left, right) => left.document_id.localeCompare(right.document_id)),
    };
    idInput.value = result.document.document_id;
    setCollaborationRealtimeStatus("已保存，等待同步");
    renderCollaboration();
    logEvent(`共享笔记 ${result.document.document_id} 已保存。`, "muted");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function addCollaborationComment() {
  if (!state.projectId) return;
  const body = $("collaborationCommentInput").value.trim();
  if (!body) return;
  try {
    await request(`/projects/${encodeURIComponent(state.projectId)}/comments`, {
      method: "POST",
      body: JSON.stringify({
        body,
        shot_id: $("collaborationShotSelect").value || null,
      }),
    });
    $("collaborationCommentInput").value = "";
    logEvent("协作评论已添加。", "muted");
    await loadProjectContext(state.projectId);
    setActiveTab("collaboration");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  }
}

async function updateCollaborationPresence() {
  if (!state.projectId) return;
  const button = $("collaborationPresenceButton");
  setBusy(button, true, "更新中");
  try {
    await request(`/projects/${encodeURIComponent(state.projectId)}/collaboration/presence`, {
      method: "POST",
      body: JSON.stringify({
        status: $("collaborationPresenceStatus").value,
        section: $("collaborationPresenceSection").value.trim() || null,
      }),
    });
    logEvent("协作状态已更新。", "muted");
    await loadProjectContext(state.projectId);
    setActiveTab("collaboration");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function acquireCollaborationLock() {
  if (!state.projectId) return;
  const [target_type, target_id] = $("collaborationLockTarget").value.split(":", 2);
  const button = $("collaborationLockButton");
  setBusy(button, true, "锁定中");
  try {
    await request(`/projects/${encodeURIComponent(state.projectId)}/collaboration/locks`, {
      method: "POST",
      body: JSON.stringify({ target_type, target_id: target_id === "project" ? state.projectId : target_id }),
    });
    logEvent("编辑锁已获取。", "muted");
    await loadProjectContext(state.projectId);
    setActiveTab("collaboration");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function releaseCollaborationLock(lockId) {
  if (!state.projectId || !lockId) return;
  try {
    await request(`/projects/${encodeURIComponent(state.projectId)}/collaboration/locks/${encodeURIComponent(lockId)}`, { method: "DELETE" });
    logEvent("编辑锁已释放。", "muted");
    await loadProjectContext(state.projectId);
    setActiveTab("collaboration");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  }
}

async function saveCollaborationMember() {
  if (!state.projectId) return;
  const subject = $("collaborationMemberInput").value.trim();
  if (!subject) return;
  try {
    await request(`/projects/${encodeURIComponent(state.projectId)}/collaboration/members`, {
      method: "POST",
      body: JSON.stringify({ subject, role: $("collaborationMemberRole").value }),
    });
    $("collaborationMemberInput").value = "";
    logEvent(`${subject} 已加入项目协作。`, "muted");
    await loadProjectContext(state.projectId);
    setActiveTab("collaboration");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  }
}

async function removeCollaborationMember(subject) {
  if (!state.projectId || !subject) return;
  try {
    await request(`/projects/${encodeURIComponent(state.projectId)}/collaboration/members/${encodeURIComponent(subject)}`, { method: "DELETE" });
    logEvent(`${subject} 已移出项目协作。`, "muted");
    await loadProjectContext(state.projectId);
    setActiveTab("collaboration");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  }
}

async function removeCollaborationComment(commentId) {
  if (!state.projectId || !commentId) return;
  try {
    await request(`/projects/${encodeURIComponent(state.projectId)}/comments/${encodeURIComponent(commentId)}`, { method: "DELETE" });
    logEvent("协作评论已删除。", "muted");
    await loadProjectContext(state.projectId);
    setActiveTab("collaboration");
  } catch (error) {
    logEvent(error.message, "muted");
    alert(error.message);
  }
}

document.addEventListener("click", (event) => {
  const projectTarget = event.target.closest("[data-project-id]");
  if (projectTarget) {
    event.preventDefault();
    loadProjectContext(projectTarget.dataset.projectId);
    return;
  }
  const target = event.target.closest("[data-action]");
  if (!target) return;
  event.preventDefault();
  if (target.dataset.action === "workflow-open-stage") {
    setActiveTab(target.dataset.stageTab || "inspector");
    return;
  }
  if (target.dataset.action === "workflow-lock") {
    lockProductionStage(target.dataset.stageKey);
    return;
  }
  if (target.dataset.action === "workflow-return") {
    returnProductionStage(target.dataset.stageKey);
    return;
  }
  if (target.dataset.action === "cancel-job") {
    cancelJob(target.dataset.jobId);
    return;
  }
  if (target.dataset.action === "process-job") {
    processJob(target.dataset.jobId);
    return;
  }
  if (target.dataset.action === "compare-shot") {
    state.selectedShotId = target.dataset.shotId;
    setActiveTab("ab");
    compareSelectedShot();
    return;
  }
  if (target.dataset.action === "promote-variant") {
    promoteVariant(target.dataset.shotId, target.dataset.variantId);
    return;
  }
  if (target.dataset.action === "generate-derivatives") {
    generateAssetDerivatives(target.dataset.assetId);
    return;
  }
  if (target.dataset.action === "create-content-credential") {
    createContentCredential(target.dataset.assetId);
    return;
  }
  if (target.dataset.action === "verify-content-credential") {
    verifyContentCredential(target.dataset.credentialId);
    return;
  }
  if (target.dataset.action === "activate-prompt") {
    activatePromptVersion(target.dataset.promptId);
    return;
  }
  if (target.dataset.action === "remove-member") {
    removeCollaborationMember(target.dataset.subject);
    return;
  }
  if (target.dataset.action === "remove-comment") {
    removeCollaborationComment(target.dataset.commentId);
    return;
  }
  if (target.dataset.action === "release-edit-lock") {
    releaseCollaborationLock(target.dataset.lockId);
    return;
  }
  if (target.dataset.action === "select-collaboration-document") {
    selectCollaborationDocument(target.dataset.documentId);
    return;
  }
  if (target.dataset.action === "triage-delivery-feedback") {
    triageDeliveryFeedback(target.dataset.feedbackId, target);
    return;
  }
  if (target.dataset.action === "edit-narrative-event") {
    beginNarrativeEventEdit(target.dataset.eventId);
    return;
  }
  if (target.dataset.action === "edit-adaptation-scene") {
    beginAdaptationSceneEdit(target.dataset.sceneId);
    return;
  }
  if (target.dataset.action === "edit-source-chapter") {
    beginSourceChapterEdit(target.dataset.chapterId);
    return;
  }
  if (target.dataset.action === "canvas-open-source") {
    setActiveTab("source");
    return;
  }
  if (target.dataset.action === "canvas-open-event") {
    setActiveTab("narrative");
    beginNarrativeEventEdit(target.dataset.eventId);
    return;
  }
  if (target.dataset.action === "canvas-open-script") {
    setActiveTab("script");
    beginAdaptationSceneEdit(target.dataset.sceneId);
    return;
  }
  if (target.dataset.action === "extract-narrative-candidates") {
    extractNarrativeCandidates(target.dataset.chapterId);
    return;
  }
  if (target.dataset.action === "adopt-narrative-candidate") {
    adoptNarrativeCandidate(target.dataset.candidateId);
    return;
  }
  if (target.dataset.action === "discard-narrative-candidate") {
    discardNarrativeCandidate(target.dataset.candidateId);
    return;
  }
  if (target.dataset.action === "approve-narrative-event") {
    reviewNarrativeEvent(target.dataset.eventId, "APPROVED");
    return;
  }
  if (target.dataset.action === "request-narrative-changes") {
    reviewNarrativeEvent(target.dataset.eventId, "CHANGES_REQUESTED");
    return;
  }
  if (target.dataset.action === "approve-adaptation-scene") {
    reviewAdaptationScene(target.dataset.sceneId, "APPROVED");
    return;
  }
  if (target.dataset.action === "request-adaptation-scene-changes") {
    reviewAdaptationScene(target.dataset.sceneId, "CHANGES_REQUESTED");
    return;
  }
  handleShotAction(target.dataset.action, target.dataset.shotId);
});

$("briefForm").addEventListener("submit", createProject);
$("saveProjectButton").addEventListener("click", saveProjectEdits);
$("sourceDocumentChooseButton").addEventListener("click", () => $("sourceDocumentFileInput").click());
$("sourceDocumentImportButton").addEventListener("click", importSourceDocument);
$("sourceDocumentFileInput").addEventListener("change", (event) => {
  state.pendingSourceDocumentFile = event.target.files?.[0] || null;
  renderSourceChapterControls();
});
$("sourceChapterForm").addEventListener("submit", saveSourceChapter);
$("cancelSourceChapterButton").addEventListener("click", resetSourceChapterForm);
$("narrativeEventForm").addEventListener("submit", saveNarrativeEvent);
$("cancelNarrativeEventButton").addEventListener("click", resetNarrativeEventForm);
$("adaptationSceneForm").addEventListener("submit", saveAdaptationScene);
$("cancelAdaptationSceneButton").addEventListener("click", resetAdaptationSceneForm);
$("deriveAdaptationScenesButton").addEventListener("click", deriveAdaptationScenes);
$("saveShotButton").addEventListener("click", saveShotEdits);
$("authTokenButton").addEventListener("click", saveAuthToken);
$("authLoginButton").addEventListener("click", beginBrowserLogin);
$("authLogoutButton").addEventListener("click", logoutBrowserSession);
$("authTokenInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter") saveAuthToken();
});
$("collaborationCommentButton").addEventListener("click", addCollaborationComment);
$("collaborationMemberButton").addEventListener("click", saveCollaborationMember);
$("collaborationPresenceButton").addEventListener("click", updateCollaborationPresence);
$("collaborationLockButton").addEventListener("click", acquireCollaborationLock);
$("collaborationDocumentSaveButton").addEventListener("click", saveCollaborationDocument);
$("providerHealthButton").addEventListener("click", checkProviderHealth);
$("providerDiagnosticsButton").addEventListener("click", checkProviderHealth);
$("providerContractButton").addEventListener("click", validateProviderContract);
$("providerWarmupButton").addEventListener("click", warmupProvider);
$("planButton").addEventListener("click", planProject);
$("generateAllButton").addEventListener("click", generateAllShots);
$("queueAllButton").addEventListener("click", queueAllShots);
$("drainQueueButton").addEventListener("click", drainQueue);
$("drainAllQueuesButton").addEventListener("click", drainAllQueues);
$("recoverStaleButton").addEventListener("click", recoverStaleJobs);
$("refreshWorkersButton").addEventListener("click", loadWorkerStatus);
$("registerWorkerButton").addEventListener("click", registerWorker);
$("heartbeatWorkerButton").addEventListener("click", heartbeatWorker);
$("claimWorkerButton").addEventListener("click", claimWorkerJobs);
$("workerIdInput").addEventListener("input", renderWorkers);
$("approveReadyButton").addEventListener("click", approveReadyShots);
$("cloneButton").addEventListener("click", cloneProject);
$("exportButton").addEventListener("click", exportProject);
$("packageButton").addEventListener("click", packageProject);
$("verifyPackageButton").addEventListener("click", verifyPackage);
$("releaseButton").addEventListener("click", releaseProject);
$("recordDeliveryButton").addEventListener("click", recordDelivery);
$("acknowledgeDeliveryButton").addEventListener("click", acknowledgeDelivery);
$("deliveryFeedbackButton").addEventListener("click", submitDeliveryFeedback);
$("closeoutButton").addEventListener("click", closeoutProject);
$("archivePackageButton").addEventListener("click", buildArchivePackage);
$("verifyArchiveButton").addEventListener("click", verifyArchivePackage);
$("auditExportButton").addEventListener("click", exportAudit);
$("auditCsvExportButton").addEventListener("click", exportAuditCsv);
$("auditIntegrityButton").addEventListener("click", verifyAuditIntegrity);
$("auditIntegrityExportButton").addEventListener("click", exportAuditIntegrity);
$("auditAnchorButton").addEventListener("click", anchorAuditChain);
$("traceExportButton").addEventListener("click", exportTrace);
$("retrospectiveExportButton").addEventListener("click", exportRetrospective);
$("datasetExportButton").addEventListener("click", exportTrainingDataset);
$("snapshotButton").addEventListener("click", exportSnapshot);
$("importSnapshotButton").addEventListener("click", () => $("snapshotImportInput").click());
$("snapshotImportInput").addEventListener("change", importSnapshotFile);
$("importPackageButton").addEventListener("click", () => $("packageImportInput").click());
$("packageImportInput").addEventListener("change", importPackageFile);
$("importArchiveButton").addEventListener("click", () => $("archiveImportInput").click());
$("archiveImportInput").addEventListener("change", importArchiveFile);
$("reportButton").addEventListener("click", exportReport);
$("acceptanceExportButton").addEventListener("click", exportAcceptance);
$("provenanceButton").addEventListener("click", exportProvenance);
$("complianceButton").addEventListener("click", exportCompliance);
$("continuityExportButton").addEventListener("click", exportContinuity);
$("timelineOtioExportButton").addEventListener("click", () => exportEditTimeline("otio_json", "timelineOtioExportButton"));
$("timelineEdlExportButton").addEventListener("click", () => exportEditTimeline("edl_csv", "timelineEdlExportButton"));
$("distributionButton").addEventListener("click", exportDistribution);
$("routeExportButton").addEventListener("click", exportRoute);
$("evaluateButton").addEventListener("click", runEvaluation);
$("evaluationBaselineButton").addEventListener("click", createEvaluationBaseline);
$("benchmarkButton").addEventListener("click", runBenchmark);
$("promptCreateButton").addEventListener("click", createPromptVersion);
$("promptTemplateInput").addEventListener("input", renderLlmops);
$("promptKeySelect").addEventListener("change", () => {
  const active = state.llmops?.prompt_registry?.active?.[$("promptKeySelect").value];
  if (active?.template) $("promptTemplateInput").value = active.template;
  renderLlmops();
});
$("annotationCreateButton").addEventListener("click", recordProjectAnnotation);
$("compareButton").addEventListener("click", compareSelectedShot);
$("comparisonExportButton").addEventListener("click", exportComparison);
$("archiveButton").addEventListener("click", archiveProject);
$("registryValidateButton").addEventListener("click", validateRegistry);
$("registrySyncButton").addEventListener("click", syncRegistry);
$("registryImportButton").addEventListener("click", () => $("registryImportInput").click());
$("registryImportInput").addEventListener("change", importRegistryFile);
$("refreshProjectsButton").addEventListener("click", loadProjectList);
$("showArchivedToggle").addEventListener("change", (event) => {
  state.includeArchived = event.target.checked;
  loadProjectList().catch((error) => logEvent(error.message, "muted"));
});
document.querySelectorAll("[data-panel-tab]").forEach((button) => {
  button.addEventListener("click", () => setActiveTab(button.dataset.panelTab));
});
$("shotFilterInput").addEventListener("input", (event) => {
  state.shotQuery = event.target.value;
  renderProject();
});
$("shotFilterStatus").addEventListener("change", (event) => {
  state.shotStatus = event.target.value;
  renderProject();
});
$("jobFilterStatus").addEventListener("change", (event) => {
  state.jobStatusFilter = event.target.value;
  renderJobs();
});
$("referenceChooseButton").addEventListener("click", () => {
  $("referenceFileInput").click();
});
$("referenceFileInput").addEventListener("change", (event) => {
  state.pendingReferenceFile = event.target.files?.[0] || null;
  $("referenceFileName").textContent = state.pendingReferenceFile?.name || "未选择文件";
  renderAssets();
});
$("referenceKind").addEventListener("change", renderAssets);
$("referenceUploadButton").addEventListener("click", uploadReferenceAsset);
$("audioChooseButton").addEventListener("click", () => {
  $("audioFileInput").click();
});
$("audioFileInput").addEventListener("change", (event) => {
  state.pendingAudioFile = event.target.files?.[0] || null;
  renderAssets();
});
$("audioUploadButton").addEventListener("click", uploadAudioTrack);
$("audioRemoveButton").addEventListener("click", removeAudioTrack);
$("voiceoverButton").addEventListener("click", generateVoiceover);
$("lipsyncButton").addEventListener("click", generateLipSync);
$("voiceoverText").addEventListener("input", renderAssets);
$("dialogueAddLineButton").addEventListener("click", () => {
  state.dialogueDraft.push(newDialogueLine());
  state.dialogueDirty = true;
  renderDialogueTimeline();
  renderDialogueControls();
});
$("dialogueGenerateButton").addEventListener("click", generateDialogueTimeline);
$("dialogueLineList").addEventListener("input", (event) => {
  const field = event.target.closest("[data-dialogue-field]");
  const row = event.target.closest("[data-dialogue-index]");
  if (!field || !row) return;
  updateDialogueDraft(Number(row.dataset.dialogueIndex), field.dataset.dialogueField, field.value);
});
$("dialogueLineList").addEventListener("change", (event) => {
  const field = event.target.closest("[data-dialogue-field]");
  const row = event.target.closest("[data-dialogue-index]");
  if (!field || !row) return;
  const index = Number(row.dataset.dialogueIndex);
  updateDialogueDraft(index, field.dataset.dialogueField, field.value);
  renderDialogueTimeline();
  renderDialogueControls();
});
$("dialogueLineList").addEventListener("click", (event) => {
  const button = event.target.closest("[data-dialogue-remove]");
  if (!button) return;
  state.dialogueDraft.splice(Number(button.dataset.dialogueRemove), 1);
  state.dialogueDirty = true;
  renderDialogueTimeline();
  renderDialogueControls();
});
$("recheckQualityButton").addEventListener("click", recheckQuality);
$("enterpriseRefresh").addEventListener("click", loadEnterprisePanel);
$("enterpriseProbe").addEventListener("click", probeEnterprise);
$("planningProbe").addEventListener("click", probePlanning);
$("memoryProbe").addEventListener("click", probeMemory);
$("planningRefresh").addEventListener("click", loadPlanningPanel);
$("planningRunSelect").addEventListener("change", () => { state.planningRunId = $("planningRunSelect").value; renderPlanningRun(); });
$("planningComment").addEventListener("input", renderPlanningControls);
for (const action of ["Start", "Approve", "Revise", "Reject", "Resume", "Migrate", "Cancel"]) $("planning" + action).addEventListener("click", () => performPlanningAction(action.toLowerCase()));
$("billingFilterForm").addEventListener("submit", (event) => { event.preventDefault(); state.billingOffset = 0; loadBillingEvents(); });
$("billingNext").addEventListener("click", () => { state.billingOffset += 20; loadBillingEvents(); });
$("billingPrevious").addEventListener("click", () => { state.billingOffset = Math.max(0, state.billingOffset - 20); loadBillingEvents(); });
$("billingDownload").addEventListener("click", downloadBillingPage);
$("memorySearchForm").addEventListener("submit", searchStoryMemory);
$("memoryRagflowSync").addEventListener("click", syncStoryMemoryToRagflow);
$("memorySourceList").addEventListener("change", () => {
  state.memorySourceIds = Array.from($("memorySourceList").querySelectorAll("input:checked"), (input) => input.value);
  state.memoryGeneration += 1;
  $("memoryResults").replaceChildren();
  $("memoryResultCount").textContent = "0 条";
});

const stamp = timestampKey();
$("projectId").value = `drama_studio_${stamp}`;
$("workerIdInput").value = window.localStorage.getItem("mediaforge.workerId") || "studio-worker";
setActiveTab("inspector");
resetSourceChapterForm();
resetNarrativeEventForm();
resetAdaptationSceneForm();
loadAuthStatus();
loadQuotaStatus();
loadWorkerStatus();
loadProviderStatus();
loadPlannerStatus();
request("/speech/status").then((status) => { state.speechStatus = status; renderAssets(); }).catch((error) => { $("voiceoverStatus").textContent = "状态读取失败"; logEvent(error.message, "muted"); });
request("/lipsync/status").then((status) => { state.lipsyncStatus = status; renderAssets(); }).catch((error) => { $("lipsyncStatus").textContent = "状态读取失败"; logEvent(error.message, "muted"); });
loadRegistry().catch((error) => logEvent(error.message, "muted"));
loadProjectList().catch((error) => {
  logEvent(error.message, "muted");
}).then(() => {
  if (!state.project && state.projects[0]) {
    return loadProjectContext(state.projects[0].project_id);
  }
});
