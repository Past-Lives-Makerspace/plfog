/* One orientation on the guild Orientations page and the equipment Orientation tab (#732),
 * collapsed to a line of its name, length and price with an Edit button.
 *
 * Alpine component `plOrientationRow(open)`, on each row of templates/hub/partials/
 * _orientation_row_summary.html's callers. The fields stay in the DOM while the row is
 * collapsed (x-show), so the equipment Save and the guild autosave post every row. The server
 * renders the line first (OrientationTypeForm.summary_name / summary_meta); this keeps it in
 * step with what is typed, in the same words. A row opens itself when the guild autosave puts
 * an error inside it (the bubbling `pl-autosave-error` from guild_autosave.js).
 *
 * Loaded once, deferred, from hub/base.html's <head> before Alpine (FRONTEND.md, Scripts
 * under hx-boost).
 */
(function () {
  "use strict";

  function dollars(cents) {
    var whole = Math.floor(cents / 100);
    var remainder = cents % 100;
    return remainder === 0 ? "$" + whole : "$" + whole + "." + (remainder < 10 ? "0" : "") + remainder;
  }

  function registerPlOrientationRow() {
    window.Alpine.data("plOrientationRow", function (open) {
      return {
        editing: open,
        name: "",
        meta: "",
        init: function () {
          this.refresh();
        },
        field: function (suffix) {
          return this.$root.querySelector('[name$="-' + suffix + '"]');
        },
        refresh: function () {
          var name = this.field("name");
          var duration = this.field("duration_minutes");
          var donation = this.field("is_donation");
          var price = this.field("price");
          var active = this.field("is_active");
          var parts = [];
          this.name = (name && name.value.trim()) || "New orientation";
          if (duration && duration.value.trim()) parts.push(duration.value.trim() + " min");
          if (donation && donation.checked) {
            parts.push("Donation based");
          } else {
            var cents = price ? Math.round(parseFloat(price.value) * 100) : 0;
            parts.push(cents > 0 ? dollars(cents) : "Free");
          }
          if (active && !active.checked) parts.push("Inactive");
          this.meta = parts.join(" · ");
        },
      };
    });
  }

  if (window.Alpine) {
    registerPlOrientationRow();
  } else {
    document.addEventListener("alpine:init", registerPlOrientationRow);
  }
})();
