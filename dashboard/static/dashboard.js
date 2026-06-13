Chart.defaults.color = '#8b949e';
Chart.defaults.borderColor = '#30363d';
Chart.defaults.font.family = "'Segoe UI', system-ui, sans-serif";

let timelineChart, severityChart, modeChart;
let allReviews = [];
let sortCol = 'id', sortDir = 'desc';

const SORT_COLS = {
  id: r => r.id,
  pr: r => `${r.repo}/${String(r.pr_id).padStart(8, '0')}`,
  timestamp: r => r.timestamp,
  mode: r => r.mode,
  status: r => r.status,
  findings_count: r => r.findings_count,
};

async function loadStats() {
  let data;
  try {
    data = await fetch('/api/stats').then(r => r.json());
  } catch (err) {
    console.error('Failed to load stats:', err);
    return;
  }

  const total = (data.mode_split.shadow || 0) + (data.mode_split.live || 0);
  document.getElementById('stat-total').textContent    = total;
  document.getElementById('stat-errors').textContent   = data.severity.error   || 0;
  document.getElementById('stat-warnings').textContent = data.severity.warning || 0;
  document.getElementById('stat-shadow').textContent   = data.mode_split.shadow || 0;
  document.getElementById('stat-live').textContent     = data.mode_split.live   || 0;
  document.getElementById('last-updated').textContent  = 'Updated ' + new Date().toLocaleTimeString();

  const tCtx = document.getElementById('timelineChart').getContext('2d');
  if (timelineChart) timelineChart.destroy();
  timelineChart = new Chart(tCtx, {
    type: 'line',
    data: {
      labels: data.timeline.labels,
      datasets: [
        { label: 'Live',   data: data.timeline.live,   borderColor: '#3fb950', backgroundColor: 'rgba(63,185,80,0.1)',   fill: true, tension: 0.3, pointRadius: 3 },
        { label: 'Shadow', data: data.timeline.shadow, borderColor: '#388bfd', backgroundColor: 'rgba(56,139,253,0.1)', fill: true, tension: 0.3, pointRadius: 3 },
      ],
    },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } }, scales: { x: { ticks: { maxTicksLimit: 8, maxRotation: 0 } }, y: { beginAtZero: true, ticks: { stepSize: 1 } } } },
  });

  const sCtx = document.getElementById('severityChart').getContext('2d');
  if (severityChart) severityChart.destroy();
  severityChart = new Chart(sCtx, {
    type: 'doughnut',
    data: { labels: ['Error', 'Warning', 'Info'], datasets: [{ data: [data.severity.error || 0, data.severity.warning || 0, data.severity.info || 0], backgroundColor: ['#f85149', '#d29922', '#388bfd'], borderColor: '#161b22', borderWidth: 3 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } }, cutout: '65%' },
  });

  const mCtx = document.getElementById('modeChart').getContext('2d');
  if (modeChart) modeChart.destroy();
  modeChart = new Chart(mCtx, {
    type: 'doughnut',
    data: { labels: ['Live', 'Shadow'], datasets: [{ data: [data.mode_split.live || 0, data.mode_split.shadow || 0], backgroundColor: ['#3fb950', '#388bfd'], borderColor: '#161b22', borderWidth: 3 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } }, cutout: '65%' },
  });

  allReviews = data.recent || [];
  applyFilters();
}

function applyFilters() {
  const repo   = document.getElementById('f-repo').value.trim().toLowerCase();
  const mode   = document.getElementById('f-mode').value;
  const status = document.getElementById('f-status').value;
  const from   = document.getElementById('f-from').value;
  const to     = document.getElementById('f-to').value;

  const filtered = allReviews.filter(r => {
    if (repo   && !r.repo.toLowerCase().includes(repo)) return false;
    if (mode   && r.mode   !== mode)   return false;
    if (status && r.status !== status) return false;
    if (from && r.timestamp.slice(0, 10) < from) return false;
    if (to   && r.timestamp.slice(0, 10) > to)   return false;
    return true;
  });
  renderTable(filtered, allReviews.length);
}

