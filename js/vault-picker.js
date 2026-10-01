// The one way media is chosen anywhere in the app: a lightbox over the Content
// Vault with the vault's own toolbar (search, filter, sort, cards/list, folders,
// upload). A page with its own library (motion clips) passes `items` instead
// and gets the same box without folders or upload. Pages keep only the picked items on screen, never a strip of the
// whole vault, which is what made the old pickers slow.
//
//   VaultPicker.open({ persona, personas: true (adds a persona switcher), title, kind: 'image'|'video'|'', max, picked,
//                      filter, items, onDone(ids, items, persona), onUpload(ids) })
//   VaultPicker.field({ items, remove: 'fn', open: 'js', label, empty })
//   VaultPicker.thumb(m)   a tile's media; a clip loads only once it is on screen
(function () {
  const PER_PAGE = 40;
  const cache = {};
  let o = null, st = null, seen = null;

  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const $ = sel => document.querySelector('#vp-root ' + sel);

  const css = `
#vp-root { position:fixed; inset:0; z-index:4000; background:#000000b8; display:flex;
  align-items:center; justify-content:center; padding:24px; font-family:var(--font); }
#vp-root[hidden] { display:none; }
.vp-box { width:min(1180px,100%); height:min(860px,100%); display:flex; flex-direction:column;
  background:var(--panel); color:var(--text); border:1px solid var(--border-soft);
  border-radius:var(--r-lg); box-shadow:var(--shadow-md); overflow:hidden; }
.vp-top { display:flex; align-items:center; gap:10px; padding:16px 20px 0; }
.vp-top h3 { margin:0; font-size:1rem; font-weight:600; flex:1; }
.vp-x { width:32px; height:32px; border:0; border-radius:50%; background:transparent;
  color:var(--text-muted); font-size:1.3rem; cursor:pointer; }
.vp-x:hover { background:var(--surface); color:var(--text); }
.vp-bar { padding:12px 20px 0; }
.vp-bar .pg-head { margin-bottom:10px; }
.vp-folders { display:flex; gap:6px; overflow-x:auto; scrollbar-width:none; padding-bottom:10px; }
.vp-fc { flex-shrink:0; font-size:.74rem; padding:5px 11px; border-radius:9999px; cursor:pointer;
  border:1px solid var(--border); background:transparent; color:var(--text-muted); font-family:var(--font); }
.vp-fc.on { background:var(--accent); border-color:transparent; color:#fff; font-weight:600; }
.vp-body { flex:1; min-height:0; overflow-y:auto; padding:4px 20px 16px; border-top:1px solid var(--border-soft); }
.vp-body .pg-grid { grid-template-columns:repeat(auto-fill,minmax(132px,1fr)); gap:10px; padding-top:12px; }
.vp-body .pg-list { padding-top:12px; }
.vp-t { position:relative; aspect-ratio:3/4; border-radius:10px; overflow:hidden; cursor:pointer;
  background:var(--surface); border:2px solid transparent; padding:0; }
.vp-t.on { border-color:var(--accent); }
.vp-t img, .vp-t video, .vp-mini img, .vp-mini video, .vp-r-th img, .vp-r-th video {
  width:100%; height:100%; object-fit:cover; display:block; }
.vp-tag { position:absolute; left:6px; top:6px; font-size:.58rem; font-weight:700; letter-spacing:.05em;
  padding:2px 6px; border-radius:5px; color:#fff; background:#000000a0; }
.vp-tag.nsfw { background:#d6336cdd; } .vp-tag.sfw { background:#1f9d55dd; }
.vp-tick { position:absolute; top:6px; right:6px; width:22px; height:22px; border-radius:6px;
  border:2px solid #fff; background:#0006; color:#fff; font-size:.72rem; font-weight:700;
  display:flex; align-items:center; justify-content:center; }
.vp-t.on .vp-tick, .vp-r.on .vp-tick { background:var(--accent); border-color:var(--accent); }
.vp-zoom { position:absolute; bottom:6px; right:6px; width:24px; height:24px; border:0; border-radius:6px;
  background:#00000088; color:#fff; font-size:.8rem; cursor:pointer; opacity:0; transition:opacity .15s; }
.vp-t:hover .vp-zoom { opacity:1; }
.vp-name { position:absolute; left:0; right:0; bottom:0; padding:14px 34px 6px 8px; font-size:.68rem; color:#fff;
  background:linear-gradient(transparent,#000000b0); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; text-align:left; }
.vp-r { display:flex; align-items:center; gap:12px; padding:8px 10px; border-radius:10px; cursor:pointer;
  border:1px solid transparent; }
.vp-r:hover { background:var(--surface); }
.vp-r.on { border-color:var(--accent-line); background:var(--surface); }
.vp-r .vp-tick { position:static; border-color:var(--border); background:var(--panel); flex-shrink:0; }
.vp-r-th { width:44px; height:44px; border-radius:8px; overflow:hidden; flex-shrink:0; background:var(--surface); }
.vp-r-n { flex:1; min-width:0; font-size:.8rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.vp-r-s { font-size:.7rem; color:var(--text-muted); }
.vp-more { display:flex; justify-content:center; padding:16px 0 4px; }
.vp-none { color:var(--text-muted); font-size:.85rem; padding:48px 0; text-align:center; }
.vp-foot { display:flex; align-items:center; gap:10px; padding:12px 20px; border-top:1px solid var(--border-soft); }
.vp-foot .hint { flex:1; font-size:.76rem; color:var(--text-muted); }
.vp-zv { position:fixed; inset:0; z-index:4100; background:#000000ee; display:flex; align-items:center;
  justify-content:center; padding:24px; }
.vp-zv img, .vp-zv video { max-width:100%; max-height:100%; border-radius:8px; }
.vp-field { display:flex; flex-wrap:wrap; align-items:center; gap:8px; }
.vp-mini { position:relative; width:56px; height:56px; border-radius:9px; overflow:hidden;
  background:var(--surface); border:1px solid var(--border); flex-shrink:0; }
.vp-mini .x { position:absolute; top:2px; right:2px; width:18px; height:18px; border:0; border-radius:50%;
  background:#000000b0; color:#fff; font-size:.7rem; line-height:1; cursor:pointer; padding:0; }
.vp-mini .vp-tag { left:3px; top:auto; bottom:3px; font-size:.5rem; padding:1px 4px; }
.vp-open { height:56px; padding:0 14px; border-radius:9px; border:1px dashed var(--border); background:transparent;
  color:var(--text-2); font-size:.78rem; font-weight:600; cursor:pointer; font-family:var(--font); }
.vp-open:hover { border-color:var(--accent-line); color:var(--text); }
@media (max-width:640px) {
  #vp-root { padding:0; }
  .vp-box { height:100%; border-radius:0; border:0; }
  .vp-body .pg-grid { grid-template-columns:repeat(3,minmax(0,1fr)); gap:6px; }
  .vp-zoom { opacity:1; }
}`;

  // A clip's file is fetched only once its tile scrolls into view; forty
  // <video> elements asking for metadata at once is what froze the strips.
  function watch(root) {
    if (!('IntersectionObserver' in window)) {
      root.querySelectorAll('video[data-src]').forEach(v => { v.src = v.dataset.src; v.removeAttribute('data-src'); });
      return;
    }
    if (!seen) seen = new IntersectionObserver(es => es.forEach(e => {
      if (!e.isIntersecting) return;
      const v = e.target;
      seen.unobserve(v);
      if (v.dataset.src) { v.src = v.dataset.src + '#t=0.1'; v.removeAttribute('data-src'); }
    }), { rootMargin: '200px' });
    root.querySelectorAll('video[data-src]').forEach(v => seen.observe(v));
  }

  function thumb(m) {
    return m.kind === 'video'
      ? '<video data-src="' + esc(m.thumb) + '" muted playsinline preload="metadata"></video>'
      : '<img src="' + esc(m.thumb) + '" alt="" loading="lazy" decoding="async">';
  }

  function mount() {
    if (document.getElementById('vp-root')) return;
    const s = document.createElement('style');
    s.textContent = css;
    document.head.appendChild(s);
    const d = document.createElement('div');
    d.id = 'vp-root';
    d.hidden = true;
    d.innerHTML = '<div class="vp-box" role="dialog" aria-modal="true">'
      + '<div class="vp-top"><h3 id="vp-title"></h3><button type="button" class="vp-x" aria-label="Close" data-a="close">×</button></div>'
      + '<div class="vp-bar"><div id="vp-head"></div><div class="vp-folders" id="vp-folders"></div></div>'
      + '<div class="vp-body" id="vp-body"></div>'
      + '<div class="vp-foot"><button type="button" class="btn btn-ghost" data-a="upload">Upload</button>'
      + '<span class="hint" id="vp-hint"></span>'
      + '<button type="button" class="btn btn-primary" data-a="done">Done</button></div>'
      + '<input type="file" id="vp-file" accept="image/*,video/*" multiple hidden></div>';
    document.body.appendChild(d);
    d.addEventListener('click', onClick);
    d.addEventListener('input', e => {
      if (e.target.id === 'vp-q') { st.q = e.target.value; st.page = 1; drawBody(); }
    });
    d.querySelector('#vp-file').addEventListener('change', e => upload(e.target));
    d.addEventListener('change', async e => {
      if (e.target.id !== 'vp-persona') return;
      o.persona = e.target.value;
      st.picked = [];
      st.folder = '';
      drawHead();
      drawBody();
      await load(o.persona);
      drawHead();
      drawBody();
    });
    document.addEventListener('keydown', e => {
      if (e.key !== 'Escape' || d.hidden) return;
      const z = document.querySelector('.vp-zv');
      if (z) z.remove(); else close();
    });
  }

  async function load(slug, force) {
    if (cache[slug] && !force) return cache[slug];
    const q = encodeURIComponent(slug);
    const [m, f] = await Promise.all([
      fetch('/api/personas/' + q + '/media').then(r => r.json()).catch(() => ({})),
      fetch('/api/personas/' + q + '/folders').then(r => r.json()).catch(() => ({}))
    ]);
    const outfits = ((m && m.outfits) || []).filter(x => x && x.name);
    cache[slug] = {
      items: (m && m.vault) || [],
      own: (f && f.folders) || [],
      folders: ((f && f.folders) || []).concat(outfits.map(x => x.name)),
      outfits
    };
    return cache[slug];
  }

  function data() {
    return o.items ? { items: o.items, folders: [], outfits: [], own: [] } : cache[o.persona];
  }

  function foldersOf(m) {
    const outs = (data() || {}).outfits || [];
    return (m.folders || []).concat((m.outfits || [])
      .map(n => (outs.find(x => String(x.n) === String(n)) || {}).name).filter(Boolean));
  }

  function rows() {
    const all = (data() || { items: [] }).items;
    const q = st.q.trim().toLowerCase();
    const out = all.filter(m => {
      if (o.kind && (m.kind || 'image') !== o.kind) return false;
      if (o.filter && !o.filter(m)) return false;
      if (st.type && (m.kind || 'image') !== st.type) return false;
      if (st.rating && (m.rating || 'unrated') !== st.rating) return false;
      if (st.fav && !m.favourite) return false;
      if (st.folder && foldersOf(m).indexOf(st.folder) === -1) return false;
      if (q && ((m.purpose || '') + ' ' + foldersOf(m).join(' ')).toLowerCase().indexOf(q) === -1) return false;
      return true;
    });
    if (st.sort === 'name') out.sort((a, b) => (a.purpose || '').localeCompare(b.purpose || ''));
    else if (st.sort === 'old') out.reverse();
    return out;
  }

  function opt(key, val, label) {
    const on = st[key] === val;
    return '<button type="button" class="pg-opt' + (on ? ' on' : '') + '" data-a="set" data-k="' + key
      + '" data-v="' + esc(val) + '"><span class="pg-tick">' + (on ? '✓' : '') + '</span>' + esc(label) + '</button>';
  }

  function drawHead() {
    const n = (st.rating ? 1 : 0) + (st.type ? 1 : 0) + (st.fav ? 1 : 0);
    const filters = '<div class="pg-opt-h">Rating</div>'
      + opt('rating', '', 'Any') + opt('rating', 'sfw', 'Safe for work')
      + opt('rating', 'nsfw', 'Explicit') + opt('rating', 'unrated', 'Unrated')
      + (o.kind ? '' : '<div class="pg-opt-h">Type</div>' + opt('type', '', 'Any')
        + opt('type', 'image', 'Photos') + opt('type', 'video', 'Clips'))
      + '<div class="pg-opt-h">Other</div>' + opt('fav', st.fav ? '' : '1', 'Favourites only');
    const SORTS = { new: 'Newest first', old: 'Oldest first', name: 'Name' };
    const ppl = o.personas && window.vpPeople || [];
    $('#vp-head').innerHTML = '<div class="pg-head">'
      + (ppl.length > 1 ? '<select class="pg-ctl" id="vp-persona" style="padding:0 30px 0 10px;">'
        + ppl.map(p => '<option value="' + esc(p.slug) + '"' + (p.slug === o.persona ? ' selected' : '') + '>'
          + esc(p.name || p.slug) + '</option>').join('') + '</select>' : '')
      + '<div class="pg-search"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3" stroke-linecap="round"/></svg>'
      + '<input type="text" id="vp-q" placeholder="Search the vault…" value="' + esc(st.q) + '"></div>'
      + '<div class="pg-menu' + (st.menu === 'filter' ? ' open' : '') + '"><button type="button" class="pg-ctl' + (n ? ' active' : '')
      + '" data-a="menu" data-v="filter">Filter' + (n ? '<span class="pg-ctl-n">' + n + '</span>' : '') + '</button>'
      + '<div class="pg-pop">' + filters + '</div></div>'
      + '<div class="pg-menu' + (st.menu === 'sort' ? ' open' : '') + '"><button type="button" class="pg-ctl" data-a="menu" data-v="sort">Sort</button>'
      + '<div class="pg-pop">' + Object.keys(SORTS).map(k => opt('sort', k, SORTS[k])).join('') + '</div></div>'
      + '<div class="pg-toggle"><button type="button" class="' + (st.view === 'cards' ? 'active' : '') + '" data-a="set" data-k="view" data-v="cards">▦ Cards</button>'
      + '<button type="button" class="' + (st.view === 'list' ? 'active' : '') + '" data-a="set" data-k="view" data-v="list">☰ List</button></div>'
      + '</div>';
    const fs = (data() || {}).folders || [];
    $('#vp-folders').innerHTML = o.items ? '' : fs.length
      ? ['<button type="button" class="vp-fc' + (st.folder ? '' : ' on') + '" data-a="set" data-k="folder" data-v="">All</button>']
        .concat(fs.map(f => '<button type="button" class="vp-fc' + (st.folder === f ? ' on' : '')
          + '" data-a="set" data-k="folder" data-v="' + esc(f) + '">' + esc(f) + '</button>'))
        .concat('<button type="button" class="vp-fc" data-a="folder">+ Folder</button>').join('')
      : '<button type="button" class="vp-fc" data-a="folder">+ Folder</button>';
  }

  function tile(m) {
    const on = st.picked.indexOf(m.id) !== -1;
    const tag = m.kind === 'video' ? '<span class="vp-tag">VIDEO</span>'
      : m.rating ? '<span class="vp-tag ' + m.rating + '">' + m.rating.toUpperCase() + '</span>' : '';
    if (st.view === 'list') {
      return '<div class="vp-r' + (on ? ' on' : '') + '" data-a="pick" data-id="' + esc(m.id) + '">'
        + '<span class="vp-tick">' + (on ? '✓' : '') + '</span>'
        + '<span class="vp-r-th">' + thumb(m) + '</span>'
        + '<span class="vp-r-n">' + esc(m.purpose || 'untitled')
        + '<div class="vp-r-s">' + esc(foldersOf(m).join(', ') || (m.kind === 'video' ? 'clip' : 'photo'))
        + (m.rating ? ' · ' + m.rating.toUpperCase() : '') + '</div></span></div>';
    }
    return '<div class="vp-t' + (on ? ' on' : '') + '" data-a="pick" data-id="' + esc(m.id) + '" title="' + esc(m.purpose || '') + '">'
      + thumb(m) + (m.purpose ? '<span class="vp-name">' + esc(m.purpose) + '</span>' : '') + tag
      + '<span class="vp-tick">' + (on ? '✓' : '') + '</span>'
      + '<button type="button" class="vp-zoom" title="View" data-a="zoom" data-id="' + esc(m.id) + '">⤢</button></div>';
  }

  function drawBody() {
    const body = $('#vp-body');
    if (!data()) { body.innerHTML = window.skelGrid ? skelGrid(12) : '<div class="vp-none">Loading…</div>'; return; }
    const all = rows();
    const shown = all.slice(0, st.page * PER_PAGE);
    st.shown = all;
    body.innerHTML = shown.length
      ? '<div class="' + (st.view === 'list' ? 'pg-list' : 'pg-grid') + '">' + shown.map(tile).join('') + '</div>'
        + (all.length > shown.length ? '<div class="vp-more"><button type="button" class="btn btn-ghost" data-a="more">Show more ('
          + (all.length - shown.length) + ' left)</button></div>' : '')
      : '<div class="vp-none">' + ((data().items || []).length
          ? 'Nothing matches.' : 'The vault is empty — upload something.') + '</div>';
    watch(body);
    drawFoot();
  }

  function drawFoot() {
    const n = st.picked.length;
    $('#vp-hint').textContent = st.note || ((st.shown || []).length + ' items'
      + (o.max > 1 ? ' · ' + n + ' of ' + o.max + ' picked' : n ? ' · 1 picked' : ''));
  }

  // Ticking changes two classes, so a pick never rebuilds the grid or refetches a clip.
  function toggle(id) {
    const i = st.picked.indexOf(id);
    if (i !== -1) st.picked.splice(i, 1);
    else if (o.max <= 1) st.picked = [id];
    else if (st.picked.length < o.max) st.picked.push(id);
    else { st.note = 'That is the most it takes — ' + o.max + '.'; drawFoot(); st.note = ''; return; }
    if (o.max <= 1 && i === -1) { done(); return; }
    document.querySelectorAll('#vp-body [data-a="pick"]').forEach(t => {
      const on = st.picked.indexOf(t.dataset.id) !== -1;
      t.classList.toggle('on', on);
      t.querySelector('.vp-tick').textContent = on ? '✓' : '';
    });
    drawFoot();
  }

  function zoom(id) {
    const m = data().items.find(x => x.id === id);
    if (!m) return;
    const z = document.createElement('div');
    z.className = 'vp-zv';
    z.innerHTML = m.kind === 'video'
      ? '<video src="' + esc(m.thumb) + '" controls autoplay playsinline></video>'
      : '<img src="' + esc(m.thumb) + '" alt="">';
    z.addEventListener('click', e => { if (e.target === z) z.remove(); });
    document.body.appendChild(z);
  }

  function onClick(e) {
    const b = e.target.closest('[data-a]');
    if (e.target.id === 'vp-root') { close(); return; }
    if (!b) { if (st.menu && !e.target.closest('.pg-pop')) { st.menu = ''; drawHead(); } return; }
    const a = b.dataset.a;
    if (a === 'close') close();
    else if (a === 'done') done();
    else if (a === 'upload') $('#vp-file').click();
    else if (a === 'folder') newFolder();
    else if (a === 'more') { st.page++; drawBody(); }
    else if (a === 'zoom') { e.stopPropagation(); zoom(b.dataset.id); }
    else if (a === 'pick') toggle(b.dataset.id);
    else if (a === 'menu') { st.menu = st.menu === b.dataset.v ? '' : b.dataset.v; drawHead(); }
    else if (a === 'set') {
      st[b.dataset.k] = b.dataset.v;
      st.page = 1;
      if (b.dataset.k !== 'view') st.menu = '';
      drawHead();
      drawBody();
    }
  }

  async function newFolder() {
    const c = cache[o.persona];
    const name = (prompt('Name the folder') || '').trim();
    if (!c || !name || c.folders.indexOf(name) !== -1) return;
    c.own.push(name);
    c.folders.unshift(name);
    st.folder = name;
    drawHead();
    drawBody();
    try {
      await fetch('/api/personas/' + encodeURIComponent(o.persona) + '/folders', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ folders: c.own }) });
    } catch (e) {}
  }

  // An upload lands in the folder being looked at: an outfit through the
  // upload's own field, a plain folder as a tag set straight after.
  async function upload(input) {
    const c = cache[o.persona] || { outfits: [] };
    const outfit = (c.outfits.find(x => x.name === st.folder) || {}).n;
    const folder = st.folder && !outfit ? st.folder : '';
    const files = [...(input.files || [])];
    input.value = '';
    if (!files.length) return;
    const fresh = [];
    for (let i = 0; i < files.length; i++) {
      const f = files[i];
      st.note = 'Uploading ' + (i + 1) + ' of ' + files.length + '…';
      drawFoot();
      try {
        const data = await new Promise(res => { const r = new FileReader(); r.onload = () => res(r.result); r.readAsDataURL(f); });
        const poster = f.type.startsWith('video/') ? await frame(f) : '';
        const d = await (await fetch('/api/personas/' + encodeURIComponent(o.persona) + '/media', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ image: data, mime: f.type, kind: f.type.startsWith('video/') ? 'video' : 'image',
                                 purpose: f.name.replace(/\.[^.]+$/, '').slice(0, 60),
                                 outfit: outfit ? String(outfit) : '', poster })
        })).json();
        if (d.id) fresh.push(d.id);
        if (d.id && folder) {
          await fetch('/api/personas/' + encodeURIComponent(o.persona) + '/media/' + encodeURIComponent(d.id), {
            method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ folders: [folder] }) });
        }
      } catch (e) {}
    }
    st.note = '';
    await load(o.persona, true);
    fresh.forEach(id => {
      if (st.picked.indexOf(id) !== -1) return;
      if (o.max <= 1) st.picked = [id];
      else if (st.picked.length < o.max) st.picked.push(id);
    });
    if (o.onUpload) o.onUpload(fresh);
    st.sort = 'new';
    st.page = 1;
    drawHead();
    drawBody();
    if (fresh.length < files.length) { st.note = (files.length - fresh.length) + ' file(s) would not upload.'; drawFoot(); st.note = ''; }
  }

  // A clip's first frame, taken while the file is in the browser: Instagram
  // wants one as a Reel's cover and nothing on the server can open a video.
  function frame(file) {
    return new Promise(resolve => {
      const v = document.createElement('video');
      v.preload = 'metadata';
      v.muted = true;
      const give = url => { URL.revokeObjectURL(v.src); resolve(url); };
      v.onloadedmetadata = () => { v.currentTime = Math.min(0.1, (v.duration || 0) / 2); };
      v.onseeked = () => {
        try {
          const c = document.createElement('canvas');
          c.width = v.videoWidth; c.height = v.videoHeight;
          c.getContext('2d').drawImage(v, 0, 0, c.width, c.height);
          give(c.toDataURL('image/jpeg', 0.85));
        } catch (e) { give(''); }
      };
      v.onerror = () => give('');
      v.src = URL.createObjectURL(file);
    });
  }

  function close() { document.getElementById('vp-root').hidden = true; document.querySelector('.vp-zv')?.remove(); }

  function done() {
    const items = data().items;
    const ids = st.picked.slice();
    close();
    if (o.onDone) o.onDone(ids, ids.map(id => items.find(m => m.id === id)).filter(Boolean), o.persona);
  }

  async function open(opts) {
    mount();
    o = Object.assign({ max: 1, kind: '', title: 'Pick from the vault' }, opts || {});
    st = { q: '', type: '', rating: '', fav: '', folder: '', sort: 'new', menu: '',
           view: st ? st.view : 'cards', page: 1, picked: (o.picked || []).slice(), note: '' };
    document.getElementById('vp-root').hidden = false;
    $('#vp-title').textContent = o.title;
    $('[data-a="upload"]').hidden = !!o.items;
    $('#vp-file').accept = o.kind === 'image' ? 'image/*' : o.kind === 'video' ? 'video/*' : 'image/*,video/*';
    drawHead();
    drawBody();
    if (o.items) return;
    if (o.personas && !window.vpPeople) {
      try { window.vpPeople = await (await fetch('/api/personas')).json(); } catch (e) { window.vpPeople = []; }
      if (!Array.isArray(window.vpPeople)) window.vpPeople = [];
    }
    if (o.personas && !o.persona && window.vpPeople.length) o.persona = window.vpPeople[0].slug;
    if (!o.persona) { $('#vp-body').innerHTML = '<div class="vp-none">Pick a persona first.</div>'; return; }
    await load(o.persona);
    drawHead();
    drawBody();
  }

  // What a page shows in place of a strip: the picked items, and the button.
  function field(f) {
    const items = f.items || [];
    const html = '<div class="vp-field">' + items.map(m => '<span class="vp-mini" title="' + esc(m.purpose || '') + '">'
      + thumb(m) + (m.kind === 'video' ? '<span class="vp-tag">VIDEO</span>' : '')
      + (f.remove ? '<button type="button" class="x" aria-label="Remove" onclick="event.stopPropagation();'
        + f.remove + '(\'' + esc(m.id) + '\')">×</button>' : '') + '</span>').join('')
      + '<button type="button" class="vp-open" onclick="' + esc(f.open) + '">'
      + esc(f.label || (items.length ? 'Change' : 'Choose from vault')) + '</button>'
      + (!items.length && f.empty ? '<span class="hint">' + esc(f.empty) + '</span>' : '') + '</div>';
    setTimeout(() => document.querySelectorAll('.vp-field').forEach(watch));
    return html;
  }

  window.VaultPicker = {
    open, field, thumb,
    invalidate: slug => { delete cache[slug]; },
    items: slug => (cache[slug] || {}).items || null
  };
})();
