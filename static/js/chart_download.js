/*
 * Download a Board Report chart as a PNG (Instructor Inquiries, #690).
 *
 * A [data-chart-download="<filename>"] button inside a [data-chart] figure draws that
 * figure's inline svg onto a white canvas at three times its size and saves it. The svg's own
 * fill and stroke attributes are the image's colours; the page stylesheet that themes the
 * chart on screen does not reach an svg drawn as an image, which is the point.
 *
 * A body script, so it re-runs on every boosted arrival; the click is delegated on
 * document once, behind a flag on window (FRONTEND.md, Scripts under hx-boost).
 */
(function () {
  "use strict";
  if (window.plChartDownloadBound) return;
  window.plChartDownloadBound = true;

  var SCALE = 3;

  function save(blob, filename) {
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  function download(svg, filename) {
    var width = Number(svg.getAttribute("width"));
    var height = Number(svg.getAttribute("height"));
    var markup = new XMLSerializer().serializeToString(svg);
    var image = new Image();
    image.onload = function () {
      var canvas = document.createElement("canvas");
      canvas.width = width * SCALE;
      canvas.height = height * SCALE;
      var context = canvas.getContext("2d");
      context.fillStyle = "#ffffff";
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      canvas.toBlob(function (blob) { save(blob, filename); }, "image/png");
    };
    image.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(markup);
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-chart-download]");
    if (!button) return;
    var svg = button.closest("[data-chart]").querySelector("svg");
    download(svg, button.dataset.chartDownload);
  });
})();
