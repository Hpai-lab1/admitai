/* AdmitAI console: browser push (PRD F7.5). The push carries no content on purpose, so nothing
 * about an applicant passes through the push service; the details are in the console. */
self.addEventListener("push", (event) => {
  event.waitUntil(
    self.registration.showNotification("AdmitAI", {
      body: "Something new is waiting for you in the console.",
      tag: "admitai-console", // one at a time, replaced by the next
      renotify: true,
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
      const open = wins.find((w) => new URL(w.url).pathname.startsWith("/console"));
      return open ? open.focus() : self.clients.openWindow("/console");
    }),
  );
});
