# Exact replay detection for biometric credentials

**Status:** approved 2026-09-06, follow up to #333
**Ships as:** one PR off `main` (v1.44.0)

## The problem, reproduced

`BiometricCredential.previous_secret_hash` is a **single slot**, so the server remembers
exactly one generation of secret. A credential is found by hashing whatever secret is
presented and matching it against `secret_hash` or `previous_secret_hash`. Once a secret is
two generations stale, it matches neither, so the credential cannot be identified at all.

Someone holding a stolen copy defeats replay detection by simply **using it twice**:

```
issue -> S1                        (thief copies S1; the victim's phone also holds S1)
thief redeems S1 -> S2   branch 1  previous = H(S1)
thief redeems S2 -> S3   branch 1  previous = H(S2), and H(S1) is gone
victim's phone returns with S1  -> "Unknown biometric credential."
                                -> revoked_at = None, nothing logged
                                -> the thief keeps redeeming, expiry renewed
```

No timing trick is involved, which makes it cheaper than the grace window variant fixed in
#333. It is also exactly what a thief does naturally, since the point of stealing a
credential is to use it more than once.

What this costs is **detection, not containment**: the member is still bounced to an emailed
code, the stolen device still appears under Signed In Devices to revoke by hand, and getting
the secret out of the Keychain or Keystore needs a compromised device or an extracted backup
to begin with. That is why #333 shipped with the limit documented rather than patched at the
end of a review cycle.

## The fix: split the token into a selector and a verifier

The root cause is that the **lookup key rotates**. Identification and authentication are
doing the same job, so losing the secret loses the identity. Split them, which is the
standard shape for a persistent login token:

* **`selector`** — random, unguessable, **stable for the life of the credential**, unique
  and indexed. Identifies which credential is being presented.
* **`verifier`** — the rotating secret, stored only as a hash. Proves the presenter holds it.

The device stores and sends both, e.g. `"<selector>.<verifier>"`.

`redeem` then becomes exact:

1. Look up by `selector`. **Not found** → unknown credential, raise.
2. Found, but revoked or expired → raise (as today).
3. `hash(verifier) == verifier_hash` → the normal path. Rotate, return the new token.
4. `hash(verifier) == previous_verifier_hash` **and** inside the grace window → the dropped
   reply case. Rotate with `keep_previous=True`, exactly as #333 established.
5. **Anything else** → the presented verifier does not match a credential we positively
   identified. That is a replay at ANY generation depth. Revoke, warn, raise.

Branch 5 is the whole point: detection stops depending on how stale the copy is.

### Why the selector must be unguessable

Do **not** use the primary key. Looking a credential up by a sequential id and revoking on
verifier mismatch hands anyone a denial of service: walk the integers, send a wrong verifier
for each, and revoke every member's biometric sign in. A random selector (32 bytes, same
generator as the secret) makes branch 5 unreachable without already holding a real token.

State this reasoning in the model docstring. It is the non obvious part, and the obvious
implementation is the dangerous one.

## Work

1. **Model.** Add `selector` (unique, indexed, unguessable) and rename the hash columns to
   `verifier_hash` / `previous_verifier_hash` for honesty. Migration must carry existing
   rows: mint a selector per row. Note existing credentials cannot be rescued, because no
   shipped device knows its selector, so the data migration should **revoke** pre existing
   rows rather than leave them unusable in a confusing way. Reverse function required.
2. **Manager.** `issue` returns the combined token. `redeem` implements the five branches
   above. Keep `keep_previous` semantics from #333 unchanged.
3. **Views.** `unlock` and `disable` accept the combined token. Preserve everything #333
   established: the identical failure body for every failure mode, rate limiting before any
   DB access on the unforgeable key, the JSON content type guard, the `is_active` refusal,
   and no secret bound to a local outside Sentry's denylist.
4. **JS.** `static/js/biometric-auth.js` stores and sends the combined token. Members with an
   existing enrollment are revoked by the migration, so the 401 path must land them cleanly
   back on the login code form and offer re enrollment. Verify that path specifically.
5. **Docs.** Remove the KNOWN LIMIT block from the model docstring and the caveat from
   `2026-09-06-biometric-login.md`, since the limit is gone.

## Tests

Port every existing biometric spec, then add:

* The exact scenario at the top of this file: thief redeems twice, victim's original returns
  → **revoked**, thief's current token dead. This is the regression test for the whole PR.
* The same at three, five and ten generations of staleness. Depth must not matter.
* A wrong verifier against a **valid** selector revokes (branch 5).
* An unknown selector does **not** revoke anything and is indistinguishable in its response
  from every other failure.
* The grace window still works, and still cannot be slid forward.
* The migration revokes pre existing rows and its reverse runs.

## Not in scope

The JS remains unverified by CI. This repo has no JS test infrastructure, which is why both
open redirect fixes in this file shipped unverified. Worth its own decision; do not build a
harness inside this PR.
