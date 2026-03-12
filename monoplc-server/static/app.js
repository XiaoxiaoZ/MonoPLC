// API Base URL
const API_BASE = 'http://127.0.0.1:8000/api';

// Elements
const elServerDot = document.getElementById('server-status-dot');
const elServerText = document.getElementById('server-status-text');
const elEffectsCount = document.getElementById('val-effects-count');
const logContainer = document.getElementById('log-container');
const chatWindow = document.getElementById('chat-window');
const chatInput = document.getElementById('chat-input');
const btnSendChat = document.getElementById('btn-send-chat');
const elInterpretSummary = document.getElementById('llm-interpret-summary');
const elInterpretDetails = document.getElementById('llm-interpret-details');
const statusCardsContainer = document.getElementById('status-cards-container');

// Poll interval
const POLL_INTERVAL = 1000;

// State
let lastLogCount = 0;

// Status color map
const STATUS_COLORS = {
    normal: { bg: 'rgba(16, 185, 129, 0.15)', border: '#10b981', text: '#10b981' },
    active: { bg: 'rgba(59, 130, 246, 0.15)', border: '#3b82f6', text: '#3b82f6' },
    inactive: { bg: 'rgba(148, 163, 184, 0.15)', border: '#94a3b8', text: '#94a3b8' },
    warning: { bg: 'rgba(245, 158, 11, 0.15)', border: '#f59e0b', text: '#f59e0b' },
    critical: { bg: 'rgba(239, 68, 68, 0.15)', border: '#ef4444', text: '#ef4444' },
};

// ----------------------------------------------------
// UI Updaters
// ----------------------------------------------------

function updateConnectionState(isOnline) {
    if (isOnline) {
        elServerDot.className = 'status-dot online';
        elServerText.textContent = 'Server Connected / PLC Active';
    } else {
        elServerDot.className = 'status-dot';
        elServerText.textContent = 'Server Disconnected or PLC Down';
    }
}

function renderStatusCards(cards) {
    if (!cards || cards.length === 0) return;

    statusCardsContainer.innerHTML = '';

    const grid = document.createElement('div');
    grid.style.cssText = 'display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 12px;';

    cards.forEach(card => {
        const colors = STATUS_COLORS[card.status] || STATUS_COLORS.normal;
        const el = document.createElement('div');
        el.style.cssText = `
            background: ${colors.bg};
            border: 1px solid ${colors.border};
            border-radius: 12px;
            padding: 16px;
            text-align: center;
            transition: transform 0.2s ease;
        `;
        el.innerHTML = `
            <div style="font-size: 0.75em; text-transform: uppercase; letter-spacing: 0.05em; opacity: 0.7; margin-bottom: 8px;">${card.label}</div>
            <div style="font-size: 1.4em; font-weight: 700; color: ${colors.text};">${card.value}</div>
        `;
        el.addEventListener('mouseenter', () => el.style.transform = 'scale(1.05)');
        el.addEventListener('mouseleave', () => el.style.transform = 'scale(1)');
        grid.appendChild(el);
    });

    statusCardsContainer.appendChild(grid);
}

function renderLogs(logs) {
    if (logs.length === 0) return;

    if (logs.length === lastLogCount) {
        // Naive check, assuming we only append
    }
    lastLogCount = logs.length;

    logContainer.innerHTML = '';

    [...logs].reverse().forEach(log => {
        const div = document.createElement('div');
        div.className = 'log-item';

        const time = new Date(log.timestamp).toLocaleTimeString();
        let desc = log.payload || (log.target ? `Target: ${log.target} Val: ${log.value}` : 'No Payload');

        div.innerHTML = `
            <div>
                <span class="log-type">[${log.e_type}]</span>
                <span class="log-content">${desc}</span>
            </div>
            <span class="log-time">${time}</span>
        `;
        logContainer.appendChild(div);
    });
}

// ----------------------------------------------------
// REST Polling
// ----------------------------------------------------

async function fetchStatus() {
    try {
        const res = await fetch(`${API_BASE}/status`);
        if (res.ok) {
            const data = await res.json();
            // Only update effects count from raw status
            if (data.total_effects_consumed !== undefined) {
                elEffectsCount.textContent = data.total_effects_consumed;
            }
            updateConnectionState(true);
        } else {
            updateConnectionState(false);
        }
    } catch (e) {
        updateConnectionState(false);
    }
}

async function fetchLogs() {
    try {
        const res = await fetch(`${API_BASE}/logs?n=20`);
        if (res.ok) {
            const data = await res.json();
            renderLogs(data.logs);
        }
    } catch (e) { }
}

async function fetchInterpret() {
    try {
        const res = await fetch(`${API_BASE}/llm/interpret`);
        if (res.ok) {
            const data = await res.json();
            elInterpretSummary.textContent = data.summary;
            elInterpretDetails.textContent = data.details;

            // Render LLM-decided status cards
            if (data.status_cards && data.status_cards.length > 0) {
                renderStatusCards(data.status_cards);
            }
        } else {
            elInterpretSummary.textContent = "AI Analysis Unavailable";
            elInterpretDetails.textContent = "--";
        }
    } catch (e) {
        elInterpretSummary.textContent = "Server Disconnected";
        elInterpretDetails.textContent = "--";
    }
}

setInterval(fetchStatus, POLL_INTERVAL);
setInterval(fetchLogs, POLL_INTERVAL);

fetchStatus();
fetchLogs();

// ----------------------------------------------------
// UI Actions
// ----------------------------------------------------

document.getElementById('btn-start').addEventListener('click', () => {
    fetch(`${API_BASE}/buttons/start`, { method: 'POST' });
});

document.getElementById('btn-stop').addEventListener('click', () => {
    fetch(`${API_BASE}/buttons/stop`, { method: 'POST' });
});

document.getElementById('btn-reset').addEventListener('click', () => {
    fetch(`${API_BASE}/buttons/reset`, { method: 'POST' });
});

document.getElementById('btn-analyze').addEventListener('click', async () => {
    const btn = document.getElementById('btn-analyze');
    const icon = btn.querySelector('i');
    btn.disabled = true;
    icon.classList.add('fa-spin');
    await fetchInterpret();
    icon.classList.remove('fa-spin');
    btn.disabled = false;
});

btnSendChat.addEventListener('click', async () => {
    const text = chatInput.value.trim();
    if (!text) return;

    // Append user message
    const p = document.createElement('div');
    p.className = 'message user-msg';
    p.innerHTML = `<div class="msg-bubble">${text}</div>`;
    chatWindow.appendChild(p);
    chatInput.value = '';
    chatWindow.scrollTop = chatWindow.scrollHeight;

    try {
        const res = await fetch(`${API_BASE}/llm/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_input: text })
        });
        const data = await res.json();

        // Append LLM response
        const respP = document.createElement('div');
        respP.className = 'message system-msg';
        respP.innerHTML = `<div class="msg-bubble">${data.effect.e_type !== 0 ? '🛠️ Action executing: ' : ''}${data.reasoning}</div>`;
        chatWindow.appendChild(respP);
        chatWindow.scrollTop = chatWindow.scrollHeight;

        // Also fetch LLM interpretation after each chat
        fetchInterpret();

    } catch (e) {
        console.error("Chat error", e);
    }
});

chatInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') btnSendChat.click();
});