function setSort(col) {
  if (sortCol === col) {
    sortDir = sortDir === 'asc' ? 'desc' : 'asc';
  } else {
    sortCol = col;
    sortDir = (col === 'id' || col === 'timestamp') ? 'desc' : 'asc';
  }
  applyFilters();
}

function renderTable(reviews, total) {
  const wrap  = document.getElementById('recent-wrap');
  const count = document.getElementById('filter-count');

  count.textContent = total === 0 ? '' : reviews.length === total
    ? `${total} review${total !== 1 ? 's' : ''}`
    : `${reviews.length} of ${total}`;

  if (total === 0) { wrap.innerHTML = '<div class="empty">No reviews yet — webhook events will appear here.</div>'; return; }
  if (reviews.length === 0) { wrap.innerHTML = '<div class="empty">No reviews match the current filters.</div>'; return; }

  const extract = SORT_COLS[sortCol] ?? (r => r.id);
  const sorted = [...reviews].sort((a, b) => {
    let cmp = extract(a) < extract(b) ? -1 : extract(a) > extract(b) ? 1 : 0;
    return sortDir === 'desc' ? -cmp : cmp;
  });

  function th(label, col) {
    const cls = ['sortable', sortCol === col ? `sort-${sortDir}` : ''].filter(Boolean).join(' ');
    return `<th class="${cls}" onclick="setSort('${col}')">${label}</th>`;
  }

  const rows = sorted.map(r => `
    <tr onclick="openDetail(${r.id})">
      <td>${r.id}</td>
      <td><a href="https://github.com/${r.repo}/pull/${r.pr_id}" target="_blank" rel="noopener" style="color:#58a6ff" onclick="event.stopPropagation()">${r.repo} #${r.pr_id}</a></td>
      <td>${new Date(r.timestamp).toLocaleString()}</td>
      <td><span class="pill ${r.mode}">${r.mode}</span></td>
      <td><span class="pill ${r.status}">${r.status}</span></td>
      <td>${r.findings_count}</td>
    </tr>`).join('');

  wrap.innerHTML = `<table><thead><tr>${th('#','id')}${th('PR','pr')}${th('Reviewed At','timestamp')}${th('Mode','mode')}${th('Status','status')}${th('Findings','findings_count')}</tr></thead><tbody>${rows}</tbody></table>`;
}

['f-repo'].forEach(id => document.getElementById(id).addEventListener('input', applyFilters));
['f-mode', 'f-status', 'f-from', 'f-to'].forEach(id => document.getElementById(id).addEventListener('change', applyFilters));
document.getElementById('filter-clear').addEventListener('click', () => {
  ['f-repo','f-mode','f-status','f-from','f-to'].forEach(id => document.getElementById(id).value = '');
  applyFilters();
});

const SEV_CLASS = { error: 'sev-error', warning: 'sev-warning', info: 'sev-info' };
const SEV_ICON  = { error: '🔴', warning: '🟡', info: '🔵' };
let _currentReviewId = null;

function closeDetail() {
  document.getElementById('detail-panel').classList.remove('open');
  document.getElementById('detail-overlay').style.display = 'none';
  document.body.style.overflow = '';
}

