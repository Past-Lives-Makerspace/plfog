/* Keeps what a visitor typed on the tour booking form while they go back and forth between the
   steps (Morlock, 2026-11-22: "it saves your data").

   Picking another day or time is a new page, so without this the name, email, phone and answers
   were gone the moment someone pressed Back. Each change is copied into sessionStorage: this one
   browser tab only, gone when the tab closes, never sent anywhere. A field the page already filled
   (a signed in visitor's name, or what the server sent back with an error) is left as it is, so
   only empty fields are filled back in.

   Never at the lobby kiosk: that screen is shared by strangers, so its form carries no
   data-tour-draft and this script is not loaded there. The You're Booked page carries
   data-tour-draft-clear, which forgets the copy once the booking is made. */
(function () {
    "use strict";

    var KEY = "pl-tour-booking-draft";
    // The chosen time, the CSRF token and the honeypot are the page's, not the visitor's.
    var SKIP = { tour: true, csrfmiddlewaretoken: true, website: true };

    function store() {
        try {
            return window.sessionStorage;
        } catch (error) {
            return null; // Storage blocked (a private window in some browsers): the form still works.
        }
    }

    function fields(form) {
        return Array.prototype.filter.call(form.elements, function (field) {
            return field.name && !SKIP[field.name] && field.type !== "hidden" && field.type !== "file";
        });
    }

    function read(storage) {
        try {
            return JSON.parse(storage.getItem(KEY) || "{}");
        } catch (error) {
            return {};
        }
    }

    function save(form, storage) {
        var draft = {};
        fields(form).forEach(function (field) {
            draft[field.name] = field.type === "checkbox" ? field.checked : field.value;
        });
        storage.setItem(KEY, JSON.stringify(draft));
    }

    function restore(form, storage) {
        var draft = read(storage);
        fields(form).forEach(function (field) {
            if (!(field.name in draft)) {
                return;
            }
            if (field.type === "checkbox") {
                if (field.checked !== draft[field.name]) {
                    // A click, not a property set, so Alpine's x-model sees it (the ideal studio field follows its box).
                    field.click();
                }
            } else if (!field.value) {
                field.value = draft[field.name];
                field.dispatchEvent(new Event("input", { bubbles: true }));
                field.dispatchEvent(new Event("change", { bubbles: true }));
            }
        });
    }

    function start() {
        var storage = store();
        if (!storage) {
            return;
        }
        if (document.querySelector("[data-tour-draft-clear]")) {
            storage.removeItem(KEY);
            return;
        }
        var form = document.querySelector("form[data-tour-draft]");
        if (!form) {
            return;
        }
        restore(form, storage);
        var keep = function () {
            save(form, storage);
        };
        form.addEventListener("input", keep);
        form.addEventListener("change", keep);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();
