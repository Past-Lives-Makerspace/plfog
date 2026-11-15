/* One row of the Edit Hours modal (templates/hub/partials/_orienter_hours_row.html, #430).
 *
 * Alpine component `plHoursRow(style)`. `style` is the row's booking style (fixed or open),
 * which the How members book radios drive through x-model and the fixed only fields follow.
 * `summary` is the row's recurrence in plain words, written before anything is saved:
 * "Every 3 weeks on Friday · 6:00 p.m. to 8:00 p.m.", "Every month on the last Thursday from
 * Nov 26 · 6:00 p.m. to 8:00 p.m.". It uses the same words as
 * OrientationAvailability.cadence_display and starts_on_note (membership/models.py) and the
 * Manage Bookings rule line's times (|time:"g:i a"), which say the rule once saved; change them
 * together. With Last of the month on and a start day that is not the last such weekday of its
 * month, it says the save's own refusal instead of a rule the save would not keep. `monthly` is
 * true under a month based cadence, the only place Last of the month means anything.
 *
 * Loaded once, deferred, from hub/base.html's <head> before Alpine (FRONTEND.md, Scripts
 * under hx-boost).
 */
(function () {
  "use strict";

  var WEEKS = { fortnightly: 2, every_3_weeks: 3, every_4_weeks: 4 };
  var MONTHS = { monthly: 1, every_2_months: 2, every_3_months: 3, every_6_months: 6, yearly: 12 };
  var ORDINALS = ["1st", "2nd", "3rd", "4th", "5th"];
  var MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
  ];

  function field(root, name) {
    return root.querySelector('[name$="-' + name + '"]');
  }

  // "18:00" as Django's |time:"g:i a" writes it: "6:00 p.m.".
  function timeWords(value) {
    var parts = (value || "").split(":");
    if (parts.length !== 2) return "";
    var hour = Number(parts[0]);
    return (hour % 12 || 12) + ":" + parts[1] + " " + (hour < 12 ? "a.m." : "p.m.");
  }

  function isLastOfMonth(day) {
    var weekLater = new Date(day.getFullYear(), day.getMonth(), day.getDate() + 7);
    return weekLater.getMonth() !== day.getMonth();
  }

  function chosenLabel(select) {
    if (!select || select.selectedIndex < 0) return "";
    return select.options[select.selectedIndex].text;
  }

  function parseDay(value) {
    var parts = (value || "").split("-");
    if (parts.length !== 3) return null;
    var day = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
    return isNaN(day.getTime()) ? null : day;
  }

  function cadenceWords(cadence, cadenceLabel, weekday, anchor, last) {
    if (cadence === "weekly") return "Every " + weekday;
    if (cadence === "fortnightly") return "Every other " + weekday;
    if (WEEKS[cadence]) return cadenceLabel + " on " + weekday;
    if (!anchor) return cadenceLabel;
    var ordinal = last ? "last" : ORDINALS[Math.floor((anchor.getDate() - 1) / 7)];
    if (cadence === "yearly") {
      return "Every year on the " + ordinal + " " + weekday + " of " + MONTH_NAMES[anchor.getMonth()];
    }
    return cadenceLabel + " on the " + ordinal + " " + weekday;
  }

  function startsOnNote(anchor) {
    var today = new Date();
    today.setHours(0, 0, 0, 0);
    if (!anchor || anchor <= today) return "";
    return " from " + MONTH_NAMES[anchor.getMonth()].slice(0, 3) + " " + anchor.getDate();
  }

  function registerPlHoursRow() {
    window.Alpine.data("plHoursRow", function (style) {
      return {
        style: style,
        summary: "",
        monthly: false,
        init: function () {
          this.refresh();
        },
        refresh: function () {
          var root = this.$root;
          var cadenceSelect = field(root, "cadence");
          var cadence = cadenceSelect ? cadenceSelect.value : "weekly";
          var lastBox = field(root, "last_of_month");
          var anchor = parseDay((field(root, "anchor_date") || {}).value);
          var weekday = chosenLabel(field(root, "weekday"));
          this.monthly = Boolean(MONTHS[cadence]);
          var last = this.monthly && Boolean(lastBox && lastBox.checked);
          if (last && anchor && !isLastOfMonth(anchor)) {
            // The model's clean refuses this start day; say what the save will say.
            this.summary = "The start day must be the last " + weekday + " of its month.";
            return;
          }
          var words = cadenceWords(cadence, chosenLabel(cadenceSelect), weekday, anchor, last);
          var times =
            timeWords((field(root, "start_time") || {}).value) + " to " + timeWords((field(root, "end_time") || {}).value);
          this.summary = words + startsOnNote(anchor) + " · " + times;
        },
      };
    });
  }

  if (window.Alpine) registerPlHoursRow();
  else document.addEventListener("alpine:init", registerPlHoursRow);
})();
