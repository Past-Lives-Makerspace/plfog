/* Guild Settings save themselves as they are edited (#575).
 *
 * Alpine component `plGuildAutosave`, the root of templates/hub/guild_edit.html. Every
 * `<form data-autosave>` on the page posts itself whole, with an X-Autosave header, through
 * one queue (one request at a time, each reading the page as it is when it runs); the view
 * answers JSON (hub/autosave.py). The save pill, the 422 contract (errors keyed by html
 * field name, the server's toast relayed from HX-Trigger, what was typed kept) and the
 * redirected answer read as a signed out session all follow the Leadership Directory editor.
 *
 * Formsets: a row is `[data-formset-row]` inside a form carrying `data-formset="<prefix>"`
 * and `data-formset-required="<field names>"`. A new row (blank hidden id) whose required
 * fields are not all filled is posted as it rendered, so Django reads it as unchanged and
 * skips it while the rest of the form saves. A 200 stamps every returned pk into its row's
 * hidden id, drops the rows that were flagged for deletion, and renumbers the rows so the
 * saved ones come first (INITIAL_FORMS counts them), which is what keeps a new row from
 * ever being posted as new twice. A 200 also clears every file input whose file went out, so
 * a banner or a FAQ document posted once is never re-sent with the next edit of that form,
 * while one picked on a half typed row waits until the row posts for real. Delete flips the
 * row's DELETE field (made on the fly for a row saved since the page loaded) and posts; a form
 * with `data-autosave-confirm` asks first. A refusal keeps the flag, so a delete clicked while
 * another row is invalid completes with the next good post; only a `<prefix>-__all__` error
 * (where the model's delete blockers land) or a failed request unflags it.
 *
 * Leaving: a pending typing timer is flushed (with keepalive, so a hard navigation cannot
 * lose it) and the page holds while a save is pending or failed: the native prompt on a hard
 * navigation (the sidebar carries hx-boost="false"), and on a boosted one the request is
 * held until the queue drains, then resumed on its own or, after a failure, behind the
 * leave-while-saving confirm.
 *
 * Loaded once, deferred, from hub/base.html's <head> before Alpine (FRONTEND.md, Scripts
 * under hx-boost); the document and window guards bind once per document on `window`.
 */
