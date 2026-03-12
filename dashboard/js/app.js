/* ============================================================
   Agentic Mesh Dashboard — Application Logic
   ============================================================ */

// ── Configuration ────────────────────────────────────────────
const API_BASE = "https://vczletkr9k.execute-api.us-east-1.amazonaws.com/Prod";
const POLL_INTERVAL = 2500; // ms between polls
const MAX_POLLS = 120; // max 5 minutes of polling

// ── State ────────────────────────────────────────────────────
const state = {
    tasks: JSON.parse(localStorage.getItem("meshTasks") || "[]"),
    activeTaskId: null,
    pollTimer: null,
    pollCount: 0,
    stats: {
        total: 0,
        success: 0,
        totalCost: 0,
        totalLatency: 0,
        cacheHits: 0,
        agentCounts: { coder: 0, researcher: 0, summarizer: 0 },
    },
};

// ── Initialization ───────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
    initNavigation();
    initTextarea();
    initFilters();
    recalculateStats();
    renderHistory();
});

// ── Navigation ───────────────────────────────────────────────
function initNavigation() {
    const links = document.querySelectorAll(".nav-link");
    links.forEach((link) => {
        link.addEventListener("click", (e) => {
            links.forEach((l) => l.classList.remove("active"));
            link.classList.add("active");
        });
    });

    // Highlight active section on scroll
    const sections = ["submit", "pipeline", "analytics", "history"];
    const observer = new IntersectionObserver(
        (entries) => {
            entries.forEach((entry) => {
                if (entry.isIntersecting) {
                    const id = entry.target.id;
                    links.forEach((l) => {
                        l.classList.toggle("active", l.dataset.section === id);
                    });
                }
            });
        },
        { threshold: 0.3, rootMargin: `-${64 + 24}px 0px 0px 0px` }
    );
    sections.forEach((s) => {
        const el = document.getElementById(s);
        if (el) observer.observe(el);
    });
}

// ── Auto-resize Textarea ─────────────────────────────────────
function initTextarea() {
    const textarea = document.getElementById("task-input");
    textarea.addEventListener("input", () => {
        textarea.style.height = "auto";
        textarea.style.height = Math.min(textarea.scrollHeight, 120) + "px";
    });
    textarea.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submitTask();
        }
    });
}

// ── Quick Prompt ─────────────────────────────────────────────
function usePrompt(text) {
    const textarea = document.getElementById("task-input");
    textarea.value = text;
    textarea.style.height = "auto";
    textarea.style.height = Math.min(textarea.scrollHeight, 120) + "px";
    textarea.focus();
}

// ── Submit Task ──────────────────────────────────────────────
async function submitTask() {
    const textarea = document.getElementById("task-input");
    const typeSelect = document.getElementById("task-type");
    const submitBtn = document.getElementById("submit-btn");
    const taskText = textarea.value.trim();

    if (!taskText) return;

    // Disable UI
    submitBtn.disabled = true;
    textarea.disabled = true;

    // Clear welcome message
    const welcome = document.querySelector(".chat-welcome");
    if (welcome) welcome.remove();

    // Add user message to chat
    addChatMessage("user", taskText);

    // Clear input
    textarea.value = "";
    textarea.style.height = "auto";

    // Show typing indicator
    const typingId = addTypingIndicator();

    // Reset pipeline
    resetPipeline();
    activatePipelineNode("submit");

    try {
        const response = await fetch(`${API_BASE}/task`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                task: taskText,
                type_hint: typeSelect.value === "auto" ? undefined : typeSelect.value,
            }),
        });

        const data = await response.json();
        let result;

        if (data.body) {
            result = typeof data.body === "string" ? JSON.parse(data.body) : data.body;
        } else {
            result = data;
        }

        if (result.task_id) {
            state.activeTaskId = result.task_id;

            // Update info panel
            showInfoPanel(result.task_id);

            // Start pipeline animation
            completePipelineNode("submit");
            activatePipelineNode("guardrail");

            // Show toast
            showToast("info", `Task submitted: ${result.task_id.slice(0, 8)}...`);

            // Start polling
            startPolling(result.task_id, taskText, typingId);
        } else {
            removeTypingIndicator(typingId);
            addChatMessage("assistant", "❌ Error: " + (result.error || "Failed to submit task"));
            showToast("error", "Failed to submit task");
            resetPipeline();
        }
    } catch (err) {
        removeTypingIndicator(typingId);
        addChatMessage("assistant", "❌ Network error: " + err.message);
        showToast("error", "Network error — is the API reachable?");
        resetPipeline();
    } finally {
        submitBtn.disabled = false;
        textarea.disabled = false;
        textarea.focus();
    }
}

