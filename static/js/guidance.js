// Guidance (#1007, part 3): a click on anything carrying a New! badge marks that key seen, and a
// click on a What's New button settles its unseen state. Loaded once per document from
// hub/base.html's <head>, deferred, and delegated on document, so a boosted arrival's badges are
// covered with nothing bound per page. Plain ES5.
//
// The badge is <span class="pl-new" data-new-key="guild.cubbies">New!</span> inside the label of
// a tab button, a link, a card or a sidebar link ({% new_badge %}). When a click lands inside such
// an element the key is posted to /whats-new/seen/<key>/ (hub_whats_new_seen, with the CSRF token
// hub_boot.js reads for htmx; keepalive, so a link that navigates away still lands it) and every
// badge with that key leaves the page in place. Nothing waits on the post. The phone picker's
// mirror of a button row follows the row by itself (plTabPicker's observer); its copy of a link
// row's badge carries the key, so a tap there is this same click.
//
// The What's New button ({% whats_new_button %}) opens the modal, whose body marks everything it
// lists seen on the server, so the button drops its pulse, its dot and its "something is new"
// wording at once rather than on the next load.
(function () {
  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') || '' : '';
  }

  function markSeen(key) {
    fetch('/whats-new/seen/' + encodeURIComponent(key) + '/', {
      method: 'POST',
      headers: { 'X-CSRFToken': csrfToken() },
      credentials: 'same-origin',
      keepalive: true,
    }).catch(function () {}); // the next load that lists the entry as seen catches up
    var badges = document.querySelectorAll('.pl-new[data-new-key="' + key + '"]');
    Array.prototype.forEach.call(badges, function (badge) { badge.parentNode.removeChild(badge); });
  }

  function settle(button) {
    button.classList.remove('pl-whats-new-btn--unseen');
    var parts = button.querySelectorAll('[data-whats-new-dot], [data-whats-new-sr]');
    Array.prototype.forEach.call(parts, function (part) { part.parentNode.removeChild(part); });
    var label = button.getAttribute('aria-label');
    if (label) button.setAttribute('aria-label', label.replace(/, something is new for you$/, ''));
  }

  document.addEventListener('click', function (event) {
    var target = event.target;
    if (!target || !target.closest) return;
    var button = target.closest('[data-whats-new-button]');
    if (button) settle(button);
    var element = target.closest('a, button');
    var badge = element && element.querySelector('.pl-new[data-new-key]');
    if (badge) markSeen(badge.getAttribute('data-new-key'));
  });
})();
