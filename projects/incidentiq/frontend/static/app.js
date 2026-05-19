/**
 * IncidentIQ — frontend controller (vanilla JS, ES2022).
 *
 * Responsibilities:
 *  - Health probe + live clock on DOMContentLoaded.
 *  - POST /api/query with optional severity filter; map response to UI.
 *  - Multi-step loading state machine (1: search → 2: analyze → 3: generate → 4: finalize).
 *  - Markdown-ish renderer (## headings, ordered steps, **bold**, `code`).
 *  - Source card severity colouring + relevance bar animation + click-to-expand.
 *  - Copy answer / Ask follow-up / New Query FAB / session counters.
 *  - Strict error mapping (network / 422 / 5xx / confidence===none).
 *  - All listeners bound here; no inline handlers.
 */

"use strict";

const API = Object.freeze({
  health: "/health",
  query: "/api/query",
});

const MAX_QUESTION_LEN = 500;
const MIN_QUESTION_LEN = 3;
const CHAR_WARN_THRESHOLD = 400;
const CHAR_CRIT_THRESHOLD = 480;

/**
 * Preset queries with category icons. Icons map to the NOC severity/category
 * legend requested in the spec.
 */
const PRESETS = Object.freeze([
  {
    icon: "🔴",
    category: "Kubernetes / P1",
    text: "What are the triage steps for a P1 Kubernetes pod crash loop?",
  },
  {
    icon: "🟠",
    category: "Database",
    text: "How do I resolve PostgreSQL connection pool exhaustion?",
  },
  {
    icon: "🟡",
    category: "Kafka / Queue",
    text: "What is the SOP for Kafka consumer lag incidents?",
  },
  {
    icon: "🔵",
    category: "API / Gateway",
    text: "We are seeing 5xx errors spiking on our API gateway, what do I do?",
  },
  {
    icon: "☁️",
    category: "Cloud / AWS",
    text: "How do I handle an AWS IAM permission denied error in CI/CD?",
  },
]);

const CONFIDENCE_CLASS = Object.freeze({
  high: "is-high",
  medium: "is-medium",
  low: "is-low",
  none: "is-none",
});

const SEVERITY_CLASS = Object.freeze({
  P1: "is-p1",
  P2: "is-p2",
  P3: "is-p3",
});

/**
 * Multi-step loading scheduler. Each step has a min display duration so the
 * user always sees the full sequence even on fast queries.
 */
const LOADING_STEPS = Object.freeze([
  { id: 1, minMs: 600,  progressTo: 30 },
  { id: 2, minMs: 800,  progressTo: 60 },
  { id: 3, minMs: 1000, progressTo: 90 },
  { id: 4, minMs: 400,  progressTo: 100 },
]);

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const state = {
  severity: "All",
  loading: false,
  lastProcessingMs: null,
  sessionQueryCount: 0,
  lastSeverityCount: null,
  loadingTimers: [],
  clockTimer: null,
};

// ---------------------------------------------------------------------------
// DOM refs
// ---------------------------------------------------------------------------

/** @type {Record<string, HTMLElement>} */
const el = {};

