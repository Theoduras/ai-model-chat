/* Option drawings for the character builder.
 *
 * Parametric on purpose: a handful of base shapes (a head, a front and a side
 * silhouette, a torso, a pelvis) and each option moves one parameter, drawing
 * the part it changes in the accent colour. That keeps ~110 options in one
 * small file and every drawing in the same hand. Option labels must match
 * characters.FEATURES exactly; test_characters.py checks every one renders.
 */
(function (root) {
  const A = 'var(--accent)';

  function svg(inner) {
    return '<svg viewBox="0 0 64 64" width="56" height="56" fill="none" stroke="currentColor" ' +
      'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + inner + '</svg>';
  }
  const hl = (d, w) => `<path d="${d}" stroke="${A}" stroke-width="${w || 2.4}"/>`;
  const ln = (d, extra) => `<path d="${d}"${extra || ''}/>`;
  const dot = (x, y, r, color) => `<circle cx="${x}" cy="${y}" r="${r || 1.6}" fill="${color || A}" stroke="none"/>`;
  const swatch = hex => svg(`<circle cx="32" cy="32" r="20" fill="${hex}" stroke="currentColor" stroke-opacity=".35"/>`);
  const f = n => Math.round(n * 10) / 10;
  const P = p => f(p[0]) + ',' + f(p[1]);

  // Catmull-Rom through the points, as one cubic per segment, so a part of an
  // outline can be redrawn on its own in the accent colour.
  function segments(pts) {
    const n = pts.length, out = [];
    for (let i = 0; i < n; i++) {
      const p0 = pts[(i - 1 + n) % n], p1 = pts[i], p2 = pts[(i + 1) % n], p3 = pts[(i + 2) % n];
      const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
      const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
      out.push('C' + P(c1) + ' ' + P(c2) + ' ' + P(p2));
    }
    return out;
  }
  function outline(pts, segs) { return 'M' + P(pts[0]) + segs.join('') + 'Z'; }
  function part(pts, segs, from, to) { return 'M' + P(pts[from]) + segs.slice(from, to).join(''); }

  // ── Head ──────────────────────────────────────────────────────────────────
  const HEAD = {top: 8, fw: 14, templeY: 18, c: 16, cheekY: 32, j: 12, jawY: 47, cw: 4, chinY: 58};
  function headPts(o) {
    o = Object.assign({}, HEAD, o || {});
    return [[32, o.top], [32 + o.fw, o.templeY], [32 + o.c, o.cheekY], [32 + o.j, o.jawY], [32 + o.cw, o.chinY],
            [32 - o.cw, o.chinY], [32 - o.j, o.jawY], [32 - o.c, o.cheekY], [32 - o.fw, o.templeY]];
  }
  const FACE_BITS = ln('M22,31 q4,-2.5 8,0 M34,31 q4,-2.5 8,0 M32,33 l-2,8 h4 M27,48 q5,3 10,0', ' stroke-opacity=".55"');
  function head(o, mode, extra) {
    const pts = headPts(o), segs = segments(pts);
    let body = mode === 'all' ? hl(outline(pts, segs)) : ln(outline(pts, segs));
    if (mode === 'jaw') body += hl(part(pts, segs, 2, 7));
    return svg(body + (mode === 'bare' ? '' : FACE_BITS) + (extra || ''));
  }

  const FACE_SHAPE = {
    'Oval': {}, 'Heart-shaped': {fw: 17, c: 16, j: 10, cw: 2},
    'Round': {fw: 16, c: 19, j: 16, cw: 7, chinY: 55, top: 10},
    'Square': {fw: 16, c: 16.5, j: 16, cw: 9, jawY: 50},
    'Diamond': {fw: 11, c: 19, j: 11, cw: 3}, 'Long': {fw: 13, c: 14, j: 11, cw: 5, top: 4, chinY: 61},
  };
  const JAW = {
    'Soft, rounded chin': {j: 12, cw: 6, chinY: 57}, 'Defined jaw': {j: 15, jawY: 46, cw: 5},
    'Pointed chin': {j: 10, cw: 1.2, chinY: 60}, 'Square jaw': {j: 16.5, jawY: 51, cw: 9},
  };
  const CHEEK = {High: [19, 28], Medium: [16, 32], Soft: [15, 35]};

  const EYE = {
    'Almond': ['M8,32 Q32,16 56,32', 'M8,32 Q32,44 56,32'],
    'Round': ['M10,33 Q32,10 54,33', 'M10,33 Q32,52 54,33'],
    'Hooded': ['M8,32 Q32,22 56,32', 'M8,32 Q32,44 56,32', 'M6,28 Q32,12 58,30'],
    'Monolid': ['M8,32 Q32,24 56,32', 'M8,32 Q32,40 56,32'],
    'Upturned': ['M8,36 Q30,18 56,26', 'M8,36 Q34,44 56,26'],
    'Downturned': ['M8,26 Q34,18 56,36', 'M8,26 Q30,44 56,36'],
  };
  function eye(shape, iris) {
    const [up, lo, hood] = EYE[shape] || EYE.Almond;
    return svg((iris ? `<circle cx="32" cy="32" r="8" fill="${iris}" stroke="none"/>` : '') +
      (iris ? ln(up) + ln(lo) : hl(up) + hl(lo)) + `<circle cx="32" cy="32" r="8"/>` +
      dot(32, 32, 2.6, 'currentColor') + (hood ? ln(hood, ' stroke-opacity=".6"') : ''));
  }

  const BROW = {
    'Soft arch, medium': ['M10,36 Q30,24 54,32', 4], 'High arch, thin': ['M10,38 Q28,16 54,34', 2],
    'Straight, full': ['M10,33 Q32,29 54,33', 6], 'Bold, thick': ['M10,37 Q30,24 54,33', 8],
  };
  const NOSE = {
    'Straight, narrow': 'M24,6 L37,40 Q41,46 34,48 L27,48',
    'Button': 'M24,6 Q30,28 33,36 Q44,42 35,48 L27,48',
    'Slightly upturned': 'M24,6 L34,35 Q43,36 37,46 L27,48',
    'Roman': 'M24,6 Q39,17 37,40 Q39,46 33,48 L27,48',
    'Wide': 'M24,6 L35,38 Q47,44 37,50 L25,50',
  };
  const LIPS = {
    'Full, defined bow': [20, 9, 10, 4], 'Medium': [19, 6, 8, 2], 'Thin': [19, 3, 4, 1], 'Very full': [21, 12, 14, 3],
  };
  function lips(w, up, lo, bow) {
    const L = 32 - w, R = 32 + w;
    return svg(hl(`M${L},32 C${L + w * 0.4},${32 - up} ${26},${32 - up - 1} 32,${32 - up + bow} ` +
      `C38,${32 - up - 1} ${R - w * 0.4},${32 - up} ${R},32`) +
      hl(`M${L},32 C${L + w * 0.3},${32 + lo * 1.25} ${R - w * 0.3},${32 + lo * 1.25} ${R},32`) +
      ln(`M${L},32 Q32,34 ${R},32`, ' stroke-opacity=".6"'));
  }
  const EAR = {'Small, close-set': [2.5, 8], 'Medium': [4.5, 10], 'Prominent': [8, 11]};
  function ears(out, h) {
    return svg(`<ellipse cx="32" cy="32" rx="13" ry="19"/>` +
      hl(`M19,${32 - h} Q${19 - out},${32 - h} ${19 - out},32 Q${19 - out * 0.8},${32 + h} 20,${32 + h * 0.8}`) +
      hl(`M45,${32 - h} Q${45 + out},${32 - h} ${45 + out},32 Q${45 + out * 0.8},${32 + h} 44,${32 + h * 0.8}`));
  }

  const HAIR = {
    'Long, straight': 'M20,26 Q20,6 32,6 Q44,6 44,26 L46,60 M20,26 L18,60',
    'Long, loose waves': 'M20,26 Q20,6 32,6 Q44,6 44,26 q4,6 0,12 q-4,6 0,12 q4,6 1,10 M20,26 q-4,6 0,12 q4,6 0,12 q-4,6 -1,10',
    'Long, curly': 'M20,24 Q20,5 32,5 Q44,5 44,24 a3,3 0 1 1 2,6 a3,3 0 1 1 0,7 a3,3 0 1 1 -1,7 a3,3 0 1 1 0,7 ' +
      'M20,24 a3,3 0 1 0 -2,6 a3,3 0 1 0 0,7 a3,3 0 1 0 1,7 a3,3 0 1 0 0,7',
    'Shoulder-length': 'M20,26 Q20,6 32,6 Q44,6 44,26 L45,46 q1,3 4,3 M20,26 L19,46 q-1,3 -4,3',
    'Bob': 'M20,28 Q19,6 32,6 Q45,6 44,28 L45,40 L40,40 M20,28 L19,40 L24,40',
    'Pixie': 'M21,26 Q18,7 32,7 Q46,7 43,26 M21,20 q6,-6 14,-4 q6,1 8,6',
  };
  const HAIRLINE = {
    'Rounded, middle part': 'M17,30 Q17,14 32,14 Q47,14 47,30 M32,14 L32,6',
    'Side part': 'M17,30 Q17,14 32,14 Q47,14 47,30 M26,14 L25,6 M26,14 Q38,15 47,24',
    'Straight, fringe': 'M17,24 L47,24 M21,24 l0,-8 M27,24 l0,-9 M33,24 l0,-9 M39,24 l0,-9 M44,24 l0,-8',
    "Widow's peak": 'M17,30 Q18,16 27,15 L32,21 L37,15 Q46,16 47,30',
  };
  const MARKS = {
    'None': '', 'Light freckles, nose': [[27, 37], [37, 37], [25, 40], [39, 40], [30, 35], [34, 35]].map(p => dot(p[0], p[1], 0.9)).join(''),
    'Heavy freckles': [[22, 36], [25, 39], [27, 35], [20, 40], [24, 43], [29, 38], [42, 36], [39, 39], [37, 35],
      [44, 40], [40, 43], [35, 38], [31, 36], [33, 40], [23, 33], [41, 33]].map(p => dot(p[0], p[1], 0.9)).join(''),
    'Beauty mark, cheek': dot(41, 40, 1.8), 'Beauty mark, lip': dot(37, 44, 1.6),
  };

  const COLOURS = {
    skin_tone: {'Fair': '#f6dfcf', 'Light': '#eecbb0', 'Light olive': '#d9b48c', 'Olive': '#c49a6c', 'Tan': '#b07d52',
      'Brown': '#8a5a3b', 'Deep brown': '#6a4029', 'Dark': '#4a2c1d'},
    hair_colour: {'Black': '#1c1a1a', 'Dark brown': '#3b2618', 'Light brown': '#7a5436', 'Auburn': '#7e3a1e',
      'Red': '#a8391c', 'Strawberry blonde': '#d19a6b', 'Blonde': '#e3c27a', 'Platinum': '#efe6d0'},
    areola_colour: {'Light pink': '#e8b3a8', 'Pink': '#d98f86', 'Rosy brown': '#b87562', 'Brown': '#8e5a45', 'Dark brown': '#5e3a2c'},
    vulva_colour: {'Pink': '#e3a197', 'Rosy': '#c9867a', 'Tan': '#b08466', 'Brown': '#8a5d47', 'Dark': '#5a3a2c'},
    anus_colour: {'Pink': '#e3a197', 'Rosy': '#c9867a', 'Tan': '#b08466', 'Brown': '#8a5d47', 'Dark': '#5a3a2c'},
  };
  const EYE_COLOURS = {'Brown': '#6b4226', 'Dark brown': '#3a2414', 'Hazel': '#8e7440', 'Green': '#4f7a3e',
    'Blue': '#4a7fb5', 'Grey': '#8a949c'};

  // ── Front silhouette ──────────────────────────────────────────────────────
  const BODY = {sh: 11, bu: 11, wa: 8.5, hi: 12, th: 11.5};
  function bodyPts(o) {
    o = Object.assign({}, BODY, o || {});
    const R = [[35, 14], [32 + o.sh, 18], [32 + (o.sh + o.bu) / 2 - 1.5, 23], [32 + o.bu, 27], [32 + o.wa, 35], [32 + o.hi, 42],
      [32 + o.th, 49], [40, 55], [37.5, 62], [34, 62], [34.5, 55], [32, 47]];
    const L = R.slice(0, 11).reverse().map(p => [64 - p[0], p[1]]);
    return R.concat(L);   // 0..11 right side down to the crotch, 12..22 left side back up
  }
  const BODY_PARTS = {
    shoulders: [[0, 2], [20, 22]], bust: [[2, 4], [18, 20]], waist: [[3, 5], [17, 19]],
    hips: [[4, 6], [16, 18]], thighs: [[5, 7], [15, 17], [10, 12]],
  };
  function body(o, partKey, extra, arms) {
    const pts = bodyPts(o), segs = segments(pts);
    const whole = partKey === 'all';
    let s = `<circle cx="32" cy="8" r="5"/>` + (whole ? hl(outline(pts, segs), 2) : ln(outline(pts, segs)));
    (BODY_PARTS[partKey] || []).forEach(([a, b]) => { s += hl(part(pts, segs, a, b)); });
    if (arms) {
      const sh = (o && o.sh) || BODY.sh;
      s += ln(`M${32 + sh},18 Q${35 + sh},30 ${34 + sh},40 M${32 - sh},18 Q${29 - sh},30 ${30 - sh},40`, ' stroke-opacity=".7"');
    }
    return svg(s + (extra || ''));
  }
  const BUILD = {
    'Slim': {sh: 10, bu: 10, wa: 7.5, hi: 10.5, th: 10}, 'Athletic': {sh: 12.5, bu: 11, wa: 9, hi: 11, th: 11.5},
    'Average': {}, 'Curvy': {sh: 11, bu: 13, wa: 8, hi: 15, th: 13},
    'Voluptuous': {sh: 12, bu: 15, wa: 10, hi: 16, th: 14}, 'Muscular': {sh: 14, bu: 12, wa: 10, hi: 12, th: 13},
  };
  const HEIGHT = {'Under 155 cm': 0.8, '155–165 cm': 0.87, '165–175 cm': 0.94, 'Over 175 cm': 1};
  function height(s) {
    const pts = bodyPts(), segs = segments(pts);
    const top = f(63 - 60 * s);
    return svg(`<g transform="translate(32,63) scale(${s}) translate(-32,-63)"><circle cx="32" cy="8" r="5"/>` +
      ln(outline(pts, segs)) + '</g>' + hl(`M56,63 L56,${top} M53,${top} L59,${top}`) +
      ln('M4,63 L60,63', ' stroke-opacity=".5"'));
  }
  const MARKERS = {
    tattoos: {'None': '', 'Small, wrist': [[45, 38]], 'Small, ankle': [[37, 59]], 'Hip': [[42, 41]],
      'Sleeve': 'sleeve', 'Back piece': 'back'},
    piercings: {'None': '', 'Ears': [[27, 8], [37, 8]], 'Nose': [[32, 9]], 'Navel': [[32, 37]]},
    birthmarks: {'None': '', 'Shoulder': [[41, 19]], 'Hip': [[41, 42]]},
  };
  function marked(spec) {
    let extra = '';
    if (spec === 'sleeve') extra = hl('M43,18 Q46,30 45,40', 4);
    else if (spec === 'back') extra = `<rect x="25" y="18" width="14" height="16" rx="3" stroke="${A}" stroke-dasharray="2 2"/>`;
    else if (Array.isArray(spec)) extra = spec.map(p => dot(p[0], p[1], 1.8)).join('');
    return body({}, '', extra, true);
  }
  function side(p) {
    const pts = [[28, 14], [25, 22], [27, 33], [26, 42], [28, 52], [29, 62], [33, 62], [34, 56], [35, 49], [34 + p, 42],
      [33, 34], [35, 22], [33, 14]];
    const segs = segments(pts);
    return svg(`<circle cx="30" cy="8" r="5"/>` + ln(outline(pts, segs)) + hl(part(pts, segs, 7, 11)));
  }
  const NAILS = {
    'Short, nude': [2, false], 'Medium, painted': [5, true], 'Long, painted': [9, true], 'French tips': [5, 'tip'],
  };
  function nails(len, paint) {
    let s = '';
    [18, 32, 46].forEach(x => {
      s += ln(`M${x - 5},62 L${x - 5},${30} Q${x - 5},22 ${x},22 Q${x + 5},22 ${x + 5},30 L${x + 5},62`);
      const nail = `M${x - 3.5},30 L${x - 3.5},${24 - len} Q${x},${20 - len - 1} ${x + 3.5},${24 - len} L${x + 3.5},30 Z`;
      s += paint === true ? `<path d="${nail}" fill="${A}" stroke="${A}"/>` : hl(nail, 1.8);
      if (paint === 'tip') s += `<path d="M${x - 3.5},${26 - len} Q${x},${20 - len - 1} ${x + 3.5},${26 - len}" stroke="${A}" stroke-width="3"/>`;
    });
    return svg(s);
  }

  // ── Schematic NSFW ────────────────────────────────────────────────────────
  const TORSO = ln('M26,4 Q32,8 38,4 M14,6 Q11,34 18,62 M50,6 Q53,34 46,62', ' stroke-opacity=".7"');
  const CUP = {A: 6, B: 7.5, C: 9, D: 10.5, DD: 12, 'E+': 13.5};
  const BREAST = {r: 9, gap: 11, cy: 28, w: 1, h: 0.9, aug: false, drop: 0};
  function breasts(o) {
    o = Object.assign({}, BREAST, o || {});
    let s = TORSO;
    [-1, 1].forEach(side => {
      const cx = 32 + side * o.gap, rw = o.r * o.w, rh = o.r * o.h, cy = o.cy + o.drop;
      s += hl(`M${f(cx - rw)},${cy} A${f(rw)},${f(rh)} 0 0 0 ${f(cx + rw)},${cy}`);
      const outer = cx + side * rw;
      s += ln(`M${f(outer - side * 1)},${f(cy - o.r * 1.4)} Q${f(outer + side * 0.5)},${f(cy - o.r * 0.4)} ${f(outer)},${cy}`);
      if (o.aug) s += hl(`M${f(cx - rw)},${cy} A${f(rw)},${f(rw * 0.8)} 0 0 1 ${f(cx + rw)},${cy}`, 1.4);
      s += dot(f(cx + side * rw * 0.15), f(cy + rh * 0.35), 1.4, 'currentColor');
    });
    return svg(s);
  }
  const SHAPE = {'Round': {}, 'Teardrop': {h: 1.15, drop: 1}, 'Athletic': {w: 1.1, h: 0.55, r: 8},
    'Relaxed': {w: 1.1, h: 1.2, drop: 4}};
  const SPACING = {Close: 9, Average: 11, Wide: 14};
  const PERK = {Perky: {cy: 24, h: 0.8}, Natural: {}, Soft: {cy: 29, h: 1.1, drop: 2}};
  function areola(ar, nr, hlArea, bar) {
    return svg((hlArea ? `<circle cx="32" cy="32" r="${ar}" stroke="${A}" stroke-width="2.4" fill="${A}" fill-opacity=".15"/>`
                       : `<circle cx="32" cy="32" r="${ar}"/>`) +
      (hlArea ? dot(32, 32, nr, 'currentColor') : dot(32, 32, nr)) + (bar || ''));
  }
  const NIP_SHAPE = {
    Flat: 'M10,8 Q46,12 50,32 Q46,52 10,56',
    Puffy: 'M10,8 Q44,12 46,24 Q56,26 56,32 Q56,38 46,40 Q44,52 10,56',
    Protruding: 'M10,8 Q46,12 50,28 L56,28 Q58,32 56,36 L50,36 Q46,52 10,56',
    Inverted: 'M10,8 Q46,12 50,28 Q46,32 50,36 Q46,52 10,56',
  };
  const piercingBar = (cx) => `<path d="M${cx - 7},32 L${cx + 7},32" stroke="${A}" stroke-width="1.6"/>` +
    dot(cx - 7, 32, 1.8) + dot(cx + 7, 32, 1.8);
  function pair(which) {
    let s = '';
    [18, 46].forEach((cx, i) => {
      s += `<circle cx="${cx}" cy="32" r="8"/>` + dot(cx, 32, 2.4, 'currentColor');
      if (which === 2 || (which === 1 && i === 1)) s += piercingBar(cx);
    });
    return svg(s);
  }
  const PELVIS = ln('M10,6 Q13,30 24,48 Q32,58 40,48 Q51,30 54,6 M17,18 Q24,34 29,48 M47,18 Q40,34 35,48', ' stroke-opacity=".7"');
  const PUBIC = {
    'Trimmed': '<path d="M23,28 L41,28 L33,48 L31,48 Z"',
    'Landing strip': '<rect x="29.5" y="26" width="5" height="21" rx="2"',
    'Triangle': '<path d="M22,26 L42,26 L32,48 Z"',
    'Natural': '<path d="M18,24 Q32,20 46,24 Q44,34 36,48 L28,48 Q20,34 18,24 Z"',
    'Shaved': '',
  };
  function pubic(shape, opacity) {
    const region = PUBIC[shape];
    return svg(PELVIS + (region ? `${region} fill="${A}" fill-opacity="${opacity}" stroke="${A}" stroke-width="1.2"/>`
                                : ln('M22,26 L42,26 L32,48 Z', ` stroke="${A}" stroke-dasharray="2 2"`)));
  }

  // ── Table ─────────────────────────────────────────────────────────────────
  const map = (obj, fn) => Object.fromEntries(Object.entries(obj).map(([k, v]) => [k, () => fn(v, k)]));
  const TABLE = {
    face_shape: map(FACE_SHAPE, o => head(o, 'all')),
    jaw: map(JAW, o => head(o, 'jaw')),
    cheekbones: map(CHEEK, ([c, y]) => head({c, cheekY: y}, '',
      hl(`M${32 - c + 3},${y - 1} q5,-3 9,-1 M${32 + c - 3},${y - 1} q-5,-3 -9,-1`))),
    eye_shape: map(EYE, (v, k) => eye(k)),
    eye_colour: map(EYE_COLOURS, hex => eye('Almond', hex)),
    brows: map(BROW, ([d, w]) => svg(hl(d, w) + ln('M16,50 Q32,42 48,50', ' stroke-opacity=".55"'))),
    nose: map(NOSE, d => svg(hl(d))),
    lips: map(LIPS, a => lips.apply(null, a)),
    ears: map(EAR, ([o, h]) => ears(o, h)),
    hair_texture: map(HAIR, d => svg(`<ellipse cx="32" cy="28" rx="11" ry="14" stroke-opacity=".6"/>` + hl(d, 2))),
    hairline: map(HAIRLINE, d => svg(`<ellipse cx="32" cy="36" rx="16" ry="22"/>` + hl(d))),
    marks: map(MARKS, extra => head({}, '', extra)),
    height: map(HEIGHT, height),
    build: map(BUILD, o => body(o, 'all')),
    shoulders: map({Narrow: 9, Medium: 11, Broad: 14}, sh => body({sh}, 'shoulders')),
    waist: map({Defined: 7, Straight: 10.5, Soft: 11.5}, wa => body({wa}, 'waist')),
    hips: map({Narrow: 10, Medium: 12, Wide: 16}, hi => body({hi}, 'hips')),
    bust: map({Small: 9.5, Medium: 11, Large: 13, 'Very large': 15}, bu => body({bu}, 'bust')),
    glutes: map({Flat: 1.5, Round: 4.5, Full: 7, 'Very full': 9.5}, side),
    thighs: map({Slim: 10, Toned: 11.5, Full: 14}, th => body({th}, 'thighs')),
    tattoos: map(MARKERS.tattoos, marked), piercings: map(MARKERS.piercings, marked),
    birthmarks: map(MARKERS.birthmarks, marked),
    nails: map(NAILS, ([len, paint]) => nails(len, paint)),
    cup: map(CUP, r => breasts({r})),
    breast_shape: map(SHAPE, o => breasts(o)),
    spacing: map(SPACING, gap => breasts({gap})),
    augmented: map({Natural: false, Augmented: true}, aug => breasts({aug})),
    perkiness: map(PERK, o => breasts(o)),
    areola_size: map({Small: 8, Medium: 12, Large: 17}, r => areola(r, 3, true)),
    nipple_size: map({Small: 2.5, Medium: 4, Large: 5.5}, r => areola(12, r, false)),
    nipple_shape: map(NIP_SHAPE, d => svg(ln('M10,8 L10,56', ' stroke-opacity=".4"') + hl(d))),
    nipple_piercing: map({None: 0, One: 1, Both: 2}, pair),
    pubic_style: map(PUBIC, (v, k) => pubic(k, 0.35)),
    pubic_density: map({Sparse: 0.15, Medium: 0.4, Dense: 0.75}, o => pubic('Natural', o)),
  };
  Object.entries(COLOURS).forEach(([key, set]) => { TABLE[key] = map(set, swatch); });

  function render(feature, option, sheet) {
    if (feature === 'pubic_colour') {
      if (option === 'Dark') return swatch('#2a1d17');
      if (option === 'Light') return swatch('#c9a36c');
      if (option === 'Matches hair') {
        const hair = COLOURS.hair_colour[(sheet || {}).hair_colour];
        return hair ? swatch(hair)
          : svg(`<circle cx="32" cy="32" r="20" stroke-dasharray="3 3"/>` + ln('M22,32 h20'));
      }
    }
    const fn = (TABLE[feature] || {})[option];
    return fn ? fn() : '';
  }

  const api = {render, TEXT_ONLY: ['labia', 'labia_fullness']};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CharacterVisuals = api;
})(this);
