// Optional enhancement: collapses the nav into a menu button on phones. Without JavaScript the full nav stays visible.
(function () {
  var btn = document.querySelector(".menu-toggle");
  var nav = document.getElementById("site-nav");
  if (!btn || !nav) return;
  var root = document.documentElement;
  root.classList.add("js");
  btn.hidden = false;
  function set(open) {
    root.classList.toggle("nav-open", open);
    btn.setAttribute("aria-expanded", open ? "true" : "false");
  }
  btn.addEventListener("click", function () {
    set(btn.getAttribute("aria-expanded") !== "true");
  });
  nav.addEventListener("click", function (e) {
    if (e.target.closest("a")) set(false);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && btn.getAttribute("aria-expanded") === "true") {
      set(false);
      btn.focus();
    }
  });
})();