function cacheDom() {
  const ids = [
    "status-dot",
    "status-label",
    "live-clock-text",
    "severity-chips",
    "filter-result-info",
    "filter-result-count",
    "query-form",
    "query-input",
    "char-count",
    "submit-btn",
    "clear-btn",
    "preset-grid",
    "loading-panel",
    "loading-steps",
    "loading-progress-fill",
    "loading-doc-count",
    "error-banner",
    "error-message",
    "info-banner",
    "info-message",
    "response-panel",
    "confidence-badge",
    "timing-pill",
    "timing-text",
    "model-pill",
    "model-text",
    "copy-btn",
    "copy-btn-label",
    "followup-btn",
    "answer-content",
    "sources-section",
    "sources-toggle",
    "sources-count",
    "sources-list",
    "new-query-fab",
    "footer-timing",
    "footer-timing-value",
    "footer-last-time",
    "session-query-count",
  ];
  for (const id of ids) {
    const node = document.getElementById(id);
    if (!node) {
      throw new Error(`Missing required element: #${id}`);
    }
    el[id] = node;
  }
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

/** Escape an arbitrary string for safe injection into HTML. */
function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** Apply inline `**bold**` and `` `code` `` transforms to already-escaped text. */
function applyInlineFormatting(escaped) {
  return escaped
    .replace(/`([^`]+)`/g, (_m, p1) => `<code>${p1}</code>`)
    .replace(/\*\*([^*]+)\*\*/g, (_m, p1) => `<strong>${p1}</strong>`);
}

/**
 * Render a constrained markdown-like answer to safe HTML.
 *  - Lines starting with `## ` become <h3>.
 *  - Sequences of lines starting with `<n>. ` become a single <ol>.
 *  - Other non-empty lines become <p>.
 *  - Inline `**bold**` and `` `code` `` are honoured.
 */
function renderAnswer(rawText) {
  const lines = String(rawText ?? "").replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let listBuf = [];

  const flushList = () => {
    if (listBuf.length === 0) return;
    const items = listBuf
      .map((line) => {
        const stripped = line.replace(/^\s*\d+\.\s+/, "");
        return `<li>${applyInlineFormatting(escapeHtml(stripped))}</li>`;
      })
      .join("");
    out.push(`<ol>${items}</ol>`);
    listBuf = [];
  };

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();

    if (line === "") {
      flushList();
      continue;
    }

    if (/^##\s+/.test(line)) {
      flushList();
      const heading = line.replace(/^##\s+/, "");
      out.push(`<h3>${applyInlineFormatting(escapeHtml(heading))}</h3>`);
      continue;
    }

    if (/^\s*\d+\.\s+/.test(line)) {
      listBuf.push(line);
      continue;
    }

    flushList();
    out.push(`<p>${applyInlineFormatting(escapeHtml(line))}</p>`);
  }

  flushList();
  return out.join("");
}

/** Format milliseconds as a human-readable "X.Xs" or "Xms". */
function formatProcessing(ms) {
  if (typeof ms !== "number" || Number.isNaN(ms)) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

/** Map a confidence value to its CSS class. */
function confidenceClass(conf) {
  return CONFIDENCE_CLASS[conf] || CONFIDENCE_CLASS.none;
}

/** Map a severity value to its CSS class (default: N/A). */
function severityClass(sev) {
  return SEVERITY_CLASS[sev] || "is-na";
}

/** Format a Date as "HH:MM:SS" in 24-hour local time. */
function formatClock(date) {
  const h = String(date.getHours()).padStart(2, "0");
  const m = String(date.getMinutes()).padStart(2, "0");
  const s = String(date.getSeconds()).padStart(2, "0");
  return `${h}:${m}:${s}`;
}

/** Format a Date as "HH:MM:SS" for the footer last-query label. */
function formatLastQueryTime(date) {
  return formatClock(date);
}

// ---------------------------------------------------------------------------
// Live clock
// ---------------------------------------------------------------------------

function startClock() {
  const tick = () => {
    el["live-clock-text"].textContent = formatClock(new Date());
  };
  tick();
  state.clockTimer = window.setInterval(tick, 1000);
}

// ---------------------------------------------------------------------------
// Health probe
// ---------------------------------------------------------------------------

async function probeHealth() {
  try {
    const res = await fetch(API.health, {
      method: "GET",
      headers: { Accept: "application/json" },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const body = await res.json();
    const healthy = body?.status === "healthy" && body?.faiss_index_loaded;
    setStatus(healthy ? "healthy" : "degraded", healthy ? "Healthy" : "Degraded");

    const total = Number(body?.total_documents_indexed ?? 0);
    if (total > 0) {
      el["loading-doc-count"].textContent = String(total);
    }
  } catch {
    setStatus("degraded", "Unreachable");
  }
}

function setStatus(kind, label) {
  el["status-dot"].classList.remove(
    "status-dot--healthy",
    "status-dot--degraded",
    "status-dot--unknown",
  );
  el["status-dot"].classList.add(`status-dot--${kind}`);
  el["status-label"].textContent = label;
}

// ---------------------------------------------------------------------------
// Presets, chips, counter, clear visibility
// ---------------------------------------------------------------------------

function renderPresets() {
  const frag = document.createDocumentFragment();
  PRESETS.forEach((preset, idx) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "preset-btn";
    btn.dataset.preset = String(idx + 1);
    btn.setAttribute("aria-label", `${preset.category}: ${preset.text}`);
    btn.innerHTML = `
      <span class="preset-num">${idx + 1}</span>
      <span class="preset-icon" aria-hidden="true">${escapeHtml(preset.icon)}</span>
      <span class="preset-text">${escapeHtml(preset.text)}</span>
    `;
    btn.addEventListener("click", () => {
      el["query-input"].value = preset.text;
      el["query-input"].focus();
      updateCharCount();
      updateSubmitState();
      updateClearVisibility();
      btn.classList.remove("is-clicked");
      // Restart pulse animation on every click.
      void btn.offsetWidth;
      btn.classList.add("is-clicked");
    });
    frag.appendChild(btn);
  });
  el["preset-grid"].appendChild(frag);
}

function wireSeverityChips() {
  const chips = el["severity-chips"].querySelectorAll(".chip");
  chips.forEach((chip) => {
    chip.addEventListener("click", () => {
      if (state.loading) return;
      const sev = chip.getAttribute("data-severity") || "All";
      state.severity = sev;
      chips.forEach((c) => {
        const active = c === chip;
        c.classList.toggle("is-active", active);
        c.setAttribute("aria-checked", active ? "true" : "false");
      });
      updateFilterResultInfo();
    });
  });
}

function updateFilterResultInfo() {
  if (state.severity === "All" || state.lastSeverityCount === null) {
    el["filter-result-info"].hidden = true;
    return;
  }
  el["filter-result-count"].textContent = String(state.lastSeverityCount);
  el["filter-result-info"].hidden = false;
}

function updateCharCount() {
  const len = el["query-input"].value.length;
  el["char-count"].textContent = String(len);
  const counter = el["char-count"].parentElement;
  counter.classList.toggle(
    "is-warning",
    len >= CHAR_WARN_THRESHOLD && len < CHAR_CRIT_THRESHOLD,
  );
  counter.classList.toggle(
    "is-critical",
    len >= CHAR_CRIT_THRESHOLD && len <= MAX_QUESTION_LEN,
  );
  counter.classList.toggle("is-over", len > MAX_QUESTION_LEN);
}

function updateSubmitState() {
  const text = el["query-input"].value.trim();
  const valid =
    text.length >= MIN_QUESTION_LEN &&
    el["query-input"].value.length <= MAX_QUESTION_LEN &&
    !state.loading;
  el["submit-btn"].disabled = !valid;
}

function updateClearVisibility() {
  const hasInput = el["query-input"].value.length > 0;
  const hasResponse = !el["response-panel"].hidden;
  el["clear-btn"].hidden = !(hasInput || hasResponse);
}

// ---------------------------------------------------------------------------
// Render: response & sources
// ---------------------------------------------------------------------------

function renderResponse(data) {
  hideBanners();

  const conf = data?.confidence ?? "none";
  const badge = el["confidence-badge"];
  badge.classList.remove("is-high", "is-medium", "is-low", "is-none");
  badge.classList.add(confidenceClass(conf));
  badge.textContent = conf;

  const ms = Number(data?.processing_time_ms ?? 0);
  state.lastProcessingMs = ms;
  el["timing-text"].textContent = `Answered in ${formatProcessing(ms)}`;
  el["timing-pill"].hidden = false;

  const model = String(data?.model_used ?? "").trim();
  if (model) {
    el["model-text"].textContent = model;
    el["model-pill"].hidden = false;
  } else {
    el["model-pill"].hidden = true;
  }

  el["answer-content"].innerHTML = renderAnswer(data?.answer ?? "");

  const sources = Array.isArray(data?.sources) ? data.sources : [];
  renderSources(sources);

  el["footer-timing-value"].textContent = formatProcessing(ms);
  el["footer-last-time"].textContent = formatLastQueryTime(new Date());
  el["footer-timing"].hidden = false;

  state.sessionQueryCount += 1;
  el["session-query-count"].textContent = String(state.sessionQueryCount);

  if (state.severity !== "All") {
    state.lastSeverityCount = sources.length;
  } else {
    state.lastSeverityCount = null;
  }
  updateFilterResultInfo();

  el["response-panel"].hidden = false;
  el["new-query-fab"].hidden = false;

  if (conf === "none") {
    showInfo(data?.answer || "No grounded answer found in the knowledge base.");
  }

  el["response-panel"].scrollIntoView({ behavior: "smooth", block: "start" });

  updateClearVisibility();
}

function renderSources(sources) {
  el["sources-count"].textContent = String(sources.length);
  el["sources-list"].innerHTML = "";

  if (sources.length === 0) {
    el["sources-section"].hidden = true;
    return;
  }

  const frag = document.createDocumentFragment();
  for (const src of sources) {
    const pct = Math.max(
      0,
      Math.min(100, Math.round((src.relevance_score ?? 0) * 100)),
    );
    const sev = src.severity || "N/A";
    const docTypeRaw = String(src.document_type || "").toLowerCase();
    const docTypeLabel =
      docTypeRaw === "sop"
        ? "SOP"
        : docTypeRaw === "incident"
          ? "INC"
          : docTypeRaw === "reference"
            ? "REF"
            : "—";
    const docTypeClass =
      docTypeRaw === "sop"
        ? "is-sop"
        : docTypeRaw === "incident"
          ? "is-incident"
          : docTypeRaw === "reference"
            ? "is-reference"
            : "is-incident";

    let cardClass = "source-card";
    if (sev === "P1") cardClass += " is-p1";
    else if (sev === "P2") cardClass += " is-p2";
    else if (sev === "P3") cardClass += " is-p3";
    else if (docTypeRaw === "sop") cardClass += " is-sop";
    else if (docTypeRaw === "reference") cardClass += " is-ref";

    const li = document.createElement("li");
    li.className = cardClass;
    li.tabIndex = 0;
    li.setAttribute("role", "button");
    li.setAttribute("aria-expanded", "false");
    li.innerHTML = `
      <div class="source-card-top">
        <div>
          <div class="source-id">${escapeHtml(src.id || "—")}</div>
          <h4 class="source-title">${escapeHtml(src.title || "Untitled")}</h4>
        </div>
        <div class="source-tags">
          <span class="tag tag--severity ${severityClass(sev)}">${escapeHtml(sev)}</span>
          <span class="tag tag--category">${escapeHtml(src.category || "—")}</span>
          <span class="tag tag--doctype ${docTypeClass}">${escapeHtml(docTypeLabel)}</span>
        </div>
      </div>
      <div class="relevance" aria-label="Relevance score">
        <div class="relevance-bar" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100">
          <div class="relevance-fill" data-target="${pct}" style="width: 0%"></div>
        </div>
        <span class="relevance-value">${pct}%</span>
      </div>
      <div class="source-preview">
        ${escapeHtml(buildSourcePreview(src))}
      </div>
    `;
    li.addEventListener("click", () => toggleSourceExpand(li));
    li.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggleSourceExpand(li);
      }
    });
    frag.appendChild(li);
  }
  el["sources-list"].appendChild(frag);
  el["sources-section"].hidden = false;

  el["sources-toggle"].setAttribute("aria-expanded", "false");
  el["sources-list"].hidden = true;

  window.requestAnimationFrame(() => {
    el["sources-list"]
      .querySelectorAll(".relevance-fill")
      .forEach((fill) => {
        const target = Number(fill.getAttribute("data-target") || "0");
        fill.style.width = `${target}%`;
      });
  });
}

