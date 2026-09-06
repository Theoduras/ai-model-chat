// The operator's read-only view of a conversation, drawn the way the fan sees
// it on /chat — same bubbles, same sides, same date separators.
//
// It is deliberately platform-blind. Everything comes from /api/inbox, which
// serves any platform out of the one message store, so this file never learns
// what Fanvue or Telegram are: it is handed a platform key and a persona and
// renders whatever comes back.
(function () {

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  // Bare URLs are common in these threads (CTA links, PPV captions) and a fan
  // sees them as links, so the operator should too.
  function linkify(s) {
    return esc(s).replace(/(https?:\/\/[^\s<]+)/g, function (u) {
      return '<a class="bubble-link" href="' + u + '" target="_blank" rel="noopener">' + u + '</a>';
    });
  }

  function clock(ts) {
    if (!ts) return '';
    return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  function dayLabel(ts) {
    var d = new Date(ts * 1000), now = new Date();
    var same = function (a, b) { return a.toDateString() === b.toDateString(); };
    if (same(d, now)) return 'Today';
    var y = new Date(now.getTime() - 86400000);
    if (same(d, y)) return 'Yesterday';
    return d.toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' });
  }

  function ago(ts) {
    if (!ts) return '';
    var s = Math.max(0, Math.floor(Date.now() / 1000) - ts);
    if (s < 60) return 'now';
    if (s < 3600) return Math.floor(s / 60) + 'm';
    if (s < 86400) return Math.floor(s / 3600) + 'h';
    return Math.floor(s / 86400) + 'd';
  }

  function initial(name, key) {
    var s = (name || key || '?').replace(/^[a-z]+:/, '');
    return s.charAt(0) || '?';
  }

  function ChatView(host, opts) {
    this.host = host;
    this.platform = opts.platform;
    this.persona = opts.persona;          // () => slug
    this.emptyHint = opts.emptyHint || '';
    this.fan = '';
    this.fans = [];
    this.loading = false;
    this.host.innerHTML =
      '<div class="cn-inbox">' +
        '<div class="cn-fans">' +
          '<div class="cn-fans-head"><span>Conversations</span><span class="cn-fans-n"></span></div>' +
          '<div class="cn-fans-list"><div class="cn-empty">Loading…</div></div>' +
        '</div>' +
        '<div class="cn-thread">' +
          '<div class="cn-thread-head">' +
            '<button class="cn-thread-back" title="Back to the list">←</button>' +
            '<div><div class="cn-thread-name">No conversation open</div>' +
            '<div class="cn-thread-meta"></div></div>' +
          '</div>' +
          '<div class="chat-window"><div class="cn-empty">Pick someone on the left to read the chat.</div></div>' +
        '</div>' +
      '</div>';
    this.box = host.querySelector('.cn-inbox');
    this.list = host.querySelector('.cn-fans-list');
    this.count = host.querySelector('.cn-fans-n');
    this.window = host.querySelector('.chat-window');
    this.name = host.querySelector('.cn-thread-name');
    this.meta = host.querySelector('.cn-thread-meta');
    var self = this;
    host.querySelector('.cn-thread-back').onclick = function () {
      self.box.classList.remove('reading');
    };
  }

  ChatView.prototype.url = function (extra) {
    return '/api/inbox?platform=' + encodeURIComponent(this.platform) +
           '&persona=' + encodeURIComponent(this.persona() || '') + (extra || '');
  };

  // Called on every poll. Refreshes the list, and the open thread with it, so a
  // reply the persona sends while you are reading simply appears.
  ChatView.prototype.refresh = function () {
    var self = this;
    if (this.loading || !this.persona()) return Promise.resolve();
    this.loading = true;
    return fetch(this.url()).then(function (r) { return r.json(); }).then(function (d) {
      if (!d.ok) throw new Error(d.error || 'could not load the conversations');
      self.fans = d.fans || [];
      self.renderList();
      if (self.fan && !self.fans.some(function (f) { return f.key === self.fan; })) self.fan = '';
      if (self.fan) return self.openThread(self.fan, true);
    }).catch(function (e) {
      self.list.innerHTML = '<div class="cn-empty">Could not load the conversations: ' +
        esc(String(e.message || e)) + '</div>';
    }).then(function () { self.loading = false; });
  };

  // Fans whose last word has had no reply. The shell shows this on the tab.
  ChatView.prototype.markWaiting = function () {
    var n = this.fans.filter(function (f) { return f.waiting; }).length;
    if (this.onWaiting) this.onWaiting(n);
    return n;
  };

  ChatView.prototype.renderList = function () {
    var self = this;
    this.count.textContent = this.fans.length || '';
    if (!this.fans.length) {
      this.list.innerHTML = '<div class="cn-empty">No conversations yet.' +
        (this.emptyHint ? '<br>' + esc(this.emptyHint) : '') + '</div>';
      return;
    }
    this.list.innerHTML = this.fans.map(function (f) {
      var who = f.handle || f.key.replace(/^[a-z]+:/, '');
      return '<button class="cn-fan' + (f.key === self.fan ? ' on' : '') +
        '" data-fan="' + esc(f.key) + '">' +
        '<span class="cn-fan-av">' + esc(initial(f.handle, f.key)) + '</span>' +
        '<span class="cn-fan-txt">' +
          '<span class="cn-fan-name">' + esc(who) + '</span>' +
          '<span class="cn-fan-last">' + (f.last_dir === 'out' ? '↩ ' : '') + esc(f.last || '') + '</span>' +
        '</span>' +
        '<span class="cn-fan-side"><span class="cn-fan-at">' + esc(ago(f.at)) + '</span>' +
          (f.waiting ? '<span class="cn-fan-wait">waiting</span>' : '') +
        '</span></button>';
    }).join('');
    Array.prototype.forEach.call(this.list.querySelectorAll('.cn-fan'), function (b) {
      b.onclick = function () { self.openThread(b.dataset.fan); };
    });
  };

  ChatView.prototype.openThread = function (key, quiet) {
    var self = this;
    this.fan = key;
    this.box.classList.add('reading');
    Array.prototype.forEach.call(this.list.querySelectorAll('.cn-fan'), function (b) {
      b.classList.toggle('on', b.dataset.fan === key);
    });
    if (!quiet) this.window.innerHTML = '<div class="cn-empty">Loading the chat…</div>';
    return fetch(this.url('&fan=' + encodeURIComponent(key)))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) throw new Error(d.error || 'could not load the chat');
        if (self.fan !== key) return;          // the operator moved on meanwhile
        self.renderThread(d);
      })
      .catch(function (e) {
        self.window.innerHTML = '<div class="cn-empty">Could not load the chat: ' +
          esc(String(e.message || e)) + '</div>';
      });
  };

  ChatView.prototype.renderThread = function (d) {
    var msgs = d.messages || [];
    var who = d.handle || (d.fan || '').replace(/^[a-z]+:/, '');
    this.name.textContent = who;
    this.meta.textContent = msgs.length
      ? msgs.length + ' message' + (msgs.length === 1 ? '' : 's') + ' · ' + d.platform
      : d.platform;

    // Messages and the log lines about them are one timeline, so a PPV drop or
    // a guardrail reads in the place in the conversation where it happened.
    var items = msgs.map(function (m) { return { kind: 'msg', at: m.at, m: m }; })
      .concat((d.events || []).map(function (e) { return { kind: 'event', at: e.at, e: e }; }));
    items.sort(function (a, b) { return (a.at || 0) - (b.at || 0); });

    if (!items.length) {
      this.window.innerHTML = '<div class="cn-empty">Nothing saved for this chat yet.</div>';
      return;
    }

    var html = '', day = '', prevDir = '';
    items.forEach(function (it) {
      var label = it.at ? dayLabel(it.at) : '';
      if (label && label !== day) {
        day = label;
        html += '<div class="date-sep">' + esc(label) + '</div>';
        prevDir = '';
      }
      if (it.kind === 'event') {
        html += '<div class="cn-event ' + esc(it.e.stage) + '">' +
          esc(it.e.detail || it.e.stage) + '</div>';
        prevDir = '';
        return;
      }
      // 'in' is the fan writing, so it takes the left/bot side — the same side
      // the persona's own words sit on in the fan's app, mirrored.
      var side = it.m.dir === 'out' ? 'user' : 'bot';
      var cont = side === prevDir ? ' cont' : '';
      prevDir = side;
      html += '<div class="msg-row ' + side + cont + '">' +
        '<div class="bubble">' + linkify(it.m.text) +
        '<span class="bubble-time">' + esc(clock(it.at)) + '</span></div></div>';
    });
    this.window.innerHTML = html;
    this.window.scrollTop = this.window.scrollHeight;
  };

  window.ChatView = {
    create: function (host, opts) { return new ChatView(host, opts); }
  };
})();
