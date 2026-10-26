// plUpcomingQueue: Home's Upcoming as one queue (#937, hub/partials/_home_upcoming.html). The first
// item is the featured card and the rest are the rows under it; every interval the rows slide up
// one, the next item grows into the card and the one that was featured drops to the bottom. A
// sibling of pl-rotator.js, whose controls' styles (rotator.css) and lasting pause it shares.
// Registered from hub/base.html's <head> before Alpine, on whichever side of alpine:init this lands
// (FRONTEND.md, Scripts under hx-boost), so a boosted arrival finds it.
//
// Markup contract:
//   x-data="plUpcomingQueue" on the root, with
//     data-rotator-interval  milliseconds each item is featured (required, e.g. 6000)
//     data-rotator-name      which rotator this is ("home-upcoming"), for its remembered pause
//   [data-queue-panel="i"]   item i's featured card; all of them sit in one grid cell, the shown
//                            one carries .is-active
//   [data-queue-list]        the list, holding [data-queue-row="i"] for every item; the featured
//                            item's row is hidden and kept last
//   img[data-queue-src]      a picture loaded only when its item is featured or next to it
//   [data-rotator-bar]       the time bar (rotator.css's fill animation)
// The controls call prev(), next() and toggle(). With one item nothing moves.
//
// It moves on only while no mouse or pen rests on it, nothing in it has keyboard focus, the tab is
// visible and the pause button is off; a mostly sideways swipe moves it. Under
// prefers-reduced-motion it never moves by itself and moves without animating; the arrows work.
//
// The pause lasts until play or logging out, kept in localStorage under the same key as every
// plRotator's, pl-rotator-paused:<user pk>:<name>, so pl-rotator.js's sweeps (a /logout/ submit,
// another user's keys on load) clear this one too. With storage blocked it lasts for the page.
(function () {
  var SWIPE_PX = 40;
  var MOVE_MS = 450;
  var EASE = 'cubic-bezier(0.2, 0.7, 0.2, 1)';
  var PAUSE_PREFIX = 'pl-rotator-paused:';

  function pauseKey(name) {
    var user = document.documentElement.getAttribute('data-pl-user') || '';
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

  function register() {
    window.Alpine.data('plUpcomingQueue', function () {
      return {
        order: [],
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
          this.panels = Array.prototype.slice.call(root.querySelectorAll('[data-queue-panel]'));
          this.list = root.querySelector('[data-queue-list]');
          this.rows = {};
          var self = this;
          root.querySelectorAll('[data-queue-row]').forEach(function (row) {
            self.rows[row.getAttribute('data-queue-row')] = row;
          });
          this.count = this.panels.length;
          this.order = this.panels.map(function (panel, i) { return i; });
          this.interval = parseInt(root.getAttribute('data-rotator-interval'), 10);
          this.reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
          this.tabHidden = document.hidden;
          this.remaining = this.interval;
          this.name = root.getAttribute('data-rotator-name') || '';
          this.userPaused = storedPause(this.name);
          if (this.count < 2 || !this.list) return;
          this.loadPictures();
          this.onVisibility = function () {
            self.tabHidden = document.hidden;
            self.sync();
          };
          document.addEventListener('visibilitychange', this.onVisibility);
          // A mouse or pen resting on it holds it; a finger lifts at once, so a tap never holds it.
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
          // Focus anywhere in it holds it: a keyboard user's, and the focus a click leaves on a link
          // (Flyer and Join Online open a new tab, and the page keeps that focus when the member comes
          // back), so a move never takes a focused link out from under them. Only the focus a click
          // leaves on its own arrows or pause button does not hold it.
          root.addEventListener('focusin', function (e) {
            self.focused = e.target.matches(':focus-visible') || !e.target.closest('.pl-upnext__controls');
            self.sync();
          });
          root.addEventListener('focusout', function (e) {
            if (!root.contains(e.relatedTarget)) { self.focused = false; self.sync(); }
          });
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

        // Start or stop the clock to match running(), keeping the time already spent on this item.
        sync() {
          var bar = this.$root.querySelector('[data-rotator-bar]');
          if (bar) bar.style.animationPlayState = this.running() ? 'running' : 'paused';
          if (this.running() && this.timer === null) {
            var self = this;
            this.startedAt = Date.now();
            this.timer = window.setTimeout(function () {
              self.timer = null;
              self.move(1);
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

        next() { this.move(1); },
        prev() { this.move(-1); },

        // One step along the queue: forward features the first row and sends the featured item to
        // the bottom; back features the last row and puts the featured item back on top.
        move(step) {
          if (this.count < 2) return;
          this.stop();
          this.settle();
          var before = this.positions();
          var leaving = step > 0 ? this.order[1] : this.order[this.order.length - 1];
          var entering = this.order[0];
          if (step > 0) this.order.push(this.order.shift());
          else this.order.unshift(this.order.pop());
          this.render(leaving, entering, step);
          if (!this.reduced) this.animate(before, leaving, step);
          this.remaining = this.interval;
          this.loadPictures();
          this.restartBar(this.interval);
          this.sync();
        },

        // The order on the page: the featured item's card shown, the rest's rows in order, its row hidden
        // last. Only the two rows that change place move: the one whose item is now featured goes to the
        // end, hidden, and going back the item that was featured returns to the top. Moving a node drops
        // the focus inside it, so focus anywhere else in the queue is put back where it was.
        render(leaving, entering, step) {
          var active = document.activeElement;
          var featured = this.order[0];
          this.panels.forEach(function (panel, i) { panel.classList.toggle('is-active', i === featured); });
          if (step < 0) this.list.insertBefore(this.rows[entering], this.list.firstChild);
          this.list.appendChild(this.rows[leaving]);
          this.rows[entering].hidden = false;
          this.rows[leaving].hidden = true;
          if (active && active !== document.activeElement && this.list.contains(active) && !active.closest('[hidden]')) {
            active.focus({ preventScroll: true });
          }
        },

        // Every shown row's top before a move, by item.
        positions() {
          var tops = {};
          for (var k = 1; k < this.order.length; k++) {
            tops[this.order[k]] = this.rows[this.order[k]].getBoundingClientRect().top;
          }
          return tops;
        },

        // A move made while the last one is still animating starts from where the rows really sit.
        settle() {
          var self = this;
          Object.keys(this.rows).forEach(function (key) {
            self.rows[key].getAnimations().forEach(function (animation) { animation.cancel(); });
          });
          this.list.querySelectorAll('[data-queue-ghost]').forEach(function (ghost) { ghost.remove(); });
        },

        // FLIP: each row that stayed slides from where it was to where it is; the row that came in
        // rises (or drops) into place; the row whose item is now featured lifts away as a copy.
        animate(before, leaving, step) {
          var timing = { duration: MOVE_MS, easing: EASE };
          var listTop = this.list.getBoundingClientRect().top;
          for (var k = 1; k < this.order.length; k++) {
            var item = this.order[k];
            var row = this.rows[item];
            var top = row.getBoundingClientRect().top;
            if (Object.prototype.hasOwnProperty.call(before, item)) {
              var dy = before[item] - top;
              if (dy) row.animate([{ transform: 'translateY(' + dy + 'px)' }, { transform: 'none' }], timing);
            } else {
              var from = (step > 0 ? 1 : -1) * row.offsetHeight;
              row.animate([{ transform: 'translateY(' + from + 'px)', opacity: 0 }, { transform: 'none', opacity: 1 }], timing);
            }
          }
          var source = this.rows[leaving];
          if (!Object.prototype.hasOwnProperty.call(before, leaving)) return;
          var ghost = source.cloneNode(true);
          ghost.hidden = false;
          ghost.removeAttribute('data-queue-row');
          ghost.setAttribute('data-queue-ghost', '');
          ghost.setAttribute('aria-hidden', 'true');
          ghost.inert = true;
          ghost.classList.add('pl-upnext__ghost');
          ghost.style.top = (before[leaving] - listTop) + 'px';
          this.list.appendChild(ghost);
          var lift = -0.6 * ghost.offsetHeight;
          var fade = ghost.animate([{ transform: 'none', opacity: 1 }, { transform: 'translateY(' + lift + 'px)', opacity: 0 }], timing);
          fade.onfinish = function () { ghost.remove(); };
        },

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

        // The featured item's picture and its two neighbours', so a move either way never shows an empty frame.
        loadPictures() {
          var self = this;
          [this.order[0], this.order[1], this.order[this.order.length - 1]].forEach(function (i) {
            var panel = self.panels[i];
            if (!panel) return;
            panel.querySelectorAll('img[data-queue-src]').forEach(function (img) {
              img.src = img.getAttribute('data-queue-src');
              img.removeAttribute('data-queue-src');
            });
          });
        },
      };
    });
  }

  if (window.Alpine) register();
  else document.addEventListener('alpine:init', register);
})();
