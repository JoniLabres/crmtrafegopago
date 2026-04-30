// ── Toast ─────────────────────────────────────────────────────────────────────
function showToast(message, type = 'ok') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast ${type === 'error' ? 'error' : type === 'warn' ? 'warn' : ''}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
}

// ── Save .env ─────────────────────────────────────────────────────────────────
async function saveEnv(event, channel) {
  event.preventDefault();
  const form = event.target;
  const data = {};
  new FormData(form).forEach((v, k) => { if (v) data[k] = v; });
  try {
    const r = await fetch('/api/env/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    const d = await r.json();
    showToast(d.message || `${channel} salvo!`);
  } catch (e) {
    showToast('Erro ao salvar', 'error');
  }
}

// ── Auto-select source/medium ────────────────────────────────────────────────
document.addEventListener('change', function(e) {
  const src = e.target.id === 'u-source' ? e.target : document.getElementById('u-source');
  const med = document.getElementById('u-medium');
  if (!src || !med || e.target.id !== 'u-source') return;
  const map = { meta:'paid_social', google:'paid_search', linkedin:'paid_social', tiktok:'video', programatica:'display' };
  if (map[src.value]) med.value = map[src.value];
});
