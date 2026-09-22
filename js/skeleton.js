// Placeholder blocks drawn in a list's own shape while its fetch is in flight,
// so the panel is its final height before the data lands and nothing below it
// is pushed down when it does.
(function () {
  function box(style) {
    return '<div class="skel" style="' + (style || '') + '"></div>';
  }

  window.skelGrid = function (n, height) {
    const cells = [];
    for (let i = 0; i < (n || 8); i++) {
      cells.push(box('aspect-ratio:3/4;' + (height ? 'height:' + height + ';' : '')));
    }
    return '<div class="skel-grid">' + cells.join('') + '</div>';
  };

  window.skelRows = function (n, height) {
    const rows = [];
    for (let i = 0; i < (n || 4); i++) {
      rows.push(box('height:' + (height || '46px') + ';margin-bottom:8px;'));
    }
    return '<div class="skel-rows">' + rows.join('') + '</div>';
  };

  window.skelDays = function () {
    const days = [];
    for (let i = 0; i < 7; i++) {
      days.push(box('height:120px;'));
    }
    return '<div class="skel-days">' + days.join('') + '</div>';
  };
})();
