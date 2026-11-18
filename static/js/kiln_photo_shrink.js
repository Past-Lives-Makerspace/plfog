/* Shrink a kiln photo on the phone before it is sent (#1246).

   window.plKilnShrinkPhoto(file, longEdge) resolves to a JPEG File no longer than longEdge on
   its long side (the server's IMAGE_MAX_LONG_EDGE_GALLERY, which it keeps anyway), or to the
   original file when it is already small (under SMALL_ENOUGH, so it is not encoded twice), the
   browser cannot decode it, or the copy would not be smaller. It never rejects, so a pick
   always has something to send. The photo is decoded in an <img>, which applies the camera's
   EXIF rotation, then drawn once onto a canvas. The server still checks and resizes every
   upload.

   Loaded as a body script by each page with a kiln photo control; defining the function again
   on a boosted arrival is harmless. */
(function () {
  var QUALITY = 0.82;
  var SMALL_ENOUGH = 512 * 1024;

  function decode(file) {
    return new Promise(function (resolve, reject) {
      var url = URL.createObjectURL(file);
      var img = new Image();
      img.onload = function () { URL.revokeObjectURL(url); resolve(img); };
      img.onerror = function () { URL.revokeObjectURL(url); reject(new Error('decode')); };
      img.src = url;
    });
  }

  function jpegName(name) {
    var base = (name || 'photo').replace(/\.[^.]*$/, '');
    return (base || 'photo') + '.jpg';
  }

  window.plKilnShrinkPhoto = function (file, longEdge) {
    var edge = Number(longEdge) || 0;
    if (!edge || !file || file.size < SMALL_ENOUGH || !/^image\//.test(file.type || 'image/')) return Promise.resolve(file);
    return decode(file).then(function (img) {
      var width = img.naturalWidth;
      var height = img.naturalHeight;
      var scale = Math.min(1, edge / Math.max(width, height));
      var canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(width * scale));
      canvas.height = Math.max(1, Math.round(height * scale));
      var context = canvas.getContext('2d');
      context.imageSmoothingQuality = 'high';
      context.drawImage(img, 0, 0, canvas.width, canvas.height);
      return new Promise(function (resolve) {
        canvas.toBlob(function (blob) {
          if (!blob || blob.size >= file.size) { resolve(file); return; }
          resolve(new File([blob], jpegName(file.name), { type: 'image/jpeg', lastModified: Date.now() }));
        }, 'image/jpeg', QUALITY);
      });
    }).catch(function () { return file; });
  };
})();
