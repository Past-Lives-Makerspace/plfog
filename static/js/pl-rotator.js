// plRotator: a card that shows one slide at a time and moves on by itself (#896; Home's Featured
// Class and Featured Event, the Home announcements banner, #889, and the sidebar Spotlight's
// announcements on every hub page, #890). Each instance keeps its own clock. Registered from
// hub/base.html's <head> before Alpine, on whichever side of alpine:init this lands (FRONTEND.md,
// Scripts under hx-boost), so a boosted arrival finds it.
//
// Markup contract (styles: static/css/rotator.css, loaded on every hub page):
//   x-data="plRotator" on the root, with
//     data-rotator-interval  milliseconds each slide shows (required, e.g. 6000)
//     data-rotator-delay     extra milliseconds before the first move (optional, default 0), so two
//                            rotators side by side never change together
//   [data-rotator-slide]     each slide; every slide sits in one grid cell, so the tallest sets the
//                            height and nothing jumps. The current one carries .is-active.
//   img[data-rotator-src]    a picture on a slide after the first, loaded only when its slide (or
//                            the one before it) shows
//   [data-rotator-bar]       optional, a bar whose fill runs over each slide's time (CSS animation)
//   data-rotator-name        on the root: which rotator this is ("spotlight", "home-banner",
//                            "home-upcoming"), so its pause is remembered apart from the others'
// The controls call prev(), next(), go(i) and toggle(). With fewer than two slides nothing moves.
//
// It moves on only while no mouse or pen rests on it, nothing in it has keyboard focus, the tab
// is visible and the pause button is off; a swipe that is mostly sideways moves it. With
// prefers-reduced-motion: reduce it never moves on by itself and the CSS drops the fade; the
// arrows and the dots still work. No live region: announcing every
// automatic move would talk over whatever the member is reading.
//
// A pause lasts until play or logging out (#890, Felix): toggle() keeps it in localStorage under
// pl-rotator-paused:<user pk>:<name>, the user pk read from <html data-pl-user> (never an email),
// and init starts that rotator paused. A submitted form to a /logout/ URL clears every such key,
// and on load this script drops the keys of anyone but the signed-in user (all of them on a
// signed-out page), so a shared computer never hands one person's pause to the next. With
// storage blocked a pause lasts for the page, as before.
(function () {
  var SWIPE_PX = 40;
  var PAUSE_PREFIX = 'pl-rotator-paused:';

  function currentUser() {
    return document.documentElement.getAttribute('data-pl-user') || '';
  }

  function pauseKey(name) {
    var user = currentUser();
    return user && name ? PAUSE_PREFIX + user + ':' + name : null;
  }

  function storedPause(name) {
    var key = pauseKey(name);
    if (!key) return false;
    try {
      return window.localStorage.getItem(key) === '1';
    } catch (e) {
      return false;
    }
  }

  function rememberPause(name, paused) {
    var key = pauseKey(name);
    if (!key) return;
    try {
      if (paused) window.localStorage.setItem(key, '1');
      else window.localStorage.removeItem(key);
    } catch (e) {
      /* Storage blocked: the pause lasts for this page only. */
    }
  }

  // Every remembered pause but the ones belonging to ``keepUser`` ("" forgets them all).
  function forgetPauses(keepUser) {
    var keep = keepUser ? PAUSE_PREFIX + keepUser + ':' : null;
    try {
      var storage = window.localStorage;
      for (var i = storage.length - 1; i >= 0; i--) {
        var key = storage.key(i);
        if (key && key.indexOf(PAUSE_PREFIX) === 0 && (keep === null || key.indexOf(keep) !== 0)) storage.removeItem(key);
      }
    } catch (e) {
      /* Storage blocked: nothing was kept. */
    }
  }

  forgetPauses(currentUser());
  // Logging out, from any form that posts to a logout URL (the confirm page, the member agreement
  // page, the app's WebView, which logs out through the same form). Capture, so it runs even when
  // another handler (biometric-auth.js) holds the submit for its own cleanup.
  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (form && form.tagName === 'FORM' && (form.getAttribute('action') || '').indexOf('/logout/') !== -1) forgetPauses('');
  }, true);

  function register() {
    window.Alpine.data('plRotator', function () {
      return {
        index: 0,
        count: 0,
        userPaused: false,
        hovering: false,
        focused: false,
        tabHidden: false,
        reduced: false,
        interval: 0,
        remaining: 0,
        startedAt: 0,
        timer: null,
        touchX: null,
        touchY: null,
        name: '',

        init() {
          var root = this.$root;
          this.count = root.querySelectorAll('[data-rotator-slide]').length;
          this.interval = parseInt(root.getAttribute('data-rotator-interval'), 10);
          var delay = parseInt(root.getAttribute('data-rotator-delay') || '0', 10);
          this.reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
          this.tabHidden = document.hidden;
          this.remaining = this.interval + delay;
          this.name = root.getAttribute('data-rotator-name') || '';
          this.userPaused = storedPause(this.name);
          this.loadPictures();
          if (this.count < 2) return;
          var self = this;
          this.onVisibility = function () {
            self.tabHidden = document.hidden;
            self.sync();
          };
          document.addEventListener('visibilitychange', this.onVisibility);
          // A mouse or pen resting on it holds it; a finger lifts at once, so a tap on a phone never holds it.
          root.addEventListener('pointerenter', function (e) {
            if (e.pointerType === 'touch') return;
            self.hovering = true;
            self.sync();
          });
          root.addEventListener('pointerleave', function (e) {
            if (e.pointerType === 'touch') return;
            self.hovering = false;
            self.sync();
          });
          // Keyboard focus holds it; the focus a tap or click leaves on an arrow or a dot does not.
          root.addEventListener('focusin', function (e) {
            self.focused = e.target.matches(':focus-visible');
            self.sync();
          });
          root.addEventListener('focusout', function (e) {
            if (!root.contains(e.relatedTarget)) { self.focused = false; self.sync(); }
          });
          // A swipe moves it only when it is mostly sideways, so scrolling the page past it never does.
          root.addEventListener('touchstart', function (e) {
            self.touchX = e.touches[0].clientX;
            self.touchY = e.touches[0].clientY;
          }, { passive: true });
          root.addEventListener('touchend', function (e) {
            if (self.touchX === null) return;
            var dx = e.changedTouches[0].clientX - self.touchX;
            var dy = e.changedTouches[0].clientY - self.touchY;
            self.touchX = null;
            self.touchY = null;
            if (Math.abs(dx) < SWIPE_PX || Math.abs(dx) <= Math.abs(dy)) return;
            if (dx < 0) self.next();
            else self.prev();
          }, { passive: true });
          this.restartBar(this.remaining);
          this.sync();
        },

        destroy() {
          this.stop();
          if (this.onVisibility) document.removeEventListener('visibilitychange', this.onVisibility);
        },

        running() {
          return this.count > 1 && !this.reduced && !this.userPaused && !this.hovering && !this.focused && !this.tabHidden;
        },

        // Start or stop the clock to match running(), keeping the time already spent on this slide.
        sync() {
          var bar = this.$root.querySelector('[data-rotator-bar]');
          if (bar) bar.style.animationPlayState = this.running() ? 'running' : 'paused';
          if (this.running() && this.timer === null) {
            var self = this;
            this.startedAt = Date.now();
            this.timer = window.setTimeout(function () {
              self.timer = null;
              self.show(self.index + 1);
            }, this.remaining);
          } else if (!this.running() && this.timer !== null) {
            this.stop();
            this.remaining = Math.max(0, this.remaining - (Date.now() - this.startedAt));
          }
        },

        stop() {
          if (this.timer !== null) window.clearTimeout(this.timer);
          this.timer = null;
        },

        show(i) {
          this.stop();
          this.index = (i + this.count) % this.count;
          this.remaining = this.interval;
          this.loadPictures();
          this.restartBar(this.interval);
          this.sync();
        },

        next() { this.show(this.index + 1); },
        prev() { this.show(this.index - 1); },
        go(i) { this.show(i); },

        toggle() {
          this.userPaused = !this.userPaused;
          rememberPause(this.name, this.userPaused);
          this.sync();
        },

        // The bar fills over ``ms``; restarting the animation keeps it in step with the clock.
        restartBar(ms) {
          var bar = this.$root.querySelector('[data-rotator-bar]');
          if (!bar) return;
          bar.style.animationDuration = ms + 'ms';
          bar.style.animationName = 'none';
          void bar.offsetWidth;
          bar.style.animationName = '';
        },

        // The current slide's pictures and the next one's, so a move never shows an empty frame.
        loadPictures() {
          var slides = this.$root.querySelectorAll('[data-rotator-slide]');
          [this.index, (this.index + 1) % Math.max(this.count, 1)].forEach(function (i) {
            if (!slides[i]) return;
            slides[i].querySelectorAll('img[data-rotator-src]').forEach(function (img) {
              img.src = img.getAttribute('data-rotator-src');
              img.removeAttribute('data-rotator-src');
            });
          });
        },
      };
    });
  }

  if (window.Alpine) register();
  else document.addEventListener('alpine:init', register);
})();