// ── Polling ──────────────────────────────────────────────────
function startPolling(taskId, taskText, typingId) {
    state.pollCount = 0;

    // Simulate pipeline stages over time
    const stageTimers = [
        setTimeout(() => {
            completePipelineNode("guardrail");
            activatePipelineNode("broker");
        }, 2000),
        setTimeout(() => {
            completePipelineNode("broker");
            activatePipelineNode("worker");
        }, 4000),
        setTimeout(() => {
            completePipelineNode("worker");
            activatePipelineNode("verify");
        }, 8000),
    ];

    state.pollTimer = setInterval(async () => {
        state.pollCount++;

        if (state.pollCount > MAX_POLLS) {
            clearInterval(state.pollTimer);
            stageTimers.forEach(clearTimeout);
            removeTypingIndicator(typingId);
            addChatMessage("assistant", "⏱️ Task is taking longer than expected. Check back later.");
            return;
        }

        try {
            const response = await fetch(`${API_BASE}/task/${taskId}`);
            const data = await response.json();
            let result;

            if (data.body) {
                result = typeof data.body === "string" ? JSON.parse(data.body) : data.body;
            } else {
                result = data;
            }

            // Update info panel status
            updateInfoPanel(result);

            if (result.status === "SUCCESS" || result.status === "FAILED" || result.answer) {
                clearInterval(state.pollTimer);
                stageTimers.forEach(clearTimeout);
                removeTypingIndicator(typingId);

                if (result.status === "SUCCESS" || result.answer) {
                    // Complete pipeline
                    completePipelineNode("guardrail");
                    completePipelineNode("broker");
                    completePipelineNode("worker");
                    completePipelineNode("verify");
                    completePipelineNode("save");

                    // Add response to chat
                    addChatMessage("assistant", formatAnswer(result.answer || "No answer received"), result);

                    // Save to history
                    saveTask(taskId, taskText, result);

                    showToast("success", `Task completed by ${result.agent || "agent"} (${(result.quality_score || 0).toFixed(1)}/10)`);
                } else {
                    // Failed
                    failPipelineNode("worker");
                    addChatMessage("assistant", "❌ Task failed: " + (result.error || "Unknown error"));
                    saveTask(taskId, taskText, { ...result, status: "FAILED" });
                    showToast("error", "Task processing failed");
                }
            }
        } catch (err) {
            // Silent retry on poll errors
            console.warn("Poll error:", err);
        }
    }, POLL_INTERVAL);
}

// ── Chat Messages ────────────────────────────────────────────
function addChatMessage(role, content, meta = null) {
    const container = document.getElementById("chat-messages");
    const msg = document.createElement("div");
    msg.className = `message ${role}`;

    const avatar = role === "user" ? "👤" : "🕸️";
    let metaHtml = "";

    if (meta && role === "assistant") {
        const parts = [];
        if (meta.agent) parts.push(`<span>${getAgentEmoji(meta.agent)} ${meta.agent}</span>`);
        if (meta.cost_estimate) parts.push(`<span class="cost-tag">$${meta.cost_estimate.toFixed(4)}</span>`);
        if (meta.quality_score) parts.push(`<span>⭐ ${meta.quality_score.toFixed(1)}/10</span>`);
        if (meta.worker_latency_ms) parts.push(`<span>⚡ ${(meta.worker_latency_ms / 1000).toFixed(1)}s</span>`);
        if (parts.length) metaHtml = `<div class="message-meta">${parts.join("")}</div>`;
    }

    msg.innerHTML = `
        <div class="message-avatar">${avatar}</div>
        <div>
            <div class="message-content">${content}</div>
            ${metaHtml}
        </div>
    `;

    container.appendChild(msg);
    container.scrollTop = container.scrollHeight;
}

