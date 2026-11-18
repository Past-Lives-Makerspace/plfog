/* Kiln ticket photos (#691, #1246): each photo uploads on its own the moment it is picked.

   A pick is read and the file input emptied, so Save never posts the photos again. Each file
   gets a tile saying "Uploading", is shrunk on the phone (window.plKilnShrinkPhoto, to the
   form's data-kiln-long-edge) and joins one queue that sends a photo at a time to the form's
   data-kiln-photo-url, the progress on its tile. A stored photo's tile is swapped for the one
   the server rendered (Make cover, Remove); one lost to the connection or a server error
   becomes a red row with "Didn't upload", a Retry and an X that drops it. A photo the server
   refuses (400: not a photo, too large; 409: the ticket can no longer change) gets the
   server's own words and only the X. A failure costs that photo only.

   On a new ticket the first stored photo started a draft: the form now saves to that draft's
   edit page, the address bar says so (history.replaceState, so a reload keeps it), and the
   next photos go to its upload address. The form hears the stored and pending counts through
   "kiln-photos-changed", which enables Submit and holds Save while an upload is running; a
   submit listener stops every other way of saving then and says to wait.

   Without this script the inputs keep their files and the photos post with the form.

   A body script re-runs on every boosted arrival (FRONTEND.md, Scripts under hx-boost), so
   it binds once on document behind a window flag; the queue lives with it. */
