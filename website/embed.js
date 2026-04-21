(function() {
  var BASE = "https://semeclaw.fly.dev";
  function mount(el) {
    if (el.getAttribute("data-semeclaw-mounted") === "1") return;
    el.setAttribute("data-semeclaw-mounted", "1");
    var meeting = el.getAttribute("data-semeclaw-meeting") || "";
    var layout  = el.getAttribute("data-semeclaw-v") || "1";
    var theme   = el.getAttribute("data-semeclaw-theme") || "dark";
    var url = BASE + "/embed?v=" + encodeURIComponent(layout) +
              "&theme=" + encodeURIComponent(theme) +
              (meeting ? "&meeting=" + encodeURIComponent(meeting) : "");
    var iframe = document.createElement("iframe");
    iframe.src = url;
    iframe.style.width = el.style.width || "100%";
    iframe.style.height = el.style.height || "640px";
    iframe.style.border = "0";
    iframe.style.borderRadius = el.style.borderRadius || "12px";
    iframe.setAttribute("allow", "autoplay; clipboard-write");
    iframe.setAttribute("loading", "lazy");
    iframe.title = "SemeClaw War Room";
    el.innerHTML = "";
    el.appendChild(iframe);
  }
  function scan() {
    var nodes = document.querySelectorAll("[data-semeclaw-meeting], [data-semeclaw-embed]");
    for (var i = 0; i < nodes.length; i++) mount(nodes[i]);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", scan);
  } else {
    scan();
  }
  window.SemeClaw = { mount: mount, scan: scan, base: BASE };
})();