async function openDetail(id) {
  _currentReviewId = id;
  const panel = document.getElementById('detail-panel');
  const overlay = document.getElementById('detail-overlay');
  document.getElementById('detail-title').textContent = `Review #${id}`;
  document.getElementById('detail-meta').innerHTML = '';
  document.getElementById('detail-body').innerHTML = '<div id="detail-loading">Loading…</div>';
  overlay.style.display = 'block';
  panel.classList.add('open');
  document.body.style.overflow = 'hidden';

  let d;
  try {
    const res = await fetch(`/api/reviews/${id}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    d = await res.json();
  } catch (err) {
    document.getElementById('detail-body').innerHTML = `<div id="detail-empty">Failed to load: ${err.message}</div>`;
    return;
  }

  const sha = d.head_sha ? d.head_sha.slice(0, 7) : '—';
  document.getElementById('detail-meta').innerHTML = `
    <span>📁 <a href="https://github.com/${d.repo}/pull/${d.pr_id}" target="_blank" rel="noopener" style="color:#58a6ff">${d.repo} #${d.pr_id}</a></span>
    <span>🔖 <code style="font-size:0.75rem;color:#e6edf3">${sha}</code></span>
    <span>🕒 ${new Date(d.timestamp).toLocaleString()}</span>
    <span><span class="pill ${d.mode}">${d.mode}</span></span>
    <span><span class="pill ${d.status}">${d.status}</span></span>
    <span>🔍 ${d.findings_count} finding${d.findings_count !== 1 ? 's' : ''}</span>`;

  if (!d.findings || d.findings.length === 0) {
    document.getElementById('detail-body').innerHTML = '<div id="detail-empty">No findings recorded.</div>';
    return;
  }

  const ORDER = { error: 0, warning: 1, info: 2 };
  const sorted = [...d.findings].sort((a, b) => (ORDER[a.severity] ?? 3) - (ORDER[b.severity] ?? 3));

  document.getElementById('detail-body').innerHTML = sorted.map(f => `
    <div class="finding-card">
      <div class="finding-top">
        <span class="${SEV_CLASS[f.severity] || ''}" style="font-size:0.9rem">${SEV_ICON[f.severity] || '⚪'}</span>
        <span class="finding-file">${f.file}</span>
        <span class="finding-line">line ${f.line}</span>
      </div>
      <div class="finding-suggestion">${f.suggestion.replace(/</g,'&lt;').replace(/>/g,'&gt;')}</div>
      <div class="finding-source">source: ${f.source}</div>
    </div>`).join('');
}

const rerunBtn = document.getElementById('detail-rerun');
rerunBtn.addEventListener('click', async () => {
  if (_currentReviewId === null) return;
  rerunBtn.disabled = true; rerunBtn.className = 'running'; rerunBtn.textContent = '⏳ Running…';
  try {
    const res = await fetch(`/api/reviews/${_currentReviewId}/rerun`, { method: 'POST' });
    if (!res.ok) { const b = await res.json().catch(() => ({})); throw new Error(b.error || `HTTP ${res.status}`); }
    rerunBtn.className = 'success'; rerunBtn.textContent = '✓ Queued!';
    loadStats();
    setTimeout(() => { rerunBtn.disabled = false; rerunBtn.className = ''; rerunBtn.textContent = '↺ Re-run'; openDetail(_currentReviewId); }, 3000);
  } catch (err) {
    rerunBtn.className = 'error'; rerunBtn.textContent = `✗ ${err.message}`;
    setTimeout(() => { rerunBtn.disabled = false; rerunBtn.className = ''; rerunBtn.textContent = '↺ Re-run'; }, 4000);
  }
});

document.getElementById('detail-close').addEventListener('click', closeDetail);
document.getElementById('detail-overlay').addEventListener('click', closeDetail);

function openPayload(eventId, label) {
  const overlay = document.getElementById('payload-overlay');
  const modal   = document.getElementById('payload-modal');
  document.getElementById('payload-modal-title').textContent = label;
  document.getElementById('payload-code').textContent = 'Loading…';
  document.getElementById('payload-copy').className = '';
  document.getElementById('payload-copy').textContent = 'Copy';
  overlay.style.display = 'block';
  document.body.style.overflow = 'hidden';
  requestAnimationFrame(() => modal.classList.add('open'));
  fetch(`/api/events/${eventId}/payload`)
    .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
    .then(d => { document.getElementById('payload-code').textContent = JSON.stringify(d.payload, null, 2); })
    .catch(err => { document.getElementById('payload-code').textContent = `Error: ${err.message}`; });
}

function closePayload() {
  document.getElementById('payload-modal').classList.remove('open');
  setTimeout(() => { document.getElementById('payload-overlay').style.display = 'none'; document.body.style.overflow = ''; }, 200);
}

document.getElementById('payload-copy').addEventListener('click', () => {
  const text = document.getElementById('payload-code').textContent;
  if (!text || text === 'Loading…') return;
  navigator.clipboard.writeText(text).then(() => {
    const btn = document.getElementById('payload-copy');
    btn.className = 'copied'; btn.textContent = '✓ Copied!';
    setTimeout(() => { btn.className = ''; btn.textContent = 'Copy'; }, 2000);
  });
});
document.getElementById('payload-modal-close').addEventListener('click', closePayload);
document.getElementById('payload-overlay').addEventListener('click', closePayload);

const OUTCOME_ICON = { queued: '🟢', ignored: '⚫', rejected: '🔴', error: '🔴' };

async function loadEvents() {
  let events;
  try {
    const res = await fetch('/api/events');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    events = await res.json();
  } catch (err) {
    document.getElementById('event-wrap').innerHTML = `<div class="empty">Failed to load events: ${err.message}</div>`;
    return;
  }

  const wrap  = document.getElementById('event-wrap');
  const count = document.getElementById('event-count');
  count.textContent = events.length ? `${events.length} event${events.length !== 1 ? 's' : ''}` : '';

  if (events.length === 0) { wrap.innerHTML = '<div class="empty">No webhook events yet.</div>'; return; }

  const rows = events.map(ev => {
    const prCell = ev.pr_number
      ? `<a class="event-link" href="https://github.com/${ev.repo}/pull/${ev.pr_number}" target="_blank" rel="noopener">${ev.repo} #${ev.pr_number}</a>`
      : `<span style="color:#8b949e">${ev.repo || '—'}</span>`;
    const reviewCell = ev.review_log_id
      ? `<a class="event-link" href="#" onclick="event.preventDefault();openDetail(${ev.review_log_id})">#${ev.review_log_id}</a>`
      : '<span style="color:#8b949e">—</span>';
    const label = `Event #${ev.id} · ${ev.event_type}${ev.action ? ' / ' + ev.action : ''}`;
    return `<tr>
      <td style="color:#8b949e;font-size:0.78rem">${new Date(ev.received_at).toLocaleString()}</td>
      <td><code style="font-size:0.78rem;color:#c9d1d9">${ev.event_type}</code></td>
      <td><code style="font-size:0.78rem;color:#8b949e">${ev.action || '—'}</code></td>
      <td>${prCell}</td>
      <td style="color:#8b949e">${ev.sender || '—'}</td>
      <td>${OUTCOME_ICON[ev.outcome] || '⚪'} <span class="pill ${ev.outcome}">${ev.outcome}</span></td>
      <td>${reviewCell}</td>
      <td><button class="btn-payload" onclick="openPayload(${ev.id}, ${JSON.stringify(label)})">{ } Payload</button></td>
    </tr>`;
  }).join('');

  wrap.innerHTML = `<table><thead><tr><th>Received At</th><th>Event</th><th>Action</th><th>PR</th><th>Sender</th><th>Outcome</th><th>Review</th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
}

const wz = { repo: '', publicUrl: '', secret: '' };

function openWizard() {
  document.getElementById('wizard-overlay').style.display = 'block';
  document.body.style.overflow = 'hidden';
  requestAnimationFrame(() => document.getElementById('wizard-modal').classList.add('open'));
  fetch('/api/setup/info').then(r => r.json()).then(info => {
    const el = document.getElementById('wz-secret-status');
    el.innerHTML = info.webhook_secret_set
      ? '<span class="wz-status ok" style="margin-top:0.4rem;display:inline-block">✓ WEBHOOK_SECRET is already set</span>'
      : '<span class="wz-status warn" style="margin-top:0.4rem;display:inline-block">⚠ WEBHOOK_SECRET not configured</span>';
  }).catch(() => {});
}

function closeWizard() {
  document.getElementById('wizard-modal').classList.remove('open');
  setTimeout(() => { document.getElementById('wizard-overlay').style.display = 'none'; document.body.style.overflow = ''; }, 220);
}

function wizardRepoChanged() {
  wz.repo = (document.getElementById('wz-repo').value || '').trim();
  _wizardUpdateDynamic();
}

function wizardUrlChanged() {
  wz.publicUrl = (document.getElementById('wz-public-url').value || '').trim().replace(/\/$/, '');
  _wizardUpdateDynamic();
}

function _wizardUpdateDynamic() {
  document.getElementById('wz-payload-url').textContent = wz.publicUrl ? wz.publicUrl + '/webhook' : '<enter public URL in Step 2>';
  const ghLink = document.getElementById('wz-gh-link');
  if (wz.repo && wz.repo.includes('/')) {
    ghLink.href = `https://github.com/${wz.repo}/settings/hooks/new`;
    ghLink.style.opacity = '1';
  } else {
    ghLink.href = '#'; ghLink.style.opacity = '0.45';
  }
  const testEl = document.getElementById('wz-test-cmd');
  if (wz.repo && wz.repo.includes('/')) {
    const [owner, repo] = wz.repo.split('/');
    testEl.textContent = `python test_demo.py --owner ${owner} --repo ${repo}`;
  } else {
    testEl.textContent = 'python test_demo.py  # enter repo in Step 1 first';
  }
  const secRef = document.getElementById('wz-secret-ref');
  secRef.textContent = wz.secret || '(paste the value from Step 3)';
  if (wz.secret) { secRef.style.fontFamily = 'monospace'; secRef.style.fontSize = '0.75rem'; secRef.style.color = '#c9d1d9'; }
  else { secRef.style.fontFamily = ''; secRef.style.color = '#8b949e'; }
}

