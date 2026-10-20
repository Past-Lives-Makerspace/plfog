// plTabPicker: the phone section picker beside a .pl-tabs row (#852, components/tab_picker.html).
// Registered from hub/base.html's <head> before Alpine, on whichever side of alpine:init this
// lands (FRONTEND.md, Scripts under hx-boost), so a boosted arrival finds it.
//
// Every picker closes on Escape and on a tap outside. A picker over a row of in-page buttons
// (data-tab-picker-buttons) also mirrors that row: the row's Alpine component decides which
// tab is current, and the leadership editor reorders and renames its tabs in place, so the
// picker re-reads the row whenever it changes and a choice presses the row's own button.
// A composer's step bar (.pl-phase-tabs) is such a row; its done tick and error dot (a child
// with a __mark class, shown or hidden by x-show) and aria-invalid come across as well.
(function () {
  function isRow(el) {
    return el.classList.contains('pl-tabs') || el.classList.contains('pl-phase-tabs');
  }

  function isTab(el) {
    return el.classList.contains('vote-tab') || el.classList.contains('pl-phase-tab');
  }

  // The same reading as core/templatetags/tab_picker.py: text inside a direct child whose class
  // names a count is the count (its aria-label says what it counts), a shown mark is the mark,
  // the rest is the label.
  // Only button rows come here, and their labels are often typed by a person ("Board (2)"),
  // so a "(n)" in the label stays part of it, as the server reads a button row too.
  function readTab(el) {
    var label = '';
    var count = '';
    var countLabel = '';
    var mark = '';
    Array.prototype.forEach.call(el.childNodes, function (node) {
      var cls = node.nodeType === 1 ? node.getAttribute('class') || '' : '';
      if (/__mark\b/.test(cls)) {
        if (node.style.display !== 'none' && !node.hasAttribute('x-cloak')) mark += node.textContent;
      } else if (/count/.test(cls)) {
        count += node.textContent;
        countLabel = countLabel || node.getAttribute('aria-label') || '';
      } else {
        label += node.textContent;
      }
    });
    return {
      label: label.replace(/\s+/g, ' ').trim(),
      count: count.trim(),
      countLabel: countLabel,
      mark: mark.trim(),
      error: el.getAttribute('aria-invalid') === 'true',
      active: el.classList.contains('vote-tab--active') ||
        el.getAttribute('aria-current') === 'page' ||
        el.getAttribute('aria-selected') === 'true',
    };
  }

  function register() {
    window.Alpine.data('plTabPicker', function () {
      return {
        items: [],

        init() {
          if (!('tabPickerButtons' in this.$root.dataset)) return;
          var row = this.$root.previousElementSibling;
          if (!row || !isRow(row)) {
            throw new Error('plTabPicker: no .pl-tabs or .pl-phase-tabs row before the picker');
          }
          this.row = row;
          this.sync();
          var self = this;
          this.observer = new MutationObserver(function () { self.sync(); });
          this.observer.observe(row, {
            subtree: true, childList: true, characterData: true,
            attributes: true, attributeFilter: ['class', 'aria-selected', 'aria-current', 'aria-invalid', 'style', 'x-cloak'],
          });
        },

        destroy() {
          if (this.observer) this.observer.disconnect();
        },

        rowTabs() {
          return Array.prototype.filter.call(this.row.children, isTab);
        },

        sync() {
          this.items = this.rowTabs().map(readTab);
        },

        currentIndex() {
          var index = this.items.findIndex(function (item) { return item.active; });
          return index === -1 ? 0 : index;
        },

        currentText() {
          var item = this.items[this.currentIndex()];
          if (!item) return '';
          return item.count ? item.label + ' (' + item.count + ')' : item.label;
        },

        positionText() {
          return (this.currentIndex() + 1) + ' of ' + this.items.length;
        },

        summaryLabel() {
          var item = this.items[this.currentIndex()];
          var eyebrow = this.$root.querySelector('.pl-tab-picker__eyebrow').textContent;
          return eyebrow + ': ' + (item ? item.label : '') + ', section ' + this.positionText();
        },

        choose(index) {
          this.rowTabs()[index].click();
          this.close();
        },

        close() {
          this.$root.open = false;
        },

        escape() {
          if (!this.$root.open) return;
          this.close();
          this.$root.querySelector('summary').focus();
        },
      };
    });
  }

  if (window.Alpine) register();
  else document.addEventListener('alpine:init', register);
})();
