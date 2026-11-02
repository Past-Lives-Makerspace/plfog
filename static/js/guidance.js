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
// The What's New button ({% whats_new_button %}) opens the modal, whose body records the open
// over everything it could show (Member.whats_new_opened, listed or not), so the button drops its
// pulse, its dot and its "something is new" wording at once rather than on the next load, and
// stays quiet until a release adds an entry.
//
// Open <feature> and Show me (#1051). An entry's Open <feature> link lands on the feature's page
// with ?show=<key>; hub/base.html carries the entry it names as #pl-guidance-show (its target,
// title and first bullet), read once here on the load or the boosted arrival, then removed, and
// the param is stripped so a reload does not ring the control again. Show me, on the page the
// entry lives on, carries the same three on its button (data-whats-new-show), closes the modal
// and rings in place. Both hand them to pl_tour.js's one step mode, which does nothing at all
// when no element carries data-guidance-target="<key>".
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

  function highlight(key, title, body) {
    if (!window.plTour || !window.plTour.show) return;
    window.plTour.show({ selector: '[data-guidance-target="' + key + '"]', title: title, body: body });
  }

  var consumed = ''; // the ?show= key this page's payload named, until the URL has been stripped of it

  function stripShow(key) {
    // Only our own key leaves the URL: the Bookings tabs carry ?show= for their chips (show=reply,
    // show=all) and keep it. A key always has a dot, so the two can never be the same value.
    try {
      var url = new URL(window.location.href);
      if (!key || url.searchParams.get('show') !== key) return false;
      url.searchParams.delete('show');
      var qs = url.searchParams.toString();
      window.history.replaceState(window.history.state, '', url.pathname + (qs ? '?' + qs : '') + url.hash);
      return true;
    } catch (e) {
      return false; // the param stays; a reload rings the control once more
    }
  }

  function showFromPage() {
    var data = document.getElementById('pl-guidance-show');
    if (!data) return;
    var payload = null;
    try { payload = JSON.parse(data.textContent); } catch (e) { payload = null; }
    data.parentNode.removeChild(data); // once per arrival
    if (!payload || !payload.target) return;
    consumed = payload.target;
    if (stripShow(consumed)) consumed = '';
    highlight(payload.target, payload.title, payload.body);
  }

  function showHere(button) {
    window.dispatchEvent(new CustomEvent('close-modal', { detail: 'whats-new' }));
    highlight(button.getAttribute('data-whats-new-show'), button.getAttribute('data-show-title'), button.getAttribute('data-show-body'));
  }

  document.addEventListener('click', function (event) {
    var target = event.target;
    if (!target || !target.closest) return;
    var button = target.closest('[data-whats-new-button]');
    if (button) settle(button);
    var show = target.closest('[data-whats-new-show]');
    if (show) showHere(show);
    var element = target.closest('a, button');
    var badge = element && element.querySelector('.pl-new[data-new-key]');
    if (badge) markSeen(badge.getAttribute('data-new-key'));
  });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', showFromPage);
  else showFromPage();
  document.addEventListener('htmx:afterSettle', showFromPage); // a boosted arrival swaps the body, payload included
  document.addEventListener('htmx:pushedIntoHistory', function () {
    // htmx pushes the URL after the swap, so on a boosted arrival the key is still to be stripped here.
    if (consumed && stripShow(consumed)) consumed = '';
  });
})();
