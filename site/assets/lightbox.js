// Optional enhancement: click a screen to see it full size. Without JavaScript the link just opens the image.
(function () {
  var dlg = document.getElementById("lightbox");
  if (!dlg || !dlg.showModal) return;
  var big = dlg.querySelector("img");
  document.querySelectorAll("a[data-zoom]").forEach(function (a) {
    a.addEventListener("click", function (e) {
      if (e.metaKey || e.ctrlKey || e.shiftKey) return;
      e.preventDefault();
      var img = a.querySelector("img");
      big.src = a.getAttribute("href");
      big.alt = img ? img.alt : "";
      dlg.showModal();
    });
  });
  dlg.addEventListener("click", function (e) {
    if (e.target !== big) dlg.close();
  });
})();