function buildSourcePreview(src) {
  const parts = [
    `ID: ${src.id || "—"}`,
    `Title: ${src.title || "Untitled"}`,
    `Severity: ${src.severity || "N/A"}`,
    `Category: ${src.category || "—"}`,
    `Document type: ${src.document_type || "—"}`,
    `Rank: ${src.rank ?? "—"}`,
    `Relevance: ${Math.round((src.relevance_score ?? 0) * 1000) / 10}%`,
  ];
  return parts.join("\n");
}

function toggleSourceExpand(card) {
  const expanded = card.classList.toggle("is-expanded");
  card.setAttribute("aria-expanded", expanded ? "true" : "false");
  // Auto-expand the sources list when a card is opened.
  if (expanded) {
    el["sources-list"].hidden = false;
    el["sources-toggle"].setAttribute("aria-expanded", "true");
  }
}

function wireSourcesToggle() {
  el["sources-toggle"].addEventListener("click", () => {
    const expanded =
      el["sources-toggle"].getAttribute("aria-expanded") === "true";
    el["sources-toggle"].setAttribute(
      "aria-expanded",
      expanded ? "false" : "true",
    );
    el["sources-list"].hidden = expanded;
    window.requestAnimationFrame(() => {
      el["sources-list"]
        .querySelectorAll(".relevance-fill")
        .forEach((fill) => {
          const target = Number(fill.getAttribute("data-target") || "0");
          fill.style.width = `${target}%`;
        });
    });
  });
}