function wzGuardGhLink(e) {
  if (!wz.repo || !wz.repo.includes('/')) {
    e.preventDefault();
    const inp = document.getElementById('wz-repo');
    inp.focus(); inp.style.borderColor = '#f85149';
    setTimeout(() => { inp.style.borderColor = ''; }, 1200);
    return false;
  }
  return true;
}

function wzGenerateSecret() {
  fetch('/api/setup/generate-secret').then(r => r.json()).then(data => {
    wz.secret = data.secret;
    document.getElementById('wz-secret').value = wz.secret;
    document.getElementById('wz-secret-copy').disabled = false;
    document.getElementById('wz-env-snippet').textContent = `WEBHOOK_SECRET=${wz.secret}`;
    document.getElementById('wz-secret-status').innerHTML =
      '<span class="wz-status warn" style="margin-top:0.4rem;display:inline-block">⚠ Add to .env and restart services</span>';
    _wizardUpdateDynamic();
  }).catch(() => alert('Could not reach /api/setup/generate-secret'));
}

function wzCopySecret() {
  if (!wz.secret) return;
  navigator.clipboard.writeText(wz.secret).then(() => {
    const btn = document.getElementById('wz-secret-copy');
    btn.textContent = '✓ Copied!'; btn.classList.add('ok');
    setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('ok'); }, 2000);
  });
}

