/* ═══════════════════════════════════════════════════════════
   AutoRepro — Client-Side Application Logic
   ═══════════════════════════════════════════════════════════ */

const API_BASE = '';  // Same origin
const POLL_INTERVAL = 3000;  // 3 seconds

let currentJobId = null;
let pollTimer = null;

/* ── Initialization ────────────────────────────────────── */

document.addEventListener('DOMContentLoaded', () => {
    checkHealth();
    loadHistory();
    setupForm();
    setupCharCount();
});

/* ── Health Check ──────────────────────────────────────── */

async function checkHealth() {
    const dot = document.getElementById('healthDot');
    const text = document.getElementById('healthText');
    try {
        const res = await fetch(`${API_BASE}/health`);
        if (res.ok) {
            dot.className = 'health-dot online';
            text.textContent = 'System Online';
        } else {
            throw new Error('Unhealthy');
        }
    } catch {
        dot.className = 'health-dot offline';
        text.textContent = 'Offline';
    }
}

/* ── Form Setup ────────────────────────────────────────── */

function setupForm() {
    const form = document.getElementById('submitForm');
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        await submitJob();
    });
}

function setupCharCount() {
    const textarea = document.getElementById('bugReport');
    const counter = document.getElementById('charCount');
    textarea.addEventListener('input', () => {
        const len = textarea.value.length;
        counter.textContent = `${len} / 20 min`;
        counter.className = len < 20 && len > 0 ? 'char-count warn' : 'char-count';
    });
}

/* ── Submit Job ────────────────────────────────────────── */

async function submitJob() {
    const bugReport = document.getElementById('bugReport').value.trim();
    const targetUrl = document.getElementById('targetUrl').value.trim();
    const submitBtn = document.getElementById('submitBtn');

    if (bugReport.length < 20) {
        shakeElement(document.getElementById('bugReport'));
        return;
    }

    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="status-spinner" style="width:16px;height:16px;border-width:2px;"></span> Submitting…';

    try {
        const res = await fetch(`${API_BASE}/reproduce`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ bug_report: bugReport, target_url: targetUrl }),
        });

        if (!res.ok) {
            const error = await res.json();
            throw new Error(error.detail?.message || error.detail || 'Submission failed');
        }

        const data = await res.json();
        currentJobId = data.job_id;

        // Show status section
        showStatus('Processing…', 'Analyzing your bug report and generating a Selenium script.');
        hideResult();

        // Start polling
        startPolling(data.job_id);

        // Reset form
        document.getElementById('bugReport').value = '';
        document.getElementById('targetUrl').value = '';
        document.getElementById('charCount').textContent = '0 / 20 min';

    } catch (err) {
        alert(`Error: ${err.message}`);
    } finally {
        submitBtn.disabled = false;
        submitBtn.innerHTML = '<span>🚀</span> Start Reproduction';
    }
}

/* ── Polling ───────────────────────────────────────────── */

function startPolling(jobId) {
    stopPolling();
    pollTimer = setInterval(() => pollResult(jobId), POLL_INTERVAL);
}

function stopPolling() {
    if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
}

async function pollResult(jobId) {
    try {
        const res = await fetch(`${API_BASE}/result/${jobId}`);
        if (!res.ok) return;

        const data = await res.json();

        if (data.status === 'processing') {
            updateStatus(data);
            return;
        }

        // Job is done or failed — stop polling
        stopPolling();
        hideStatus();
        renderResult(data);
        loadHistory();

    } catch (err) {
        console.error('Poll error:', err);
    }
}

/* ── Status UI ─────────────────────────────────────────── */

function showStatus(title, detail) {
    const section = document.getElementById('statusSection');
    document.getElementById('statusTitle').textContent = title;
    document.getElementById('statusDetail').textContent = detail;
    document.getElementById('statusAttempts').textContent = '';
    section.classList.add('visible');
}

function updateStatus(data) {
    if (data.attempt_count !== undefined && data.attempt_count > 0) {
        document.getElementById('statusAttempts').textContent = `Attempt ${data.attempt_count}`;
        document.getElementById('statusDetail').textContent =
            'Running script in Docker sandbox, evaluating results…';
    }
}

function hideStatus() {
    document.getElementById('statusSection').classList.remove('visible');
}

/* ── Result UI ─────────────────────────────────────────── */

function renderResult(data) {
    const section = document.getElementById('resultSection');
    const badge = document.getElementById('resultBadge');
    const meta = document.getElementById('resultMeta');

    // Badge
    if (data.success) {
        badge.className = 'result-badge success';
        badge.innerHTML = '✅ Bug Reproduced';
    } else {
        badge.className = 'result-badge failed';
        badge.innerHTML = '❌ Reproduction Failed';
    }

    // Meta
    const attempts = data.attempt_count || '—';
    const created = data.created_at ? formatTime(data.created_at) : '—';
    const completed = data.completed_at ? formatTime(data.completed_at) : '—';
    meta.innerHTML = `
    <span>🔄 ${attempts} attempt${attempts !== 1 ? 's' : ''}</span>
    <span>📅 ${created}</span>
    <span>✓ ${completed}</span>
  `;

    // Script
    const scriptViewer = document.getElementById('scriptViewer');
    if (data.final_script) {
        document.getElementById('scriptCode').textContent = data.final_script;
        Prism.highlightElement(document.getElementById('scriptCode'));
        scriptViewer.style.display = '';
    } else {
        scriptViewer.style.display = 'none';
    }

    // Logs
    const logsViewer = document.getElementById('logsViewer');
    if (data.logs && data.logs.trim()) {
        document.getElementById('logsContent').textContent = data.logs;
        logsViewer.style.display = '';
    } else {
        logsViewer.style.display = 'none';
    }

    // Proof of Execution (Screenshots)
    const screenshotsSection = document.getElementById('screenshotsSection');
    const grid = document.getElementById('screenshotsGrid');
    grid.innerHTML = '';
    if (data.screenshot_urls && data.screenshot_urls.length > 0) {
        // Sort by filename to ensure step order
        const sorted = [...data.screenshot_urls].sort();
        sorted.forEach((url, idx) => {
            const filename = url.split('/').pop();
            const label = formatScreenshotLabel(filename, idx + 1);
            const card = document.createElement('div');
            card.className = 'screenshot-card';
            card.onclick = () => openLightbox(url);
            card.innerHTML = `
        <div class="screenshot-step-num">${idx + 1}</div>
        <img src="${url}" alt="${label}" loading="lazy" />
        <div class="caption">${label}</div>
      `;
            grid.appendChild(card);
        });
        // Update subtitle with count
        document.getElementById('proofSubtitle').textContent =
            `${sorted.length} screenshot${sorted.length > 1 ? 's' : ''} captured from the real browser session inside the Docker sandbox.`;
        screenshotsSection.style.display = '';
    } else {
        screenshotsSection.style.display = 'none';
    }

    section.classList.add('visible');
}