function addTypingIndicator() {
    const container = document.getElementById("chat-messages");
    const id = "typing-" + Date.now();
    const msg = document.createElement("div");
    msg.className = "message assistant";
    msg.id = id;
    msg.innerHTML = `
        <div class="message-avatar">🕸️</div>
        <div class="message-content">
            <div class="typing-indicator">
                <span></span><span></span><span></span>
            </div>
        </div>
    `;
    container.appendChild(msg);
    container.scrollTop = container.scrollHeight;
    return id;
}

function removeTypingIndicator(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}

function formatAnswer(text) {
    if (!text) return "<em>No content</em>";

    // Convert markdown code blocks to proper HTML
    let html = escapeHtml(text);

    // Code blocks with language
    html = html.replace(/```(\w+)?\n([\s\S]*?)```/g, (_, lang, code) => {
        return `<pre><code>${code.trim()}</code></pre>`;
    });

    // Inline code
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");

    // Bold
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

    // Paragraphs
    html = html
        .split("\n\n")
        .map((p) => {
            if (p.startsWith("<pre>")) return p;
            return `<p>${p.replace(/\n/g, "<br>")}</p>`;
        })
        .join("");

    return html;
}

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

// ── Info Panel ───────────────────────────────────────────────
function showInfoPanel(taskId) {
    document.getElementById("info-placeholder").classList.add("hidden");
    document.getElementById("info-content").classList.remove("hidden");
    document.getElementById("info-task-id").textContent = taskId.slice(0, 16) + "…";
    document.getElementById("info-status").innerHTML = '<span class="status-badge processing">PROCESSING</span>';
    document.getElementById("info-agent").textContent = "Routing…";
    document.getElementById("info-model").textContent = "—";
    document.getElementById("info-quality").textContent = "—";
    document.getElementById("info-cost").textContent = "—";
    document.getElementById("info-latency").textContent = "—";
    document.getElementById("info-escalated").textContent = "—";
    document.getElementById("info-cache").textContent = "—";
}

function updateInfoPanel(result) {
    const status = result.status || (result.answer ? "SUCCESS" : "PROCESSING");
    const statusClass = status.toLowerCase();
    document.getElementById("info-status").innerHTML = `<span class="status-badge ${statusClass}">${status}</span>`;

    if (result.agent) {
        document.getElementById("info-agent").innerHTML = `${getAgentEmoji(result.agent)} ${result.agent}`;
    }
    if (result.model) {
        const short = result.model.split(".").pop().replace(/-v\d.*$/, "");
        document.getElementById("info-model").textContent = short;
    }
    if (result.quality_score != null) {
        const q = parseFloat(result.quality_score);
        const cls = q >= 7 ? "high" : q >= 5 ? "medium" : "low";
        document.getElementById("info-quality").innerHTML = `<span class="quality-pill ${cls}">${q.toFixed(1)}/10</span>`;
    }
    if (result.cost_estimate != null) {
        document.getElementById("info-cost").textContent = `$${parseFloat(result.cost_estimate).toFixed(4)}`;
    }
    if (result.worker_latency_ms != null) {
        document.getElementById("info-latency").textContent = `${(parseFloat(result.worker_latency_ms) / 1000).toFixed(1)}s`;
    }
    if (result.escalated != null) {
        const esc = result.escalated === true || result.escalated === 1 || result.escalated === "true";
        document.getElementById("info-escalated").textContent = esc ? "⚠️ Yes" : "No";
    }
    if (result.cache_hit != null) {
        const hit = result.cache_hit === true || result.cache_hit === 1 || result.cache_hit === "true";
        document.getElementById("info-cache").textContent = hit ? "✅ Yes" : "No";
    }

    if (result.verification_feedback) {
        document.getElementById("info-notes").textContent = result.verification_feedback;
    } else {
        document.getElementById("info-notes").textContent = "No specific notes provided by the Judge.";
    }

    // Update analytics
    if (result.status === "SUCCESS" || result.answer) {
        updateAnalytics(result);
    }
}

