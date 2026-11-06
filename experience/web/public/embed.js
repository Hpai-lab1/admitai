/* AdmitAI chat widget loader. Paste on any page of the college website:
 *   <script src="https://<admitai-host>/embed.js" data-key="pk_..." async></script>
 * Optional: data-label="Ask Admissions"  data-color="#E34A21"
 */
(function () {
  var script = document.currentScript;
  if (!script || window.__admitaiLoaded) return;
  window.__admitaiLoaded = true;
  var key = script.getAttribute("data-key");
  var origin = new URL(script.src).origin;
  var label = script.getAttribute("data-label") || "Ask Admissions";
  var color = script.getAttribute("data-color") || "#1E3A8A";
  var utm = new URLSearchParams(location.search);
  var q = new URLSearchParams({ key: key || "", ref: location.href.slice(0, 400) });
  utm.forEach(function (v, k) {
    if (k.indexOf("utm_") === 0) q.set(k, v);
  });

  var button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.setAttribute("aria-haspopup", "dialog");
  button.setAttribute("aria-expanded", "false");
  button.style.cssText =
    "position:fixed;right:20px;bottom:20px;z-index:2147483000;border:0;border-radius:999px;" +
    "padding:14px 20px;font:600 15px system-ui,sans-serif;color:#fff;background:" +
    color +
    ";" +
    "box-shadow:0 8px 24px rgba(0,0,0,.25);cursor:pointer";

  var frame = document.createElement("iframe");
  frame.title = "Admissions chat";
  frame.setAttribute("allow", "microphone");
  frame.style.cssText =
    "position:fixed;right:20px;bottom:84px;z-index:2147483000;width:400px;height:620px;" +
    "max-width:calc(100vw - 24px);max-height:calc(100vh - 110px);border:0;border-radius:16px;" +
    "box-shadow:0 16px 48px rgba(0,0,0,.3);background:#fff;display:none";

  function setOpen(open) {
    if (open && !frame.src) frame.src = origin + "/widget?" + q.toString();
    frame.style.display = open ? "block" : "none";
    button.setAttribute("aria-expanded", String(open));
    if (open) frame.focus();
    else button.focus();
  }
  button.addEventListener("click", function () {
    setOpen(frame.style.display === "none");
  });
  window.addEventListener("message", function (e) {
    if (e.origin === origin && e.data && e.data.type === "admitai:close") setOpen(false);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && frame.style.display !== "none") setOpen(false);
  });
  window.AdmitAI = {
    open: function () {
      setOpen(true);
    },
    close: function () {
      setOpen(false);
    },
  };
  document.body.appendChild(frame);
  document.body.appendChild(button);
})();
