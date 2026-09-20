// dsh-plugin-dshpp — P1 client half.
//
// Plain-DOM floating button (portal-style append to <body>) that opens the
// host-served read-only panel at /dshpp. No React, no Node API (purity gate):
// the panel page itself is a single-file SPA served by the host webServer
// route and talks to /dshpp/* with same-origin fetch (403 fence passes
// natively for the GUI).
window.__ModuleLoader__.load({
  id: "dsh-plugin-dshpp",
  factory: (require) => {
    const BUTTON_ID = "dshpp-probe-button";
    const PANEL_HREF = "/dshpp";

    function openPanel() {
      window.open(PANEL_HREF, "_blank", "noopener");
    }

    function ensureButton() {
      if (document.getElementById(BUTTON_ID)) return;
      const btn = document.createElement("button");
      btn.id = BUTTON_ID;
      btn.textContent = "DSH++";
      btn.title = "DSH++ P1 面板（只读）：打开 /dshpp";
      btn.style.cssText = [
        "position:fixed",
        "right:16px",
        "bottom:16px",
        "z-index:2147483647",
        "padding:6px 14px",
        "border-radius:999px",
        "border:1px solid #3b4a6b",
        "background:#151b2b",
        "color:#9ecbff",
        "cursor:pointer",
        "font-size:12px",
      ].join(";");
      btn.addEventListener("click", openPanel);
      const attach = () => {
        if (document.body && !document.getElementById(BUTTON_ID)) {
          document.body.appendChild(btn);
        }
      };
      if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", attach, { once: true });
      } else {
        attach();
      }
    }

    ensureButton();

    // DevTools handle: window.__DSHPP__
    window.__DSHPP__ = {
      version: "0.2.0",
      open: openPanel,
      panelHref: PANEL_HREF,
    };
  },
});