// ── Pipeline Visualization ───────────────────────────────────
function resetPipeline() {
    const nodes = document.querySelectorAll(".pipeline-node");
    const connectors = document.querySelectorAll(".pipeline-connector");
    nodes.forEach((n) => {
        n.classList.remove("active", "completed", "failed");
        n.querySelector(".node-status").textContent = "Waiting";
    });
    connectors.forEach((c) => {
        c.classList.remove("active", "completed");
    });
}

function activatePipelineNode(stage) {
    const node = document.querySelector(`[data-stage="${stage}"]`);
    if (node) {
        node.classList.add("active");
        node.querySelector(".node-status").textContent = "Running";
    }

    // Activate connector before this node
    const connIndex = ["submit", "guardrail", "broker", "worker", "verify", "save"].indexOf(stage);
    if (connIndex > 0) {
        const conn = document.getElementById(`conn-${connIndex}`);
        if (conn) conn.classList.add("active");
    }
}

function completePipelineNode(stage) {
    const node = document.querySelector(`[data-stage="${stage}"]`);
    if (node) {
        node.classList.remove("active", "failed");
        node.classList.add("completed");
        node.querySelector(".node-status").textContent = "Done ✓";
    }

    const connIndex = ["submit", "guardrail", "broker", "worker", "verify", "save"].indexOf(stage);
    if (connIndex > 0) {
        const conn = document.getElementById(`conn-${connIndex}`);
        if (conn) {
            conn.classList.remove("active");
            conn.classList.add("completed");
        }
    }
}

function failPipelineNode(stage) {
    const node = document.querySelector(`[data-stage="${stage}"]`);
    if (node) {
        node.classList.remove("active");
        node.classList.add("failed");
        node.querySelector(".node-status").textContent = "Failed ✗";
    }
}

// ── Analytics ────────────────────────────────────────────────
function updateAnalytics(result) {
    // Quality rings
    if (result.verification_score != null || result.quality_score != null) {
        const score = parseFloat(result.quality_score || result.verification_score || 0);
        // Distribute evenly for now (we don't have individual dimension data from the GET endpoint)
        const pct = (score / 10) * 100;
        updateRing("accuracy", pct, score.toFixed(1));
        updateRing("completeness", pct, score.toFixed(1));
        updateRing("relevance", pct, score.toFixed(1));
    }

    // Cost breakdown
    if (result.cost_estimate != null) {
        const cost = parseFloat(result.cost_estimate);
        document.getElementById("cost-worker").textContent = `$${(cost * 0.75).toFixed(4)}`;
        document.getElementById("cost-verify").textContent = `$${(cost * 0.2).toFixed(4)}`;
        document.getElementById("cost-broker").textContent = `$${(cost * 0.05).toFixed(4)}`;
        document.getElementById("cost-total-breakdown").textContent = `$${cost.toFixed(4)}`;
    }

    if (result.cache_hit === true || result.cache_hit === 1) {
        document.getElementById("cost-savings").textContent = `−$${(parseFloat(result.cost_estimate || 0)).toFixed(4)}`;
    }
}

function updateRing(name, percentage, label) {
    const circumference = 2 * Math.PI * 42; // r=42
    const dashArray = `${(percentage / 100) * circumference} ${circumference}`;

    const ring = document.querySelector(`.ring-fill.${name}`);
    if (ring) ring.setAttribute("stroke-dasharray", dashArray);

    const ringItem = ring?.closest(".ring-item");
    if (ringItem) {
        const valueEl = ringItem.querySelector(".ring-value");
        if (valueEl) valueEl.textContent = label;
    }
}

