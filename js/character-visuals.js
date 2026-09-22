/* Option drawings for the character builder.
 *
 * One-colour outline icons: an even 2px stroke in currentColor, round caps, no
 * fills except where the option is a colour or a paint. What an option changes
 * shows in the shape itself; the picked card carries the accent.
 *
 * Parametric on purpose: a few base figures (a head with neck and ears, a front
 * and a side body, a torso, a pelvis) and each option moves one parameter, so
 * ~110 options stay in one small file and in one hand. Option labels must match
 * characters.FEATURES exactly; test_characters.py checks every one renders.
 */
(function (root) {
  function svg(inner) {
    return '<svg viewBox="0 0 64 64" width="64" height="64" fill="none" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + inner + '</svg>';
  }
  const f = n => Math.round(n * 10) / 10;
  const P = p => f(p[0]) + ',' + f(p[1]);
  const line = (d, extra) => `<path d="${d}"${extra || ''}/>`;
  const ring = (x, y, r) => `<circle cx="${f(x)}" cy="${f(y)}" r="${r}"/>`;
  const spot = (x, y, r) => `<circle cx="${f(x)}" cy="${f(y)}" r="${r || 1.2}" fill="currentColor" stroke="none"/>`;
  // The dashed shape a type is named after, in the accent, with a dot on each
  // corner or extreme like the charts creators already know.
  const guide = (d, dots) => `<path d="${d}" stroke="var(--accent)" stroke-width="1.4" stroke-dasharray="2.5 2.5"/>` +
    (dots || []).map(p => `<circle cx="${p[0]}" cy="${p[1]}" r="1.4" fill="var(--accent)" stroke="none"/>`).join('');
  const swatch = hex => svg(`<circle cx="32" cy="32" r="20" fill="${hex}" stroke-width="1.4"/>`);

  // Catmull-Rom through the points: smooth contours from a handful of numbers.
  function smooth(pts) {
    const n = pts.length;
    let d = 'M' + P(pts[0]);
    for (let i = 0; i < n; i++) {
      const p0 = pts[(i - 1 + n) % n], p1 = pts[i], p2 = pts[(i + 1) % n], p3 = pts[(i + 2) % n];
      d += 'C' + P([p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6]) + ' ' +
        P([p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6]) + ' ' + P(p2);
    }
    return d + 'Z';
  }

  // ── Head: outline, ears, neck and a quiet face ─────────────────────────────
  const HEAD = {top: 5, fw: 11, templeY: 13, c: 12.5, cheekY: 25, j: 9.5, jawY: 37, cw: 3, chinY: 45};
  function head(o, opts) {
    o = Object.assign({}, HEAD, o || {});
    opts = opts || {};
    const pts = [[32, o.top], [32 + o.fw, o.templeY], [32 + o.c, o.cheekY], [32 + o.j, o.jawY], [32 + o.cw, o.chinY],
      [32 - o.cw, o.chinY], [32 - o.j, o.jawY], [32 - o.c, o.cheekY], [32 - o.fw, o.templeY]];
    const nx = (o.j + o.cw) / 2 * 0.8, ny = (o.jawY + o.chinY) / 2 + 1;
    let s = line(smooth(pts));
    if (!opts.noEars) {
      const ex = 32 + o.c - 0.6, ey = o.cheekY - 4;
      s += line(`M${f(ex)},${f(ey)} q3.2,-0.6 3.2,3.6 q0,4.2 -3,4.6 M${f(64 - ex)},${f(ey)} q-3.2,-0.6 -3.2,3.6 q0,4.2 3,4.6`);
    }
    s += line(`M${f(32 - nx)},${f(ny)} L${f(32 - nx - 0.5)},56 Q${f(32 - nx - 5)},59 ${f(32 - nx - 14)},61 ` +
      `M${f(32 + nx)},${f(ny)} L${f(32 + nx + 0.5)},56 Q${f(32 + nx + 5)},59 ${f(32 + nx + 14)},61`);
    if (!opts.blank) {
      const ey = o.cheekY - 2;
      s += line(`M22.5,${ey - 4.5} q3,-1.8 6,-0.4 M41.5,${ey - 4.5} q-3,-1.8 -6,-0.4`) +
        line(`M23,${ey} q3,-2.2 6,0 q-3,2.2 -6,0 M35,${ey} q3,-2.2 6,0 q-3,2.2 -6,0`, ' stroke-width="1.6"') +
        spot(26, ey, 1.1) + spot(38, ey, 1.1) +
        line(`M32.5,${ey + 2} l-1.6,6.5 h2.6`, ' stroke-width="1.6"') +
        line(`M28,${f(o.chinY - 9)} q4,2.2 8,0`);
    }
    return s + (opts.extra || '');
  }

  const FACE_SHAPE = {   // head parameters, then the guide and its dots
    'Oval': [{}, 'M32,3 A14,21 0 1 1 31.9,3 Z', [[32, 3], [46, 24], [32, 45], [18, 24]]],
    'Heart-shaped': [{fw: 13, c: 12.5, j: 8, cw: 1.5}, 'M32,10 C28,3 16,4 16,14 C16,25 27,34 32,47 C37,34 48,25 48,14 C48,4 36,3 32,10 Z',
      [[19, 6], [45, 6], [32, 47]]],
    'Round': [{fw: 12, c: 14.5, j: 12, cw: 5, chinY: 43, top: 6}, 'M32,7 A17,17 0 1 1 31.9,7 Z', [[32, 7], [49, 24], [32, 41], [15, 24]]],
    'Square': [{fw: 12, c: 12.5, j: 12.5, cw: 6.5, jawY: 39}, 'M17,6 H47 V44 H17 Z', [[17, 6], [47, 6], [47, 44], [17, 44]]],
    'Diamond': [{fw: 8.5, c: 14.5, j: 8.5, cw: 2}, 'M32,3 L48,25 L32,47 L16,25 Z', [[32, 3], [48, 25], [32, 47], [16, 25]]],
    'Long': [{fw: 10, c: 10.5, j: 8.5, cw: 3.5, top: 2, chinY: 48}, 'M28,1 H36 Q44,1 44,9 V42 Q44,50 36,50 H28 Q20,50 20,42 V9 Q20,1 28,1 Z',
      [[32, 1], [44, 25], [32, 50], [20, 25]]],
    'Triangle': [{fw: 7, templeY: 15, c: 11, j: 14.5, jawY: 40, cw: 7.5, chinY: 45}, 'M32,4 L47,45 L17,45 Z', [[32, 4], [47, 45], [17, 45]]],
  };
  // A face shape reads best with hair framing it, as in the usual charts: a
  // cap from temple to temple with a centre part.
  function faceShape([o, g, dots]) {
    const h = Object.assign({}, HEAD, o);
    const cap = line(`M${f(32 - h.fw - 1.5)},${h.templeY + 7} Q${f(32 - h.fw - 3)},${h.top - 3} 32,${h.top - 2.5} ` +
      `Q${f(32 + h.fw + 3)},${h.top - 3} ${f(32 + h.fw + 1.5)},${h.templeY + 7}`) +
      line(`M${f(32 - h.fw + 0.5)},${h.templeY + 4} Q${f(32 - h.fw / 2)},${h.top + 1} 32,${h.top + 3} ` +
      `Q${f(32 + h.fw / 2)},${h.top + 1} ${f(32 + h.fw - 0.5)},${h.templeY + 4} M32,${h.top - 2.5} Q31.4,${h.top} 32,${h.top + 3}`, ' stroke-width="1.4"');
    return svg(head(o) + cap + guide(g, dots));
  }
  const JAW = {
    'Soft, rounded chin': {j: 9.5, cw: 4.5, chinY: 44}, 'Defined jaw': {j: 11.5, jawY: 36, cw: 3.5},
    'Pointed chin': {j: 8, cw: 1, chinY: 47}, 'Square jaw': {j: 12.5, jawY: 40, cw: 6.5},
  };
  const CHEEK = {High: [13.5, 22], Medium: [12.5, 25], Soft: [12, 28]};

  // ── Eye close-up: lids, crease, iris, pupil and outer lashes ───────────────
  const EYE = {   // upper [x0,y0,cx,cy,x1,y1], lower control y, crease lift (0 = none)
    'Almond': [[8, 34, 32, 16, 56, 32], 46, 7], 'Round': [[10, 35, 32, 11, 54, 34], 52, 7],
    'Hooded': [[8, 34, 32, 22, 56, 32], 46, 2.5], 'Monolid': [[8, 34, 32, 25, 56, 33], 42, 0],
    'Upturned': [[8, 38, 30, 18, 56, 27], 48, 7], 'Downturned': [[8, 28, 34, 18, 56, 38], 48, 7],
  };
  function eye(shape, iris) {
    const [[x0, y0, cx, cy, x1, y1], loY, crease] = EYE[shape] || EYE.Almond;
    const q = t => [(1 - t) * (1 - t) * x0 + 2 * (1 - t) * t * cx + t * t * x1, (1 - t) * (1 - t) * y0 + 2 * (1 - t) * t * cy + t * t * y1];
    const midY = q(0.5)[1], lowMid = (y0 + y1) / 4 + loY / 2;
    const ir = Math.min(9, (lowMid - midY) / 2 + 1.5), icy = (midY + lowMid) / 2;
    let s = (iris ? `<circle cx="32" cy="${f(icy)}" r="${f(ir)}" fill="${iris}" stroke="none"/>` : '') +
      ring(32, icy, f(ir)) + spot(32, icy, f(ir * 0.38)) +
      line(`M${x0},${y0} Q${cx},${cy} ${x1},${y1}`, ' stroke-width="2.6"') +
      line(`M${x0},${y0} Q${32},${loY} ${x1},${y1}`);
    if (crease) s += line(`M${x0 + 5},${f(y0 - crease * 0.5)} Q${cx},${f(cy - crease)} ${x1 - 3},${f(y1 - crease * 0.7)}`, ' stroke-width="1.6"');
    [0.74, 0.85, 0.96].forEach(t => {
      const [px, py] = q(t);
      const dx = 2 * (1 - t) * (cx - x0) + 2 * t * (x1 - cx), dy = 2 * (1 - t) * (cy - y0) + 2 * t * (y1 - cy);
      const len = Math.hypot(dx, dy) || 1, nx = dy / len, ny = -dx / len;
      s += line(`M${f(px)},${f(py)} l${f(nx * 5 + dx / len * 1.5)},${f(ny * 5 + dy / len * 1.5)}`, ' stroke-width="1.6"');
    });
    return svg(s);
  }

  const BROW = {   // curve [x0,y0,cx,cy,x1,y1], thickness
    'Soft arch, medium': [[10, 34, 30, 22, 54, 30], 3.5], 'High arch, thin': [[10, 36, 28, 16, 54, 32], 2],
    'Straight, full': [[10, 31, 32, 27, 54, 31], 5], 'Bold, thick': [[10, 35, 30, 22, 54, 31], 7],
  };
  function brow([x0, y0, cx, cy, x1, y1], t) {
    const b = t < 2.5 ? line(`M${x0},${y0} Q${cx},${cy} ${x1},${y1}`) :
      line(`M${x0},${y0} Q${cx},${cy} ${x1},${y1} Q${cx},${cy + t * 1.6} ${x0 + 2},${y0 + t * 0.7} Z`);
    return svg(b + line('M18,50 Q32,42 46,50 Q32,56 18,50', ' stroke-width="1.6"'));
  }
  const NOSE = {
    'Straight, narrow': 'M22,6 L36,40 Q41,46 34,48 L27,48',
    'Button': 'M22,6 Q29,28 32,36 Q43,40 35,48 L27,48',
    'Slightly upturned': 'M22,6 L33,35 Q43,35 37,46 L27,48',
    'Roman': 'M22,6 Q38,16 36,40 Q39,46 33,48 L27,48',
    'Wide': 'M22,6 L34,38 Q46,44 37,50 L25,50',
  };
  const nose = d => svg(line(d) + line('M30,44 q3,1.5 5,0', ' stroke-width="1.6"') +
    line('M22,50 Q24,54 28,56', ' stroke-width="1.6"'));
  const LIPS = {'Full, defined bow': [20, 9, 10, 4], 'Medium': [19, 6, 8, 2], 'Thin': [19, 3, 4, 1], 'Very full': [21, 12, 14, 3]};
  function lips(w, up, lo, bow) {
    const L = 32 - w, R = 32 + w;
    return svg(line(`M${L},32 C${L + w * 0.4},${32 - up} 26,${32 - up - 1} 32,${32 - up + bow} ` +
      `C38,${32 - up - 1} ${R - w * 0.4},${32 - up} ${R},32`) +
      line(`M${L},32 C${L + w * 0.3},${32 + lo * 1.25} ${R - w * 0.3},${32 + lo * 1.25} ${R},32`) +
      line(`M${L + 1},32 Q26,${32 + Math.min(2, up / 3)} 32,${32 + Math.min(1.2, up / 4)} Q38,${32 + Math.min(2, up / 3)} ${R - 1},32`));
  }
  const EAR = {'Small, close-set': [2.2, 6], 'Medium': [3.6, 7.5], 'Prominent': [6.5, 8.5]};
  function ears(out, h) {
    return svg(head({}, {noEars: true, blank: true}) +
      line(`M${f(19.7)},${f(21 - h / 2)} Q${f(19.7 - out)},${f(21 - h / 2)} ${f(19.7 - out)},21 Q${f(19.7 - out * 0.8)},${f(21 + h)} 20,${f(21 + h * 0.8)}`) +
      line(`M${f(44.3)},${f(21 - h / 2)} Q${f(44.3 + out)},${f(21 - h / 2)} ${f(44.3 + out)},21 Q${f(44.3 + out * 0.8)},${f(21 + h)} 44,${f(21 + h * 0.8)}`));
  }

  const HAIR = {
    'Long, straight': 'M20,24 Q19,4 32,4 Q45,4 44,24 L46,60 M20,24 L18,60',
    'Long, loose waves': 'M20,24 Q19,4 32,4 Q45,4 44,24 q4,6 0,12 q-4,6 0,12 q4,6 1,10 M20,24 q-4,6 0,12 q4,6 0,12 q-4,6 -1,10',
    'Long, curly': 'M20,22 Q20,3 32,3 Q44,3 44,22 a3,3 0 1 1 2,6 a3,3 0 1 1 0,7 a3,3 0 1 1 -1,7 a3,3 0 1 1 0,7 ' +
      'M20,22 a3,3 0 1 0 -2,6 a3,3 0 1 0 0,7 a3,3 0 1 0 1,7 a3,3 0 1 0 0,7',
    'Shoulder-length': 'M20,24 Q19,4 32,4 Q45,4 44,24 L45,44 q1,3 4,3 M20,24 L19,44 q-1,3 -4,3',
    'Bob': 'M20,26 Q18,4 32,4 Q46,4 44,26 L45,38 L39,38 M20,26 L19,38 L25,38',
    'Pixie': 'M21,24 Q18,5 32,5 Q46,5 43,24 M21,18 q6,-6 14,-4 q6,1 8,6',
  };
  const hairOver = d => svg(head({top: 7, fw: 10, c: 11.5, j: 8.5}, {noEars: true}) + line(d));
  const HAIRLINE = {
    'Rounded, middle part': 'M21,17 Q22,7 32,7 Q42,7 43,17 M32,7 L32,3',
    'Side part': 'M21,17 Q22,7 32,7 Q42,7 43,17 M27,7 L26,3 M27,7 Q37,8 43,14',
    'Straight, fringe': 'M21,14 L43,14 M24,14 l0,-6 M29,14 l0,-7 M34,14 l0,-7 M39,14 l0,-6',
    "Widow's peak": 'M21,17 Q22,8 28,8 L32,12 L36,8 Q42,8 43,17',
  };
  const FRECKLES = (list, r) => list.map(p => spot(p[0], p[1], r || 0.8)).join('');
  const MARKS = {
    'None': '', 'Light freckles, nose': FRECKLES([[29, 27], [35, 27], [27.5, 29.5], [36.5, 29.5], [31, 25.5], [33.5, 25.5]]),
    'Heavy freckles': FRECKLES([[23, 27], [25.5, 29.5], [27.5, 26.5], [21.5, 30], [24.5, 32], [29, 28.5], [41, 27], [38.5, 29.5],
      [36.5, 26.5], [42.5, 30], [39.5, 32], [35, 28.5], [31, 26], [33.5, 29], [24, 24.5], [40, 24.5]]),
    'Beauty mark, cheek': spot(40, 30, 1.4), 'Beauty mark, lip': spot(36.5, 34, 1.3),
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

  // ── Front figure, arms at the sides ────────────────────────────────────────
  const BODY = {sh: 10.5, bu: 10.5, wa: 8, hi: 11.5, th: 11};
  function bodyPts(o) {
    const R = [[34.5, 13], [32 + o.sh, 17], [32 + (o.sh + o.bu) / 2 - 1.5, 22], [32 + o.bu, 26], [32 + o.wa, 34], [32 + o.hi, 41],
      [32 + o.th, 48], [39, 55], [37, 62], [34, 62], [34.3, 55], [32, 47]];
    return R.concat(R.slice(0, 11).reverse().map(p => [64 - p[0], p[1]]));
  }
  function figure(o, extra) {
    o = Object.assign({}, BODY, o || {});
    const w = Math.max(o.hi, o.bu, o.sh) + 2.5;
    const arm = side => {
      const s = x => 32 + side * x;
      return `M${f(s(o.sh))},17 Q${f(s(o.sh + 3))},22 ${f(s(w + 1))},32 L${f(s(w + 1.5))},42 ` +
        `M${f(s(o.sh - 0.5))},22 Q${f(s(w - 1))},30 ${f(s(w - 1))},41 M${f(s(w - 1))},41 q${side * 1.2},2.5 ${side * 2.5},1`;
    };
    return line(smooth(bodyPts(o))) + ring(32, 6.5, 4.8) + line(arm(1)) + line(arm(-1)) + (extra || '');
  }
  const body = (o, extra) => svg(figure(o, extra));
  const BUILD = {
    'Slim': {sh: 9.5, bu: 9.5, wa: 7, hi: 10, th: 9.5}, 'Athletic': {sh: 12, bu: 10.5, wa: 8.5, hi: 10.5, th: 11},
    'Average': {}, 'Curvy': {sh: 10.5, bu: 12.5, wa: 7.5, hi: 14.5, th: 12.5},
    'Voluptuous': {sh: 11.5, bu: 14.5, wa: 9.5, hi: 15.5, th: 13.5}, 'Muscular': {sh: 13.5, bu: 11.5, wa: 9.5, hi: 11.5, th: 12.5},
  };
  const HEIGHT = {'Under 155 cm': 0.8, '155–165 cm': 0.87, '165–175 cm': 0.94, 'Over 175 cm': 1};
  function height(s) {
    const top = f(63 - 61 * s);
    return svg(`<g transform="translate(32,63) scale(${s}) translate(-32,-63)">${figure({})}</g>` +
      line(`M58,62 L58,${top} M55.5,${top} L60.5,${top} M55.5,62 L60.5,62`, ' stroke-width="1.6"'));
  }
  const MARKERS = {
    tattoos: {'None': '', 'Small, wrist': [[46.5, 39]], 'Small, ankle': [[37.5, 58.5]], 'Hip': [[41, 40]],
      'Sleeve': 'sleeve', 'Back piece': 'back'},
    piercings: {'None': '', 'Ears': [[27, 7], [37, 7]], 'Nose': [[32, 8]], 'Navel': [[32, 36]]},
    birthmarks: {'None': '', 'Shoulder': [[40, 18.5]], 'Hip': [[40.5, 41]]},
  };
  function marked(spec) {
    let extra = '';
    if (spec === 'sleeve') extra = line('M43.5,19 l2,-1 M45,23 l2.5,-1 M46,27.5 l2.5,-0.8 M46.5,32 l2.5,-0.6 M47,36.5 l2.5,-0.4', ' stroke-width="1.6"');
    else if (spec === 'back') extra = `<rect x="26" y="17" width="12" height="15" rx="3" stroke-width="1.6" stroke-dasharray="2 2.4"/>`;
    else if (Array.isArray(spec)) extra = spec.map(p => ring(p[0], p[1], 1.9)).join('');
    return body({}, extra);
  }
  function sideFigure(p) {
    const pts = [[28, 13], [25, 21], [26.5, 32], [26, 41], [28, 51], [29, 62], [33, 62], [34, 55], [35, 48], [34 + p, 41],
      [33, 33], [35, 21], [33, 13]];
    return svg(ring(30, 6.5, 4.8) + line(smooth(pts)) + line('M30.5,18 Q29,28 30.5,40 q0.5,2 -1,2.5'));
  }
  const NAILS = {'Short, nude': [2, 'line'], 'Medium, painted': [5, 'paint'], 'Long, painted': [9, 'paint'], 'French tips': [5, 'tip']};
  function nails(len, kind) {
    let s = line('M12,64 L12,46 Q12,40 16,38');   // back of the hand
    [[18, 30], [30, 26], [42, 28], [54, 34]].forEach(([x, y]) => {
      s += line(`M${x - 5},64 L${x - 5},${y + 4} Q${x - 5},${y - 2} ${x},${y - 2} Q${x + 5},${y - 2} ${x + 5},${y + 4} L${x + 5},64`);
      const tip = y - 2 - len, nail = `M${x - 3},${y + 5} L${x - 3},${tip + 3} Q${x},${tip - 1} ${x + 3},${tip + 3} L${x + 3},${y + 5} Z`;
      s += kind === 'paint' ? `<path d="${nail}" fill="currentColor" stroke-width="1.4"/>` : line(nail, ' stroke-width="1.4"');
      if (kind === 'tip') s += `<path d="M${x - 3},${tip + 4} Q${x},${tip - 1} ${x + 3},${tip + 4} Z" fill="currentColor" stroke-width="1"/>`;
    });
    return svg(s);
  }

  // ── Body shape: the usual chart figure ─────────────────────────────────────
  // No head, open contour lines and a single line between the legs, with the
  // type as a soft filled shape on the torso. Apple stands with arms down,
  // the rest with hands on hips.
  const BODY_SHAPE = {   // shoulder, waist, hip, thigh, arms down; filled type
    'Apple': [[12, 13.5, 13, 11.5, true], 'M32,13 C29.5,10.5 24,11 24,17 C24,23 27.5,26 32,26 C36.5,26 40,23 40,17 C40,11 34.5,10.5 32,13 Z'],
    'Pear': [[10, 9, 14.5, 13], 'M31,10 C28,10 29.2,15.5 27,18.5 C23.6,22.5 25,27 29,27 C34.5,27 36.3,23 34.6,18.3 C33.8,14.5 34.2,10 31,10 Z'],
    'Hourglass': [[12, 7.5, 12.5, 11], 'M27.5,9 H36.5 V11 Q36.5,14 33,18 Q36.5,22 36.5,25 V27 H27.5 V25 Q27.5,22 31,18 Q27.5,14 27.5,11 Z'],
    'Rectangle': [[11.5, 10.5, 11, 10], 'M29,9 H35 V27 H29 Z'],
    'Oval': [[11.5, 12, 12, 10.5], 'M32,10 C36.8,10 39,13 39,17.5 C39,22.5 36,26 32,26 C28,26 25,22.5 25,17.5 C25,13 27.2,10 32,10 Z'],
    'Inverted triangle': [[14, 9.5, 9.5, 9.5], 'M25,8.5 H39 L32,27 Z'],
  };
  function bodyShape([[sh, wa, hi, th, armsDown], fill]) {
    const side = k => {
      const x = v => f(32 + k * v);
      let d = `M${x(2)},0 Q${x(2.5)},3 ${x(sh)},4.5 Q${x(sh + 3)},6 ${x(sh + 3.5)},10 `;
      d += armsDown
        ? `Q${x(sh + 4.5)},18 ${x(sh + 5)},27 M${x(sh)},11 Q${x(sh + 1.5)},18 ${x(sh + 2.5)},26 `
        : `L${x(sh + 7)},16 L${x(wa + 1.5)},19.5 M${x(sh + 0.5)},11 L${x(sh + 3.8)},16 L${x(wa + 0.8)},18.5 `;
      d += `M${x(sh - 0.5)},11.5 C${x(sh - 1)},15 ${x(wa)},16 ${x(wa)},19.5 C${x(wa)},22 ${x(hi)},23 ${x(hi)},26 ` +
        `C${x(hi)},29.5 ${x(th)},31 ${x(th - 0.5)},35 C${x(th - 1)},39 ${x(9)},41 ${x(8.6)},46 C${x(8.2)},51 ${x(8)},55 ${x(6.5)},63 ` +
        `M${x(3.4)},44 Q${x(3.9)},53 ${x(3)},62`;
      return d;
    };
    return svg(`<path d="${fill}" fill="var(--accent)" fill-opacity=".22" stroke="none"/>` +
      line(side(1) + ' ' + side(-1) + ' M32,31 V63', ' stroke-width="1.4"'));
  }

  // ── Bum shape from behind ──────────────────────────────────────────────────
  const GLUTE_SHAPE = {   // waist, hip peak, peak height, low hip, thigh; guide; dots
    'Round': [[12, 18, 32, 17, 14], 'M32,17 A16,16 0 1 1 31.9,17 Z', [[32, 17], [48, 33], [32, 49], [16, 33]]],
    'Heart-shaped': [[10, 18.5, 37, 17.5, 14], 'M32,6 L14,33 C11,44 22,50 32,44 C42,50 53,44 50,33 Z', [[32, 6], [14, 33], [50, 33]]],
    'A-shaped': [[11, 15, 29, 19, 15], 'M24,12 H40 L51,46 H13 Z', [[24, 12], [40, 12], [51, 46], [13, 46]]],
    'Square': [[16, 17, 26, 17, 14.5], 'M15,14 H49 V46 H15 Z', [[15, 14], [49, 14], [49, 46], [15, 46]]],
    'V-shaped': [[16, 16.5, 22, 13, 11], 'M14,17 H50 L32,46 Z', [[14, 17], [50, 17], [32, 46]]],
  };
  function gluteShape([[wa, hp, hy, lh, th], g, dots]) {
    const sideLine = side => {
      const s = x => f(32 + side * x);
      return `M${s(wa)},3 C${s(wa)},${hy - 12} ${s(hp)},${hy - 8} ${s(hp)},${hy} C${s(hp)},${hy + 8} ${s(lh)},40 ${s(lh)},45 ` +
        `C${s(lh)},51 ${s(th)},56 ${s(th - 1)},63`;
    };
    return svg(line(sideLine(1) + ' ' + sideLine(-1)) +
      line('M32,7 v4 M32,24 Q32.6,35 32,44 M31,47 L30.3,63 M33,47 L33.7,63', ' stroke-width="1.6"') +
      line(`M31,45 Q24,49 ${f(32 - lh + 3)},45 M33,45 Q40,49 ${f(32 + lh - 3)},45`, ' stroke-width="1.6"') + guide(g, dots));
  }

  // ── Torso for the schematic NSFW views ─────────────────────────────────────
  const TORSO = line('M26,2 L26,8 Q16,9 11,14 Q7,19 7,30 L7,62 M38,2 L38,8 Q48,9 53,14 Q57,19 57,30 L57,62 ' +
    'M13,22 Q11,40 16,62 M51,22 Q53,40 48,62') + line('M25,12 Q20,11.5 16,14 M39,12 Q44,11.5 48,14', ' stroke-width="1.6"');
  const CUP = {A: 5, B: 6, C: 7, D: 8, DD: 9, 'E+': 10};
  const BREAST = {r: 7, gap: 10.5, cy: 30, w: 1, h: 0.9, aug: false, drop: 0};
  function breasts(o) {
    o = Object.assign({}, BREAST, o || {});
    let s = TORSO;
    [-1, 1].forEach(side => {
      const cx = 32 + side * o.gap, rw = o.r * o.w, rh = o.r * o.h, cy = o.cy + o.drop;
      s += line(`M${f(cx - rw)},${cy} A${f(rw)},${f(rh)} 0 0 0 ${f(cx + rw)},${cy}`);
      const inner = cx - side * rw;
      s += line(`M${f(inner + side * 1.5)},${f(cy - o.r * 0.9)} Q${f(inner - side * 0.3)},${f(cy - o.r * 0.3)} ${f(inner)},${cy}`, ' stroke-width="1.6"');
      if (o.aug) s += line(`M${f(cx - rw)},${cy} A${f(rw)},${f(rw * 0.75)} 0 0 1 ${f(cx + rw)},${cy}`, ' stroke-width="1.6"');
      s += ring(cx + side * rw * 0.12, cy + rh * 0.38, 1.3);
    });
    return svg(s);
  }
  const SHAPE = {'Round': {}, 'Teardrop': {h: 1.15, drop: 1}, 'Athletic': {w: 1.1, h: 0.55, r: 6.5}, 'Relaxed': {w: 1.1, h: 1.2, drop: 4}};
  const SPACING = {Close: 8.5, Average: 10.5, Wide: 12.5};
  const PERK = {Perky: {cy: 27, h: 0.8}, Natural: {}, Soft: {cy: 31, h: 1.1, drop: 2}};
  const areola = (ar, nr) => svg(ring(32, 32, ar) + ring(32, 32, nr));
  const NIP_SHAPE = {
    Flat: 'M10,8 Q46,12 50,32 Q46,52 10,56',
    Puffy: 'M10,8 Q44,12 46,24 Q56,26 56,32 Q56,38 46,40 Q44,52 10,56',
    Protruding: 'M10,8 Q46,12 50,28 L56,28 Q58,32 56,36 L50,36 Q46,52 10,56',
    Inverted: 'M10,8 Q46,12 50,28 Q46,32 50,36 Q46,52 10,56',
  };
  function pair(n) {
    let s = '';
    [18, 46].forEach((cx, i) => {
      s += ring(cx, 32, 8) + ring(cx, 32, 2.4);
      if (n === 2 || (n === 1 && i === 1)) s += line(`M${cx - 7},32 L${cx + 7},32`, ' stroke-width="1.6"') + ring(cx - 7.5, 32, 1.6) + ring(cx + 7.5, 32, 1.6);
    });
    return svg(s);
  }

  // ── Pelvis with hair drawn as short strokes ────────────────────────────────
  const PELVIS = line('M10,4 Q13,30 24,48 Q32,58 40,48 Q51,30 54,4') +
    line('M17,17 Q24,33 29,48 M47,17 Q40,33 35,48', ' stroke-width="1.6"');
  const PUBIC = {
    'Trimmed': [[24, 28], [40, 28], [33, 45], [31, 45]], 'Landing strip': [[30, 26], [34, 26], [34, 45], [30, 45]],
    'Triangle': [[22, 26], [42, 26], [32, 46]], 'Natural': [[18, 24], [46, 24], [38, 46], [26, 46]], 'Shaved': null,
  };
  function inside(pts, x, y) {
    let c = false;
    for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
      if (((pts[i][1] > y) !== (pts[j][1] > y)) && x < (pts[j][0] - pts[i][0]) * (y - pts[i][1]) / (pts[j][1] - pts[i][1]) + pts[i][0]) c = !c;
    }
    return c;
  }
  function pubic(shape, gap) {
    const poly = PUBIC[shape];
    if (!poly) return svg(PELVIS + line('M22,26 L42,26 L32,46 Z', ' stroke-width="1.4" stroke-dasharray="2 2.6"'));
    const xs = poly.map(p => p[0]), ys = poly.map(p => p[1]);
    let hairs = '';
    for (let r = 0, y = Math.min(...ys) + 1.5; y < Math.max(...ys) - 1; r++, y += gap * 0.8) {
      for (let x = Math.min(...xs) + 1 + (r % 2) * gap / 2; x < Math.max(...xs); x += gap) {
        if (inside(poly, x, y) && inside(poly, x + 0.8, y + 2.2)) hairs += `M${f(x)},${f(y)} l0.8,2.2 `;
      }
    }
    return svg(PELVIS + line('M' + poly.map(P).join(' L') + ' Z', ' stroke-width="1.2" stroke-opacity=".55"') +
      line(hairs, ' stroke-width="1.3"'));
  }

  // ── Table ─────────────────────────────────────────────────────────────────
  const map = (obj, fn) => Object.fromEntries(Object.entries(obj).map(([k, v]) => [k, () => fn(v, k)]));
  const TABLE = {
    face_shape: map(FACE_SHAPE, faceShape),
    body_shape: map(BODY_SHAPE, bodyShape),
    glute_shape: map(GLUTE_SHAPE, gluteShape),
    jaw: map(JAW, o => svg(head(o))),
    cheekbones: map(CHEEK, ([c, y]) => svg(head({c, cheekY: y},
      {extra: line(`M${f(32 - c + 2)},${y + 1} q3,-2 6.5,-1 M${f(32 + c - 2)},${y + 1} q-3,-2 -6.5,-1`, ' stroke-width="1.6"')}))),
    eye_shape: map(EYE, (v, k) => eye(k)),
    eye_colour: map(EYE_COLOURS, hex => eye('Almond', hex)),
    brows: map(BROW, ([c, t]) => brow(c, t)),
    nose: map(NOSE, nose),
    lips: map(LIPS, a => lips.apply(null, a)),
    ears: map(EAR, ([o, h]) => ears(o, h)),
    hair_texture: map(HAIR, hairOver),
    hairline: map(HAIRLINE, d => svg(head({top: 7, fw: 10, c: 11.5, j: 8.5}, {noEars: true}) + line(d, ' stroke-width="1.8"'))),
    marks: map(MARKS, extra => svg(head({}, {extra}))),
    height: map(HEIGHT, height),
    build: map(BUILD, o => body(o)),
    shoulders: map({Narrow: 8.5, Medium: 10.5, Broad: 13.5}, sh => body({sh})),
    waist: map({Defined: 6.5, Straight: 10, Soft: 11}, wa => body({wa})),
    hips: map({Narrow: 9.5, Medium: 11.5, Wide: 15.5}, hi => body({hi})),
    bust: map({Small: 9, Medium: 10.5, Large: 12.5, 'Very large': 14.5}, bu => body({bu})),
    glutes: map({Flat: 1.5, Round: 4.5, Full: 7, 'Very full': 9.5}, sideFigure),
    thighs: map({Slim: 9.5, Toned: 11, Full: 13.5}, th => body({th})),
    tattoos: map(MARKERS.tattoos, marked), piercings: map(MARKERS.piercings, marked),
    birthmarks: map(MARKERS.birthmarks, marked),
    nails: map(NAILS, ([len, kind]) => nails(len, kind)),
    cup: map(CUP, r => breasts({r})),
    breast_shape: map(SHAPE, o => breasts(o)),
    spacing: map(SPACING, gap => breasts({gap})),
    augmented: map({Natural: false, Augmented: true}, aug => breasts({aug})),
    perkiness: map(PERK, o => breasts(o)),
    areola_size: map({Small: 8, Medium: 12, Large: 17}, r => areola(r, 3)),
    nipple_size: map({Small: 2.5, Medium: 4, Large: 5.5}, r => areola(12, r)),
    nipple_shape: map(NIP_SHAPE, d => svg(line('M10,8 L10,56', ' stroke-width="1.4" stroke-dasharray="2 2.6"') + line(d))),
    nipple_piercing: map({None: 0, One: 1, Both: 2}, pair),
    pubic_style: map(PUBIC, (v, k) => pubic(k, 3.6)),
    pubic_density: map({Sparse: 5.2, Medium: 3.6, Dense: 2.6}, g => pubic('Natural', g)),
  };
  Object.entries(COLOURS).forEach(([key, set]) => { TABLE[key] = map(set, swatch); });

  function render(feature, option, sheet) {
    if (feature === 'pubic_colour') {
      if (option === 'Dark') return swatch('#2a1d17');
      if (option === 'Light') return swatch('#c9a36c');
      if (option === 'Matches hair') {
        const hair = COLOURS.hair_colour[(sheet || {}).hair_colour];
        return hair ? swatch(hair) : svg(`<circle cx="32" cy="32" r="20" stroke-width="1.4" stroke-dasharray="3 3"/>`);
      }
    }
    const fn = (TABLE[feature] || {})[option];
    return fn ? fn() : '';
  }

  const api = {render, TEXT_ONLY: ['labia', 'labia_fullness']};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CharacterVisuals = api;
})(this);