// ---------------------------------------------------------------------------
// Banners
// ---------------------------------------------------------------------------

function showError(message) {
  el["error-message"].textContent = message;
  el["error-banner"].hidden = false;
  el["info-banner"].hidden = true;
}

function showInfo(message) {
  el["info-message"].textContent = message;
  el["info-banner"].hidden = false;
}

function hideBanners() {
  el["error-banner"].hidden = true;
  el["info-banner"].hidden = true;
}

// ---------------------------------------------------------------------------
// Loading state machine
// ---------------------------------------------------------------------------

function clearLoadingTimers() {
  for (const t of state.loadingTimers) window.clearTimeout(t);
  state.loadingTimers = [];
}

function resetLoadingSteps() {
  const stepEls = el["loading-steps"].querySelectorAll(".loading-step");
  stepEls.forEach((stepEl) => {
    stepEl.classList.remove("is-active", "is-done");
  });
  el["loading-progress-fill"].style.right = "100%";
}

function advanceLoadingSteps() {
  resetLoadingSteps();
  let cumulative = 0;
  LOADING_STEPS.forEach((step, idx) => {
    const start = cumulative;
    cumulative += step.minMs;
    const t = window.setTimeout(() => {
      const stepEls = el["loading-steps"].querySelectorAll(".loading-step");
      stepEls.forEach((stepEl) => {
        const sid = Number(stepEl.dataset.step || "0");
        if (sid < step.id) {
          stepEl.classList.remove("is-active");
          stepEl.classList.add("is-done");
        } else if (sid === step.id) {
          stepEl.classList.add("is-active");
          stepEl.classList.remove("is-done");
        }
      });
      el["loading-progress-fill"].style.right = `${100 - step.progressTo}%`;
    }, start);
    state.loadingTimers.push(t);
  });
}