// ── Task History ─────────────────────────────────────────────
function saveTask(taskId, taskText, result) {
    const entry = {
        id: taskId,
        task: taskText,
        agent: result.agent || "unknown",
        status: result.status || (result.answer ? "SUCCESS" : "FAILED"),
        quality: parseFloat(result.quality_score || result.verification_score || 0),
        cost: parseFloat(result.cost_estimate || 0),
        latency: parseFloat(result.worker_latency_ms || 0),
        answer: result.answer || "",
        model: result.model || "",
        escalated: result.escalated || false,
        cacheHit: result.cache_hit || false,
        verificationReasoning: result.verification_feedback || "",
        time: new Date().toISOString(),
    };

    state.tasks.unshift(entry);
    localStorage.setItem("meshTasks", JSON.stringify(state.tasks));
    recalculateStats();
    renderHistory();
}

function recalculateStats() {
    const tasks = state.tasks;
    const stats = state.stats;
    stats.total = tasks.length;
    stats.success = tasks.filter((t) => t.status === "SUCCESS").length;
    stats.totalCost = tasks.reduce((s, t) => s + (t.cost || 0), 0);
    stats.totalLatency = tasks.reduce((s, t) => s + (t.latency || 0), 0);
    stats.cacheHits = tasks.filter((t) => t.cacheHit === true || t.cacheHit === 1).length;
    stats.agentCounts = { coder: 0, researcher: 0, summarizer: 0 };
    tasks.forEach((t) => {
        if (stats.agentCounts[t.agent] != null) stats.agentCounts[t.agent]++;
    });

    // Update DOM
    document.getElementById("total-tasks").textContent = stats.total;
    document.getElementById("success-rate").textContent =
        stats.total > 0 ? `${Math.round((stats.success / stats.total) * 100)}%` : "—";
    document.getElementById("total-cost").textContent = `$${stats.totalCost.toFixed(4)}`;
    document.getElementById("avg-latency").textContent =
        stats.total > 0 ? `${((stats.totalLatency / stats.total) / 1000).toFixed(1)}s` : "—";
    document.getElementById("cache-hits").textContent = stats.cacheHits;

    // Agent bars
    const maxAgent = Math.max(...Object.values(stats.agentCounts), 1);
    Object.entries(stats.agentCounts).forEach(([agent, count]) => {
        const bar = document.getElementById(`bar-${agent}`);
        const val = document.getElementById(`bar-${agent}-val`);
        if (bar) bar.style.width = `${(count / maxAgent) * 100}%`;
        if (val) val.textContent = count;
    });
}

