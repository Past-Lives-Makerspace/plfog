// plRotator: a card that shows one slide at a time and moves on by itself (#896; Home's Featured
// Class and Featured Event, and the Home announcements banner next, #889). Registered from
// hub/base.html's <head> before Alpine, on whichever side of alpine:init this lands (FRONTEND.md,
// Scripts under hx-boost), so a boosted arrival finds it.
//
// Markup contract (styles: the .pl-rotator block in static/css/home-featured.css):
//   x-data="plRotator" on the root, with
//     data-rotator-interval  milliseconds each slide shows (required, e.g. 6000)
//     data-rotator-delay     extra milliseconds before the first move (optional, default 0), so two
//                            rotators side by side never change together
//   [data-rotator-slide]     each slide; every slide sits in one grid cell, so the tallest sets the
//                            height and nothing jumps. The current one carries .is-active.
//   img[data-rotator-src]    a picture on a slide after the first, loaded only when its slide (or
//                            the one before it) shows
//   [data-rotator-bar]       optional, a bar whose fill runs over each slide's time (CSS animation)
// The controls call prev(), next(), go(i) and toggle(). With fewer than two slides nothing moves.
//
// It moves on only while nobody is pointing at it, nothing in it has focus, the tab is visible
// and the pause button is off. With prefers-reduced-motion: reduce it never moves on by itself and
// the CSS drops the fade; the arrows and the dots still work. No live region: announcing every
// automatic move would talk over whatever the member is reading.
(function () {
  var SWIPE_PX = 40;

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

        init() {
          var root = this.$root;
          this.count = root.querySelectorAll('[data-rotator-slide]').length;
          this.interval = parseInt(root.getAttribute('data-rotator-interval'), 10);
          var delay = parseInt(root.getAttribute('data-rotator-delay') || '0', 10);
          this.reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
          this.tabHidden = document.hidden;
          this.remaining = this.interval + delay;
          this.loadPictures();
          if (this.count < 2) return;
          var self = this;
          this.onVisibility = function () {
            self.tabHidden = document.hidden;
            self.sync();
          };
          document.addEventListener('visibilitychange', this.onVisibility);
          root.addEventListener('mouseenter', function () { self.hovering = true; self.sync(); });
          root.addEventListener('mouseleave', function () { self.hovering = false; self.sync(); });
          root.addEventListener('focusin', function () { self.focused = true; self.sync(); });
          root.addEventListener('focusout', function (e) {
            if (!root.contains(e.relatedTarget)) { self.focused = false; self.sync(); }
          });
          root.addEventListener('touchstart', function (e) { self.touchX = e.touches[0].clientX; }, { passive: true });
          root.addEventListener('touchend', function (e) {
            if (self.touchX === null) return;
            var dx = e.changedTouches[0].clientX - self.touchX;
            self.touchX = null;
            if (dx <= -SWIPE_PX) self.next();
            else if (dx >= SWIPE_PX) self.prev();
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