function setLoading(isLoading) {
  state.loading = isLoading;
  el["loading-panel"].hidden = !isLoading;
  el["query-input"].disabled = isLoading;
  el["submit-btn"].disabled = isLoading;
  el["severity-chips"]
    .querySelectorAll(".chip")
    .forEach((c) => c.toggleAttribute("disabled", isLoading));

  if (isLoading) {
    advanceLoadingSteps();
  } else {
    clearLoadingTimers();
    // Snap progress to 100% and mark all steps done briefly before hiding.
    el["loading-progress-fill"].style.right = "0%";
    el["loading-steps"]
      .querySelectorAll(".loading-step")
      .forEach((stepEl) => {
        stepEl.classList.remove("is-active");
        stepEl.classList.add("is-done");
      });
  }

  if (!isLoading) updateSubmitState();
}

// ---------------------------------------------------------------------------
// Submit
// ---------------------------------------------------------------------------

async function submitQuery() {
  const question = el["query-input"].value.trim();
  if (question.length < MIN_QUESTION_LEN) {
    showError(`Please enter at least ${MIN_QUESTION_LEN} characters.`);
    return;
  }
  if (question.length > MAX_QUESTION_LEN) {
    showError(`Question exceeds ${MAX_QUESTION_LEN} characters.`);
    return;
  }

  hideBanners();
  el["response-panel"].hidden = true;
  el["new-query-fab"].hidden = true;
  setLoading(true);

  /** @type {{question: string, severity_filter?: string}} */
  const payload = { question };
  if (state.severity && state.severity !== "All") {
    payload.severity_filter = state.severity;
  }

  let res;
  try {
    res = await fetch(API.query, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(payload),
    });
  } catch (networkErr) {
    console.error("[IncidentIQ] Network error:", networkErr);
    setLoading(false);
    showError(
      "Cannot connect to server. Make sure the server is running on port 8000.",
    );
    return;
  }

  let body = null;
  try {
    body = await res.json();
  } catch (parseErr) {
    console.error("[IncidentIQ] Response parse error:", parseErr);
    body = null;
  }

  setLoading(false);

  if (!res.ok) {
    if (res.status === 422) {
      showError("Question is too short or invalid. Please try again.");
    } else if (res.status === 500 || res.status === 503) {
      showError("Server error. Please try again.");
    } else {
      console.error("[IncidentIQ] Unexpected HTTP status:", res.status, body);
      const detail = body?.detail || `Unexpected error (HTTP ${res.status}).`;
      showError(typeof detail === "string" ? detail : "Unexpected error.");
    }
    return;
  }

  renderResponse(body);
}