function hideResult() {
    document.getElementById('resultSection').classList.remove('visible');
}

/* ── Copy to Clipboard ─────────────────────────────────── */

async function copyScript() {
    const code = document.getElementById('scriptCode').textContent;
    try {
        await navigator.clipboard.writeText(code);
        const feedback = document.getElementById('copyFeedback');
        feedback.classList.add('show');
        setTimeout(() => feedback.classList.remove('show'), 1500);
    } catch {
        // Fallback
        const textarea = document.createElement('textarea');
        textarea.value = code;
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        textarea.remove();
    }
}

/* ── Job History ───────────────────────────────────────── */

async function loadHistory() {
    const list = document.getElementById('historyList');
    const empty = document.getElementById('historyEmpty');

    try {
        const res = await fetch(`${API_BASE}/jobs`);
        if (!res.ok) return;

        const jobs = await res.json();

        if (jobs.length === 0) {
            empty.style.display = '';
            return;
        }

        empty.style.display = 'none';

        // Sort by created_at descending
        jobs.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));

        // Clear existing items (keep empty state node)
        list.querySelectorAll('.history-item').forEach(el => el.remove());

        jobs.forEach((job) => {
            const item = document.createElement('div');
            item.className = 'history-item';
            item.onclick = () => viewJob(job.job_id);

            const statusClass = job.status === 'done'
                ? (job.success ? 'success' : 'failed')
                : 'processing';
            const statusLabel = job.status === 'done'
                ? (job.success ? 'Success' : 'Failed')
                : 'Processing';

            const report = job.bug_report || 'No description';
            const time = job.created_at ? formatTime(job.created_at) : '';

            item.innerHTML = `
        <div class="history-item-left">
          <span class="history-item-id">${job.job_id.substring(0, 8)}…</span>
          <span class="history-item-report">${escapeHtml(report)}</span>
        </div>
        <div class="history-item-right">
          <span class="history-item-time">${time}</span>
          <span class="mini-badge ${statusClass}">${statusLabel}</span>
        </div>
      `;
            list.appendChild(item);
        });
    } catch (err) {
        console.error('History load error:', err);
    }
}

async function viewJob(jobId) {
    try {
        const res = await fetch(`${API_BASE}/result/${jobId}`);
        if (!res.ok) return;

        const data = await res.json();

        if (data.status === 'processing') {
            currentJobId = jobId;
            showStatus('Processing…', 'This job is still being processed.');
            hideResult();
            startPolling(jobId);
        } else {
            hideStatus();
            renderResult(data);
            // Scroll to result
            document.getElementById('resultSection').scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    } catch (err) {
        console.error('View job error:', err);
    }
}

/* ── Lightbox ──────────────────────────────────────────── */

function openLightbox(src) {
    const lightbox = document.getElementById('lightbox');
    document.getElementById('lightboxImg').src = src;
    lightbox.classList.add('open');
}

function closeLightbox() {
    document.getElementById('lightbox').classList.remove('open');
}

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeLightbox();
});

/* ── Utilities ─────────────────────────────────────────── */

function formatTime(isoString) {
    try {
        const date = new Date(isoString);
        const now = new Date();
        const diffMs = now - date;
        const diffMins = Math.floor(diffMs / 60000);

        if (diffMins < 1) return 'Just now';
        if (diffMins < 60) return `${diffMins}m ago`;
        if (diffMins < 1440) return `${Math.floor(diffMins / 60)}h ago`;
        return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    } catch {
        return isoString;
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function shakeElement(el) {
    el.style.animation = 'none';
    el.offsetHeight; // Trigger reflow
    el.style.animation = 'shake 0.4s ease';
    el.style.borderColor = 'var(--accent-danger)';
    setTimeout(() => {
        el.style.borderColor = '';
        el.style.animation = '';
    }, 1000);
}

function formatScreenshotLabel(filename, fallbackIdx) {
    // Convert: step_1_page_loaded.png → "Page Loaded"
    // Convert: failure_1709511234.png → "Failure"
    const base = filename.replace(/\.png$/i, '');
    if (base.startsWith('step_')) {
        const parts = base.replace(/^step_\d+_?/, '').replace(/_/g, ' ').trim();
        return parts.length > 0
            ? parts.charAt(0).toUpperCase() + parts.slice(1)
            : `Step ${fallbackIdx}`;
    }
    if (base.startsWith('failure')) {
        return 'Error Screenshot';
    }
    return base.replace(/_/g, ' ');
}
