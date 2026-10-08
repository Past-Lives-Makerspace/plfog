/* Kiln ticket photos (#691): preview what the maker picked, and let them keep adding.

   A file input holds only its latest pick, so each pick parks the used input in the form's
   holder (it still posts as "photos") and puts a fresh empty one back where it was. Each
   pick shows as "New" tiles with a Remove that drops that pick. The form hears the running
   count through a "kiln-photos-changed" event, which enables Submit.

   A body script re-runs on every boosted arrival (FRONTEND.md, Scripts under hx-boost), so
   it binds once on document behind a window flag. */
(function () {
  if (window.plKilnPhotosBound) return;
  window.plKilnPhotosBound = true;

  function newCount(form) {
    var count = 0;
    form.querySelectorAll('[data-kiln-photo-holder] input[type="file"]').forEach(function (input) {
      count += input.files ? input.files.length : 0;
    });
    return count;
  }

  function announce(form) {
    form.dispatchEvent(new CustomEvent('kiln-photos-changed', { detail: { count: newCount(form) } }));
  }

  document.addEventListener('change', function (event) {
    var input = event.target;
    if (!input.matches || !input.matches('[data-kiln-photo-input]')) return;
    var form = input.closest('[data-kiln-form]');
    if (!form || !input.files || !input.files.length) return;
    var grid = form.querySelector('[data-kiln-photos]');
    var addTile = form.querySelector('[data-kiln-photo-add]');
    var holder = form.querySelector('[data-kiln-photo-holder]');

    var fresh = input.cloneNode(false);
    fresh.value = '';
    input.parentNode.insertBefore(fresh, input);
    holder.appendChild(input);

    var tiles = [];
    Array.prototype.forEach.call(input.files, function (file) {
      var tile = document.createElement('div');
      tile.className = 'pl-kiln-photo pl-kiln-photo--new';
      tile.setAttribute('data-kiln-new-photo', '');
      var img = document.createElement('img');
      img.alt = 'New photo: ' + file.name;
      img.src = URL.createObjectURL(file);
      tile.appendChild(img);
      var remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'pl-kiln-photo__remove';
      remove.setAttribute('aria-label', 'Remove ' + file.name);
      remove.textContent = '×';
      remove.addEventListener('click', function () {
        tiles.forEach(function (t) { t.remove(); });
        input.remove();
        announce(form);
      });
      tile.appendChild(remove);
      grid.insertBefore(tile, addTile);
      tiles.push(tile);
    });
    addTile.classList.remove('pl-kiln-photo-add--big');
    announce(form);
  });
})();
