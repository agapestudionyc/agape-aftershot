/* The part of Agape Aftershot that keeps running when nothing is open.

   A service worker is not the app. It has no window, it is not on screen, and
   the browser starts it, hands it one event and stops it again. That is
   exactly what makes it the right place for this: it is alive when the app is
   closed and the phone is in a pocket, which is when a long job most needs to
   be able to say something.

   The push that wakes it carries nothing at all - no title, no text, no
   numbers. It is a knock. Everything shown below is fetched from the machine
   with the photographs on it, at the moment the notification is drawn, which
   means two things: nothing about your work passes through Google's servers on
   the way, and what you read is the state *now* rather than whenever the push
   happened to be queued. */

const TAG = 'aftershot-run';   // one line that keeps changing, never a stack

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

self.addEventListener('push', event => {
  event.waitUntil((async () => {
    let title = 'Agape Aftershot';
    let body = 'Still working.';
    let done = true;
    try {
      // no-store, because a cached answer here is a lie about the present.
      const r = await fetch('/api/progress', {cache: 'no-store'});
      const d = await r.json();
      if (d && d.title) { title = d.title; body = d.body; done = !!d.done; }
    } catch (e) {
      // Unreachable - off the tailnet, or the computer went to sleep. Say the
      // honest thing rather than the last thing we happened to know.
      body = 'Something moved, but the computer is not answering right now.';
    }
    await self.registration.showNotification(title, {
      body,
      tag: TAG,
      icon: '/static/icon-192.png',
      badge: '/static/icon-192.png',
      // A job that is still going should update the line quietly. A job that
      // has finished is the thing you have been waiting for, so it is allowed
      // to buzz and to stay on screen until it is looked at.
      renotify: done,
      silent: !done,
      requireInteraction: done,
      timestamp: Date.now(),
    });
  })());
});

/* Tapping it should land you in the app you already have open, not open a
   second copy of it beside the first. */
self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil((async () => {
    const open = await self.clients.matchAll({type: 'window',
                                              includeUncontrolled: true});
    for (const c of open) {
      if ('focus' in c) return c.focus();
    }
    return self.clients.openWindow('/');
  })());
});
