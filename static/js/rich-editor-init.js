/* Shared initializer for every Quill rich-text mount (RichTextEditorWidget +
   PageContentEditorWidget). Loaded once by _components/rich_editor_assets.html.

   Why a shared, delegated init instead of per-widget inline scripts: formset rows added
   client-side (a "+ Add" button cloning a <template>'s innerHTML) never execute embedded
   <script> tags (FRONTEND.md rule 16), so a per-widget script leaves freshly-added rows
   dead. window.plRteInitAll() is idempotent (each mount is claimed once, see readyOnce)
   and is run on DOMContentLoaded, after every htmx settle, and by row-clone handlers.

   Boot contract. This file is loaded from <body>, so htmx re-runs the whole IIFE in a
   fresh scope on every boosted arrival. Anything bound to document therefore has to be
   flagged somewhere that outlives that re-execution — window, where this file already
   keeps plRteInitAll — or it stacks one more listener per visit. Issue #383.

   Seeding: by default the mount is filled from the hidden textarea's value (the email
   editors — stored values are already HTML). A mount with data-rte-seed="server" was
   pre-rendered server-side (the dual-mode page-content editors, where the stored value
   may be Markdown) and is used as-is. Either way the textarea is immediately re-seeded
   with Quill's normalized HTML so an unedited submit still carries clean markup, then
   kept in sync on every edit. */
(function () {
    "use strict";

    var TOOLBARS = {
        // The email editors' seven controls — unchanged.
        default: [
            ["bold", "italic", "underline"],
            [{ header: 2 }, { header: 3 }],
            [{ list: "bullet" }, { list: "ordered" }],
            ["link"],
            ["clean"]
        ],
        // The /help/edit/ page-content editors: + strike and blockquote. No image button
        // on purpose — help screenshots come from the committed /static/help/ pipeline.
        page: [
            ["bold", "italic", "underline", "strike"],
            [{ header: 2 }, { header: 3 }],
            [{ list: "bullet" }, { list: "ordered" }],
            ["link", "blockquote"],
            ["clean"]
        ],
        // The member wiki: the page set plus an image button. A member writing about a
        // machine has a photo, and a photo of the right blade beats three paragraphs.
        wiki: [
            ["bold", "italic", "underline", "strike"],
            [{ header: 2 }, { header: 3 }],
            [{ list: "bullet" }, { list: "ordered" }],
            ["link", "blockquote", "image"],
            ["clean"]
        ]
    };

    /* Custom toolbar handlers, keyed by the same data-rte-toolbar name.

       Quill accepts either a bare format array or a { container, handlers } object for
       modules.toolbar, and this file used to pass the bare array — which has nowhere to
       attach a handler. Moving the array into `container` is what makes the wiki image
       button possible; the two existing toolbars behave exactly as before.

       The wiki image handler uploads through the page's own endpoint and inserts the URL it
       gets back. Quill's default is a base64 data URI, which the wiki sanitizer refuses, so
       the image would silently vanish on the next render if this were left to the default. */
    function wikiImageHandler(quill, uploadUrl) {
        return function () {
            if (!uploadUrl) {
                window.dispatchEvent(new CustomEvent("show-toast", {
                    detail: { message: "Save the page first, then add photos.", type: "info" }
                }));
                return;
            }
            var input = document.createElement("input");
            input.type = "file";
            input.accept = "image/*";
            input.onchange = function () {
                var file = input.files && input.files[0];
                if (!file) return;
                var data = new FormData();
                data.append("image", file);
                var token = document.querySelector("[name=csrfmiddlewaretoken]");
                fetch(uploadUrl, {
                    method: "POST",
                    body: data,
                    headers: token ? { "X-CSRFToken": token.value } : {}
                }).then(function (response) {
                    return response.json();
                }).then(function (payload) {
                    if (payload.error) {
                        window.dispatchEvent(new CustomEvent("show-toast", {
                            detail: { message: payload.error, type: "error" }
                        }));
                        return;
                    }
                    var range = quill.getSelection(true);
                    quill.insertEmbed(range.index, "image", payload.url, "user");
                    quill.setSelection(range.index + 1, 0);
                }).catch(function () {
                    window.dispatchEvent(new CustomEvent("show-toast", {
                        detail: { message: "That photo did not upload. Try again.", type: "error" }
                    }));
                });
            };
            input.click();
        };
    }

    /* Claim a mount for one Quill instance. False when this mount was already wired, which
       is what makes plRteInitAll safe to call from four places on the same markup.

       The key is a property on the element, deliberately NOT a data- attribute. It used to
       be mount.dataset.rteReady, and that is markup: htmx serializes the body's innerHTML
       into its history snapshot, so on Back every restored mount arrived already claimed,
       plRteInitAll no-opped, and the editor came back looking perfect and completely dead
       — everything typed into the restored contenteditable was dropped on save, with no
       error anywhere. A property lives on the element object rather than in its markup, so
       it never serializes and dies with the node it belongs to. Same trap, same fix, as
       space_map_editor.js (issue #382). */
    function readyOnce(element, key) {
        if (element[key]) return false;
        element[key] = true;
        return true;
    }

    window.plRteInitAll = function () {
        if (!window.Quill) return;
        document.querySelectorAll(".pl-rte[data-rte-for]").forEach(function (mount) {
            var ta = document.getElementById(mount.dataset.rteFor);
            if (!ta) return;
            if (!readyOnce(mount, "plRteReady")) return;
            if (mount.dataset.rteSeed !== "server") {
                mount.innerHTML = ta.value;
            }
            var quill = new Quill(mount, {
                theme: "snow",
                modules: {
                    toolbar: { container: TOOLBARS[mount.dataset.rteToolbar] || TOOLBARS.default }
                }
            });
            if (mount.dataset.rteToolbar === "wiki") {
                var toolbarModule = quill.getModule("toolbar");
                toolbarModule.addHandler("image", wikiImageHandler(quill, mount.dataset.rteUploadUrl || ""));
            }
            ta.value = quill.root.innerHTML;
            quill.on("text-change", function () {
                ta.value = quill.root.innerHTML;
            });
        });
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", window.plRteInitAll);
    } else {
        window.plRteInitAll();
    }
    /* hx-boost swaps the body without firing DOMContentLoaded — re-init after a settle.
       document survives that swap, so this registration has to happen exactly once per
       document or every boosted arrival at one of the six pages carrying this file leaves
       another listener behind and plRteInitAll then runs once per accumulated listener on
       every settle for the rest of the visit.

       The flag is on window because the guard has to outlive a re-execution of this file:
       a `var bound = false` in this IIFE's scope is rebuilt as false on every arrival and
       would guard nothing while looking correct on a single visit.

       The listener is a wrapper rather than window.plRteInitAll itself, so that the one
       binding always calls whichever definition is current. Each re-execution replaces
       window.plRteInitAll with a new closure, and a direct reference would pin the very
       first one for the life of the document. */
    if (!window.plRteSettleBound) {
        window.plRteSettleBound = true;
        document.addEventListener("htmx:afterSettle", function () {
            window.plRteInitAll();
        });
    }
})();