// ---------------------------------------------------------------------------
// Clear / New query / Copy / Follow-up
// ---------------------------------------------------------------------------

function clearAll() {
  el["query-input"].value = "";
  el["query-input"].focus();
  el["response-panel"].hidden = true;
  el["answer-content"].innerHTML = "";
  el["sources-list"].innerHTML = "";
  el["sources-section"].hidden = true;
  el["timing-pill"].hidden = true;
  el["model-pill"].hidden = true;
  el["footer-timing"].hidden = true;
  el["new-query-fab"].hidden = true;
  state.lastProcessingMs = null;
  hideBanners();
  updateCharCount();
  updateSubmitState();
  updateClearVisibility();
}

function startNewQuery() {
  // Keep session counter and last query timing visible; clear textarea + answer.
  clearAll();
  window.scrollTo({ top: 0, behavior: "smooth" });
  el["query-input"].focus();
}

async function copyAnswer() {
  const text = el["answer-content"].innerText.trim();
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    el["copy-btn-label"].textContent = "Copied!";
    el["copy-btn"].classList.add("is-copied");
    window.setTimeout(() => {
      el["copy-btn-label"].textContent = "Copy answer";
      el["copy-btn"].classList.remove("is-copied");
    }, 1600);
  } catch (err) {
    console.error("[IncidentIQ] Clipboard error:", err);
    showError("Could not copy to clipboard.");
  }
}

function askFollowUp() {
  // Keep the answer visible; focus the textarea for a follow-up question.
  el["query-input"].value = "";
  el["query-input"].focus();
  el["query-input"].scrollIntoView({ behavior: "smooth", block: "center" });
  updateCharCount();
  updateSubmitState();
  updateClearVisibility();
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

function wireEvents() {
  el["query-input"].addEventListener("input", () => {
    updateCharCount();
    updateSubmitState();
    updateClearVisibility();
  });

  el["query-input"].addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      if (!el["submit-btn"].disabled) submitQuery();
    }
  });

  el["query-form"].addEventListener("submit", (event) => {
    event.preventDefault();
    submitQuery();
  });

  el["clear-btn"].addEventListener("click", clearAll);
  el["new-query-fab"].addEventListener("click", startNewQuery);
  el["copy-btn"].addEventListener("click", copyAnswer);
  el["followup-btn"].addEventListener("click", askFollowUp);
}

function init() {
  cacheDom();
  renderPresets();
  wireSeverityChips();
  wireSourcesToggle();
  wireEvents();
  updateCharCount();
  updateSubmitState();
  updateClearVisibility();
  startClock();
  probeHealth();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
