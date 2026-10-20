// plTabPicker: the phone section picker beside a .pl-tabs row (#852, components/tab_picker.html).
// Registered from hub/base.html's <head> before Alpine, on whichever side of alpine:init this
// lands (FRONTEND.md, Scripts under hx-boost), so a boosted arrival finds it.
//
// Every picker closes on Escape and on a tap outside. A picker over a row of in-page buttons
// (data-tab-picker-buttons) also mirrors that row: the row's Alpine component decides which
// tab is current, and the leadership editor reorders and renames its tabs in place, so the
// picker re-reads the row whenever it changes and a choice presses the row's own button.
(function () {
  var LABEL_COUNT = /^(.+?)\s*\((\d+)\)$/;

  // The same reading as core/templatetags/tab_picker.py: text inside an element whose class
  // names a count is the count, the rest is the label, and "Label (3)" splits the same way.
  function readTab(el) {
    var label = '';
    var count = '';
    Array.prototype.forEach.call(el.childNodes, function (node) {
      if (node.nodeType === 1 && /count/.test(node.getAttribute('class') || '')) count += node.textContent;
      else label += node.textContent;
    });
    label = label.replace(/\s+/g, ' ').trim();
    count = count.trim();
    var written = label.match(LABEL_COUNT);
    if (written) {
      label = written[1];
      count = written[2];
    }
    return {
      label: label,
      count: count,
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
          if (!row || !row.classList.contains('pl-tabs')) {
            throw new Error('plTabPicker: no .pl-tabs row before the picker');
          }
          this.row = row;
          this.sync();
          var self = this;
          this.observer = new MutationObserver(function () { self.sync(); });
          this.observer.observe(row, {
            subtree: true, childList: true, characterData: true,
            attributes: true, attributeFilter: ['class', 'aria-selected', 'aria-current'],
          });
        },

        destroy() {
          if (this.observer) this.observer.disconnect();
        },

        rowTabs() {
          return Array.prototype.filter.call(this.row.children, function (el) {
            return el.classList.contains('vote-tab');
          });
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