(function () {
  "use strict";

  var TYPED = { text: 1, url: 1, email: 1, number: 1, search: 1, tel: 1, password: 1 };
  var DEBOUNCE_MS = 800;
  var SIGNED_OUT = "You were signed out, so that change was not saved. Reload the page and sign in.";

  function toast(message, type) {
    window.dispatchEvent(new CustomEvent("show-toast", { detail: { message: message, type: type } }));
  }

  // The 422 contract sets its error toast in HX-Trigger, as for htmx; fetch has to relay it.
  function relayToast(response) {
    var header = response.headers.get("HX-Trigger");
    if (!header) return;
    var events = JSON.parse(header);
    if (events.showToast) toast(events.showToast.message, events.showToast.type);
  }

  function csrfToken() {
    return document.querySelector('meta[name="csrf-token"]').content;
  }

  function isTyped(el) {
    if (el.tagName === "TEXTAREA") return true;
    return el.tagName === "INPUT" && TYPED[el.type] === 1;
  }

  function formOf(el) {
    return el.closest ? el.closest("form[data-autosave]") : null;
  }

  function ignored(el) {
    return !!el.closest("[data-autosave-ignore]");
  }

  // --- Formset rows ---

  function prefixOf(form) {
    return form.dataset.formset || "";
  }

  function rowsOf(form) {
    return Array.prototype.slice.call(form.querySelectorAll("[data-formset-row]"));
  }

  function rowOf(el) {
    return el.closest("[data-formset-row]");
  }

  function controls(row) {
    return Array.prototype.slice.call(row.querySelectorAll("input, select, textarea"));
  }

  function idInput(row) {
    return row.querySelector('input[name$="-id"]');
  }

  function hasId(row) {
    var input = idInput(row);
    return !!(input && input.value);
  }

  function managementInput(form, name) {
    return form.querySelector('input[name="' + prefixOf(form) + "-" + name + '"]');
  }

  function requiredNames(form) {
    return (form.dataset.formsetRequired || "").split(" ").filter(Boolean);
  }

  function fieldName(el, form) {
    return el.name.replace(new RegExp("^" + prefixOf(form) + "-\\d+-"), "");
  }

  function isRequired(el, form) {
    if (el.required) return true;
    if (!rowOf(el)) return false;
    return requiredNames(form).indexOf(fieldName(el, form)) !== -1;
  }

  function rowComplete(row, form) {
    var names = requiredNames(form);
    return controls(row).every(function (el) {
      if (!el.name || el.disabled || names.indexOf(fieldName(el, form)) === -1) return true;
      if (el.type === "checkbox" || el.type === "radio") return true;
      return el.value.trim() !== "";
    });
  }

  /* Put a row's rendered defaults into the FormData in place of what is typed: a text input's
     defaultValue, a checkbox's defaultChecked, a select's defaultSelected option (a select
     that rendered with none is left out, which Django also reads as unchanged). */
  function postAsRendered(data, row) {
    var els = controls(row);
    els.forEach(function (el) {
      if (el.name) data.delete(el.name);
    });
    els.forEach(function (el) {
      if (!el.name || el.disabled || el.type === "file") return;
      if (el.type === "checkbox" || el.type === "radio") {
        if (el.defaultChecked) data.append(el.name, el.value);
        return;
      }
      if (el.tagName === "SELECT") {
        Array.prototype.forEach.call(el.options, function (option) {
          if (option.defaultSelected) data.append(el.name, option.value);
        });
        return;
      }
      data.append(el.name, el.defaultValue);
    });
  }

  function payload(form) {
    var data = new FormData(form);
    if (prefixOf(form)) {
      rowsOf(form).forEach(function (row) {
        if (!hasId(row) && !rowComplete(row, form)) postAsRendered(data, row);
      });
    }
    return data;
  }

  function signature(data) {
    var parts = [];
    data.forEach(function (value, key) {
      var text = typeof value === "string" ? value : "file:" + value.name + ":" + value.size + ":" + value.lastModified;
      parts.push(key + "=" + text);
    });
    return parts.join("\n");
  }

  /* Saved rows first, then the new ones, each row's name, id and for attributes rewritten to
     its index; the management form then counts rows and saved rows. Nodes never move. */
  function renumber(form) {
    var prefix = prefixOf(form);
    if (!prefix) return;
    var rows = rowsOf(form);
    var saved = rows.filter(hasId);
    var ordered = saved.concat(rows.filter(function (row) { return !hasId(row); }));
    var namePattern = new RegExp("^" + prefix + "-\\d+-");
    var idPattern = new RegExp("^id_" + prefix + "-\\d+-");
    ordered.forEach(function (row, index) {
      row.querySelectorAll("[name], [id], [for]").forEach(function (el) {
        if (el.name && namePattern.test(el.name)) el.name = el.name.replace(namePattern, prefix + "-" + index + "-");
        if (el.id && idPattern.test(el.id)) el.id = el.id.replace(idPattern, "id_" + prefix + "-" + index + "-");
        var target = el.getAttribute("for");
        if (target && idPattern.test(target)) {
          el.setAttribute("for", target.replace(idPattern, "id_" + prefix + "-" + index + "-"));
        }
      });
    });
    managementInput(form, "TOTAL_FORMS").value = rows.length;
    managementInput(form, "INITIAL_FORMS").value = saved.length;
    var empty = form.querySelector("[data-formset-empty]");
    if (empty) empty.hidden = rows.length > 0;
  }

  function deleteInput(row) {
    return row.querySelector('input[name$="-DELETE"]');
  }

  function flagDelete(row) {
    var input = deleteInput(row);
    if (!input) {
      input = document.createElement("input");
      input.type = "checkbox";
      input.name = idInput(row).name.replace(/-id$/, "-DELETE");
      input.hidden = true;
      input.setAttribute("data-autosave-synthetic", "");
      row.appendChild(input);
    }
    input.checked = true;
    row.hidden = true;
  }

  // The rows come back and the next edit posts clean: after a failed request, or a refusal that
  // names the formset itself (a delete blocker), never for another row's field error.
  function unflagDeletes(form) {
    form.querySelectorAll('input[name$="-DELETE"]').forEach(function (input) {
      var row = rowOf(input);
      if (row) row.hidden = false;
      if (input.hasAttribute("data-autosave-synthetic")) input.remove();
      else input.checked = false;
    });
  }

  // A posted file stays selected in its input, and the next post of the form would send it again
  // (a new storage object each time, and a body no keepalive flush could carry). Only a file that
  // went out is cleared: a row posted as rendered had its file taken out of the data.
  function clearFiles(form, data) {
    form.querySelectorAll('input[type="file"]').forEach(function (input) {
      if (input.name && data.get(input.name) instanceof File) input.value = "";
    });
  }

  // A row flagged for deletion whose post has not landed yet (kept through a sibling's refusal).
  function hasPendingDelete(form) {
    return !!form.querySelector('input[name$="-DELETE"]:checked');
  }

  function formsWithPendingDeletes(root) {
    return Array.prototype.filter.call(root.querySelectorAll("form[data-autosave]"), hasPendingDelete);
  }

  // Anything that changes what a form would post, so a save finishing mid edit remembers nothing.
  function markEdited(form) {
    form.plAutosaveEdited = true;
  }

  function refusesFormset(form, errors) {
    return Object.prototype.hasOwnProperty.call(errors, prefixOf(form) + "-__all__");
  }

  function applyRows(form, rows) {
    var prefix = prefixOf(form);
    if (!prefix || !rows[prefix]) return;
    rows[prefix].forEach(function (pk, index) {
      var input = form.querySelector('input[name="' + prefix + "-" + index + '-id"]');
      var row = input && rowOf(input);
      if (!row) return;
      if (pk !== null) {
        input.value = pk;
      } else if (deleteInput(row) && deleteInput(row).checked) {
        row.remove();
      }
    });
    renumber(form);
  }

  // --- Errors under their fields (the leadership editor's lists) ---

  function clearErrors(form) {
    form.querySelectorAll(".pl-field-errors").forEach(function (list) { list.remove(); });
  }

  function errorList(messages) {
    var list = document.createElement("ul");
    list.className = "pl-field-errors";
    list.setAttribute("data-autosave-error", "");
    (messages || []).forEach(function (message) {
      var item = document.createElement("li");
      item.className = "pl-field-error";
      item.textContent = message;
      list.appendChild(item);
    });
    return list;
  }

  function atTopOf(container, list) {
    var heading = container.querySelector("h2");
    if (heading) heading.insertAdjacentElement("afterend", list);
    else container.insertAdjacentElement("afterbegin", list);
  }

  function placeErrors(form, key, messages) {
    var prefix = prefixOf(form);
    var list = errorList(messages);
    var rowMatch = prefix && key.match(new RegExp("^" + prefix + "-(\\d+)-__all__$"));
    if (rowMatch) {
      var input = form.querySelector('input[name="' + prefix + "-" + rowMatch[1] + '-id"]');
      var row = input && rowOf(input);
      (row || form).insertAdjacentElement("afterbegin", list);
      return;
    }
    var field = key === "__all__" || key === prefix + "-__all__" ? null : form.elements[key];
    if (field && typeof RadioNodeList !== "undefined" && field instanceof RadioNodeList) field = field[0];
    if (!field) {
      atTopOf(form.querySelector(".hub-card") || form, list);
      return;
    }
    (field.closest(".pl-form-group, .pl-toggle-row") || field.parentElement).appendChild(list);
  }

  // --- The document and window guards, bound once per document ---

  function currentEditor() {
    var root = document.querySelector("[data-guild-autosave]");
    return root ? root.plGuildAutosave : null;
  }

  function bindGuards() {
    if (window.plGuildAutosaveGuardBound) return;
    window.plGuildAutosaveGuardBound = true;

    // Hard navigations (the sidebar, the address bar, a close): flush what is typed so the
    // save goes out whatever the member picks, then the native prompt while anything is open.
    window.addEventListener("beforeunload", function (e) {
      var editor = currentEditor();
      if (!editor) return;
      var hadWork = editor.hasWork();
      editor.flushAll(true);
      if (hadWork || editor.hasWork()) {
        e.preventDefault();
        e.returnValue = "";
      }
    });

    // Boosted navigations (body hx-boost="true", where beforeunload does not fire): hold the
    // request, flush, let the queue drain, then go on, or ask after a failure.
    document.addEventListener("htmx:confirm", function (evt) {
      var editor = currentEditor();
      if (!editor) return;
      var elt = evt.detail.elt;
      var hasVerb = ["hx-get", "hx-post", "hx-put", "hx-patch", "hx-delete"].some(function (name) {
        return elt.hasAttribute(name);
      });
      var boosted = (elt instanceof HTMLAnchorElement || elt instanceof HTMLFormElement) && !hasVerb;
      if (!boosted) return;
      editor.flushAll(false);
      if (!editor.hasWork()) return;
      evt.preventDefault();
      editor.hold(function () { evt.detail.issueRequest(true); });
    });
  }

  function registerPlGuildAutosave() {
    Alpine.data("plGuildAutosave", function (options) {
      // Plumbing kept out of Alpine's reactive state: the root, typing timers, the save chain.
      var root = null;
      var timers = new Map();
      var chain = Promise.resolve();
      var pendingDelete = null;
      var heldNavigation = null;

      return {
        section: options.section,
        state: "idle",
        reason: "",
        pending: 0,
        saves: 0,

        init() {
          root = this.$el;
          root.plGuildAutosave = this;
          var self = this;
          root.addEventListener("input", function (e) { self.onInput(e.target); });
          // Capture: the banner drop zone dispatches a change that does not bubble.
          root.addEventListener("change", function (e) { self.onChange(e.target); }, true);
          root.addEventListener("click", function (e) { self.onClick(e); });
          bindGuards();
        },

        label() {
          if (this.pending > 0) return "Saving…";
          if (this.state === "error") return this.reason === "http" ? "Couldn't save." : "Couldn't save. Check your connection.";
          return "Saved";
        },

        hasWork() {
          return timers.size > 0 || this.pending > 0 || this.state === "error" || formsWithPendingDeletes(root).length > 0;
        },

        // --- The queue: one request at a time, each reading the page as it is when it runs ---
        enqueue(job) {
          var self = this;
          self.pending += 1;
          chain = chain
            .then(job)
            .catch(function () { self.reason = "connection"; self.state = "error"; })
            .then(function () { self.pending -= 1; });
        },
        done() {
          this.saves += 1;
          this.state = "saved";
        },
        post(url, data, keepalive) {
          var options = {
            method: "POST",
            headers: { "X-Autosave": "1", "X-CSRFToken": csrfToken() },
            body: data,
            credentials: "same-origin",
          };
          if (keepalive) options.keepalive = true;
          return fetch(url, options).then(function (response) {
            // fetch follows the login redirect of an expired session and reads it as a 200,
            // so a redirected answer is a save that never happened: say so and fail the call.
            if (response.redirected) {
              toast(SIGNED_OUT, "error");
              return new Response(null, { status: 401 });
            }
            relayToast(response);
            return response;
          });
        },

        // --- Typed fields: after a pause in typing, and at once when the field changes ---
        // A required field left blank waits for the change (blur): mid typing, a cleared name
        // is a field being retyped, not a value to refuse. An hours form saves on change only.
        onInput(el) {
          var form = formOf(el);
          if (!form || ignored(el) || !isTyped(el) || form.dataset.autosave === "change") return;
          var self = this;
          markEdited(form);
          clearTimeout(timers.get(form));
          timers.delete(form);
          if (isRequired(el, form) && !el.value.trim()) return;
          timers.set(form, setTimeout(function () {
            timers.delete(form);
            self.save(form);
          }, DEBOUNCE_MS));
        },
        onChange(el) {
          var form = formOf(el);
          if (!form || ignored(el) || !el.name) return;
          markEdited(form);
          this.saveNow(form);
        },
        save(form, keepalive) {
          var self = this;
          self.enqueue(function () { return self.saveForm(form, keepalive); });
        },
        saveNow(form) {
          clearTimeout(timers.get(form));
          timers.delete(form);
          this.save(form);
        },
        flushAll(keepalive) {
          var self = this;
          var forms = Array.from(timers.keys());
          formsWithPendingDeletes(root).forEach(function (form) {
            if (forms.indexOf(form) === -1) forms.push(form);
          });
          forms.forEach(function (form) {
            clearTimeout(timers.get(form));
            timers.delete(form);
            self.save(form, keepalive);
          });
        },
        async saveForm(form, keepalive) {
          if (!form.isConnected) return;
          form.plAutosaveEdited = false;
          var data = payload(form);
          var sig = signature(data);
          if (sig === form.plAutosaveSaved) return;
          var response = await this.post(form.action, data, keepalive);
          if (response.ok) {
            var saved = await response.json();
            clearErrors(form);
            applyRows(form, saved.rows);
            clearFiles(form, data);
            // The form as it now reads is what the server holds, unless it was edited while this
            // save ran: then that edit must post, so nothing is remembered.
            form.plAutosaveSaved = form.plAutosaveEdited ? null : signature(payload(form));
            this.done();
          } else if (response.status === 422) {
            var refused = await response.json();
            clearErrors(form);
            Object.keys(refused.errors).forEach(function (key) {
              placeErrors(form, key, refused.errors[key]);
            });
            if (refusesFormset(form, refused.errors)) unflagDeletes(form);
            this.state = "saved";
          } else {
            unflagDeletes(form);
            this.reason = "http";
            this.state = "error";
          }
        },

        // --- Leaving: a held boosted request resumes once the queue drains, or asks after a failure ---
        hold(resume) {
          var self = this;
          heldNavigation = resume;
          chain.then(function () {
            if (heldNavigation !== resume) return;
            if (self.state !== "error") {
              heldNavigation = null;
              resume();
              return;
            }
            window.dispatchEvent(new CustomEvent("open-confirm", { detail: "leave-while-saving" }));
          });
        },
        leave() {
          var resume = heldNavigation;
          heldNavigation = null;
          if (resume) resume();
        },

        // --- Formset rows: add, remove, delete ---
        onClick(e) {
          var target = e.target.closest("[data-formset-add], [data-formset-remove]");
          if (!target || !root.contains(target)) return;
          var form = formOf(target);
          if (!form) return;
          if (target.matches("[data-formset-add]")) this.addRow(form);
          else this.removeRow(form, rowOf(target));
        },
        addRow(form) {
          var template = form.querySelector("template[data-formset-template]");
          var total = managementInput(form, "TOTAL_FORMS");
          var index = parseInt(total.value, 10);
          var holder = document.createElement("div");
          holder.innerHTML = template.innerHTML.replaceAll("__prefix__", index);
          var row = holder.firstElementChild;
          markEdited(form);
          form.querySelector("[data-formset-rows]").appendChild(row);
          total.value = index + 1;
          var empty = form.querySelector("[data-formset-empty]");
          if (empty) empty.hidden = true;
          var first = row.querySelector("input:not([type=hidden]), select, textarea");
          if (first) first.focus();
        },
        // A row never saved just goes, unless a save is in flight or pending, in which case it
        // goes after that save (which may have just created it, and then it is a delete).
        removeRow(form, row) {
          var self = this;
          markEdited(form);
          if (!hasId(row)) {
            if (self.pending === 0 && !timers.has(form)) {
              row.remove();
              renumber(form);
              // The row that kept the form refused is gone: a delete waiting behind it posts now.
              if (hasPendingDelete(form)) self.saveNow(form);
              return;
            }
            self.enqueue(function () {
              if (hasId(row)) {
                self.deleteRow(form, row);
                return;
              }
              row.remove();
              renumber(form);
            });
            return;
          }
          var confirmId = form.dataset.autosaveConfirm;
          if (confirmId) {
            pendingDelete = { form: form, row: row };
            window.dispatchEvent(new CustomEvent("open-confirm", { detail: confirmId }));
            return;
          }
          self.deleteRow(form, row);
        },
        confirmDelete() {
          var held = pendingDelete;
          pendingDelete = null;
          if (held) this.deleteRow(held.form, held.row);
        },
        deleteRow(form, row) {
          markEdited(form);
          flagDelete(row);
          this.saveNow(form);
        },
      };
    });
  }

  if (window.Alpine) {
    registerPlGuildAutosave();
  } else {
    document.addEventListener("alpine:init", registerPlGuildAutosave);
  }
})();
