/* One optional photo on a crew message or unload note (#1026).

   Each box ([data-kiln-photo-pick]) has two file inputs of the same name, the camera and the
   chooser. A pick over the box's data-max-bytes (the server's 10 MB cap) is cleared with a
   message under the control, so it is never sent. Otherwise the pick clears the other input,
   so one photo posts, shrinks it on the phone (window.plKilnShrinkPhoto, to the box's
   data-long-edge) back into the input, and shows the photo with a Remove that clears both. On Unload, ticking a
   row again (data-kiln-photo-pick-reset) clears its photo, so a fired piece uploads nothing.
   The server still checks every file.

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

  function say(box, text) {
    var error = box.querySelector('[data-kiln-photo-pick-error]');
    error.textContent = text;
    error.hidden = !text;
  }

  function clear(box) {
    box.querySelectorAll('[data-kiln-photo-pick-input]').forEach(function (input) { input.value = ''; });
    show(box, null);
  }

  document.addEventListener('change', function (event) {
    var input = event.target;
    if (!input.matches) return;
    if (input.matches('[data-kiln-photo-pick-reset]')) {
      var row = input.closest('[data-unload-row]');
      if (input.checked && row) row.querySelectorAll('[data-kiln-photo-pick]').forEach(function (box) { clear(box); say(box, ''); });
      return;
    }
    if (!input.matches('[data-kiln-photo-pick-input]')) return;
    var box = input.closest('[data-kiln-photo-pick]');
    if (!box) return;
    var file = input.files && input.files[0];
    var cap = Number(box.dataset.maxBytes) || 0;
    if (file && cap && file.size > cap) {
      clear(box);
      say(box, 'That photo is too large. A photo can be up to ' + Math.round(cap / 1048576) + ' MB.');
      return;
    }
    say(box, '');
    box.querySelectorAll('[data-kiln-photo-pick-input]').forEach(function (other) {
      if (other !== input) other.value = '';
    });
    if (!file) { show(box, null); return; }
    // Shrunk on the phone (#1246) and put back in the input, so the form posts the small copy.
    var pick = (box.plKilnPick || 0) + 1;
    box.plKilnPick = pick;
    window.plKilnShrinkPhoto(file, box.dataset.longEdge).then(function (small) {
      if (box.plKilnPick !== pick || !input.files || input.files[0] !== file) return;
      if (small !== file && window.DataTransfer) {
        try {
          var transfer = new DataTransfer();
          transfer.items.add(small);
          input.files = transfer.files;
        } catch (e) { small = file; }
      }
      show(box, input.files[0] || small);
    });
  });

  document.addEventListener('click', function (event) {
    var remove = event.target.closest && event.target.closest('[data-kiln-photo-pick-remove]');
    if (!remove) return;
    var box = remove.closest('[data-kiln-photo-pick]');
    clear(box);
    box.querySelector('[data-kiln-photo-pick-input]').focus();
  });
})();