function wzCopyEnvSnippet(btn) {
  const text = document.getElementById('wz-env-snippet').textContent;
  if (!text || text.includes('<generate')) return;
  navigator.clipboard.writeText(text).then(() => {
    btn.textContent = '✓ Done!'; btn.classList.add('ok');
    setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('ok'); }, 2000);
  });
}

function wzCopyPayloadUrl(btn) {
  const url = document.getElementById('wz-payload-url').textContent.trim();
  if (!url || url.startsWith('<')) return;
  navigator.clipboard.writeText(url).then(() => {
    btn.textContent = '✓ Copied!'; btn.classList.add('ok');
    setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('ok'); }, 2000);
  });
}

function wzCopyTestCmd(btn) {
  navigator.clipboard.writeText(document.getElementById('wz-test-cmd').textContent).then(() => {
    btn.textContent = '✓ Copied!'; btn.classList.add('ok');
    setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('ok'); }, 2000);
  });
}

function wzCopy(btn, text) {
  navigator.clipboard.writeText(text).then(() => {
    btn.textContent = '✓'; btn.classList.add('ok');
    setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('ok'); }, 2000);
  });
}

document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  if (document.getElementById('wizard-modal').classList.contains('open')) { closeWizard(); return; }
  if (document.getElementById('payload-modal').classList.contains('open')) { closePayload(); } else { closeDetail(); }
});

loadStats();
loadEvents();
setInterval(loadStats,  30_000);
setInterval(loadEvents, 30_000);
