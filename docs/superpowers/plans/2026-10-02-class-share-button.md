# A Share button on the public class page

Round: 2026-10-02 instructor feedback round, PR 3 of 7.

**User story.** As a member or instructor looking at a class page on my phone, I would like a Share button that opens my phone's share sheet (text, email, Instagram, whatever apps I have), so I can send the class to a friend. Currently I have to copy the address bar by hand; inside the Past Lives app there is no address bar at all.

**Summary.** The public class page gets a Share button that uses the device share sheet where one exists and a small Copy / Text / Email menu everywhere else.

## Facts that shape the design

- `navigator.share` (the Web Share API) exists in Chrome for Android, Safari and iOS Safari, and in the iOS WKWebView the Past Lives app uses. It does **not** exist in the Android WebView (caniuse `android`: no support in any version), so inside the Android app it is undefined.
- The Capacitor shell (`mobile/`) has no Share plugin today (`@capacitor/share` is not in `package.json`). A plugin ships inside the app binary, so adding one needs an app release (STANDARDS.md § 11, `mobile/README.md`).
- The share link is `offering.public_url` (the canonical classes host, built by `core.urls_util.book_absolute_url`). The QR card uses `offering.qr_url`, the slug proof permalink; the Share button uses `public_url` because a human reads it.

## Expected behavior

**Button.** On `templates/classes/public/detail.html`, in the row under the hero that holds "← All classes" (left) and the editor controls (right), add a Share button next to "← All classes": class `pl-btn pl-btn--secondary` (check `cms-public.css` for the public surface's secondary button; if none exists, style it with the surface's tokens, never hardcoded colors), a share icon (the standard three node share glyph as inline SVG, 14px) and the word "Share". It renders for everyone, logged in or not. It does not render on a preview (`is_preview`), because the page is not public yet.

**Behavior** (`static/js/class_share.js`, loaded from the detail page's `classes_extra_head` block with `defer`; it must survive hx-boost arrivals the way `native-downloads.js` does, binding once on `window`):

1. `window.Capacitor && Capacitor.isNativePlatform() && Capacitor.Plugins && Capacitor.Plugins.Share` → `Capacitor.Plugins.Share.share({title, text, url, dialogTitle: "Share this class"})`. Nothing in the app has this plugin yet; this branch is what the next app release lights up with no further web change.
2. else `navigator.share` → `navigator.share({title, text, url})`. An `AbortError` (the person closed the sheet) is silent; any other rejection falls through to 3.
3. else a small menu opens under the button (Alpine `x-data` on the button's wrapper, `x-show`, closes on outside click and Escape, `role="menu"`): **Copy link** (writes `url` to the clipboard with `navigator.clipboard.writeText`, label flips to "Copied!" for two seconds; on failure selects a readonly input holding the url so the person can copy by hand), **Text** (`href="sms:?&body={text url}"`, URL encoded; this form works on both iOS and Android), **Email** (`href="mailto:?subject={title}&body={text url}"`).

The button carries the facts as data attributes rendered by the server: `data-share-url="{{ offering.public_url }}"`, `data-share-title="{{ offering.title|strip_date_suffix }}"`, `data-share-text="{{ offering.title|strip_date_suffix }} at Past Lives Makerspace"`. No dashes in the text.

**Mobile shell prep** (second commit in the same PR, so the next app build carries the plugin): add `@capacitor/share` to `mobile/package.json`, add it to BOTH `includePlugins` allowlists in `mobile/capacitor.config.ts` (they are allowlists; read the comment there), run `npm install` in `mobile/` to update the lock file and `npx cap sync android` so the committed Android project references the plugin. Do not bump `versionCode`; that happens at release. If `npm install` cannot reach the network, skip this commit and say so in the report.

## Acceptance criteria

- [ ] A public class page renders one element `[data-share-url]` whose value equals `offering.public_url`, with the title and text attributes above; a preview page renders none.
- [ ] e2e (Chromium on Linux has no `navigator.share`): clicking Share opens the menu with three items; Copy link puts the url on the clipboard (grant `clipboard-read`/`clipboard-write` in the context) and the label reads Copied!; the Text and Email hrefs start with `sms:` and `mailto:` and contain the url. Escape closes the menu.
- [ ] e2e: with `navigator.share` stubbed via `page.add_init_script` to record its argument, clicking Share calls it with `{title, text, url}` and opens no menu.
- [ ] Unit spec on the view/template for the attributes and the preview case (anchor on `data-share-url`).
- [ ] Both themes checked; screenshots of the button and the open menu under `mockups/screenshots/` (desktop and 390px wide).
- [ ] The mobile commit touches only `mobile/package.json`, `mobile/package-lock.json`, `mobile/capacitor.config.ts` and the files `cap sync android` rewrites.

## Out of scope

A share button on the catalog card, the instructor page or the hub. An app store release (Felix does that; the PR says the Android app shows the menu until then and the iOS app gets the sheet now).

## Files

`templates/classes/public/detail.html`, `static/js/class_share.js` (new), `static/css/cms-public.css`, `tests/e2e/class_share_spec.py` (new), a spec under `classes/spec/views/`, `mobile/package.json`, `mobile/package-lock.json`, `mobile/capacitor.config.ts`, `mobile/android/...` (sync output).
