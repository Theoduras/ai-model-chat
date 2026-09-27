// Service worker for the admin support inbox's push alerts. The push arrives
// empty; what to show is fetched with the admin's own cookie, so no message
// text passes through the browser vendor's push service.
self.addEventListener('push', function (event) {
  event.waitUntil(
    fetch('/api/admin/support/latest', { credentials: 'include' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; })
      .then(function (d) {
        d = d || {};
        return self.registration.showNotification(d.title || 'Support', {
          body: d.body || 'New message in the support inbox.',
          tag: d.tag || 'support',
          renotify: true,
          icon: '/favicon.png',
          data: { url: d.url || '/admin/support' },
        });
      })
  );
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  var url = (event.notification.data && event.notification.data.url) || '/admin/support';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (wins) {
      for (var i = 0; i < wins.length; i++) {
        if (wins[i].url.indexOf('/admin/support') >= 0 && 'focus' in wins[i]) {
          wins[i].navigate(url);
          return wins[i].focus();
        }
      }
      return self.clients.openWindow(url);
    })
  );
});
