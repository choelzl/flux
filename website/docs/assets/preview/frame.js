// The Preview app's tab (preview.md): the app in a frame (preview/app.html) under the docs' header. Its address is the
// tab's: /preview/#/app/adder8/live opens the frame there, and the frame's moves are written back to the tab's
// address (replaced, so Back leaves the tab as it would any page). The docs' light or dark scheme is the app's theme;
// the frame fills the window below the header.
(function () {
  var f = document.getElementById("flux-preview");
  if (!f || f.dataset.ready) return;
  f.dataset.ready = "1";
  var appHash = function () { return location.hash && location.hash.indexOf("#/") === 0 ? location.hash : "#/"; };
  var as = new URLSearchParams(location.search).get("as");
  f.src = "app.html" + (as ? "?as=" + encodeURIComponent(as) : "") + appHash();
  var theme = function () {
    var dark = document.body.getAttribute("data-md-color-scheme") === "slate";
    try { f.contentDocument.documentElement.dataset.theme = dark ? "dark" : "light"; } catch (e) { /* not loaded yet */ }
  };
  new MutationObserver(theme).observe(document.body, { attributes: true, attributeFilter: ["data-md-color-scheme"] });
  var fit = function () { f.style.height = Math.max(420, window.innerHeight - f.getBoundingClientRect().top - window.scrollY) + "px"; };
  window.addEventListener("resize", fit);
  f.addEventListener("load", function () {
    theme(); fit();
    var w = f.contentWindow;
    var follow = function () {
      var q = new URLSearchParams(w.location.search).get("as");
      var here = location.pathname + (q ? "?as=" + encodeURIComponent(q) : "") + (w.location.hash || "");
      if (location.pathname + location.search + location.hash !== here) history.replaceState(null, "", here);
    };
    w.addEventListener("hashchange", follow);
    follow();
  });
  window.addEventListener("hashchange", function () {
    var w = f.contentWindow;
    if (w && w.location.hash !== appHash()) w.location.hash = appHash();
  });
  fit();
})();
