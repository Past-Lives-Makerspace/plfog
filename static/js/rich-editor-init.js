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
        ]
    };

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
                modules: { toolbar: TOOLBARS[mount.dataset.rteToolbar] || TOOLBARS.default }
            });
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
