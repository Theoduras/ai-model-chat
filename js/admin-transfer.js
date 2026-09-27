// Admin-only: move or copy a persona or character onto another account.
(function () {
  let accounts = null;
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c])); }
  window.adminTransfer = async function (kind, id, name, onDone) {
    if (!accounts) {
      try { accounts = ((await (await fetch('/api/admin/workspaces')).json()).workspaces) || []; }
      catch (e) { alert('Could not load accounts.'); return; }
    }
    const wrap = document.createElement('div');
    wrap.style.cssText = 'position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,.6);display:flex;align-items:center;justify-content:center;padding:16px;';
    wrap.innerHTML = `<div style="background:var(--surface,#16161a);color:var(--text,#eee);border:1px solid var(--border,#333);border-radius:12px;padding:20px;width:100%;max-width:420px;">
      <h3 style="margin:0 0 12px;font-size:1rem;">Put ${esc(name)} on another account</h3>
      <select id="at-ws" style="width:100%;padding:8px;margin-bottom:12px;">${accounts.map(a => `<option value="${esc(a.id)}">${esc(a.label)}</option>`).join('')}</select>
      <label style="display:block;margin-bottom:6px;"><input type="radio" name="at-mode" value="copy" checked> Copy &mdash; both accounts keep one${kind === 'persona' ? ', vault included' : ''}</label>
      <label style="display:block;margin-bottom:14px;"><input type="radio" name="at-mode" value="move"> Move &mdash; the current owner loses it</label>
      <div id="at-msg" style="font-size:.8rem;color:var(--err,#f66);min-height:1em;margin-bottom:8px;"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;">
        <button type="button" class="btn btn-ghost" id="at-cancel">Cancel</button>
        <button type="button" class="btn btn-primary" id="at-go">Confirm</button></div></div>`;
    document.body.appendChild(wrap);
    const close = () => wrap.remove();
    wrap.querySelector('#at-cancel').onclick = close;
    wrap.onclick = e => { if (e.target === wrap) close(); };
    wrap.querySelector('#at-go').onclick = async function () {
      this.disabled = true;
      const mode = wrap.querySelector('input[name="at-mode"]:checked').value;
      try {
        const r = await fetch('/api/admin/transfer', {method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({kind, id, mode, workspace: wrap.querySelector('#at-ws').value})});
        const d = await r.json();
        if (!r.ok || !d.ok) throw new Error(d.error || 'Transfer failed');
        close();
        if (onDone) onDone(d);
      } catch (e) { wrap.querySelector('#at-msg').textContent = e.message; this.disabled = false; }
    };
  };
})();
