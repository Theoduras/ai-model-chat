// Click any admin table header to sort by that column; click again to reverse.
// Delegated, so tables rendered after load sort too.
(function () {
  function key(cell) {
    var t = (cell.getAttribute('data-sort') || cell.textContent || '').trim();
    var n = t.replace(/[,\s€$%]/g, '');
    if (n !== '' && !isNaN(n)) return [0, parseFloat(n)];
    var d = /\d{4}-\d{2}-\d{2}|\d{1,2} \w{3}|\w{3} \d{1,2}/.test(t) ? Date.parse(t) : NaN;
    if (!isNaN(d)) return [0, d];
    return [1, t.toLowerCase()];
  }
  document.addEventListener('click', function (e) {
    var th = e.target.closest && e.target.closest('th');
    if (!th || e.target.closest('a,button,input,select')) return;
    var row = th.parentElement, table = th.closest('table');
    if (!table) return;
    var col = Array.prototype.indexOf.call(row.children, th);
    var dir = th.getAttribute('data-dir') === 'asc' ? 'desc' : 'asc';
    row.querySelectorAll('th').forEach(function (h) {
      h.removeAttribute('data-dir');
      h.textContent = h.textContent.replace(/ [▲▼]$/, '');
    });
    th.setAttribute('data-dir', dir);
    th.textContent += dir === 'asc' ? ' ▲' : ' ▼';
    var body = row.parentElement.tagName === 'THEAD' ? table.tBodies[0] : row.parentElement;
    if (!body) return;
    var rows = Array.prototype.filter.call(body.children, function (r) {
      return r !== row && r.children[col] && !r.querySelector('th');
    });
    rows.sort(function (a, b) {
      var x = key(a.children[col]), y = key(b.children[col]);
      var c = x[0] - y[0] || (x[1] < y[1] ? -1 : x[1] > y[1] ? 1 : 0);
      return dir === 'asc' ? c : -c;
    });
    rows.forEach(function (r) { body.appendChild(r); });
  });
  var css = document.createElement('style');
  css.textContent = 'th{cursor:pointer;user-select:none}th:hover{color:var(--text,#fff)}';
  document.head.appendChild(css);
})();
