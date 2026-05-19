/**
 * IncidentIQ — frontend controller (vanilla JS, ES2022).
 *
 * - Health probe on DOMContentLoaded.
 * - POST /api/query with optional severity filter.
 * - Lightweight markdown-ish renderer (## headings, ordered steps, **bold**, `code`).
 * - All listeners bound here (no inline handlers).
 */

"use strict";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const API = Object.freeze({
  health: "/health",
  query: "/api/query",
});

const MAX_QUESTION_LEN = 500;
const MIN_QUESTION_LEN = 3;

const PRESETS = Object.freeze([
  "What are the triage steps for a P1 Kubernetes pod crash loop?",
  "How do I resolve PostgreSQL connection pool exhaustion?",
  "What is the SOP for Kafka consumer lag incidents?",
  "We are seeing 5xx errors spiking on our API gateway, what do I do?",
  "How do I handle an AWS IAM permission denied error in CI/CD?",
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

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const state = {
  severity: "All",
  loading: false,
  lastProcessingMs: null,
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
    "severity-chips",
    "query-form",
    "query-input",
    "char-count",
    "submit-btn",
    "clear-btn",
    "preset-grid",
    "loading-panel",
    "error-banner",
    "error-message",
    "info-banner",
    "info-message",
    "response-panel",
    "confidence-badge",
    "timing-pill",
    "timing-text",
    "answer-content",
    "sources-section",
    "sources-toggle",
    "sources-count",
    "sources-list",
    "footer-timing",
    "footer-timing-value",
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
// UI: presets, chips, counter, clear visibility
// ---------------------------------------------------------------------------

function renderPresets() {
  const frag = document.createDocumentFragment();
  PRESETS.forEach((text, idx) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "preset-btn";
    btn.dataset.preset = String(idx + 1);
    btn.innerHTML = `<span class="preset-num">${idx + 1}</span><span class="preset-text">${escapeHtml(text)}</span>`;
    btn.addEventListener("click", () => {
      el["query-input"].value = text;
      el["query-input"].focus();
      updateCharCount();
      updateSubmitState();
      updateClearVisibility();
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
    });
  });
}

function updateCharCount() {
  const len = el["query-input"].value.length;
  el["char-count"].textContent = String(len);
  const counter = el["char-count"].parentElement;
  counter.classList.toggle("is-warning", len > 450 && len <= MAX_QUESTION_LEN);
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

  // Confidence badge
  const conf = data?.confidence ?? "none";
  const badge = el["confidence-badge"];
  badge.classList.remove("is-high", "is-medium", "is-low", "is-none");
  badge.classList.add(confidenceClass(conf));
  badge.textContent = conf;

  // Timing
  const ms = Number(data?.processing_time_ms ?? 0);
  state.lastProcessingMs = ms;
  el["timing-text"].textContent = `Answered in ${formatProcessing(ms)}`;
  el["timing-pill"].hidden = false;

  // Answer body
  el["answer-content"].innerHTML = renderAnswer(data?.answer ?? "");

  // Sources
  const sources = Array.isArray(data?.sources) ? data.sources : [];
  renderSources(sources);

  // Footer timing
  el["footer-timing-value"].textContent = formatProcessing(ms);
  el["footer-timing"].hidden = false;

  el["response-panel"].hidden = false;

  // If confidence none, also surface server message as an info banner.
  if (conf === "none") {
    showInfo(data?.answer || "No grounded answer found in the knowledge base.");
  }

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
    const pct = Math.max(0, Math.min(100, Math.round((src.relevance_score ?? 0) * 100)));
    const sev = src.severity || "N/A";
    const docType = (src.document_type || "").toLowerCase();
    const docTypeLabel = docType === "sop" ? "SOP" : docType === "incident" ? "Incident" : (src.document_type || "—");
    const docTypeClass = docType === "sop" ? "is-sop" : "is-incident";

    const li = document.createElement("li");
    li.className = "source-card";
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
          <div class="relevance-fill" style="width: ${pct}%"></div>
        </div>
        <span class="relevance-value">${pct}%</span>
      </div>
    `;
    frag.appendChild(li);
  }
  el["sources-list"].appendChild(frag);
  el["sources-section"].hidden = false;

  // Collapsed by default.
  el["sources-toggle"].setAttribute("aria-expanded", "false");
  el["sources-list"].hidden = true;
}

function wireSourcesToggle() {
  el["sources-toggle"].addEventListener("click", () => {
    const expanded = el["sources-toggle"].getAttribute("aria-expanded") === "true";
    el["sources-toggle"].setAttribute("aria-expanded", expanded ? "false" : "true");
    el["sources-list"].hidden = expanded;
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
// Loading state
// ---------------------------------------------------------------------------

function setLoading(isLoading) {
  state.loading = isLoading;
  el["loading-panel"].hidden = !isLoading;
  el["query-input"].disabled = isLoading;
  el["submit-btn"].disabled = isLoading;
  el["severity-chips"]
    .querySelectorAll(".chip")
    .forEach((c) => c.toggleAttribute("disabled", isLoading));
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
    setLoading(false);
    showError("Connection failed. Is the server running?");
    return;
  }

  let body = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }

  setLoading(false);

  if (!res.ok) {
    if (res.status === 422) {
      showError("Please check your question and try again.");
    } else if (res.status === 500 || res.status === 503) {
      showError("Server error. Please try again shortly.");
    } else {
      const detail = body?.detail || `Unexpected error (HTTP ${res.status}).`;
      showError(detail);
    }
    return;
  }

  renderResponse(body);
}

// ---------------------------------------------------------------------------
// Clear
// ---------------------------------------------------------------------------

function clearAll() {
  el["query-input"].value = "";
  el["query-input"].focus();
  el["response-panel"].hidden = true;
  el["answer-content"].innerHTML = "";
  el["sources-list"].innerHTML = "";
  el["sources-section"].hidden = true;
  el["timing-pill"].hidden = true;
  el["footer-timing"].hidden = true;
  state.lastProcessingMs = null;
  hideBanners();
  updateCharCount();
  updateSubmitState();
  updateClearVisibility();
}

// ---------------------------------------------------------------------------
// Init
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
  probeHealth();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
