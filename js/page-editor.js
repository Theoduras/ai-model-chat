/* Inline "click to edit" overlay for the plain marketing pages.
 *
 * A page opts in with <body data-page="home"> and marks each editable spot:
 *   <span data-edit-id="unique-id">text</span>                  -- text
 *   <img data-edit-id="unique-id" data-edit-image>               -- image
 *   <div data-edit-id="unique-id" data-edit-image>...</div>      -- background-image
 *
 * Content is fetched publicly from /api/site-content/<page> on every load.
 * The edit button only appears for operators (server-checked via /api/me);
 * saving is enforced server-side by the same operator check, so hiding the
 * button here is a convenience, not the security boundary.
 */
(function () {
  var PAGE = document.body.getAttribute('data-page');
  if (!PAGE) return;

  var editing = false;
  var original = {};

  function imgGet(el) {
    if (el.tagName === 'IMG') return el.getAttribute('src') || '';
    var bg = el.style.backgroundImage || '';
    var m = /^url\((['"]?)(.*)\1\)$/.exec(bg.trim());
    return m ? m[2] : '';
  }

  function imgSet(el, val) {
    if (!val) return;
    if (el.tagName === 'IMG') el.src = val;
    else el.style.backgroundImage = "url('" + val + "')";
  }

  function applyContent(content) {
    document.querySelectorAll('[data-edit-id]').forEach(function (el) {
      var id = el.getAttribute('data-edit-id');
      if (!(id in content)) return;
      if (el.hasAttribute('data-edit-image')) imgSet(el, content[id]);
      else el.textContent = content[id];
    });
  }

  fetch('/api/site-content/' + encodeURIComponent(PAGE))
    .then(function (r) { return r.ok ? r.json() : { content: {} }; })
    .then(function (d) { applyContent(d.content || {}); })
    .catch(function () {});

  fetch('/api/me')
    .then(function (r) { return r.ok ? r.json() : {}; })
    .then(function (me) { if (me && me.is_operator) initEditor(); })
    .catch(function () {});

  function initEditor() {
    var style = document.createElement('style');
    style.textContent =
      '.pe-editable{outline:2px dashed #ff5c38;outline-offset:2px;border-radius:4px;cursor:text;}' +
      '.pe-editable-img{outline:2px dashed #ff5c38;outline-offset:2px;cursor:pointer;position:relative;}' +
      '#pe-toggle,#pe-bar button{font-family:system-ui,sans-serif;font-size:14px;font-weight:600;' +
        'border:none;border-radius:9999px;padding:12px 20px;cursor:pointer;' +
        'box-shadow:0 8px 24px rgba(0,0,0,.35);}';
    document.head.appendChild(style);

    // A click that lands inside contenteditable text must not fire a real
    // navigation or form submit while editing.
    document.addEventListener('submit', function (e) { if (editing) e.preventDefault(); }, true);

    var toggle = document.createElement('button');
    toggle.id = 'pe-toggle';
    toggle.textContent = '✎ Edit page';
    toggle.style.cssText = 'position:fixed;bottom:20px;right:20px;z-index:9999;background:#ff2d78;color:#fff;';
    document.body.appendChild(toggle);

    var bar = document.createElement('div');
    bar.id = 'pe-bar';
    bar.style.cssText = 'position:fixed;bottom:20px;right:20px;z-index:9999;display:none;gap:8px;';
    var saveBtn = document.createElement('button');
    saveBtn.textContent = 'Save';
    saveBtn.style.background = '#22c55e';
    saveBtn.style.color = '#fff';
    var cancelBtn = document.createElement('button');
    cancelBtn.textContent = 'Cancel';
    cancelBtn.style.background = '#333';
    cancelBtn.style.color = '#fff';
    bar.appendChild(saveBtn);
    bar.appendChild(cancelBtn);
    document.body.appendChild(bar);

    toggle.onclick = startEditing;
    cancelBtn.onclick = function () { applyContent(original); stopEditing(); };
    saveBtn.onclick = save;

    function startEditing() {
      editing = true;
      original = {};
      toggle.style.display = 'none';
      bar.style.display = 'flex';
      document.querySelectorAll('[data-edit-id]').forEach(function (el) {
        var id = el.getAttribute('data-edit-id');
        if (el.hasAttribute('data-edit-image')) {
          original[id] = imgGet(el);
          el.classList.add('pe-editable-img');
          el.addEventListener('click', onImageClick);
        } else {
          original[id] = el.textContent;
          el.contentEditable = 'true';
          el.classList.add('pe-editable');
        }
      });
    }

    function onImageClick(e) {
      if (!editing) return;
      e.preventDefault();
      var el = e.currentTarget;
      var input = document.createElement('input');
      input.type = 'file';
      input.accept = 'image/*';
      input.onchange = function () {
        var file = input.files[0];
        if (!file) return;
        var reader = new FileReader();
        reader.onload = function () { imgSet(el, reader.result); };
        reader.readAsDataURL(file);
      };
      input.click();
    }

    function stopEditing() {
      editing = false;
      toggle.style.display = '';
      bar.style.display = 'none';
      document.querySelectorAll('[data-edit-id]').forEach(function (el) {
        if (el.hasAttribute('data-edit-image')) {
          el.classList.remove('pe-editable-img');
          el.removeEventListener('click', onImageClick);
        } else {
          el.contentEditable = 'false';
          el.classList.remove('pe-editable');
        }
      });
    }

    function save() {
      var content = {};
      document.querySelectorAll('[data-edit-id]').forEach(function (el) {
        var id = el.getAttribute('data-edit-id');
        content[id] = el.hasAttribute('data-edit-image') ? imgGet(el) : el.textContent;
      });
      saveBtn.disabled = true;
      saveBtn.textContent = 'Saving…';
      fetch('/api/site-content/' + encodeURIComponent(PAGE), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: content })
      })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          saveBtn.disabled = false;
          saveBtn.textContent = 'Save';
          if (d.ok) stopEditing();
          else alert('Save failed: ' + (d.error || 'unknown error'));
        })
        .catch(function () {
          saveBtn.disabled = false;
          saveBtn.textContent = 'Save';
          alert('Save failed.');
        });
    }
  }
})();
