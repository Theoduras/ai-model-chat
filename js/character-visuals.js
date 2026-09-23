/* Option drawings for the character builder.
 *
 * Thin slate sketch lines after the creator's reference charts, with what an
 * option changes marked in soft pink; colours and paints are swatches.
 *
 * Parametric on purpose: a few base figures (a head with hair, ears and neck, a
 * front and a side body, a torso, a pelvis) and each option moves one parameter, so
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
  // Every body drawing shares the creator's reference sketch: thin slate lines,
  // with the type or the marked spot in soft pink. The page can retune both for
  // its theme through the --fp-* variables; the hex after each is the default.
  const INK = 'var(--fp-ink, #6b6fa8)', PINK = 'var(--fp-mark, #f0a8c6)';
  const bsvg = inner => svg(`<g color="${INK}" stroke-width="1.3">` +
    inner.replace(/stroke-width="([\d.]+)"/g, (m, w) => `stroke-width="${f(w * 0.75)}"`) + '</g>');
  const swatch = hex => svg(`<circle cx="32" cy="32" r="20" fill="${hex}" stroke-width="1.4"/>`);

  // Catmull-Rom through the points: smooth contours from a handful of numbers.
  function seg(pts, i) {
    const n = pts.length, p0 = pts[(i - 1 + n) % n], p1 = pts[i], p2 = pts[(i + 1) % n], p3 = pts[(i + 2) % n];
    return [p1, [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6],
      [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6], p2];
  }
  // The closed curve, or only its run from one point to another.
  function smooth(pts, from, to) {
    const open = from !== undefined;
    let d = 'M' + P(pts[open ? from : 0]);
    for (let i = open ? from : 0; i < (open ? to : pts.length); i++) {
      const [, a, b, c] = seg(pts, i);
      d += 'C' + P(a) + ' ' + P(b) + ' ' + P(c);
    }
    return open ? d : d + 'Z';
  }

  // ── Head, after the creator's face-shapes chart ───────────────────────────
  // A full face that keeps its width down to the jaw and rounds into the chin,
  // a hair cap down to the ears, a wide neck with a shadow under the chin, and
  // the features as soft marks. Face shapes keep the chart's dusty pinks; every
  // other face option marks what it changes in the body's pink.
  const HAIR_PINK = 'var(--fp-hair, #e3cbc8)', FEATURE_PINK = 'var(--fp-feature, #d3a8a4)', GUIDE_GREY = 'var(--fp-guide, #aaa3a1)';
  const HEAD = {top: 9, fw: 12.5, c: 13.5, cheekY: 32, j: 12.3, jawY: 41, jl: 8, lowY: 48, chinY: 51.4};
  function headPts(o) {
    const r = [[o.fw, 18], [o.c, o.cheekY], [o.j, o.jawY], [o.jl, o.lowY]];
    return [[32, o.top], ...r.map(([x, y]) => [32 + x, y]), [32, o.chinY],
      ...r.slice().reverse().map(([x, y]) => [32 - x, y])];
  }
  // Where the jaw comes in to meet the neck, so a wide jaw never has the neck
  // lines running up inside the face.
  function neckTop(pts, half) {
    for (let i = 2; i < 5; i++) {
      const [a, b, c, d] = seg(pts, i);
      for (let t = 0; t <= 1; t += 0.05) {
        const u = 1 - t, at = k => u * u * u * a[k] + 3 * u * u * t * b[k] + 3 * u * t * t * c[k] + t * t * t * d[k];
        if (at(0) <= 32 + half) return [at(0) - 32, at(1)];
      }
    }
    return [half, pts[5][1]];
  }
  const CAP = [[-14.4, 31.8], [-16.3, 25], [-15.8, 16], [-12.3, 8], [-6.3, 4], [0, 3], [6.3, 4], [12.3, 8],
    [15.8, 16], [16.3, 25], [14.4, 31.8]];
  const HAIRLINE = {   // the cap's inner edge, right to left; the part, or the fringe
    'Rounded, middle part': [[[13.2, 29], [12.3, 23], [9.2, 16.5], [5.2, 12.2], [0, 10.5], [-5.2, 12.2], [-9.2, 16.5],
      [-12.3, 23], [-13.2, 29]], 0],
    'Side part': [[[13.2, 29], [12.4, 22], [9.5, 15.5], [3, 11.8], [-5, 10], [-8.6, 13.5], [-11.8, 21], [-13.2, 29]], -5],
    'Straight, fringe': [[[13.2, 29], [12.8, 20.5], [11.2, 17], [4, 16.6], [-4, 16.6], [-11.2, 17], [-12.8, 20.5],
      [-13.2, 29]], 'fringe'],
    "Widow's peak": [[[13.2, 29], [12.3, 23], [9.4, 15.8], [4.6, 11.6], [1.3, 13.2], [0, 14.8], [-1.3, 13.2], [-4.6, 11.6],
      [-9.4, 15.8], [-12.3, 23], [-13.2, 29]], 0],
  };
  const PIXIE = [[[-13.9, 24], [-15.3, 17], [-12, 8.5], [-6, 4.6], [0, 3.8], [6, 4.6], [12, 8.5], [15.3, 17], [13.9, 24]],
    [[13.3, 23.5], [12.2, 18.5], [8.8, 14.5], [3.5, 12.6], [-2.5, 12.2], [-7.5, 13.6], [-11.4, 17.5], [-13.3, 23.5]]];
  const HAIR = {   // what falls below the cap on the right, mirrored left
    'Long, straight': [[16.2, 24], [17, 36], [17.4, 50], [17.6, 62], [12, 62], [11.2, 54], [12.2, 46], [13.6, 38], [14.2, 31]],
    'Long, loose waves': [[16.2, 24], [17.6, 32], [16.4, 40], [18, 48], [16.8, 56], [18, 62], [12.2, 62], [11, 56], [12.4, 49],
      [12.2, 43], [13.8, 37], [14.2, 31]],
    'Long, curly': [[16.6, 22], [19.2, 28], [17.9, 34], [19.8, 40], [18.4, 46], [20.2, 52], [18.6, 58], [19.6, 62], [12, 62],
      [10.8, 57], [12, 50], [12.8, 44], [13.9, 37], [14.3, 31]],
    'Shoulder-length': [[16.2, 24], [17, 36], [17.4, 47], [19.6, 53.5], [16.6, 54.2], [14.4, 52], [12.6, 47], [13.6, 38], [14.2, 31]],
    'Bob': [[16.6, 24], [17.6, 34], [17.7, 43], [16.9, 47.4], [12.6, 47.6], [12.6, 44], [13.6, 38], [14.2, 31]],
    'Pixie': null,
  };
  function hairStrands(part) {
    if (part === 'fringe') {
      return [-9, -4.5, 0, 4.5, 9].map(x => `M${f(32 + x)},5.5 Q${f(32 + x * 1.1 + 0.6)},11 ${f(32 + x * 1.15)},16`).join(' ') +
        ' M22,7.5 Q17.5,12 17,20 M42,7.5 Q46.5,12 47,20';
    }
    const x = v => f(32 + part + v);
    return `M${x(0.5)},3.9 Q${x(9)},4.8 ${f(47.2 - Math.max(0, part) * 0.3)},18 M${x(1)},6.2 Q${x(8)},7.6 45.8,21 ` +
      `M${x(-0.6)},4.3 Q${x(-8.5)},5.4 ${f(17.2 + Math.min(0, part) * -0.2)},19 M${x(-1.2)},6.8 Q${x(-8)},8.6 18.6,21.5`;
  }
  function ear(k, c, [out, h], fill) {
    const x = v => f(32 + k * v), y0 = 31.5 - h / 2;
    return `<path d="M${x(c - 0.4)},${f(y0)} C${x(c + out)},${f(y0 - 1.8)} ${x(c + out + 1)},${f(y0 + h * 0.35)} ` +
      `${x(c + out * 0.85)},${f(y0 + h * 0.6)} C${x(c + out * 0.6)},${f(y0 + h * 0.85)} ${x(c + out * 0.3)},${f(y0 + h)} ` +
      `${x(c - 0.4)},${f(y0 + h)}"${fill ? ` fill="${PINK}"` : ''} stroke-width="1.3"/>` +
      line(`M${x(c + out * 0.55)},${f(y0 + h * 0.22)} q${f(-k * out * 0.35)},${f(h * 0.15)} ${f(-k * out * 0.12)},${f(h * 0.42)}`,
        ' stroke-width=".9"');
  }
  const FACE_MARKS = `<g stroke="${FEATURE_PINK}" stroke-width="2.6">` +
      line('M20.3,28.2 Q24.2,25.4 28.6,27 M43.7,28.2 Q39.8,25.4 35.4,27') +
      line('M21.4,30.6 q3,-1.6 6.2,0.3 M42.6,30.6 q-3,-1.6 -6.2,0.3', ' stroke-width="1.8"') +
      line('M29.6,43.4 q2.4,-0.8 4.8,0 M30,44.5 q2,0.9 4,0', ' stroke-width="1.6"') + '</g>' +
    `<path d="M29,37.3 Q30.5,35.9 32,37.3 Q33.5,35.9 35,37.3 Q33.6,39.8 32,39.6 Q30.4,39.8 29,37.3 Z" fill="${FEATURE_PINK}" stroke="none"/>`;
  // opts: guide (a face shape's grey type), mark ('jaw' or 'cheeks'), ear
  // [out, h] for the ears option, hair / hairline for the hair options, extra.
  function headInner(o, opts) {
    o = Object.assign({}, HEAD, o || {});
    opts = opts || {};
    const pts = headPts(o), w = o.c / 13.5, at = ([x, y]) => [32 + x * w, y];
    const [nx, ny] = neckTop(pts, 8.7), N = k => x => f(32 + k * x);
    const chosen = opts.hair || opts.hairline, hairFill = chosen ? PINK : HAIR_PINK;
    const [inner, part] = opts.hair === 'Pixie' ? [PIXIE[1], 0] : HAIRLINE[opts.hairline || 'Rounded, middle part'];
    const back = HAIR[opts.hair];
    let s = '';
    if (back) {
      [1, -1].forEach(k => {
        s += `<path d="${smooth(back.map(([x, y]) => [32 + k * x * w, y]))}" fill="${hairFill}" stroke-width="1.3"/>`;
      });
      if (opts.hair === 'Long, curly') {
        s += [[16, 36], [16.6, 45], [15.4, 54], [17, 59]].map(([x, y]) =>
          `<path d="M${f(32 + x * w)},${y} a1.4,1.4 0 1 1 1.4,1.4 M${f(32 - x * w)},${y} a1.4,1.4 0 1 0 -1.4,1.4" stroke-width=".9"/>`).join('');
      } else {
        s += line([1, -1].map(k => `M${f(32 + k * 15.3 * w)},34 Q${f(32 + k * 15.8 * w)},42 ${f(32 + k * 15.2 * w)},` +
          (opts.hair === 'Bob' ? '45' : opts.hair === 'Shoulder-length' ? '50' : '59')).join(' '), ' stroke-width=".9"');
      }
    }
    s += `<path d="M${f(32 - nx)},${f(ny)} Q32,${f(2 * o.chinY - ny)} ${f(32 + nx)},${f(ny)} L41,55.5 ` +
      `Q32,61.5 23,55.5 Z" fill="${HAIR_PINK}" stroke="none"/>`;
    s += line([1, -1].map(k => { const x = N(k);
      return `M${x(nx)},${f(ny)} C${x(nx + 0.2)},${f(ny + 4)} ${x(9)},53 ${x(9.2)},56.5 Q${x(9.8)},60.5 ${x(13.8)},62.5`; }).join(' '),
      ' stroke-width="1.5"');
    if (!opts.ear) s += ear(1, o.c, [3.6, 12]) + ear(-1, o.c, [3.6, 12]);
    if (opts.mark === 'jaw') s += `<path d="${smooth(pts, 3, 7)}" stroke="${PINK}" stroke-width="4" stroke-opacity=".8"/>`;
    s += line(smooth(pts), ' stroke-width="1.5"');
    const capPts = opts.hair === 'Pixie' ? PIXIE[0].concat(inner) : CAP.concat(inner);
    s += `<path d="${smooth(capPts.map(at))}" fill="${hairFill}" stroke-width="1.3"/>`;
    s += opts.hair === 'Pixie'
      ? line('M31,4.5 Q24,6 19.5,13 M33.5,4.6 Q40,6 44.5,13 M27,6.5 Q22,10 20.6,16 M38,6.8 Q43,10 43.6,16 ' +
          'M20,19 l-1.2,2.4 M44,19 l1.2,2.4 M29,12.8 l-1.4,1.6 M35,12.6 l1.4,1.6', ' stroke-width=".9"')
      : line(hairStrands(part), ' stroke-width=".9"');
    if (opts.ear) s += ear(1, o.c, opts.ear, true) + ear(-1, o.c, opts.ear, true);
    if (opts.mark === 'cheeks') {
      const y = o.cheekY + 3.5;
      s += line(`M${f(32 - o.c + 1.8)},${f(y + 1)} q3.4,-2.6 7,-1.6 M${f(32 + o.c - 1.8)},${f(y + 1)} q-3.4,-2.6 -7,-1.6`,
        ` stroke="${PINK}" stroke-width="3.2"`);
    }
    s += FACE_MARKS + (opts.extra || '');
    if (opts.guide) s += `<path d="${opts.guide}" stroke="${GUIDE_GREY}" stroke-width="1.3"/>`;
    return s;
  }
  const chartHead = (o, opts) => bsvg(headInner(o, opts));
  const FACE_SHAPE = {   // head parameters, then the guide, both fitted to the chart
    'Oval': [{}, 'M32,15.5 A12.3,17.1 0 1 1 31.9,15.5 Z'],
    'Round': [{fw: 13, c: 14.5, j: 13.6, jl: 10, lowY: 47.5, chinY: 50.5}, 'M32,19.7 A13.2,13.2 0 1 1 31.9,19.7 Z'],
    'Square': [{fw: 13.3, c: 13.8, j: 13.6, jawY: 43, jl: 10.6, lowY: 49.4, chinY: 51}, 'M20.4,22.5 H43.6 V45.9 H20.4 Z'],
    'Heart-shaped': [{fw: 13.8, c: 13.8, j: 11.6, jawY: 40.5, jl: 5.8, lowY: 48.4, chinY: 52},
      'M32,24.2 C29.5,20.6 18.3,19.6 18.3,25 C18.3,31 28,38.6 32,45.2 C36,38.6 45.7,31 45.7,25 C45.7,19.6 34.5,20.6 32,24.2 Z'],
    'Diamond': [{fw: 11, c: 14.3, j: 11.2, jl: 5.6, lowY: 48.4, chinY: 52}, 'M32,13.6 L45,30.4 L32,47 L19,30.4 Z'],
    'Rectangle': [{fw: 12, c: 12.6, j: 12.4, jawY: 44, jl: 9.6, lowY: 50.6, chinY: 53}, 'M20.4,22 H43.6 V49.2 H20.4 Z'],
    'Triangle': [{fw: 14, c: 13.6, j: 10.6, jl: 5.2, lowY: 48.6, chinY: 52.2}, 'M18.5,23 H45.5 L32,46.1 Z'],
    'Base-down triangle': [{fw: 11, c: 13, j: 14, jawY: 43.5, jl: 12, lowY: 49.6, chinY: 51.2}, 'M32,22.2 L45.7,45.2 H18.3 Z'],
  };
  const JAW = {
    'Soft, rounded chin': {jl: 9, chinY: 51}, 'Defined jaw': {j: 13, jawY: 42, jl: 8.6, lowY: 48.6},
    'Pointed chin': {j: 11.4, jl: 5, lowY: 48.8, chinY: 52.6}, 'Square jaw': {j: 13.6, jawY: 43, jl: 11, lowY: 49.6, chinY: 51},
  };
  const CHEEK = {High: [14.2, 29], Medium: [13.5, 32], Soft: [13.2, 34]};

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
    let s = `<circle cx="32" cy="${f(icy)}" r="${f(ir)}" fill="${iris || PINK}" stroke="none"/>` +
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
    return bsvg(s);
  }

  const BROW = {   // curve [x0,y0,cx,cy,x1,y1], thickness
    'Soft arch, medium': [[10, 34, 30, 22, 54, 30], 3.5], 'High arch, thin': [[10, 36, 28, 16, 54, 32], 2],
    'Straight, full': [[10, 31, 32, 27, 54, 31], 5], 'Bold, thick': [[10, 35, 30, 22, 54, 31], 7],
  };
  function brow([x0, y0, cx, cy, x1, y1], t) {
    const b = t < 2.5 ? line(`M${x0},${y0} Q${cx},${cy} ${x1},${y1}`, ` stroke="${PINK}" stroke-width="3.4"`) +
        line(`M${x0},${y0} Q${cx},${cy} ${x1},${y1}`) :
      line(`M${x0},${y0} Q${cx},${cy} ${x1},${y1} Q${cx},${cy + t * 1.6} ${x0 + 2},${y0 + t * 0.7} Z`, ` fill="${PINK}"`);
    return bsvg(b + line('M18,50 Q32,42 46,50 Q32,56 18,50', ' stroke-width="1.6"'));
  }
  const NOSE = {
    'Straight, narrow': 'M22,6 L36,40 Q41,46 34,48 L27,48',
    'Button': 'M22,6 Q29,28 32,36 Q43,40 35,48 L27,48',
    'Slightly upturned': 'M22,6 L33,35 Q43,35 37,46 L27,48',
    'Roman': 'M22,6 Q38,16 36,40 Q39,46 33,48 L27,48',
    'Wide': 'M22,6 L34,38 Q46,44 37,50 L25,50',
  };
  const nose = d => bsvg(`<ellipse cx="33" cy="45" rx="4.2" ry="2.6" fill="${PINK}" fill-opacity=".6" stroke="none"/>` + line(d) + line('M30,44 q3,1.5 5,0', ' stroke-width="1.6"') +
    line('M22,50 Q24,54 28,56', ' stroke-width="1.6"'));
  const LIPS = {'Full, defined bow': [20, 9, 10, 4], 'Medium': [19, 6, 8, 2], 'Thin': [19, 3, 4, 1], 'Very full': [21, 12, 14, 3]};
  function lips(w, up, lo, bow) {
    const L = 32 - w, R = 32 + w;
    return bsvg(`<path d="M${L},32 C${L + w * 0.4},${32 - up} 26,${32 - up - 1} 32,${32 - up + bow} ` +
      `C38,${32 - up - 1} ${R - w * 0.4},${32 - up} ${R},32 C${R - w * 0.3},${32 + lo * 1.25} ${L + w * 0.3},${32 + lo * 1.25} ${L},32 Z" ` +
      `fill="${PINK}" fill-opacity=".7" stroke="none"/>` +
      line(`M${L},32 C${L + w * 0.4},${32 - up} 26,${32 - up - 1} 32,${32 - up + bow} ` +
      `C38,${32 - up - 1} ${R - w * 0.4},${32 - up} ${R},32`) +
      line(`M${L},32 C${L + w * 0.3},${32 + lo * 1.25} ${R - w * 0.3},${32 + lo * 1.25} ${R},32`) +
      line(`M${L + 1},32 Q26,${32 + Math.min(2, up / 3)} 32,${32 + Math.min(1.2, up / 4)} Q38,${32 + Math.min(2, up / 3)} ${R - 1},32`));
  }
  const EAR = {'Small, close-set': [2.6, 9.5], 'Medium': [3.6, 12], 'Prominent': [5.6, 13]};
  const FRECKLES = (list, r) => list.map(p => `<circle cx="${p[0]}" cy="${p[1]}" r="${r || 0.8}" fill="${PINK}" stroke="none"/>`).join('');
  const MARKS = {
    'None': '', 'Light freckles, nose': FRECKLES([[29, 34.4], [35, 34.4], [27.4, 36.2], [36.6, 36.2], [30.8, 32.8], [33.2, 32.8]]),
    'Heavy freckles': FRECKLES([[22.5, 34], [25, 36.5], [27.4, 33.6], [21.2, 37.2], [24, 39], [28.8, 35.8], [41.5, 34], [39, 36.5],
      [36.6, 33.6], [42.8, 37.2], [40, 39], [35.2, 35.8], [30.8, 33], [33.2, 33.6], [23.6, 31.8], [40.4, 31.8]]),
    'Beauty mark, cheek': FRECKLES([[41.5, 38.4]], 1.2), 'Beauty mark, lip': FRECKLES([[36.4, 42.2]], 1.1),
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
  // ── The chart figure: every front-view body option is drawn on it ─────────
  // No head, open contour lines, hands on hips and long legs with one line
  // between them, after the creator's body-shape chart. An option moves one
  // proportion and marks that region in soft pink behind the lines.
  const BODY = {sh: 10.5, bu: 10, wa: 8, hi: 11.5, th: 11};
  function chartFigure(o, extra, opts) {
    o = Object.assign({}, BODY, o || {});
    opts = opts || {};
    const {sh, bu, wa, hi, th} = o;
    const side = k => {
      const x = v => f(32 + k * v);
      let d = `M${x(2)},0 Q${x(2.5)},3 ${x(sh)},4.5 Q${x(sh + 3)},6 ${x(sh + 3.5)},10 `;
      d += opts.armsDown
        ? `Q${x(sh + 4.5)},18 ${x(sh + 5)},27 M${x(sh)},11 Q${x(sh + 1.5)},18 ${x(sh + 2.5)},26 `
        : `L${x(sh + 7)},16 L${x(wa + 1.5)},19.5 M${x(sh + 0.5)},11 L${x(sh + 3.8)},16 L${x(wa + 0.8)},18.5 `;
      return d + `M${x(sh - 0.5)},11.5 Q${x(bu + 0.3)},13 ${x(bu)},15 C${x(bu - 0.3)},17 ${x(wa)},17 ${x(wa)},19.5 ` +
        `C${x(wa)},22 ${x(hi)},23 ${x(hi)},26 C${x(hi)},29.5 ${x(th)},31 ${x(th - 0.5)},35 ` +
        `C${x(th - 1)},39 ${x(9)},41 ${x(8.6)},46 C${x(8.2)},51 ${x(8)},55 ${x(6.5)},63`;
    };
    let s = (opts.under ? opts.under(o) : '') + line(side(1) + ' ' + side(-1) + ' M32,31 V63', ' stroke-width="1.4"') + (extra || '');
    return s;
  }
  const pinkFill = d => `<path d="${d}" fill="${PINK}" fill-opacity=".5" stroke="none"/>`;
  const pinkDot = ([x, y], r) => `<circle cx="${x}" cy="${y}" r="${r || 2.2}" fill="${PINK}" stroke="none"/>`;
  const REGION = {
    shoulders: o => `<path d="M${f(32 - o.sh)},4.8 Q32,3 ${f(32 + o.sh)},4.8" stroke="${PINK}" stroke-width="4" stroke-opacity=".8"/>`,
    bust: o => pinkDot([f(32 - o.bu * 0.45), 14.5], f(o.bu * 0.33)) + pinkDot([f(32 + o.bu * 0.45), 14.5], f(o.bu * 0.33)),
    waist: o => `<ellipse cx="32" cy="19.5" rx="${o.wa}" ry="1.8" fill="${PINK}" fill-opacity=".6" stroke="none"/>`,
    hips: o => `<ellipse cx="32" cy="26" rx="${o.hi}" ry="2.2" fill="${PINK}" fill-opacity=".6" stroke="none"/>`,
    thighs: o => [-1, 1].map(k => pinkFill(`M${f(32 + k * 1)},32 C${f(32 + k * (o.th - 0.5))},30 ${f(32 + k * (o.th - 0.5))},36 ` +
      `${f(32 + k * (o.th - 1.5))},40 C${f(32 + k * 6)},42 ${f(32 + k * 1.5)},40 ${f(32 + k * 1)},32 Z`)).join(''),
  };
  const body = (o, region, extra) => bsvg(chartFigure(o, extra, {under: REGION[region]}));
  const BUILD = {
    'Slim': {sh: 9.5, bu: 9, wa: 7, hi: 10, th: 9.5}, 'Athletic': {sh: 12, bu: 10.5, wa: 8.5, hi: 10.5, th: 11},
    'Average': {}, 'Curvy': {sh: 10.5, bu: 12, wa: 7.5, hi: 13.5, th: 12.5},
    'Voluptuous': {sh: 11.5, bu: 13, wa: 9.5, hi: 14.5, th: 13.5}, 'Muscular': {sh: 13.5, bu: 11.5, wa: 9.5, hi: 11.5, th: 12},
  };
  const HEIGHT = {'Under 155 cm': 0.8, '155–165 cm': 0.87, '165–175 cm': 0.94, 'Over 175 cm': 1};
  function height(s) {
    const top = f(63 - 63 * s);
    return bsvg(`<g transform="translate(32,63) scale(${s}) translate(-32,-63)">${chartFigure({})}</g>` +
      `<path d="M58,62 L58,${top} M55.5,${top} L60.5,${top} M55.5,62 L60.5,62" stroke="${PINK}" stroke-width="2.4"/>`);
  }
  // Marks are drawn close up on the part of the body they sit on, cut from the
  // same figures by the viewBox, with the lines kept as thin as elsewhere.
  const zoom = (inner, cx, cy, z) => bsvg(`<g transform="translate(32,32) scale(${z}) translate(${-cx},${-cy})" ` +
    `stroke-width="${f(1.3 / z / 0.75)}">` + inner.replace(/stroke-width="([\d.]+)"/g, (m, w) => `stroke-width="${f(w / z)}"`) + '</g>');
  const stud = ([x, y], r) => `<circle cx="${x}" cy="${y}" r="${r}" fill="${PINK}" stroke-width="${f(r * 0.6)}"/>`;
  const tattoo = ([x, y], k) => `<path d="M${x},${y + 1.6 * k} l${-2.2 * k},${-2.2 * k} a${1.3 * k},${1.3 * k} 0 0 1 ${2.2 * k},${-1.6 * k} ` +
    `a${1.3 * k},${1.3 * k} 0 0 1 ${2.2 * k},${1.6 * k} Z" fill="${PINK}" stroke="none"/>`;
  const torso = extra => zoom(chartFigure({}, extra), 32, 14, 1.8);
  const WRIST = line('M4,60 L27,37 M13,63 L34,42') +
    line('M27,37 C29,32 34,28 39,25 C43,21 46,16 50,12.5 C52.5,10.5 55.5,12.5 54,15.5 C52,19 49.5,22 48,24.5 ' +
      'C51.5,24 54,27 51,29.5 C47,33 41,38.5 34,42') +
    line('M39,25 C37,21 38.5,17.5 41.5,17 C43.5,17 43.5,19.5 42.5,21 M47,17.5 L52.5,14 M48,24.5 L53,21', ' stroke-width="1.1"');
  const ANKLE = line('M22,1 C22.5,18 20.5,33 21.5,44 C22,50 19.5,54 20.5,57.5 C21.5,60.5 25,61 29,61 L52,61 ' +
    'C57.5,61 58.5,56.5 54.5,54.5 C48,51.5 40.5,49.5 35.5,45.5 C33,41 32,35 32.5,28 C33,18 34,9 35,1') +
    line('M28.5,46.5 q2.2,-1.8 3.4,0.6', ' stroke-width="1.1"');
  const back = () => TORSO + line('M32,8 V62', ' stroke-width="1.1" stroke-dasharray="2.5 2.5"') +
    line('M18.5,21 Q22,28.5 28.5,27 M45.5,21 Q42,28.5 35.5,27', ' stroke-width="1.2"');
  const MARKERS = {
    tattoos: {'None': () => torso(''), 'Small, wrist': () => bsvg(WRIST + tattoo([29.5, 42], 1)),
      'Small, ankle': () => bsvg(ANKLE + tattoo([26.5, 41.5], 1.1)),
      'Hip': () => zoom(chartFigure({}, tattoo([42, 25], 0.7)), 37.5, 22, 2.1),
      'Sleeve': () => zoom(chartFigure({}, `<path d="M44.3,7 L47.8,15.8 L41.4,18.8" stroke="${PINK}" stroke-width="3" ` +
        'stroke-opacity=".85"/>'), 43, 11, 2.2),
      'Back piece': () => bsvg(`<rect x="20" y="12" width="24" height="30" rx="4" fill="${PINK}" fill-opacity=".5" stroke="none"/>` + back())},
    piercings: {'None': () => chartHead({}), 'Ears': () => zoom(headInner({}, {extra: stud([46.3, 36.2], 0.9) + stud([48.6, 27.8], 0.7)}), 43.5, 31.5, 2.4),
      'Nose': () => zoom(headInner({}, {extra: stud([34.4, 37.6], 0.75)}), 32, 36, 2.8),
      'Navel': () => zoom(chartFigure({}, line('M32,21 q-0.7,1 0,2 q0.7,-1 0,-2', ' stroke-width="1"') + stud([32, 24.3], 0.75)), 32, 21, 2.6)},
    birthmarks: {'None': () => torso(''), 'Shoulder': () => zoom(chartFigure({}, pinkDot([41.5, 6.5], 1)), 40, 7, 2.6),
      'Hip': () => zoom(chartFigure({}, pinkDot([42, 25], 1.2)), 37.5, 22, 2.1)},
  };

  const NAILS = {'Short, nude': [2, 'line'], 'Medium, painted': [5, 'paint'], 'Long, painted': [9, 'paint'], 'French tips': [5, 'tip']};
  function nails(len, kind) {
    let s = line('M12,64 L12,46 Q12,40 16,38');   // back of the hand
    [[18, 30], [30, 26], [42, 28], [54, 34]].forEach(([x, y]) => {
      s += line(`M${x - 5},64 L${x - 5},${y + 4} Q${x - 5},${y - 2} ${x},${y - 2} Q${x + 5},${y - 2} ${x + 5},${y + 4} L${x + 5},64`);
      const tip = y - 2 - len, nail = `M${x - 3},${y + 5} L${x - 3},${tip + 3} Q${x},${tip - 1} ${x + 3},${tip + 3} L${x + 3},${y + 5} Z`;
      s += kind === 'paint' ? `<path d="${nail}" fill="currentColor" stroke-width="1.4"/>` : line(nail, ' stroke-width="1.4"');
      if (kind === 'tip') s += `<path d="M${x - 3},${tip + 4} Q${x},${tip - 1} ${x + 3},${tip + 4} Z" fill="currentColor" stroke-width="1"/>`;
    });
    return bsvg(s);
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
    return bsvg(chartFigure({sh, bu: sh - 0.5, wa, hi, th}, '', {armsDown, under: () => pinkFill(fill)}));
  }


  // ── Bum shape from behind ──────────────────────────────────────────────────
  // Drawn after the creator's reference sheet: slate contour lines, the centre
  // crease and folds meeting at a small dark point, and the type as a soft
  // pink outline around the cheeks rather than a dashed guide.
  const GLUTE_INK = INK, GLUTE_GUIDE = PINK;
  const GLUTE_SHAPE = {   // waist, hip peak, peak height, low hip, thigh; guide
    'Round': [[11, 17.5, 31, 17, 13.5], 'M32,16 C41,16 47.5,22.5 47.5,31 C47.5,40 41,45.5 32,45.5 C23,45.5 16.5,40 16.5,31 C16.5,22.5 23,16 32,16 Z'],
    'Heart-shaped': [[10, 18, 35, 17.5, 13.5], 'M32,20 C24,14 15,21 16,33 C16.8,42 25,46 32,43 C39,46 47.2,42 48,33 C49,21 40,14 32,20 Z'],
    'A-shaped': [[10.5, 15, 29, 18.5, 14.5], 'M23.5,15 H40.5 L49,44 H15 Z'],
    'Pear': [[12, 16, 33, 19, 15], 'M21,10 H43 L50,44 H14 Z'],
    'Bubble': [[11, 17, 30, 17.5, 14], 'M16.5,44 V26 C16.5,18 23,15 32,15 C41,15 47.5,18 47.5,26 V44 Z'],
    'Wide': [[12, 19, 36, 19, 15], 'M13,44 V33 C13,24 21,19 32,19 C43,19 51,24 51,33 V44 Z'],
    'Square': [[15, 16.5, 27, 16.5, 14], 'M16.5,17 Q16.5,16 18,16 H46 Q47.5,16 47.5,17 V43 Q47.5,44 46,44 H18 Q16.5,44 16.5,43 Z'],
    'V-shaped': [[15.5, 16.5, 22, 13, 11], 'M14.5,15 H49.5 L41,44 H23 Z'],
  };
  function gluteShape([[wa, hp, hy, lh, th], g]) {
    const sideLine = k => {
      const x = v => f(32 + k * v);
      return `M${x(wa)},2 C${x(wa - 0.5)},${hy - 13} ${x(hp)},${hy - 8} ${x(hp)},${hy} ` +
        `C${x(hp)},${hy + 8} ${x(lh)},39 ${x(lh)},45 C${x(lh)},51 ${x(th)},56 ${x(th - 1)},63`;
    };
    const fold = k => { const x = v => f(32 + k * v); return `M${x(0.8)},42.5 Q${x(7)},46.5 ${x(lh - 5)},43.5`; };
    return bsvg(`<path d="${g}" stroke="${GLUTE_GUIDE}" stroke-width="2.6" stroke-opacity=".75"/>` +
      `<g stroke="${GLUTE_INK}">` +
      line(sideLine(1) + ' ' + sideLine(-1), ' stroke-width="1.8"') +
      line('M32,5 v6', ' stroke-width="1.2" stroke-opacity=".6"') +
      line('M32,25 Q32.5,34 32,42 ' + fold(1) + ' ' + fold(-1), ' stroke-width="1.4"') +
      line('M31.2,44 Q30.2,53 30.8,63 M32.8,44 Q33.8,53 33.2,63', ' stroke-width="1.4"') + '</g>' +
      `<path d="M32,40.8 L33.3,42.8 L32,44.8 L30.7,42.8 Z" fill="${GLUTE_INK}" stroke="none"/>`);
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
    return bsvg(s);
  }
  const SHAPE = {'Round': {}, 'Teardrop': {h: 1.15, drop: 1}, 'Athletic': {w: 1.1, h: 0.55, r: 6.5}, 'Relaxed': {w: 1.1, h: 1.2, drop: 4}};
  const SPACING = {Close: 8.5, Average: 10.5, Wide: 12.5};
  const PERK = {Perky: {cy: 27, h: 0.8}, Natural: {}, Soft: {cy: 31, h: 1.1, drop: 2}};
  const areola = (ar, nr) => bsvg(ring(32, 32, ar) + ring(32, 32, nr));
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
    return bsvg(s);
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
    if (!poly) return bsvg(PELVIS + line('M22,26 L42,26 L32,46 Z', ' stroke-width="1.4" stroke-dasharray="2 2.6"'));
    const xs = poly.map(p => p[0]), ys = poly.map(p => p[1]);
    let hairs = '';
    for (let r = 0, y = Math.min(...ys) + 1.5; y < Math.max(...ys) - 1; r++, y += gap * 0.8) {
      for (let x = Math.min(...xs) + 1 + (r % 2) * gap / 2; x < Math.max(...xs); x += gap) {
        if (inside(poly, x, y) && inside(poly, x + 0.8, y + 2.2)) hairs += `M${f(x)},${f(y)} l0.8,2.2 `;
      }
    }
    return bsvg(PELVIS + line('M' + poly.map(P).join(' L') + ' Z', ' stroke-width="1.2" stroke-opacity=".55"') +
      line(hairs, ' stroke-width="1.3"'));
  }

  // ── Table ─────────────────────────────────────────────────────────────────
  const map = (obj, fn) => Object.fromEntries(Object.entries(obj).map(([k, v]) => [k, () => fn(v, k)]));
  const TABLE = {
    face_shape: map(FACE_SHAPE, ([o, guide]) => chartHead(o, {guide})),
    body_shape: map(BODY_SHAPE, bodyShape),
    glute_shape: map(GLUTE_SHAPE, gluteShape),
    jaw: map(JAW, o => chartHead(o, {mark: 'jaw'})),
    cheekbones: map(CHEEK, ([c, cheekY]) => chartHead({c, cheekY}, {mark: 'cheeks'})),
    eye_shape: map(EYE, (v, k) => eye(k)),
    eye_colour: map(EYE_COLOURS, hex => eye('Almond', hex)),
    brows: map(BROW, ([c, t]) => brow(c, t)),
    nose: map(NOSE, nose),
    lips: map(LIPS, a => lips.apply(null, a)),
    ears: map(EAR, ear => chartHead({}, {ear})),
    hair_texture: map(HAIR, (v, hair) => chartHead({}, {hair})),
    hairline: map(HAIRLINE, (v, hairline) => chartHead({}, {hairline})),
    marks: map(MARKS, extra => chartHead({}, {extra})),
    height: map(HEIGHT, height),
    build: map(BUILD, o => body(o)),
    shoulders: map({Narrow: 8.5, Medium: 10.5, Broad: 13.5}, sh => body({sh}, 'shoulders')),
    waist: map({Defined: 6.5, Straight: 10, Soft: 11}, wa => body({wa}, 'waist')),
    hips: map({Narrow: 9.5, Medium: 11.5, Wide: 15.5}, hi => body({hi}, 'hips')),
    bust: map({Small: 8.5, Medium: 10, Large: 11.5, 'Very large': 13}, bu => body({bu}, 'bust')),
    thighs: map({Slim: 9.5, Toned: 11, Full: 13.5}, th => body({th}, 'thighs')),
    tattoos: MARKERS.tattoos, piercings: MARKERS.piercings, birthmarks: MARKERS.birthmarks,
    nails: map(NAILS, ([len, kind]) => nails(len, kind)),
    cup: map(CUP, r => breasts({r})),
    breast_shape: map(SHAPE, o => breasts(o)),
    spacing: map(SPACING, gap => breasts({gap})),
    augmented: map({Natural: false, Augmented: true}, aug => breasts({aug})),
    perkiness: map(PERK, o => breasts(o)),
    areola_size: map({Small: 8, Medium: 12, Large: 17}, r => areola(r, 3)),
    nipple_size: map({Small: 2.5, Medium: 4, Large: 5.5}, r => areola(12, r)),
    nipple_shape: map(NIP_SHAPE, d => bsvg(line('M10,8 L10,56', ' stroke-width="1.4" stroke-dasharray="2 2.6"') + line(d))),
    nipple_piercing: map({None: 0, One: 1, Both: 2}, pair),
    pubic_style: map(PUBIC, (v, k) => pubic(k, 3.6)),
    pubic_density: map({Sparse: 5.2, Medium: 3.6, Dense: 2.6}, g => pubic('Natural', g)),
  };
  Object.entries(COLOURS).forEach(([key, set]) => { TABLE[key] = map(set, swatch); });

  const unset = () => svg(`<circle cx="32" cy="32" r="20" stroke-width="1.4" stroke-dasharray="3 3"/>`);
  const hairHex = sheet => (sheet || {}).hair_colour === 'Custom' ? (sheet || {}).hair_colour_hex
    : COLOURS.hair_colour[(sheet || {}).hair_colour];
  function render(feature, option, sheet) {
    if (feature === 'hair_colour' && option === 'Custom') {
      const hex = (sheet || {}).hair_colour_hex;
      return /^#[0-9a-f]{6}$/i.test(hex || '') ? swatch(hex) : unset();
    }
    if (feature === 'pubic_colour') {
      if (option === 'Dark') return swatch('#2a1d17');
      if (option === 'Light') return swatch('#c9a36c');
      if (option === 'Matches hair') {
        const hair = hairHex(sheet);
        return hair ? swatch(hair) : svg(`<circle cx="32" cy="32" r="20" stroke-width="1.4" stroke-dasharray="3 3"/>`);
      }
    }
    const fn = (TABLE[feature] || {})[option];
    return fn ? fn() : '';
  }

  const api = {render, TEXT_ONLY: ['labia', 'labia_fullness', 'ethnicity', 'apparent_age']};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CharacterVisuals = api;
})(this);
