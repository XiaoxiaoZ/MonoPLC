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

document.getElementById('btn-spray-water').addEventListener('click', () => {
    fetch(`${API_BASE}/buttons/spray`, { method: 'POST' });
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


// ----------------------------------------------------
// Algebra Demo: Incremental Computation
// ----------------------------------------------------

document.getElementById('btn-incremental').addEventListener('click', async () => {
    const btn = document.getElementById('btn-incremental');
    const icon = btn.querySelector('i');
    btn.disabled = true;
    icon.className = 'fa-solid fa-spinner fa-spin';

    try {
        const res = await fetch(`${API_BASE}/algebra/incremental/compare`);
        const data = await res.json();

        if (data.error) {
            alert(data.error);
            return;
        }

        // Show results, hide placeholder
        document.getElementById('incremental-placeholder').style.display = 'none';
        document.getElementById('incremental-result').style.display = 'block';

        // Update timing values
        document.getElementById('time-incremental').textContent = `${data.incremental_time_ms.toFixed(4)}ms`;
        document.getElementById('time-full').textContent = `${data.full_time_ms.toFixed(4)}ms`;

        // Animate timing bars (normalize to max)
        const maxTime = Math.max(data.incremental_time_ms, data.full_time_ms, 0.001);
        requestAnimationFrame(() => {
            document.getElementById('bar-incremental').style.width =
                `${Math.max((data.incremental_time_ms / maxTime) * 100, 3)}%`;
            document.getElementById('bar-full').style.width =
                `${Math.max((data.full_time_ms / maxTime) * 100, 3)}%`;
        });

        // Update badges
        const identicalBadge = document.getElementById('badge-identical');
        if (data.identical) {
            identicalBadge.innerHTML = '<i class="fa-solid fa-check-circle"></i> identical ✔';
            identicalBadge.style.color = '#10b981';
        } else {
            identicalBadge.innerHTML = '<i class="fa-solid fa-times-circle"></i> MISMATCH ✘';
            identicalBadge.style.color = '#ef4444';
        }

        document.getElementById('badge-speedup').textContent = `${data.speedup}× speedup`;
        document.getElementById('badge-effects-count').textContent = `${data.effects_total} effects`;

    } catch (e) {
        console.error('Incremental compare error:', e);
    } finally {
        icon.className = 'fa-solid fa-play';
        btn.disabled = false;
    }
});


// ----------------------------------------------------
// Algebra Demo: Parallel Fold (MapReduce)
// ----------------------------------------------------

document.getElementById('btn-parallel').addEventListener('click', async () => {
    const btn = document.getElementById('btn-parallel');
    const icon = btn.querySelector('i');
    btn.disabled = true;
    icon.className = 'fa-solid fa-spinner fa-spin';

    try {
        const res = await fetch(`${API_BASE}/algebra/fold/compare?workers=4`);
        const data = await res.json();

        if (data.error) {
            alert(data.error);
            return;
        }

        // Show results, hide placeholder
        document.getElementById('parallel-placeholder').style.display = 'none';
        document.getElementById('parallel-result').style.display = 'block';

        // Update timing values
        document.getElementById('time-parallel').textContent = `${data.parallel_time_ms.toFixed(4)}ms`;
        document.getElementById('time-seq').textContent = `${data.sequential_time_ms.toFixed(4)}ms`;

        // Animate timing bars (normalize to max)
        const maxTime = Math.max(data.parallel_time_ms, data.sequential_time_ms, 0.001);
        requestAnimationFrame(() => {
            document.getElementById('bar-parallel').style.width =
                `${Math.max((data.parallel_time_ms / maxTime) * 100, 3)}%`;
            document.getElementById('bar-seq').style.width =
                `${Math.max((data.sequential_time_ms / maxTime) * 100, 3)}%`;
        });

        // Update badges
        const identicalBadge = document.getElementById('badge-parallel-identical');
        if (data.identical) {
            identicalBadge.innerHTML = '<i class="fa-solid fa-check-circle"></i> identical ✔';
            identicalBadge.style.color = '#10b981';
        } else {
            identicalBadge.innerHTML = '<i class="fa-solid fa-times-circle"></i> MISMATCH ✘';
            identicalBadge.style.color = '#ef4444';
        }

        document.getElementById('badge-parallel-speedup').textContent = `${data.speedup_factor}× speedup`;
        document.getElementById('badge-parallel-effects-count').textContent = `${data.effects_processed} effects`;

    } catch (e) {
        console.error('Parallel compare error:', e);
    } finally {
        icon.className = 'fa-solid fa-play';
        btn.disabled = false;
    }
});



// ----------------------------------------------------
// Algebra Demo: Historical State Playback (Time-Travel)
// ----------------------------------------------------

const ttControls = document.getElementById('tt-controls');
const ttSlider = document.getElementById('tt-slider');
const ttStartLabel = document.getElementById('tt-start-label');
const ttEndLabel = document.getElementById('tt-end-label');
const ttCurrentLabel = document.getElementById('tt-current-label');
const ttResult = document.getElementById('tt-result');
const ttMethodBadge = document.getElementById('tt-method-badge');
const ttMethodText = document.getElementById('tt-method-text');
const ttEffectsBadge = document.getElementById('tt-effects-badge');
const ttStatusText = document.getElementById('tt-status-text');

// Product Monoid UI dimensions for Time-Travel
const ttStateKeys = document.getElementById('tt-state-keys');
const ttEffectCount = document.getElementById('tt-effect-count');
const ttAlarmCount = document.getElementById('tt-alarm-count');

let logBounds = null;

// Periodically check if persistent log has data
async function checkLogBounds() {
    try {
        const res = await fetch(`${API_BASE}/algebra/log/range`);
        if (res.ok) {
            const data = await res.json();
            if (data && data.effect_count > 0) {
                logBounds = {
                    start: new Date(data.start_time).getTime(),
                    end: new Date(data.end_time).getTime(),
                    count: data.effect_count
                };
                
                // Enable slider
                ttControls.style.opacity = '1';
                ttControls.style.pointerEvents = 'auto';
                ttStatusText.textContent = `${logBounds.count} effects available for time-travel`;
                
                // Format labels
                ttStartLabel.textContent = new Date(logBounds.start).toLocaleTimeString();
                ttEndLabel.textContent = new Date(logBounds.end).toLocaleTimeString();
                
            } else {
                ttControls.style.opacity = '0.5';
                ttControls.style.pointerEvents = 'none';
                ttStatusText.textContent = "Waiting for data... (Start the PLC)";
            }
        }
    } catch (e) {
        console.error("Failed to check log bounds", e);
    }
}

// Initial check and regular polling for bounds
checkLogBounds();
setInterval(checkLogBounds, 5000);

// Debounce for the slider to avoid spamming the backend
let ttDebounceTimer;

// Helper to prevent JS from shifting naive backend times to UTC
function toLocalISOString(date) {
    const tzOffsetMs = date.getTimezoneOffset() * 60000;
    const localISOTime = (new Date(date.getTime() - tzOffsetMs)).toISOString().slice(0, -1);
    return localISOTime;
}

ttSlider.addEventListener('input', () => {
    if (!logBounds) return;
    
    // Interpolate time based on slider (0 to 1000)
    const pct = parseInt(ttSlider.value) / 1000;
    const targetTimestampMs = logBounds.start + pct * (logBounds.end - logBounds.start);
    const targetDate = new Date(targetTimestampMs);
    
    ttCurrentLabel.textContent = targetDate.toLocaleTimeString();
    
    clearTimeout(ttDebounceTimer);
    ttDebounceTimer = setTimeout(() => {
        fetchHistoricalState(toLocalISOString(targetDate));
    }, 150); // 150ms debounce
});

async function fetchHistoricalState(isoTimeStr) {
    try {
        // The API expects ISO 8601 string but we must URL-encode it
        const url = `${API_BASE}/algebra/state/at?t=${encodeURIComponent(isoTimeStr)}`;
        const res = await fetch(url);
        
        if (res.ok) {
            const data = await res.json();
            ttResult.style.display = 'block';
            
            // Render Product Monoid N-dimensional state dynamically
            ttStateKeys.textContent = Object.keys(data.state).length;
            ttEffectCount.textContent = data.effect_count;
            ttAlarmCount.textContent = data.alarm_count;
            if (document.getElementById('tt-spray-ml')) {
                document.getElementById('tt-spray-ml').textContent = (data.total_spray_ml || 0).toFixed(2);
            }
            
            // Update performance info
            ttMethodText.textContent = data.method;
            if (data.method.includes('checkpoint')) {
                ttMethodBadge.style.background = 'rgba(168, 85, 247, 0.15)';
                ttMethodBadge.style.color = '#a855f7';
                ttMethodBadge.style.borderColor = 'rgba(168, 85, 247, 0.3)';
            } else {
                ttMethodBadge.style.background = 'rgba(16, 185, 129, 0.15)';
                ttMethodBadge.style.color = '#10b981';
                ttMethodBadge.style.borderColor = 'rgba(16, 185, 129, 0.3)';
            }
            
            ttEffectsBadge.textContent = `${data.effects_folded} effects folded`;
        }
    } catch (e) {
        console.error("Time-Travel fetch failed", e);
    }
}

// ----------------------------------------------------
// Algebra Demo: PLC Climate Product Monoid Visualization
// ----------------------------------------------------
async function fetchClimateMonoid() {
    try {
        const res = await fetch(`${API_BASE}/algebra/climate`);
        if (res.ok) {
            const data = await res.json();
            const tempVal = document.getElementById('plc-temp-count');
            const humVal = document.getElementById('plc-hum-spray');
            if (tempVal && humVal) {
                tempVal.textContent = data.python_reconstructed_state.total_effects_consumed || 0;
                humVal.textContent = data.plc_native_climate.humidity_spray_amount.toFixed(2);
            }
        }
    } catch (e) {
        // silently fail on UI
    }
}
setInterval(fetchClimateMonoid, 1000);
fetchClimateMonoid();
