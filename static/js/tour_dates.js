/* The Available On calendar on the Tours page's My Hours tab (#1160): a month grid a guide taps
 * to pick the days they are free, then one set of hours for all of them.
 *
 * Alpine component `plTourDates(configId, open)`, on the Available On card in
 * templates/tours/partials/_dates.html. `configId` names a json_script block written by
 * tours.views._calendar_config: `today` and `last` (the pickable range, ISO dates), `dated` (days
 * that already have dated hours, drawn with a dot), `off` (days off, struck through and still
 * pickable: the save names them) and `picked` (picks to restore after a refused save). Each pick is
 * a hidden `dates` input, so the form posts them with From, To, Tour length and Seats. Monday is
 * the first column. Every date is handled as a local YYYY-MM-DD string, never through UTC.
 *
 * Loaded once, deferred, from hub/base.html's <head> before Alpine (FRONTEND.md, Scripts
 * under hx-boost).
 */
(function () {
  "use strict";

  var MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  var WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

  function pad(number) {
    return (number < 10 ? "0" : "") + number;
  }

  function iso(year, month, day) {
    return year + "-" + pad(month + 1) + "-" + pad(day);
  }

  function parts(value) {
    var bits = value.split("-");
    return { year: parseInt(bits[0], 10), month: parseInt(bits[1], 10) - 1, day: parseInt(bits[2], 10) };
  }

  function registerPlTourDates() {
    window.Alpine.data("plTourDates", function (configId, open) {
      var config = JSON.parse(document.getElementById(configId).textContent);
      var start = parts(config.picked.length ? config.picked[0] : config.today);
      return {
        open: open,
        picked: config.picked.slice(),
        year: start.year,
        month: start.month,
        today: config.today,
        last: config.last,
        dated: config.dated,
        off: config.off,
        get monthLabel() {
          return MONTHS[this.month] + " " + this.year;
        },
        get canGoBack() {
          var first = parts(this.today);
          return this.year * 12 + this.month > first.year * 12 + first.month;
        },
        get canGoOn() {
          var end = parts(this.last);
          return this.year * 12 + this.month < end.year * 12 + end.month;
        },
        get cells() {
          var lead = (new Date(this.year, this.month, 1).getDay() + 6) % 7;
          var length = new Date(this.year, this.month + 1, 0).getDate();
          var cells = [];
          for (var blank = 0; blank < lead; blank++) {
            cells.push({ key: "blank-" + blank, blank: true });
          }
          for (var day = 1; day <= length; day++) {
            var value = iso(this.year, this.month, day);
            var weekday = WEEKDAYS[new Date(this.year, this.month, day).getDay()];
            var isDated = this.dated.indexOf(value) !== -1;
            var isOff = this.off.indexOf(value) !== -1;
            cells.push({
              key: value,
              blank: false,
              iso: value,
              day: day,
              disabled: value < this.today || value > this.last,
              dated: isDated,
              off: isOff,
              label: weekday + ", " + MONTHS[this.month] + " " + day + (isDated ? ", has hours" : "") + (isOff ? ", day off" : ""),
            });
          }
          return cells;
        },
        get count() {
          return this.picked.length;
        },
        get countLabel() {
          return this.count + (this.count === 1 ? " date picked" : " dates picked");
        },
        get addLabel() {
          if (this.count === 0) {
            return "Add Dates";
          }
          return "Add " + this.count + (this.count === 1 ? " Date" : " Dates");
        },
        isPicked: function (value) {
          return this.picked.indexOf(value) !== -1;
        },
        toggle: function (cell) {
          if (cell.blank || cell.disabled) {
            return;
          }
          var at = this.picked.indexOf(cell.iso);
          if (at === -1) {
            this.picked.push(cell.iso);
            this.picked.sort();
          } else {
            this.picked.splice(at, 1);
          }
        },
        step: function (by) {
          if ((by < 0 && !this.canGoBack) || (by > 0 && !this.canGoOn)) {
            return;
          }
          var next = new Date(this.year, this.month + by, 1);
          this.year = next.getFullYear();
          this.month = next.getMonth();
        },
        cancel: function () {
          this.picked = [];
          var now = parts(this.today);
          this.year = now.year;
          this.month = now.month;
          this.open = false;
        },
      };
    });
  }

  if (window.Alpine) {
    registerPlTourDates();
  } else {
    document.addEventListener("alpine:init", registerPlTourDates);
  }
})();
