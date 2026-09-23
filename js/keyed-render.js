// Keyed rendering for grids that hold <img>/<video> tiles. Rebuilding such a
// grid with innerHTML recreates every media element, and a recreated element
// downloads its file again -- on every poll, click or selection tick.
//
//   keyedRender(box, [[key, html], ...], emptyHtml)
//   keepMedia(oldEl, freshEl)   swap in freshEl's children, keeping old media
// A kept node takes the new attributes and children, except that an <img> or
// <video> whose src is unchanged stays the element it was.
function keepMedia(old, fresh) {
  const media = new Map([...old.querySelectorAll('img, video')].map(m => [m.tagName + m.getAttribute('src'), m]));
  fresh.querySelectorAll('img, video').forEach(m => {
    const had = media.get(m.tagName + m.getAttribute('src'));
    if (had) m.replaceWith(had);
  });
  old.replaceChildren(...fresh.childNodes);
}

function keyedRender(box, items, emptyHtml) {
  if (!items.length) { box.innerHTML = emptyHtml || ''; return; }
  const byKey = new Map([...box.children].map(n => [n.dataset.key, n]));
  const nodes = items.map(([key, html]) => {
    const t = document.createElement('template');
    t.innerHTML = html.trim();
    const fresh = t.content.firstElementChild;
    fresh.dataset.key = key;
    const old = byKey.get(key);
    if (!old || old.tagName !== fresh.tagName) { fresh._inner = fresh.innerHTML; return fresh; }
    [...old.attributes].forEach(at => { if (!fresh.hasAttribute(at.name)) old.removeAttribute(at.name); });
    [...fresh.attributes].forEach(at => {
      if (old.getAttribute(at.name) !== at.value) old.setAttribute(at.name, at.value);
    });
    const inner = t.content.firstElementChild.innerHTML;
    if (old._inner !== inner) { keepMedia(old, fresh); old._inner = inner; }
    return old;
  });
  const have = [...box.children];
  if (have.length !== nodes.length || have.some((n, i) => n !== nodes[i])) box.replaceChildren(...nodes);
}
