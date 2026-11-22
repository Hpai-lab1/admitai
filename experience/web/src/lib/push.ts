import { staff } from "./api";

const fromB64url = (s: string) => {
  const b = atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4));
  return Uint8Array.from(b, (c) => c.charCodeAt(0));
};

export const pushSupported = () =>
  typeof window !== "undefined" &&
  "serviceWorker" in navigator &&
  "PushManager" in window &&
  "Notification" in window;

async function registration() {
  return navigator.serviceWorker.register("/sw.js", { scope: "/console" });
}

/** Is this browser getting pushes now? */
export async function pushIsOn(): Promise<boolean> {
  if (!pushSupported()) return false;
  const reg = await navigator.serviceWorker.getRegistration("/console");
  return !!(await reg?.pushManager.getSubscription());
}

/** Ask permission, subscribe and tell the server. Throws with a reason staff can act on. */
export async function turnPushOn(): Promise<void> {
  const key = await staff<{ enabled: boolean; public_key: string | null }>("/notifications/push/key");
  if (!key.enabled || !key.public_key)
    throw new Error("Browser notifications are not set up on this server.");
  if ((await Notification.requestPermission()) !== "granted")
    throw new Error("Notifications are blocked for this site. Allow them in the browser's site settings.");
  const reg = await registration();
  const sub =
    (await reg.pushManager.getSubscription()) ??
    (await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: fromB64url(key.public_key),
    }));
  await staff("/notifications/push/subscription", {
    method: "POST",
    body: JSON.stringify({ endpoint: sub.endpoint }),
  });
}

export async function turnPushOff(): Promise<void> {
  const reg = await navigator.serviceWorker.getRegistration("/console");
  const sub = await reg?.pushManager.getSubscription();
  if (!sub) return;
  await staff("/notifications/push/subscription", {
    method: "DELETE",
    body: JSON.stringify({ endpoint: sub.endpoint }),
  }).catch(() => {});
  await sub.unsubscribe();
}