function renderHistory(filter = "all") {
    const tbody = document.getElementById("history-tbody");
    let tasks = state.tasks;

    if (filter !== "all") {
        tasks = tasks.filter((t) => t.status === filter);
    }

    if (tasks.length === 0) {
        tbody.innerHTML = `
            <tr class="empty-row">
                <td colspan="7">
                    <div class="empty-state">
                        <span class="empty-icon">📭</span>
                        <p>${filter === "all" ? "No tasks yet. Submit your first task above!" : "No tasks matching this filter."}</p>
                    </div>
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = tasks
        .map((t) => {
            const qClass = t.quality >= 7 ? "high" : t.quality >= 5 ? "medium" : "low";
            const statusClass = t.status.toLowerCase();
            const timeAgo = getTimeAgo(t.time);

            return `
                <tr onclick="openTaskModal('${t.id}')">
                    <td><span class="task-preview">${escapeHtml(t.task)}</span></td>
                    <td><span class="agent-badge ${t.agent}">${getAgentEmoji(t.agent)} ${t.agent}</span></td>
                    <td><span class="status-badge ${statusClass}">${t.status}</span></td>
                    <td><span class="quality-pill ${qClass}">${t.quality.toFixed(1)}</span></td>
                    <td class="mono">$${t.cost.toFixed(4)}</td>
                    <td class="mono">${(t.latency / 1000).toFixed(1)}s</td>
                    <td>${timeAgo}</td>
                </tr>
            `;
        })
        .join("");
}

function initFilters() {
    document.querySelectorAll(".filter-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".filter-btn").forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");
            renderHistory(btn.dataset.filter);
        });
    });
}

function clearHistory() {
    if (confirm("Clear all task history?")) {
        state.tasks = [];
        localStorage.removeItem("meshTasks");
        recalculateStats();
        renderHistory();
        showToast("info", "History cleared");
    }
}

// ── Task Detail Modal ────────────────────────────────────────
function openTaskModal(taskId) {
    const task = state.tasks.find((t) => t.id === taskId);
    if (!task) return;

    const modal = document.getElementById("modal-overlay");
    const body = document.getElementById("modal-body");

    const qClass = task.quality >= 7 ? "high" : task.quality >= 5 ? "medium" : "low";
    const esc = task.escalated === true || task.escalated === 1;

    body.innerHTML = `
        <div class="modal-meta">
            <div class="modal-meta-item">
                <span class="modal-meta-label">Agent</span>
                <span class="modal-meta-value">${getAgentEmoji(task.agent)} ${task.agent}</span>
            </div>
            <div class="modal-meta-item">
                <span class="modal-meta-label">Quality</span>
                <span class="modal-meta-value quality-pill ${qClass}">${task.quality.toFixed(1)}/10</span>
            </div>
            <div class="modal-meta-item">
                <span class="modal-meta-label">Cost</span>
                <span class="modal-meta-value mono">$${task.cost.toFixed(4)}</span>
            </div>
            <div class="modal-meta-item">
                <span class="modal-meta-label">Latency</span>
                <span class="modal-meta-value mono">${(task.latency / 1000).toFixed(1)}s</span>
            </div>
            <div class="modal-meta-item">
                <span class="modal-meta-label">Model</span>
                <span class="modal-meta-value mono" style="font-size:11px">${task.model || "—"}</span>
            </div>
            <div class="modal-meta-item">
                <span class="modal-meta-label">Escalated</span>
                <span class="modal-meta-value">${esc ? "⚠️ Yes" : "✅ No"}</span>
            </div>
        </div>
        <h4 style="margin-bottom:8px; color: var(--text-secondary); font-size:12px; text-transform:uppercase; letter-spacing:0.5px;">Task</h4>
        <p style="margin-bottom:20px; font-size:14px; line-height:1.6; color: var(--text-primary);">${escapeHtml(task.task)}</p>
        
        <h4 style="margin-bottom:8px; color: var(--text-secondary); font-size:12px; text-transform:uppercase; letter-spacing:0.5px;">Judge's Notes</h4>
        <p style="margin-bottom:20px; font-size:13px; line-height:1.6; color: var(--text-secondary); font-style: italic; background: rgba(255,255,255,0.02); padding: 12px; border-radius: 8px; border-left: 3px solid var(--violet-500);">${escapeHtml(task.verificationReasoning || "No feedback provided.")}</p>

        <h4 style="margin-bottom:8px; color: var(--text-secondary); font-size:12px; text-transform:uppercase; letter-spacing:0.5px;">Response</h4>
        <pre>${escapeHtml(task.answer || "No answer")}</pre>
    `;

    modal.classList.remove("hidden");
}

function closeModal() {
    document.getElementById("modal-overlay").classList.add("hidden");
}

// Close modal on Escape
document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeModal();
});

// ── Toast Notifications ──────────────────────────────────────
function showToast(type, message) {
    const container = document.getElementById("toast-container");
    const toast = document.createElement("div");
    const icons = { success: "✅", error: "❌", info: "ℹ️" };

    toast.className = `toast ${type}`;
    toast.innerHTML = `<span>${icons[type] || ""}</span><span>${message}</span>`;
    container.appendChild(toast);

    setTimeout(() => {
        toast.classList.add("out");
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// ── Helpers ──────────────────────────────────────────────────
function getAgentEmoji(agent) {
    const map = { coder: "💻", researcher: "🔍", summarizer: "📝" };
    return map[agent] || "🤖";
}

function getTimeAgo(isoString) {
    const diff = Date.now() - new Date(isoString).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "Just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    return `${days}d ago`;
}
