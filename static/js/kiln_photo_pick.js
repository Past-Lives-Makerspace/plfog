/* One optional photo on a crew message or unload note (#1026).

   Each box ([data-kiln-photo-pick]) has two file inputs of the same name, the camera and the
   chooser. A pick clears the other input, so one photo posts, and shows the photo with a
   Remove that clears both. Nothing else: the server checks the file.

   A body script re-runs on every boosted arrival (FRONTEND.md, Scripts under hx-boost), so
   it binds once on document behind a window flag. */
(function () {
  if (window.plKilnPhotoPickBound) return;
  window.plKilnPhotoPickBound = true;

  function show(box, file) {
    var preview = box.querySelector('[data-kiln-photo-pick-preview]');
    var img = preview.querySelector('img');
    if (img.src) URL.revokeObjectURL(img.src);
    if (file) {
      img.src = URL.createObjectURL(file);
      img.alt = 'Photo to send: ' + file.name;
    } else {
      img.removeAttribute('src');
      img.alt = '';
    }
    preview.hidden = !file;
    box.querySelector('[data-kiln-photo-pick-buttons]').hidden = !!file;
  }

  document.addEventListener('change', function (event) {
    var input = event.target;
    if (!input.matches || !input.matches('[data-kiln-photo-pick-input]')) return;
    var box = input.closest('[data-kiln-photo-pick]');
    if (!box) return;
    box.querySelectorAll('[data-kiln-photo-pick-input]').forEach(function (other) {
      if (other !== input) other.value = '';
    });
    show(box, input.files && input.files[0]);
  });

  document.addEventListener('click', function (event) {
    var remove = event.target.closest && event.target.closest('[data-kiln-photo-pick-remove]');
    if (!remove) return;
    var box = remove.closest('[data-kiln-photo-pick]');
    box.querySelectorAll('[data-kiln-photo-pick-input]').forEach(function (input) { input.value = ''; });
    show(box, null);
    box.querySelector('[data-kiln-photo-pick-input]').focus();
  });
})();
