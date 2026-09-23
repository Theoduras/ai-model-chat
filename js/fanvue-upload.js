// "Upload to Fanvue" for any page that can select library items (the studio's
// kept grid, the Content Vault). One dialog, so the two cannot drift apart.
//
//   fvUploadOpen({ persona, ids, onItem(id, uuid), onDone() })
(function () {
  const CSS = `
    .fvu-back { position:fixed; inset:0; z-index:4000; background:#000000b0;
      display:flex; align-items:center; justify-content:center; padding:16px; }
    .fvu { width:min(440px, 100%); max-height:90vh; overflow:auto; display:flex; flex-direction:column;
      gap:10px; padding:16px; border-radius:12px; border:1px solid var(--border, #333);
      background:var(--surface, #1b1b1b); color:var(--text, #eee); font-size:0.82rem; }
    .fvu h3 { margin:0; font-size:0.95rem; }
    .fvu .fvu-hint { color:var(--text-muted, #999); font-size:0.74rem; }
    .fvu .fvu-err { color:var(--err, #e5484d); font-size:0.74rem; }
    .fvu .fvu-err a { color:inherit; }
    .fvu .fvu-chips { display:flex; flex-wrap:wrap; gap:6px; }
    .fvu .fvu-chip { border:1px solid var(--border, #333); background:transparent; color:inherit;
      border-radius:999px; padding:4px 10px; font-size:0.74rem; cursor:pointer; }
    .fvu .fvu-chip[aria-pressed="true"] { border-color:var(--accent, #ff6b4a);
      background:color-mix(in srgb, var(--accent, #ff6b4a) 22%, transparent); }
    .fvu label { display:flex; flex-direction:column; gap:4px; font-size:0.74rem; color:var(--text-muted, #999); }
    .fvu input { padding:7px 9px; border-radius:8px; border:1px solid var(--border, #333);
      background:var(--bg, #111); color:var(--text, #eee); font-size:0.82rem; }
    .fvu .fvu-go { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
    .fvu .fvu-btn { border:none; border-radius:8px; padding:7px 14px; cursor:pointer;
      font-weight:600; font-size:0.8rem; background:var(--accent, #ff6b4a); color:#fff; }
    .fvu .fvu-btn:disabled { opacity:.5; cursor:not-allowed; }
    .fvu .fvu-btn.ghost { background:transparent; color:inherit; border:1px solid var(--border, #333); }
  `;

  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // Folders per persona, kept across openings: a Fanvue call spends the same
  // per-minute allowance the chat bot runs on.
  const folderCache = {};
  const picked = {};

  function injectCss() {
    if (document.getElementById('fvu-css')) return;
    const s = document.createElement('style');
    s.id = 'fvu-css';
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  async function loadFolders(persona) {
    try {
      const d = await (await fetch('/api/fanvue/folders?persona=' + encodeURIComponent(persona))).json();
      return { folders: d.folders || [], error: d.error || '' };
    } catch (e) {
      return { folders: [], error: 'Could not load Fanvue folders.' };
    }
  }

  window.fvUploadOpen = async function ({ persona, ids, onItem, onDone }) {
    if (!persona || !ids || !ids.length) return;
    injectCss();
    const pick = picked[persona] = picked[persona] || new Set();
    const back = document.createElement('div');
    back.className = 'fvu-back';
    back.innerHTML = `
      <div class="fvu" role="dialog" aria-label="Upload to Fanvue">
        <h3>Upload ${ids.length} item${ids.length === 1 ? '' : 's'} to the Fanvue vault</h3>
        <span class="fvu-hint">Folders to file them in (optional):</span>
        <div class="fvu-chips" data-r="chips"><span class="fvu-hint">Loading Fanvue folders…</span></div>
        <span class="fvu-err" data-r="ferr"></span>
        <label>New folder<input type="text" data-r="new" maxlength="60"
          placeholder="Name — separate several with commas"></label>
        <label>Name on Fanvue<input type="text" data-r="name" maxlength="80"
          placeholder="Optional — numbered when several are selected"></label>
        <div class="fvu-go">
          <button type="button" class="fvu-btn" data-r="go">Upload</button>
          <button type="button" class="fvu-btn ghost" data-r="close">Close</button>
        </div>
        <span class="fvu-hint" data-r="out"></span>
      </div>`;
    document.body.appendChild(back);
    const $ = r => back.querySelector(`[data-r="${r}"]`);
    let busy = false;
    const close = () => { if (!busy) back.remove(); };
    back.addEventListener('click', e => { if (e.target === back) close(); });
    $('close').onclick = close;

    let folders = [];
    const drawChips = () => {
      $('chips').innerHTML = folders.length ? folders.map(f => `
        <button type="button" class="fvu-chip" aria-pressed="${pick.has(f.name)}" data-name="${esc(f.name)}">${
          esc(f.name)}${f.count != null ? ` <span style="opacity:.6">${esc(f.count)}</span>` : ''}</button>`).join('')
        : '<span class="fvu-hint">No folders yet — name one below, or upload without one.</span>';
    };
    $('chips').addEventListener('click', e => {
      const b = e.target.closest('.fvu-chip');
      if (!b) return;
      const n = b.dataset.name;
      if (pick.has(n)) pick.delete(n); else pick.add(n);
      drawChips();
    });
    const refresh = async () => {
      const got = folderCache[persona] || (folderCache[persona] = await loadFolders(persona));
      folders = got.folders;
      [...pick].forEach(n => { if (!folders.some(f => f.name === n)) pick.delete(n); });
      $('ferr').innerHTML = got.error
        ? (/connected/i.test(got.error)
            ? 'No Fanvue account is connected for this persona — <a href="/fanvue">connect it</a> first.'
            : esc(got.error))
        : '';
      drawChips();
    };
    await refresh();

    $('go').onclick = async () => {
      const typed = $('new').value.split(',').map(x => x.trim()).filter(Boolean);
      const known = new Set(folders.map(f => f.name));
      const chosen = [...pick, ...typed.filter(x => !pick.has(x))];
      const create = typed.filter(x => !known.has(x));
      const name = $('name').value.trim();
      const out = $('out');
      busy = true;
      $('go').disabled = $('close').disabled = true;
      // One item per request: each upload waits on Fanvue, and a batch in
      // one request would outlive a request timeout.
      const done = [], reused = [], failed = [], folderErrs = new Set();
      for (let i = 0; i < ids.length; i++) {
        out.textContent = `Uploading ${i + 1} / ${ids.length}…`;
        try {
          const res = await fetch('/api/fanvue/vault-upload', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ persona, media_id: ids[i], folders: chosen, create,
                                   name: name ? (ids.length > 1 ? `${name} ${i + 1}` : name) : '' }),
          });
          let d = {};
          try { d = await res.json(); } catch (e) { d = { error: 'The server returned ' + res.status }; }
          if (!res.ok || !d.ok) throw new Error(d.error || 'upload failed');
          (d.reused ? reused : done).push(ids[i]);
          Object.entries(d.folder_errors || {}).forEach(([f, why]) => folderErrs.add(`${f}: ${why}`));
          create.length = 0;
          if (onItem) onItem(ids[i], d.uuid);
        } catch (e) {
          failed.push(e.message || String(e));
        }
      }
      busy = false;
      $('go').disabled = $('close').disabled = false;
      if (typed.length) {
        $('new').value = '';
        typed.forEach(x => pick.add(x));
        delete folderCache[persona];
        await refresh();
      }
      out.innerHTML = [
        done.length ? `${done.length} uploaded` : '',
        reused.length ? `${reused.length} already on Fanvue${chosen.length ? ' (filed into the folders)' : ''}` : '',
        failed.length ? `<span class="fvu-err">${failed.length} failed: ${esc([...new Set(failed)].join('; '))}</span>` : '',
        folderErrs.size ? `<span class="fvu-err">Folder problems: ${esc([...folderErrs].join('; '))}</span>` : '',
      ].filter(Boolean).join(' · ');
      if (onDone) onDone();
    };
  };
})();
