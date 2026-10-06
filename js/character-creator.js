// New character creator (admin-only while it is tested beside /characters).
// Fast builds her in a fixed order with one image per photo; Advanced lists
// every photo with a count and her own uploads; "I already have a character"
// starts from her own photos. Everything runs on the /api/characters routes.
(function () {
  const el = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const attr = s => esc(JSON.stringify(s));

  const LEVELS = [['sfw', 'SFW'], ['moderate', 'Nude'], ['explicit', 'Explicit']];
  const LEVEL_INFO = {
    sfw: 'Clothed photos only: her face and full body. The studio makes safe-for-work content of her.',
    moderate: 'Adds breasts, nipples and topless full-body photos in panties, so topless shots keep her real body. No genitals.',
    explicit: 'Everything in Nude, plus pubic area and vagina close-ups and full nude photos, so fully explicit shots keep her real body.'};
  const RANK = {sfw: 0, moderate: 1, explicit: 2};
  const AGE_OF = {'18–21': 21, 'Early 20s': 23, 'Late 20s': 27, '30s': 34, '40s': 44, '50s+': 53};
  const HIGHLIGHTS = ['Black', 'Dark brown', 'Light brown', 'Auburn', 'Red', 'Strawberry blonde', 'Blonde', 'Platinum'];
  const OUTFITS = ['Activewear', 'Bodysuit', 'Casual', 'Fitted dress'];
  const COLOURS = {Black: '#1d1d1f', White: '#f7f7f5', Grey: '#8e8e93', Beige: '#d8c3a5', Navy: '#23305a',
                   Red: '#b3261e', Pink: '#e8a0bf', Green: '#3f6b45', Blue: '#3a6ea5', Brown: '#6b4a33'};
  const CLOTHED = ['body_front', 'body_back', 'body_side'];
  const NIPPLES = [['Small, flat', 'Small', 'Flat'], ['Small, protruding', 'Small', 'Protruding'],
                   ['Medium, protruding', 'Medium', 'Protruding'], ['Medium, puffy', 'Medium', 'Puffy'],
                   ['Large, protruding', 'Large', 'Protruding'], ['Large, puffy', 'Large', 'Puffy']];
  // Mirrors characters.ORDERS: what each photo is built from.
  const ORDERS = {
    nude_first: {breasts: ['face_front'], pubic: ['face_front'], vulva_closed: ['pubic'],
                 nude_front: ['face_front', 'breasts', 'nipples', 'pubic', 'vulva_closed'],
                 rear_nude: ['nude_front'], body_front: ['face_front', 'nude_front'], body_back: ['body_front', 'rear_nude']},
    own: {nude_front: ['face_front'], rear_nude: ['nude_front'], breasts: ['nude_front'], pubic: ['nude_front'],
          vulva_closed: ['pubic'], body_front: ['face_front', 'nude_front'], body_back: ['body_front', 'rear_nude']}};
  const DRESSED = ['Clothed photos', 'Made from her nude photos, dressed, with no nudity.', ['body_front', 'body_back']];
  const GROUPS = {
    nude_first: {
      sfw: [['Full body', 'Made from her approved face.', ['body_front', 'body_back']]],
      moderate: [['Details', 'Made from her approved face.', ['breasts', 'nipples']],
                 ['Topless full body', 'Made from her approved face and detail photos.', ['nude_front', 'rear_nude']], DRESSED],
      explicit: [['Details', 'Made from her approved face.', ['breasts', 'nipples', 'pubic', 'vulva_closed']],
                 ['Nude full body', 'Made from her approved face and detail photos.', ['nude_front', 'rear_nude']], DRESSED]},
    own: {
      sfw: [],
      moderate: [['Details', 'Made from your nude photos.', ['breasts', 'nipples']], DRESSED],
      explicit: [['Details', 'Made from your nude photos.', ['breasts', 'nipples', 'pubic', 'vulva_closed']], DRESSED]}};
  const OWN_SLOTS = {sfw: [['face_front', 0], ['body_front', 0], ['body_back', 1]],
                     moderate: [['face_front', 0], ['nude_front', 0], ['rear_nude', 0]],
                     explicit: [['face_front', 0], ['nude_front', 0], ['rear_nude', 0]]};
  const PAIRS = {hands: ['hands', 'hands_palms'], feet: ['feet', 'feet_soles']};
  const PAIRED = new Set(['hands_palms', 'feet_soles']);
  const SHORT = {breasts: 'Breasts', nipples: 'Nipples', pubic: 'Pubic area', vulva_closed: 'Vagina, closed',
                 hands: 'Hands', feet: 'Feet', hands_palms: 'Palm', feet_soles: 'Sole'};
  const SIDE = {hands: 'Back of hand', hands_palms: 'Palm', feet: 'Top of foot', feet_soles: 'Sole'};

  const S = {step: 'start', mode: '', hist: [], name: '', level: 'sfw', attest: false,
             own: {}, ownLook: '', face: -1, faces: [], fabric: {}, adv: {}, cat: null, catLevel: '', personas: null,
             persona: '', link: '', busy: false};
  let C = null;
  const RUN = {};

  async function api(url, opts) {
    const r = await fetch(url, opts && opts.body ? Object.assign({method: 'POST',
      headers: {'Content-Type': 'application/json'}}, opts, {body: JSON.stringify(opts.body)}) : opts);
    let data = {};
    try { data = await r.json(); } catch (e) {}
    if (!r.ok || data.ok === false) throw Object.assign(new Error(data.error || ('Request failed (' + r.status + ')')), {data});
    return data;
  }
  function say(text, ok) { const m = el('cc-msg'); m.textContent = text || ''; m.className = 'cc-msg' + (ok ? ' ok' : ''); }
  const fail = e => { say(e.message || String(e)); render(); };

  // ── Character state ─────────────────────────────────────────────────────────
  const order = () => (C && C.sheet && C.sheet.order) || (S.mode === 'own' ? 'own' : 'nude_first');
  const V = k => ((C && C.views) || {})[k] || {};
  const disp = k => V(k).display || 'not_started';
  const imgs = (k, role) => ((C && C.images) || []).filter(i => i.view === k && (!role || i.role === role));
  const canon = k => imgs(k, 'canonical')[0];
  const cands = k => imgs(k, 'candidate');
  const shown = k => cands(k).slice(-1)[0] || canon(k);
  const catView = k => ((S.cat && S.cat.views) || []).find(v => v.key === k);
  const label = k => SHORT[k] || ((catView(k) || {}).label || k).replace(/\s*\(.*\)$/, '');
  const parentsOf = k => {
    const o = S.level !== 'sfw' && ORDERS[order()] && ORDERS[order()][k];
    return (o || (catView(k) || {}).parents || []).filter(p => catView(p));
  };
  const faceKeys = () => ((S.cat && S.cat.features) || []).filter(f => f.group === 'face').map(f => f.key);
  const featOpts = k => (((S.cat && S.cat.features) || []).find(f => f.key === k) || {}).options || [];
  const sheet = () => (C ? C.sheet : (S.sheet = S.sheet || {}));

  async function loadCat(level) {
    if (S.cat && S.catLevel === level) return;
    S.cat = await api('/api/characters/catalogue?level=' + level);
    S.catLevel = level;
  }
  async function refresh() { C = (await api('/api/characters/' + C.id)).character; }
  async function saveSheet(extra) {
    const body = Object.assign({sheet: Object.assign({}, sheet())}, extra || {});
    C = (await api('/api/characters/' + C.id, {method: 'PUT', body})).character;
  }
  async function create(extraSheet) {
    const body = {name: S.name.trim() || 'Untitled draft', age: AGE_OF[sheet().apparent_age] || 24, nsfw_level: S.level,
                  sheet: Object.assign({}, sheet(), extraSheet || {})};
    if (S.level === 'sfw') delete body.sheet.order;
    C = (await api('/api/characters', {body})).character;
    history.replaceState(null, '', '/character-creator?id=' + C.id);
  }

  // ── Generation, with an estimate of how long it takes ───────────────────────
  const EST_KEY = 'cc_est';
  function estimates() { try { return JSON.parse(localStorage.getItem(EST_KEY) || '{}'); } catch (e) { return {}; } }
  function learn(kind, secs) {
    const e = estimates();
    e[kind] = e[kind] ? Math.round(e[kind] * 0.6 + secs * 0.4) : Math.round(secs);
    try { localStorage.setItem(EST_KEY, JSON.stringify(e)); } catch (x) {}
  }
  const kindOf = k => ((catView(k) || {}).rating || 'sfw') === 'sfw' && !(parentsOf(k).some(p => (catView(p) || {}).rating !== 'sfw')) ? 'sfw' : 'x';
  const estFor = (k, n) => Math.round((estimates()[kindOf(k)] || (kindOf(k) === 'sfw' ? 35 : 60)) * (1 + 0.1 * ((n || 1) - 1)));

  async function startJob(key, view, n) {
    const r = await api(`/api/characters/${C.id}/generate`, {body: {view, variations: n}});
    RUN[key] = {jobs: r.jobs || [r.job], view, t0: Date.now(), est: estFor(view, n), n};
    tick();
  }
  function left(key) {
    const r = RUN[key];
    return r ? Math.max(0, r.est - (Date.now() - r.t0) / 1000) : 0;
  }
  function bar(key) {
    const r = RUN[key];
    const pct = r ? Math.min(97, (Date.now() - r.t0) / 10 / r.est) : 0;
    const s = Math.ceil(left(key));
    return `<span class="gen" data-run="${esc(key)}"><span class="gbar"><i style="width:${pct}%"></i></span><span class="gt">${s ? s + 's left' : 'Almost done…'}</span></span>`;
  }
  let ticker = 0, polling = false;
  function tick() {
    if (!ticker) ticker = setInterval(() => {
      if (!Object.keys(RUN).length) { clearInterval(ticker); ticker = 0; return; }
      document.querySelectorAll('[data-run]').forEach(n => { const k = n.dataset.run; if (RUN[k]) n.outerHTML = bar(k); });
      document.querySelectorAll('[data-left]').forEach(n => { n.textContent = groupLeft(JSON.parse(n.dataset.left)); });
    }, 500);
    if (!polling) poll();
  }
  async function poll() {
    polling = true;
    while (Object.keys(RUN).length) {
      await new Promise(r => setTimeout(r, 2500));
      let changed = false;
      for (const [key, r] of Object.entries(RUN)) {
        const out = await Promise.all(r.jobs.map(id => api('/api/generate/job/' + id).catch(() => null)));
        if (out.some(j => !j || !['done', 'failed'].includes(j.status))) continue;
        delete RUN[key];
        changed = true;
        learn(kindOf(r.view), (Date.now() - r.t0) / 1000);
        const media = out.flatMap(j => j.media || []);
        const failed = out.find(j => j.status === 'failed');
        if (failed && !media.length) say(label(r.view) + ': ' + (failed.error || 'generation failed') + ' Your tokens were refunded.');
        if (key.startsWith('face')) {
          const ids = media.map(m => m.id);
          if (key === 'face') S.faces = ids.slice(0, 4);
          else if (ids[0]) S.faces[+key.split(':')[1]] = ids[0];
        }
        if (S.fabric.pending && S.fabric.pending[key]) {
          media.forEach(m => { S.fabric[m.id] = S.fabric.pending[key]; });
          delete S.fabric.pending[key];
        }
      }
      if (changed) {
        try { await refresh(); } catch (e) {}
        await drive();
        render();
      }
    }
    polling = false;
  }

  // Fast: every required photo whose sources are approved starts by itself.
  function fastViews() {
    return ((GROUPS[order()] || {})[S.level] || GROUPS.nude_first[S.level] || []).flatMap(g => g[2]);
  }
  async function drive() {
    if (!C || S.mode === 'adv' || S.step !== 'photos' || S.busy) return;
    S.busy = true;
    try {
      // A group starts only once every photo above it is approved.
      for (const [, , vs] of (GROUPS[order()] || GROUPS.nude_first)[S.level] || []) {
        for (const k of vs) {
          if (RUN[k] || shown(k) || disp(k) !== 'not_started') continue;
          await startView(k, 1);
        }
        if (!vs.every(k => canon(k))) break;
      }
    } catch (e) { say(e.message); }
    finally { S.busy = false; }
  }
  async function startView(k, n) {
    if (k === 'nipples' && V(k).mode !== 'reference') {
      C = (await api(`/api/characters/${C.id}/views/nipples`, {method: 'PUT', body: {mode: 'reference'}})).character;
    }
    if (CLOTHED.includes(k)) {
      const pick = S.mode === 'adv' ? (S.adv[k] || {}) : {};
      const outfit = pick.outfit || 'Activewear';
      const was = ((sheet().outfit_colours || {})[outfit]) || '';
      let colour = pick.colour && pick.colour !== 'Random' ? pick.colour : '';
      while (!colour || (colour === was && !(pick.colour && pick.colour !== 'Random'))) {
        colour = Object.keys(COLOURS)[Math.floor(Math.random() * Object.keys(COLOURS).length)];
      }
      const s = sheet();
      s.view_outfits = [outfit];
      if (!outfit.startsWith('upload:')) s.outfit_colours = Object.assign({}, s.outfit_colours, {[outfit]: colour});
      await saveSheet();
      S.fabric.pending = Object.assign({}, S.fabric.pending, {[k]: outfit.startsWith('upload:') ? 'Own outfit' : outfit + ' · ' + colour});
    }
    await startJob(k, k, n);
  }
  async function redo(k) {
    say('');
    try { await startView(k, S.mode === 'adv' ? qtyOf(k) : 1); } catch (e) { return fail(e); }
    render();
  }
  async function approveImage(id, quiet) {
    const url = `/api/characters/${C.id}/images/${id}/approve`;
    const body = {confirmed: faceKeys()};
    try { C = (await api(url, {body})).character; }
    catch (e) {
      if (!(e.data && e.data.age === 'uncertain') || quiet ||
          !confirm('The adult check was uncertain. You have looked at this photo and she is clearly an adult — approve it?')) throw e;
      C = (await api(url, {body: Object.assign({}, body, {reviewed_adult: true})})).character;
    }
  }
  async function approve(id) {
    say('');
    try { await approveImage(id); } catch (e) { return fail(e); }
    await drive();
    render();
  }

  // ── Navigation ──────────────────────────────────────────────────────────────
  function go(s) { if (s !== S.step) S.hist.push(S.step); S.step = s; say(''); render(); el('cc-card').scrollIntoView({block: 'start'}); }
  function back() { const s = S.hist.pop() || 'start'; if (s === 'start') S.mode = ''; S.step = s; say(''); render(); }
  const FLOW = {fast: ['basics', 'look', 'faces', 'body', 'photos'], adv: ['basics', 'look', 'body', 'adv'], own: ['own', 'photos']};

  function render() {
    const flow = FLOW[S.mode] || [];
    el('cc-rail').innerHTML = ['start', ...flow].map((s, i, a) =>
      `<i class="${S.step === 'done' || a.indexOf(S.step) >= i ? 'on' : ''}"></i>`).join('');
    el('cc-mode').textContent = {fast: 'Fast mode', adv: 'Advanced mode', own: 'Your own character'}[S.mode] || '';
    el('cc-title').textContent = C ? C.name : 'New character';
    el('cc-card').innerHTML = (STEPS[S.step] || STEPS.start)();
  }

  // ── Steps ───────────────────────────────────────────────────────────────────
  const levelSelect = () => `<label class="fl" for="cc-level">Content level<select id="cc-level" onchange="CC.setLevel(this.value)">${
    LEVELS.map(([k, l]) => `<option value="${k}" ${S.level === k ? 'selected' : ''}>${l}</option>`).join('')}</select></label>`;
  const levelInfo = () => `<details class="info"><summary>What does each level do?</summary><dl>${
    LEVELS.map(([k, l]) => `<dt${S.level === k ? ' class="cur"' : ''}>${l}</dt><dd>${LEVEL_INFO[k]}</dd>`).join('')}</dl></details>`;
  const nameInput = () => `<label class="fl" for="cc-name">Name<input id="cc-name" type="text" maxlength="80" value="${esc(S.name)}" oninput="CC.S.name=this.value" placeholder="Her name"></label>`;

  function optRow(k, title, opts) {
    const s = sheet();
    const text = CharacterVisuals.TEXT_ONLY.includes(k);
    const list = (opts || featOpts(k)).filter(o => o !== 'Custom');
    const own = k === 'hair_colour' ? ownColour('hair') : '';
    return `<div class="fld"><div class="fl">${esc(title)}</div><div class="opts ${text ? 'txt' : ''}">${list.map(o =>
      `<button type="button" class="opt ${s[k] === o ? 'sel' : ''}" onclick="CC.pick(${attr(k)},${attr(o)})">${text ? '' : CharacterVisuals.render(k, o, s)}<span>${esc(o)}</span></button>`).join('')}${own}</div></div>`;
  }
  function ownColour(kind) {
    const s = sheet(), hair = kind === 'hair';
    const hex = hair ? (s.hair_colour === 'Custom' ? s.hair_colour_hex : '') : (s.highlights === 'Custom' ? s.highlights_hex : '');
    return `<label class="opt ${hex ? 'sel' : ''}"><input type="color" value="${esc(hex || '#7a4a8c')}" aria-label="Own colour" onchange="CC.ownColour('${kind}',this.value)">${
      hex ? CharacterVisuals.render('hair_colour', 'Custom', {hair_colour: 'Custom', hair_colour_hex: hex}) : '<span class="plus">+</span>'}<span>Own colour</span></label>`;
  }
  function highlightsRow() {
    const s = sheet();
    return `<div class="fld"><div class="fl">Highlights</div><div class="opts">
      <button type="button" class="opt ${!s.highlights ? 'sel' : ''}" onclick="CC.pick('highlights','')"><span class="plus">✕</span><span>None</span></button>
      ${HIGHLIGHTS.map(o => `<button type="button" class="opt ${s.highlights === o ? 'sel' : ''}" onclick="CC.pick('highlights',${attr(o)})">${CharacterVisuals.render('hair_colour', o, s)}<span>${esc(o)}</span></button>`).join('')}
      ${ownColour('hl')}</div></div>`;
  }
  function lookTiles(k, title, why, opts, tile) {
    return `<div class="fld"><div class="fl">${esc(title)}</div><p>${esc(why)}</p><div class="looks">${opts.map(([v, cap, art]) =>
      `<button type="button" class="tile ${tile(v) ? 'sel' : ''}" onclick="CC.look(${attr(k)},${attr(v)})" aria-label="${esc(title)}: ${esc(cap)}">${art}<span class="cap">${esc(cap)}</span></button>`).join('')}</div></div>`;
  }
  const vulvaLook = () => ((S.cat && S.cat.looks) || []).find(l => l.key === 'vulva_look');
  function bodyLooks() {
    const s = sheet(), out = [];
    if (RANK[S.level] >= 1) {
      out.push(lookTiles('cup', 'Breast size', 'Pick the closest.', featOpts('cup').slice().sort().map(o => [o, o, CharacterVisuals.render('cup', o, s)]), v => s.cup === v));
      out.push(lookTiles('nipples', 'Nipples', 'Pick the closest. Colour follows her skin tone.',
        NIPPLES.map(([cap, size, shape]) => [cap, cap, CharacterVisuals.render('nipple_shape', shape, s)]),
        v => { const n = NIPPLES.find(x => x[0] === v); return s.nipple_size === n[1] && s.nipple_shape === n[2]; }));
    }
    if (S.level === 'explicit') {
      out.push(lookTiles('pubic_style', 'Pubic hair', 'Pick the closest.', featOpts('pubic_style').map(o => [o, o, CharacterVisuals.render('pubic_style', o, s)]), v => s.pubic_style === v));
      const lk = vulvaLook();
      if (lk) out.push(lookTiles('vulva_look', 'Vagina, closed', 'Pick the shape. Her photo is made in her own skin tone from this.',
        lk.options.map((n, i) => [n, 'Look ' + (i + 1), `<img src="/api/characters/looks/${esc(lk.folder)}/${esc(n)}.jpg" alt="" loading="lazy">`]), v => s.vulva_look === v));
    }
    return out.join('');
  }
  function looksDone() {
    const s = sheet();
    if (RANK[S.level] >= 1 && !(s.cup && s.nipple_size && s.nipple_shape)) return false;
    if (S.level === 'explicit' && !(s.pubic_style && (s.vulva_look || !vulvaLook()))) return false;
    return true;
  }

  function faceTile(i) {
    const key = RUN.face ? 'face' : RUN['face:' + i] ? 'face:' + i : '';
    if (key) return `<div class="tile sk"><span class="cap">${bar(key)}</span></div>`;
    const img = ((C && C.images) || []).find(x => x.id === S.faces[i]);
    if (!img) return '<div class="tile sk"></div>';
    return `<button type="button" class="tile ${S.face === i ? 'sel' : ''}" onclick="CC.lbFace(${i})"><img src="${esc(img.url)}" alt="Face option ${i + 1}"><span class="cap">Option ${i + 1}</span></button>`;
  }

  function wearTag(k, img) {
    if (!CLOTHED.includes(k) || !img || !S.fabric[img.id]) return '';
    const f = S.fabric[img.id], colour = f.split(' · ')[1];
    return `<small>${colour ? `<span class="dot" style="background:${COLOURS[colour]}"></span>` : ''}${esc(f)}</small>`;
  }
  function statusPill(k) {
    const d = disp(k);
    if (RUN[k] || d === 'generating') return RUN[k] ? bar(k) : '<span class="pill">Generating</span>';
    if (d === 'approved') return '<span class="pill ok">Approved</span>';
    if (d === 'outdated') return '<span class="pill warn">Outdated</span>';
    return '';
  }
  function viewRow(k, n, i) {
    const img = shown(k), d = disp(k), run = RUN[k] || d === 'generating';
    const waiting = parentsOf(k).filter(p => !canon(p)).map(label);
    const th = img && !run ? `<div class="th zoom" role="button" tabindex="0" aria-label="View ${esc(label(k))}" onclick="CC.lbView(${attr(k)})"><img src="${esc(img.url)}" alt=""></div>`
      : `<div class="th ${run ? 'run' : ''}"></div>`;
    let acts;
    if (run) acts = statusPill(k);
    else if (d === 'approved' && img && img.role === 'canonical') acts = `<span class="pill ok">Approved</span><button type="button" class="btn btn-ghost btn-sm" onclick="CC.redo(${attr(k)})">Redo</button>`;
    else if (img) acts = `${d === 'approved' ? '<span class="pill ok">Approved</span>' : ''}<button type="button" class="btn btn-ghost btn-sm" onclick="CC.redo(${attr(k)})">Redo</button><button type="button" class="btn btn-primary btn-sm" onclick="CC.approve(${attr(img.id)})">Approve</button>`;
    else if (waiting.length) acts = '<span class="pill">Waiting</span>';
    else acts = `<button type="button" class="btn btn-primary btn-sm" onclick="CC.redo(${attr(k)})">Generate</button>`;
    return `<div class="view">${th}<div class="nm">${esc(label(k))}${img && !run ? wearTag(k, img) : ''}${waiting.length && !img ? `<small>Waits for ${esc(waiting.join(' and '))}</small>` : ''}</div><div class="acts">${acts}</div></div>`;
  }
  function groupLeft(vs) {
    const ss = vs.filter(v => RUN[v]).map(v => Math.ceil(left(v)));
    return ss.length ? ` · about ${Math.max(...ss)}s left` : '';
  }

  // Advanced
  const qtyOf = k => (S.adv[k] || {}).qty || 1;
  function advViews() {
    const all = ((S.cat && S.cat.views) || []).map(v => v.key).filter(k => !PAIRED.has(k));
    const first = ['face_front', ...fastViews()];
    return [...first.filter(k => all.includes(k)), ...all.filter(k => !first.includes(k))];
  }
  function upBtn(k) {
    const refs = imgs(k, 'reference');
    return refs.map(r => `<span class="chipf">Your image<button type="button" aria-label="Remove your image" onclick="CC.unref(${attr(r.id)})">✕</button></span>`).join('') +
      `<label class="btn btn-ghost btn-sm up">+ Your image<input type="file" accept="image/jpeg,image/png,image/webp" onchange="CC.upRef(${attr(k)},this)"></label>`;
  }
  function strip(k, side) {
    const list = cands(k).slice(-8), c = canon(k);
    const all = c ? [c, ...list.filter(x => x.id !== c.id)] : list;
    if (!all.length) return '';
    return `<div class="pr">${side ? `<small>${esc(side)}</small>` : `<small>${all.length > 1 ? 'Open one to approve it' : 'Open it to approve'}</small>`}<div class="mini">${
      all.map((m, i) => `<button type="button" class="tile ${m.role === 'canonical' ? 'sel' : ''}" aria-label="${esc(side || label(k))} option ${i + 1}" onclick="CC.lbVar(${attr(k)},${attr(m.id)})"><img src="${esc(m.url)}" alt=""></button>`).join('')}</div></div>`;
  }
  function advRow(k) {
    const views = PAIRS[k] || [k];
    const cat = catView(k) || {};
    const run = views.some(v => RUN[v] || disp(v) === 'generating');
    const img = canon(k) || shown(k);
    const waiting = parentsOf(k).filter(p => !canon(p)).map(label);
    const locked = !!waiting.length;
    const q = qtyOf(k), pick = S.adv[k] || {};
    const th = img ? `<div class="th zoom" role="button" tabindex="0" aria-label="View ${esc(label(k))}" onclick="CC.lbVar(${attr(k)},${attr(img.id)})"><img src="${esc(img.url)}" alt=""></div>` : `<div class="th ${run ? 'run' : ''}"></div>`;
    const sub = [cat.required ? 'Required' : 'Optional', PAIRS[k] ? 'makes ' + views.map(v => SIDE[v].toLowerCase()).join(' and ') + ', approve one of each' : '',
                 locked ? 'Waits for ' + waiting.join(' and ') : ''].filter(Boolean).join(' · ');
    const all = views.every(v => canon(v));
    const wear = CLOTHED.includes(k) ? `<div class="wear"><label class="fl">Outfit<select onchange="CC.advSet(${attr(k)},'outfit',this.value)">${
      [...OUTFITS.map(o => [o, o]), ['own', 'Own outfit']].map(([v, l]) => `<option value="${v}" ${(pick.outfit || 'Activewear') === v || (v === 'own' && (pick.outfit || '').startsWith('upload:')) ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
      ${(pick.outfit === 'own' || (pick.outfit || '').startsWith('upload:'))
        ? `<div class="fld"><div class="fl">Outfit photo</div>${(pick.outfit || '').startsWith('upload:') ? '<span class="chipf">Outfit added</span>' : `<label class="btn btn-ghost btn-sm up">+ Upload outfit<input type="file" accept="image/jpeg,image/png,image/webp" onchange="CC.upOutfit(${attr(k)},this)"></label>`}</div>`
        : `<label class="fl">Colour<select onchange="CC.advSet(${attr(k)},'colour',this.value)">${['Random', ...Object.keys(COLOURS)].map(o => `<option ${(pick.colour || 'Random') === o ? 'selected' : ''}>${o}</option>`).join('')}</select></label>`}</div>` : '';
    return `<div class="view">${th}<div class="nm">${esc(label(k))}<small>${esc(sub)}</small>${img ? wearTag(k, img) : ''}</div>
      <div class="acts">${run ? (views.map(v => RUN[v] ? bar(v) : '').join('') || '<span class="pill">Generating</span>') : all ? '<span class="pill ok">Approved</span>' : ''}
      <select class="qty" aria-label="How many of ${esc(label(k))}" onchange="CC.advSet(${attr(k)},'qty',+this.value)">${[1, 2, 4, 8].map(n => `<option ${q === n ? 'selected' : ''}>${n}</option>`).join('')}</select>
      ${upBtn(k)}
      <button type="button" class="btn btn-sm ${img ? 'btn-ghost' : 'btn-primary'}" ${locked || run ? 'disabled' : ''} onclick="CC.advGen(${attr(k)})">${img ? 'Generate again' : 'Generate'}</button></div>
      ${wear}${views.some(v => cands(v).length || canon(v)) ? `<div class="pairs">${views.map(v => strip(v, PAIRS[k] ? SIDE[v] : '')).join('')}</div>` : ''}</div>`;
  }

  const STEPS = {
    start: () => `<h2>How do you want to start?</h2>
      <div class="choices">
        <button type="button" class="choice" onclick="CC.begin('fast')"><b>Fast</b><span>A few picks, 4 faces to choose from, then every required photo makes itself.</span></button>
        <button type="button" class="choice" onclick="CC.begin('adv')"><b>Advanced</b><span>Every photo, with how many options to make and your own images to generate from.</span></button>
        <button type="button" class="choice" onclick="CC.begin('own')"><b>I already have a character</b><span>Start from your own photos of her.</span></button>
      </div>`,

    basics: () => `<h2>Basics</h2>
      <div class="row">${nameInput()}${levelSelect()}</div>${levelInfo()}
      <div class="nav"><button type="button" class="btn btn-ghost" onclick="CC.back()">Back</button><button type="button" class="btn btn-primary" onclick="CC.basicsNext()">Next</button></div>`,

    look: () => `<h2>Her look</h2><p>A few quick picks. Everything else gets a sensible default you can change in the original builder.</p>
      ${optRow('ethnicity', 'Ethnicity')}${optRow('apparent_age', 'Looks')}${optRow('skin_tone', 'Skin tone')}${optRow('hair_colour', 'Hair colour')}${highlightsRow()}${optRow('hair_texture', 'Hair style and length')}
      <div class="nav"><button type="button" class="btn btn-ghost" onclick="CC.back()">Back</button>${S.mode === 'fast'
        ? `<button type="button" class="btn btn-primary" ${lookDone() ? '' : 'disabled'} onclick="CC.genFaces()">Generate 4 faces</button>`
        : `<button type="button" class="btn btn-primary" ${lookDone() ? '' : 'disabled'} onclick="CC.lookNext()">Next</button>`}</div>`,

    faces: () => {
      const all = RUN.face;
      return `<h2>Pick her face</h2><p>This face is used for every other photo.</p>
      ${all ? `<div class="genbox"><b>Generating 4 faces</b><span>Usually about ${RUN.face.est} seconds. You can keep this page open.</span></div>` : ''}
      <div class="grid">${[0, 1, 2, 3].map(faceTile).join('')}</div>
      <div class="nav"><button type="button" class="btn btn-ghost" onclick="CC.back()">Back</button><span>
        <button type="button" class="btn btn-ghost" ${Object.keys(RUN).some(k => k.startsWith('face')) ? 'disabled' : ''} onclick="CC.genFaces()">Redo all 4</button>
        <button type="button" class="btn btn-primary" ${S.face < 0 || !S.faces[S.face] ? 'disabled' : ''} onclick="CC.useFace()">Use this face</button></span></div>`;
    },

    body: () => `<h2>Her body</h2><p>Pick the closest type. Fine-tune below if you want.</p>
      <div class="opts big">${((S.cat && S.cat.presets) || []).filter(p => p.key !== 'scratch').map(p =>
        `<button type="button" class="opt ${S.preset === p.key ? 'sel' : ''}" onclick="CC.preset(${attr(p.key)})">${CharacterVisuals.preset(p.values)}<span>${esc(p.label)}</span><small>${esc(p.hint)}</small></button>`).join('')}</div>
      ${bodyLooks()}
      <details class="info"><summary>Fine-tune</summary>${optRow('height', 'Height')}${optRow('build', 'Build')}</details>
      <div class="nav"><button type="button" class="btn btn-ghost" onclick="CC.back()">Back</button><button type="button" class="btn btn-primary" ${looksDone() ? '' : 'disabled'} onclick="CC.bodyNext()">${S.mode === 'fast' ? 'Generate photos' : 'Next'}</button></div>`,

    photos: () => {
      const groups = (GROUPS[order()] || GROUPS.nude_first)[S.level] || [];
      const done = groups.flatMap(g => g[2]).every(k => canon(k));
      const faceImg = canon('face_front');
      return `<h2>Required photos</h2><p>One image each. Approve them, or redo a single photo. Each group is built from the approved photos above it, so the bodies match her exact choices.</p>
      <div class="view"><div class="th zoom" role="button" tabindex="0" onclick="CC.lbView('face_front')">${faceImg ? `<img src="${esc(faceImg.url)}" alt="">` : ''}</div><div class="nm">Face, front<small>${S.mode === 'own' ? 'Your upload' : 'Chosen in the previous step'}</small></div><span class="pill ok">Approved</span></div>
      ${groups.map(([title, why, vs], n) => {
        const open = n === 0 || groups[n - 1][2].every(k => canon(k));
        return `<div class="stage ${open ? '' : 'locked'}"><div class="sh"><b>${n + 1}. ${esc(title)}</b><small>${open ? esc(why) : 'Waiting for group ' + n + ' to be approved'}<span data-left="${esc(JSON.stringify(vs))}">${groupLeft(vs)}</span></small></div>
          <div class="views">${vs.map((k, i) => viewRow(k, n, i)).join('')}</div></div>`;
      }).join('')}
      <div class="nav"><span><button type="button" class="btn btn-ghost" onclick="CC.back()">Back</button>${S.mode === 'own' ? '' : '<button type="button" class="btn btn-ghost" onclick="CC.toAdv()">Switch to Advanced</button>'}</span>
        <button type="button" class="btn btn-primary" ${done ? '' : 'disabled'} onclick="CC.go('done')">Finish</button></div>`;
    },

    adv: () => {
      const req = advViews().filter(k => (catView(k) || {}).required);
      const done = req.every(k => (PAIRS[k] || [k]).every(v => canon(v)));
      return `<h2>All photos</h2><p>Generate each photo when you want it, and choose how many options. Add your own image to any photo to generate from it.</p>
      <label class="fl attest"><input type="checkbox" ${S.attest ? 'checked' : ''} onchange="CC.S.attest=this.checked"> I have the rights to images I upload, and anyone shown is 18+ and agreed.</label>
      <div class="views">${advViews().map(advRow).join('')}</div>
      <div class="nav"><span><button type="button" class="btn btn-ghost" onclick="CC.back()">Back</button><button type="button" class="btn btn-ghost" onclick="CC.toFast()">Switch to Fast</button></span>
        <button type="button" class="btn btn-primary" ${done ? '' : 'disabled'} onclick="CC.go('done')">Finish</button></div>`;
    },

    own: () => {
      const slots = OWN_SLOTS[S.level];
      const need = slots.filter(x => !x[1]).every(x => S.own[x[0]]);
      const vulva = S.level !== 'explicit' || S.own.vulva_closed || S.ownLook;
      const ok = need && vulva && S.attest;
      const lk = vulvaLook();
      const rest = ((GROUPS.own[S.level]) || []).flatMap(g => g[2]).map(label);
      return `<h2>Add your own character</h2><p>Photos of the same adult person.${rest.length ? ' The rest is made from these: ' + esc(rest.join(', ')) + '.' : ''}</p>
      <div class="row">${nameInput()}${levelSelect()}</div>
      <div class="grid">${slots.map(([k, opt]) => `<label class="drop ${S.own[k] ? 'done' : ''}">${S.own[k] ? `<img src="${esc(S.own[k])}" alt=""><span class="dl">✓ ${esc(label(k))}</span>` : `Upload ${esc(label(k).toLowerCase())}${opt ? '<small>Optional</small>' : ''}`}<input type="file" accept="image/jpeg,image/png,image/webp" onchange="CC.ownFile(${attr(k)},this)"></label>`).join('')}</div>
      ${S.level === 'explicit' ? `<div class="fld"><div class="fl">Vagina, closed</div><p>Upload a close-up, or pick the closest look.</p><div class="looks">
        <label class="drop ${S.own.vulva_closed ? 'done' : ''}" style="width:78px">${S.own.vulva_closed ? `<img src="${esc(S.own.vulva_closed)}" alt=""><span class="dl">✓</span>` : 'Upload'}<input type="file" accept="image/jpeg,image/png,image/webp" onchange="CC.ownFile('vulva_closed',this)"></label>
        ${lk ? lk.options.map((n, i) => `<button type="button" class="tile ${S.ownLook === n ? 'sel' : ''}" onclick="CC.ownLook(${attr(n)})"><img src="/api/characters/looks/${esc(lk.folder)}/${esc(n)}.jpg" alt="" loading="lazy"><span class="cap">Look ${i + 1}</span></button>`).join('') : ''}</div></div>` : ''}
      <label class="fl attest"><input type="checkbox" ${S.attest ? 'checked' : ''} onchange="CC.S.attest=this.checked;CC.render()"> I have the rights to these photos, and the person shown is 18+ and agreed.</label>
      <div class="nav"><button type="button" class="btn btn-ghost" onclick="CC.back()">Back</button><button type="button" class="btn btn-primary" ${ok && !S.saving ? '' : 'disabled'} onclick="CC.saveOwn()">${S.saving ? esc(S.saving) : rest.length ? 'Generate the rest' : 'Save character'}</button></div>`;
    },

    done: () => {
      const keys = S.level === 'sfw' ? ['face_front', 'body_front', 'body_back'] : ['nude_front', 'rear_nude', 'body_front', 'body_back'];
      const list = keys.filter(k => canon(k));
      const personas = (S.personas || []).filter(p => !p.studio_only);
      return `<h2>${esc(C ? C.name : '')} is ready</h2><p>Her finished full-body photos.</p>
      <div class="grid">${list.map(k => `<button type="button" class="tile" onclick="CC.lbDone(${attr(k)})"><img src="${esc(canon(k).url)}" alt=""><span class="cap">${esc(label(k))}</span></button>`).join('')}</div>
      <div class="fld"><div class="fl">Use her for chat</div>
        <div class="choices">
          <button type="button" class="choice ${S.persona === 'new' ? 'sel' : ''}" onclick="CC.setPersona('new')"><b>Create a persona from her</b><span>A new chat persona with her name, looks and photos.</span></button>
          <button type="button" class="choice ${S.persona === 'link' ? 'sel' : ''}" onclick="CC.setPersona('link')"><b>Link to a persona</b><span>Give an existing persona her photos.</span></button>
          <button type="button" class="choice ${S.persona === 'later' ? 'sel' : ''}" onclick="CC.setPersona('later')"><b>Not now</b><span>Use her in the studio only. You can link her later.</span></button>
        </div>
        ${S.persona === 'link' ? `<label class="fl" for="cc-link">Persona<select id="cc-link" onchange="CC.S.link=this.value;CC.render()"><option value="">${S.personas ? 'Choose a persona' : 'Loading…'}</option>${
          personas.map(p => `<option value="${esc(p.slug)}" ${S.link === p.slug ? 'selected' : ''}>${esc(p.name || p.slug)}</option>`).join('')}</select></label>` : ''}</div>
      <div class="nav"><button type="button" class="btn btn-ghost" onclick="CC.back()">Back</button><button type="button" class="btn btn-primary" ${!S.persona || (S.persona === 'link' && !S.link) ? 'disabled' : ''} onclick="CC.finish()">${
        S.persona === 'new' ? 'Create persona' : S.persona === 'link' ? 'Link and open studio' : 'Open studio'}</button></div>`;
    },
  };
  const lookDone = () => ['ethnicity', 'apparent_age', 'skin_tone', 'hair_colour', 'hair_texture'].every(k => sheet()[k]);

  // ── Lightbox ────────────────────────────────────────────────────────────────
  let LB = null;
  function lbOpen(items, i) { LB = {items, i}; lbShow(); el('lb').hidden = false; el('lbx').focus(); }
  function lbClose() { el('lb').hidden = true; LB = null; }
  function lbShow() {
    const it = LB.items[LB.i];
    if (!it) return lbClose();
    el('lbt').textContent = `${it.title} · ${LB.i + 1} of ${LB.items.length}`;
    el('lbimg').innerHTML = `<img src="${esc(it.url)}" alt="${esc(it.title)}">`;
    el('lba').innerHTML = it.acts || '';
    el('lbp').disabled = LB.i === 0;
    el('lbn').disabled = LB.i === LB.items.length - 1;
  }
  el('lbp').onclick = () => { if (LB && LB.i > 0) { LB.i--; lbShow(); } };
  el('lbn').onclick = () => { if (LB && LB.i < LB.items.length - 1) { LB.i++; lbShow(); } };
  el('lbx').onclick = lbClose;
  el('lb').onclick = e => { if (e.target === el('lb')) lbClose(); };
  document.addEventListener('keydown', e => {
    if (LB) {
      if (e.key === 'Escape') lbClose();
      else if (e.key === 'ArrowLeft') el('lbp').click();
      else if (e.key === 'ArrowRight') el('lbn').click();
    } else if (e.key === 'Enter' && e.target.classList && e.target.classList.contains('zoom')) e.target.click();
  });
  const act = (fn, text, primary) => `<button type="button" class="btn ${primary ? 'btn-primary' : 'btn-ghost'}" onclick="CC.lbClose();${fn}">${text}</button>`;

  // ── Actions ─────────────────────────────────────────────────────────────────
  const fileData = f => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(f); });

  window.CC = {
    S, render, back, go, lbClose,
    begin(mode) { S.mode = mode; C = null; S.sheet = {}; S.faces = []; S.face = -1; S.own = {}; S.ownLook = ''; go(mode === 'own' ? 'own' : 'basics'); loadCat(S.level).then(render).catch(fail); },
    async setLevel(v) { S.level = v; try { await loadCat(v); } catch (e) { return fail(e); } render(); },
    async basicsNext() {
      try {
        await loadCat(S.level);
        if (C) { C.name = S.name; await api('/api/characters/' + C.id, {method: 'PUT', body: {name: S.name || C.name, nsfw_level: S.level}}).then(d => { C = d.character; }); }
      } catch (e) { return fail(e); }
      go('look');
    },
    pick(k, v) {
      const s = sheet();
      if (v === '') delete s[k]; else s[k] = v;
      if (k === 'hair_colour') delete s.hair_colour_hex;
      if (k === 'highlights') delete s.highlights_hex;
      render();
    },
    ownColour(kind, hex) {
      const s = sheet();
      if (kind === 'hair') { s.hair_colour = 'Custom'; s.hair_colour_hex = hex; } else { s.highlights = 'Custom'; s.highlights_hex = hex; }
      render();
    },
    async ensureChar() {
      if (C) { await saveSheet({name: S.name || C.name, nsfw_level: S.level, age: AGE_OF[sheet().apparent_age] || C.age}); return; }
      await create({order: S.level === 'sfw' ? undefined : 'nude_first'});
    },
    async genFaces() {
      say('');
      try {
        await CC.ensureChar();
        S.face = -1; S.faces = [];
        if (S.step !== 'faces') go('faces');
        await startJob('face', 'face_front', 4);
      } catch (e) { return fail(e); }
      render();
    },
    async redoFace(i) {
      try { if (S.face === i) S.face = -1; await startJob('face:' + i, 'face_front', 1); } catch (e) { return fail(e); }
      render();
    },
    lbFace(i) {
      const items = [0, 1, 2, 3].filter(j => S.faces[j] && !RUN.face && !RUN['face:' + j]).map(j => ({
        title: 'Face, option ' + (j + 1), url: (C.images.find(x => x.id === S.faces[j]) || {}).url,
        acts: act(`CC.redoFace(${j})`, 'Redo this face') + act('CC.genFaces()', 'Redo all 4') + act(`CC.S.face=${j};CC.render()`, 'Choose this face', true)}));
      lbOpen(items, Math.max(0, items.findIndex(x => x.title.endsWith(' ' + (i + 1)))));
    },
    async useFace() {
      say('Checking her face…', true);
      try { await approveImage(S.faces[S.face]); } catch (e) { return fail(e); }
      say('');
      go('body');
    },
    async lookNext() {
      try { await CC.ensureChar(); } catch (e) { return fail(e); }
      go('body');
    },
    async preset(k) {
      S.preset = k;
      const p = ((S.cat && S.cat.presets) || []).find(x => x.key === k);
      if (p) Object.assign(sheet(), p.values);
      render();
    },
    look(k, v) {
      const s = sheet();
      if (k === 'nipples') { const n = NIPPLES.find(x => x[0] === v); s.nipple_size = n[1]; s.nipple_shape = n[2]; }
      else s[k] = v;
      render();
    },
    async bodyNext() {
      say('');
      try { await CC.ensureChar(); } catch (e) { return fail(e); }
      if (S.mode === 'fast') { go('photos'); await drive(); render(); }
      else go('adv');
    },
    redo, approve: id => approve(id),
    lbView(k) {
      const keys = S.step === 'photos' ? ['face_front', ...fastViews()] : [k];
      const items = keys.filter(v => shown(v) && !RUN[v]).map(v => {
        const img = shown(v), approved = img.role === 'canonical';
        return {k: v, title: label(v), url: img.url,
                acts: v === 'face_front' ? '' : (approved ? '<span class="pill ok">Approved</span>' : '') + act(`CC.redo(${attr(v)})`, 'Redo') +
                  (approved ? '' : act(`CC.approve(${attr(img.id)})`, 'Approve', true))};
      });
      lbOpen(items, Math.max(0, items.findIndex(x => x.k === k)));
    },
    lbVar(k, id) {
      const c = canon(k), list = cands(k).slice(-8);
      const all = c ? [c, ...list.filter(x => x.id !== c.id)] : list;
      const items = all.map((m, i) => ({title: `${label(k)}, option ${i + 1}`, url: m.url,
        acts: m.role === 'canonical' ? '<span class="pill ok">Approved</span>' : act(`CC.approve(${attr(m.id)})`, 'Approve this one', true)}));
      lbOpen(items, Math.max(0, all.findIndex(m => m.id === id)));
    },
    lbDone(k) {
      const keys = S.level === 'sfw' ? ['face_front', 'body_front', 'body_back'] : ['nude_front', 'rear_nude', 'body_front', 'body_back'];
      const items = keys.filter(v => canon(v)).map(v => ({k: v, title: label(v), url: canon(v).url, acts: ''}));
      lbOpen(items, Math.max(0, items.findIndex(x => x.k === k)));
    },
    toAdv() { S.mode = 'adv'; go('adv'); },
    toFast() { S.mode = 'fast'; go(canon('face_front') ? 'photos' : 'look'); drive().then(render); },
    advSet(k, f, v) {
      S.adv[k] = Object.assign({}, S.adv[k], {[f]: v});
      if (f !== 'qty') render();
    },
    async advGen(k) {
      say('');
      try { for (const v of PAIRS[k] || [k]) await startView(v, qtyOf(k)); } catch (e) { return fail(e); }
      render();
    },
    async upRef(k, input) {
      const f = input.files[0];
      if (!f) return;
      if (!S.attest) { input.value = ''; return say('Tick the rights and consent box first.'); }
      try {
        const image = await fileData(f);
        for (const v of PAIRS[k] || [k]) await api(`/api/characters/${C.id}/images`, {body: {view: v, image, attest: true}});
        await refresh();
      } catch (e) { return fail(e); }
      render();
    },
    async unref(id) {
      try { C = (await api(`/api/characters/${C.id}/images/${id}`, {method: 'DELETE'})).character; } catch (e) { return fail(e); }
      render();
    },
    async upOutfit(k, input) {
      const f = input.files[0];
      if (!f) return;
      if (!S.attest) { input.value = ''; return say('Tick the rights and consent box first.'); }
      try {
        const up = await api(`/api/characters/${C.id}/images`, {body: {role: 'outfit', image: await fileData(f), attest: true}});
        S.adv[k] = Object.assign({}, S.adv[k], {outfit: 'upload:' + up.image.id});
        await refresh();
      } catch (e) { return fail(e); }
      render();
    },
    async ownFile(k, input) {
      const f = input.files[0];
      if (!f) return;
      if (f.size > 8 * 1024 * 1024) return say('Images must be 8 MB or smaller.');
      S.own[k] = await fileData(f);
      if (k === 'vulva_closed') S.ownLook = '';
      render();
    },
    ownLook(n) { S.ownLook = n; delete S.own.vulva_closed; render(); },
    async saveOwn() {
      say('');
      const steps = OWN_SLOTS[S.level].map(x => x[0]).filter(k => S.own[k]);
      try {
        S.saving = 'Creating her…'; render();
        await loadCat(S.level);
        if (!C) await create(Object.assign({order: S.level === 'sfw' ? undefined : 'own'}, S.ownLook ? {vulva_look: S.ownLook} : {}));
        for (const k of steps) {
          if (canon(k)) continue;
          S.saving = 'Checking ' + label(k).toLowerCase() + '…'; render();
          const up = await api(`/api/characters/${C.id}/images`, {body: {view: k, image: S.own[k], attest: true, import: true}});
          await approveImage(up.image.id);
        }
        if (S.own.vulva_closed && !imgs('vulva_closed').length) {
          await api(`/api/characters/${C.id}/images`, {body: {view: 'vulva_closed', image: S.own.vulva_closed, attest: true, import: true}});
        }
        await refresh();
      } catch (e) { S.saving = ''; return fail(e); }
      S.saving = '';
      if (S.level === 'sfw') return go('done');
      go('photos');
      await drive();
      render();
    },
    async setPersona(v) {
      S.persona = v;
      render();
      if (v === 'link' && !S.personas) {
        try { const list = await api('/api/personas'); S.personas = Array.isArray(list) ? list : (list.personas || []); }
        catch (e) { S.personas = []; say(e.message); }
        render();
      }
    },
    async finish() {
      say('');
      let slug = C.persona;
      try {
        if (S.persona === 'new') slug = (await api(`/api/characters/${C.id}/studio`, {body: {persona: true}})).slug;
        else if (S.persona === 'link') { C = (await api(`/api/characters/${C.id}/link`, {body: {persona: S.link}})).character; slug = S.link; }
        else if (!slug) slug = (await api(`/api/characters/${C.id}/studio`, {body: {}})).slug;
      } catch (e) { return fail(e); }
      if (S.persona === 'new') {
        try { if (window.parent !== window && window.parent.loadPersona) return window.parent.loadPersona(slug); } catch (e) {}
      }
      const url = '/studio?persona=' + encodeURIComponent(slug);
      if (window.PageWizard && PageWizard.openPage) PageWizard.openPage(url); else location.href = url;
    },
  };

  // Reopening a character made here picks up where it was left.
  async function boot() {
    const id = new URLSearchParams(location.search).get('id');
    if (!id) return render();
    try {
      C = (await api('/api/characters/' + id)).character;
      S.level = C.nsfw_level; S.name = C.name;
      S.mode = C.sheet.order === 'own' ? 'own' : 'fast';
      await loadCat(S.level);
      S.hist = ['start'];
      S.step = canon('face_front') ? 'photos' : 'look';
      render();
      await drive();
    } catch (e) { say(e.message); }
    render();
  }
  boot();
})();
