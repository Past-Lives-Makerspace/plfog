"""TEMPORARY — remove on/after 2026-08-10.

Vanilla-JS comment widget injected into the email copy-review gallery
(tests/e2e/email_gallery/build.py). One thread per email card, keyed by the
card's data-section-key (== email.key), talking to the public /copy-review/
comments API on book.pastlives.space. XSS-safe (textContent only), degrades to
"Comments unavailable" if the API is unreachable. See
docs/superpowers/plans/2026-07-27-copy-review-comments.md.
"""

COPY_REVIEW_WIDGET = """<!-- TEMPORARY — remove on/after 2026-08-10: copy-review comment widget -->
<style>
  .crc { margin-top: 14px; border-top: 1px solid #e2e2e2; padding-top: 10px; font-size: 14px; }
  .crc-head { font-weight: 600; color: #444; margin-bottom: 8px; }
  .crc-muted { color: #999; font-weight: 400; font-style: italic; }
  .crc-list { display: flex; flex-direction: column; gap: 8px; }
  .crc-item { border: 1px solid #ddd; border-radius: 6px; padding: 8px 10px; background: #fafafa; }
  .crc-meta { display: flex; justify-content: space-between; gap: 8px; margin-bottom: 4px; }
  .crc-author { font-weight: 600; color: #222; }
  .crc-time { color: #999; font-size: 12px; white-space: nowrap; }
  .crc-body { margin: 0; white-space: pre-wrap; color: #222; }
  .crc-actions, .crc-confirm { display: flex; gap: 10px; margin-top: 6px; align-items: center; }
  .crc-link { background: none; border: none; padding: 0; color: #06c; cursor: pointer; font-size: 13px; }
  .crc-link:hover { text-decoration: underline; }
  .crc-danger { color: #b00; }
  .crc-editor, .crc-form { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
  .crc-input, .crc-textarea { font: inherit; padding: 6px 8px; border: 1px solid #ccc; border-radius: 6px; width: 100%; box-sizing: border-box; }
  .crc-textarea { min-height: 60px; resize: vertical; }
  .crc-btn { align-self: flex-start; font: inherit; padding: 6px 14px; border: 1px solid #06c; background: #06c; color: #fff; border-radius: 6px; cursor: pointer; }
  .crc-btn:disabled { opacity: 0.6; cursor: default; }
</style>
<script>
(function () {
  "use strict";
  var params = new URLSearchParams(window.location.search);
  var API_BASE = params.get("api") || "https://book.pastlives.space";
  var LIST_URL = API_BASE + "/copy-review/comments/";

  function readTokens() {
    try { return JSON.parse(window.localStorage.getItem("crc_tokens") || "{}"); }
    catch (e) { return {}; }
  }
  function writeTokens(map) {
    try { window.localStorage.setItem("crc_tokens", JSON.stringify(map)); } catch (e) {}
  }
  function tokenFor(id) { return readTokens()[id]; }
  function rememberToken(id, token) { var m = readTokens(); m[id] = token; writeTokens(m); }
  function forgetToken(id) { var m = readTokens(); delete m[id]; writeTokens(m); }
  function readName() { try { return window.localStorage.getItem("crc_name") || ""; } catch (e) { return ""; } }
  function writeName(name) { try { window.localStorage.setItem("crc_name", name); } catch (e) {} }

  function fmtTime(iso) { try { return new Date(iso).toLocaleString(); } catch (e) { return iso || ""; } }
  function make(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text !== undefined && text !== null) { node.textContent = text; }
    return node;
  }
  function request(method, url, payload) {
    var opts = { method: method, headers: { "Content-Type": "application/json" } };
    if (payload) { opts.body = JSON.stringify(payload); }
    return fetch(url, opts).then(function (res) {
      return res.json().catch(function () { return null; }).then(function (data) {
        return { ok: res.ok, status: res.status, data: data };
      });
    });
  }
  function updateCount(head, count) { head.textContent = "Comments (" + count + ")"; }

  function commentNode(sectionKey, c, head, list) {
    var item = make("div", "crc-item");
    item.setAttribute("data-id", c.id);
    var meta = make("div", "crc-meta");
    meta.appendChild(make("span", "crc-author", c.author_name));
    meta.appendChild(make("span", "crc-time", fmtTime(c.updated_at || c.created_at)));
    item.appendChild(meta);
    var body = make("p", "crc-body", c.body);
    item.appendChild(body);

    if (tokenFor(c.id)) {
      var actions = make("div", "crc-actions");
      var editBtn = make("button", "crc-link", "Edit");
      var delBtn = make("button", "crc-link", "Delete");
      actions.appendChild(editBtn);
      actions.appendChild(delBtn);
      item.appendChild(actions);
      editBtn.addEventListener("click", function () { startEdit(c, item, body, meta, actions); });
      delBtn.addEventListener("click", function () { startDelete(c, item, actions, head, list); });
    }
    return item;
  }

  function startEdit(c, item, body, meta, actions) {
    actions.style.display = "none";
    body.style.display = "none";
    var editor = make("div", "crc-editor");
    var nameInput = make("input", "crc-input");
    nameInput.value = c.author_name;
    var textArea = make("textarea", "crc-textarea");
    textArea.value = c.body;
    var save = make("button", "crc-btn", "Save");
    var cancel = make("button", "crc-link", "Cancel");
    editor.appendChild(nameInput);
    editor.appendChild(textArea);
    editor.appendChild(save);
    editor.appendChild(cancel);
    item.insertBefore(editor, actions);
    function done() { editor.remove(); body.style.display = ""; actions.style.display = ""; }
    cancel.addEventListener("click", done);
    save.addEventListener("click", function () {
      request("POST", API_BASE + "/copy-review/comments/" + c.id + "/edit/", {
        edit_token: tokenFor(c.id), author_name: nameInput.value, body: textArea.value
      }).then(function (r) {
        if (r.ok && r.data && r.data.comment) {
          c.author_name = r.data.comment.author_name;
          c.body = r.data.comment.body;
          body.textContent = c.body;
          meta.querySelector(".crc-author").textContent = c.author_name;
          meta.querySelector(".crc-time").textContent = fmtTime(r.data.comment.updated_at);
          done();
        } else { save.textContent = "Save failed"; }
      }).catch(function () { save.textContent = "Save failed"; });
    });
  }

  function startDelete(c, item, actions, head, list) {
    actions.style.display = "none";
    var confirm = make("div", "crc-confirm");
    confirm.appendChild(make("span", null, "Delete?"));
    var yes = make("button", "crc-link crc-danger", "yes");
    var no = make("button", "crc-link", "no");
    confirm.appendChild(yes);
    confirm.appendChild(no);
    item.appendChild(confirm);
    function done() { confirm.remove(); actions.style.display = ""; }
    no.addEventListener("click", done);
    yes.addEventListener("click", function () {
      request("POST", API_BASE + "/copy-review/comments/" + c.id + "/delete/", {
        edit_token: tokenFor(c.id)
      }).then(function (r) {
        if (r.ok) {
          forgetToken(c.id);
          item.remove();
          updateCount(head, list.querySelectorAll(".crc-item").length);
        } else { done(); }
      }).catch(done);
    });
  }

  function formNode(sectionKey, head, list) {
    var form = make("form", "crc-form");
    var nameInput = make("input", "crc-input");
    nameInput.placeholder = "Your name";
    nameInput.value = readName();
    var textArea = make("textarea", "crc-textarea");
    textArea.placeholder = "Leave a comment on this email";
    var submit = make("button", "crc-btn", "Post");
    submit.type = "submit";
    form.appendChild(nameInput);
    form.appendChild(textArea);
    form.appendChild(submit);
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var name = nameInput.value.trim();
      var text = textArea.value.trim();
      if (!name || !text) { return; }
      submit.disabled = true;
      request("POST", LIST_URL, {
        section: sectionKey, author_name: name, body: text, website: ""
      }).then(function (r) {
        submit.disabled = false;
        if (r.status === 201 && r.data && r.data.comment) {
          rememberToken(r.data.comment.id, r.data.edit_token);
          writeName(name);
          list.appendChild(commentNode(sectionKey, r.data.comment, head, list));
          updateCount(head, list.querySelectorAll(".crc-item").length);
          textArea.value = "";
        } else {
          submit.textContent = "Try again";
          window.setTimeout(function () { submit.textContent = "Post"; }, 2000);
        }
      }).catch(function () {
        submit.disabled = false;
        submit.textContent = "Try again";
        window.setTimeout(function () { submit.textContent = "Post"; }, 2000);
      });
    });
    return form;
  }

  function mountSection(sectionEl, sectionKey, comments) {
    var box = make("div", "crc");
    var head = make("div", "crc-head");
    updateCount(head, comments.length);
    box.appendChild(head);
    var list = make("div", "crc-list");
    comments.forEach(function (c) { list.appendChild(commentNode(sectionKey, c, head, list)); });
    box.appendChild(list);
    box.appendChild(formNode(sectionKey, head, list));
    sectionEl.appendChild(box);
  }

  function mountUnavailable(sectionEl) {
    var box = make("div", "crc");
    box.appendChild(make("div", "crc-head crc-muted", "Comments unavailable"));
    sectionEl.appendChild(box);
  }

  function init() {
    var sections = Array.prototype.slice.call(document.querySelectorAll("[data-section-key]"));
    request("GET", LIST_URL, null).then(function (r) {
      if (!r.ok || !r.data || !r.data.sections) { sections.forEach(mountUnavailable); return; }
      var grouped = r.data.sections;
      sections.forEach(function (sectionEl) {
        var key = sectionEl.getAttribute("data-section-key");
        mountSection(sectionEl, key, grouped[key] || []);
      });
    }).catch(function () { sections.forEach(mountUnavailable); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else { init(); }
})();
</script>
<!-- END TEMPORARY (copy-review comment widget) -->"""
