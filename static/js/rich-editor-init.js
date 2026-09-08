/* Shared initializer for every Quill rich-text mount (RichTextEditorWidget +
   PageContentEditorWidget). Loaded once by _components/rich_editor_assets.html.

   Why a shared, delegated init instead of per-widget inline scripts: formset rows added
   client-side (a "+ Add" button cloning a <template>'s innerHTML) never execute embedded
   <script> tags (FRONTEND.md rule 16), so a per-widget script leaves freshly-added rows
   dead. window.plRteInitAll() is idempotent (keyed on data-rte-ready) and is run on
   DOMContentLoaded, after every htmx settle, and by row-clone handlers.

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

    window.plRteInitAll = function () {
        if (!window.Quill) return;
        document.querySelectorAll(".pl-rte[data-rte-for]").forEach(function (mount) {
            if (mount.dataset.rteReady) return;
            var ta = document.getElementById(mount.dataset.rteFor);
            if (!ta) return;
            mount.dataset.rteReady = "1";
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
    // hx-boost swaps the body without firing DOMContentLoaded — re-init after a settle.
    document.addEventListener("htmx:afterSettle", window.plRteInitAll);
})();