(function () {
  if (window.plKilnPhotosBound) return;
  window.plKilnPhotosBound = true;

  var queue = [];
  var busy = false;

  function pending(form) {
    return form.querySelectorAll('[data-kiln-upload-state="uploading"]').length;
  }

  function announce(form) {
    var uploading = pending(form);
    if (!uploading) form.querySelectorAll('[data-kiln-upload-wait]').forEach(function (line) { line.hidden = true; });
    form.dispatchEvent(new CustomEvent('kiln-photos-changed', {
      detail: {
        count: form.querySelectorAll('[data-kiln-photo][data-kiln-uploaded]').length,
        uploading: uploading,
      },
    }));
  }

  // One token per new ticket form, sent with every upload that may start the draft, so a
  // reply lost on the way back cannot make a Retry or the next photo start a second draft.
  function newTicketToken(form) {
    if (!form.dataset.kilnNewToken) {
      form.dataset.kilnNewToken = window.crypto && crypto.randomUUID
        ? crypto.randomUUID()
        : String(Date.now()) + Math.random().toString(16).slice(2);
    }
    return form.dataset.kilnNewToken;
  }

  function button(className, text, label) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = className;
    b.textContent = text;
    if (label) b.setAttribute('aria-label', label);
    b.hidden = true;
    return b;
  }

  function newTile(file) {
    var tile = document.createElement('div');
    tile.className = 'pl-kiln-photo pl-kiln-photo--new';
    tile.setAttribute('data-kiln-new-photo', '');
    var img = document.createElement('img');
    img.alt = 'New photo: ' + file.name;
    img.src = URL.createObjectURL(file);
    tile.appendChild(img);
    var status = document.createElement('span');
    status.className = 'pl-kiln-photo__status';
    status.setAttribute('role', 'status');
    status.setAttribute('data-kiln-photo-status', '');
    tile.appendChild(status);
    var retry = button('pl-kiln-photo__retry', 'Retry');
    retry.setAttribute('data-kiln-photo-retry', '');
    tile.appendChild(retry);
    var remove = button('pl-kiln-photo__remove', '×', 'Remove ' + file.name);
    remove.setAttribute('data-kiln-photo-drop', '');
    tile.appendChild(remove);
    return tile;
  }

  function setState(job, state, text) {
    var tile = job.tile;
    tile.setAttribute('data-kiln-upload-state', state);
    tile.classList.toggle('pl-kiln-photo--failed', state === 'failed' || state === 'refused');
    tile.classList.toggle('pl-kiln-photo--refused', state === 'refused');
    tile.querySelector('[data-kiln-photo-status]').textContent = text;
    tile.querySelector('[data-kiln-photo-retry]').hidden = state !== 'failed';
    tile.querySelector('[data-kiln-photo-drop]').hidden = state === 'uploading';
    announce(job.form);
  }

  function stored(job, answer) {
    var form = job.form;
    var holder = document.createElement('template');
    holder.innerHTML = answer.tile.trim();
    var tile = holder.content.firstElementChild;
    tile.setAttribute('data-kiln-uploaded', '');
    URL.revokeObjectURL(job.tile.querySelector('img').src);
    job.tile.replaceWith(tile);
    if (form.dataset.kilnPhotoUrl !== answer.upload_url) {
      form.dataset.kilnPhotoUrl = answer.upload_url;
      form.setAttribute('action', answer.edit_url);
      history.replaceState(history.state, '', answer.edit_url);
    }
    announce(form);
  }

  function send(job) {
    return new Promise(function (resolve) {
      var form = job.form;
      var data = new FormData();
      data.append('csrfmiddlewaretoken', form.querySelector('[name=csrfmiddlewaretoken]').value);
      data.append('photo', job.upload, job.upload.name);
      data.append('new_token', newTicketToken(form));
      var xhr = new XMLHttpRequest();
      xhr.open('POST', form.dataset.kilnPhotoUrl);
      xhr.setRequestHeader('Accept', 'application/json');
      xhr.upload.onprogress = function (event) {
        if (event.lengthComputable) setState(job, 'uploading', 'Uploading ' + Math.round((event.loaded / event.total) * 100) + '%');
      };
      xhr.onload = function () {
        var answer = null;
        try { answer = JSON.parse(xhr.responseText); } catch (e) { answer = null; }
        if (xhr.status === 200 && answer && answer.tile) stored(job, answer);
        // The server refused the photo itself (400) or the ticket can no longer change (409):
        // sending it again cannot help, so its words show with only Remove.
        else if ((xhr.status === 400 || xhr.status === 409) && answer && answer.error) setState(job, 'refused', answer.error);
        else setState(job, 'failed', "Didn't upload");
        resolve();
      };
      xhr.onerror = xhr.ontimeout = xhr.onabort = function () {
        setState(job, 'failed', "Didn't upload");
        resolve();
      };
      xhr.send(data);
    });
  }

  function pump() {
    if (busy || !queue.length) return;
    var job = queue.shift();
    if (!document.contains(job.tile)) { pump(); return; }
    busy = true;
    var ready = job.upload
      ? Promise.resolve(job.upload)
      : window.plKilnShrinkPhoto(job.file, job.form.dataset.kilnLongEdge);
    ready.then(function (upload) {
      job.upload = upload;
      return send(job);
    }).then(function () {
      busy = false;
      pump();
    });
  }

  function enqueue(job) {
    // The tile keeps its job, so Retry sends the same shrunk photo again.
    job.tile.plKilnJob = job;
    setState(job, 'uploading', 'Uploading');
    queue.push(job);
    pump();
  }

  document.addEventListener('change', function (event) {
    var input = event.target;
    if (!input.matches || !input.matches('[data-kiln-photo-input]')) return;
    var form = input.closest('[data-kiln-form]');
    if (!form || !input.files || !input.files.length) return;
    var grid = form.querySelector('[data-kiln-photos]');
    var addTile = form.querySelector('[data-kiln-photo-add]');
    var files = Array.prototype.slice.call(input.files);
    input.value = '';
    files.forEach(function (file) {
      var tile = newTile(file);
      grid.insertBefore(tile, addTile);
      enqueue({ form: form, tile: tile, file: file, upload: null });
    });
    addTile.classList.remove('pl-kiln-photo-add--big');
  });

  // While a photo is uploading no save may go out: Save, Submit, a tile's Make cover or Remove,
  // or Enter in a field (the hidden default button). The page would leave and drop the upload.
  document.addEventListener('submit', function (event) {
    var form = event.target;
    if (!form.matches || !form.matches('[data-kiln-form]') || !pending(form)) return;
    event.preventDefault();
    form.querySelectorAll('[data-kiln-upload-wait]').forEach(function (line) { line.hidden = false; });
  }, true);

  document.addEventListener('click', function (event) {
    var target = event.target.closest && event.target.closest('[data-kiln-photo-retry], [data-kiln-photo-drop]');
    if (!target) return;
    var tile = target.closest('[data-kiln-new-photo]');
    var form = tile.closest('[data-kiln-form]');
    if (target.matches('[data-kiln-photo-drop]')) {
      URL.revokeObjectURL(tile.querySelector('img').src);
      tile.remove();
      announce(form);
      return;
    }
    enqueue(tile.plKilnJob);
  });
})();
