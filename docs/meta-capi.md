# Meta Conversions API (Purchase events)

Server-side `Purchase` events for mobile-app installs (WK-328). The apps send install/registration
events themselves via the Meta SDK.

## User fields
`POST /api/v2/history/users` accepts optional `platform` (`ios`|`android`), `madid`, `anon_id`, `att` (0/1),
`os_version`, `app_version`, `app_build` and `$set`s them on the user document (new **and** existing,
non-archived users; unchanged values are not rewritten). No migration: Mongo just gains the fields.
- Every field is typed `Any` on the request model and validated by `normalize_ad_attribution`:
  a bad value is dropped, never a 422 that would fail the login.
- `madid` must be a UUID. `""` and the all-zero ID mean "no ID" (ATT denied / ad tracking off) and
  overwrite the stored one; a missing field leaves the old value.
- Returns **201 only when this request inserted the user** (a lost signup race returns 200); the apps log
  their registration event off that status.
- Account deletion (`archive_user_account`) `$unset`s all of these fields.

## When Purchase fires
After a grant is committed, once per paid charge (`event_id` = order id / Apple `transaction_id` / Play `order_id`):
- Payme, Click, Uzum: `BasePaymentService._finalize_subscription_invoice`
- App Store: `_grant_from_payload` (verify + `DID_RENEW` notifications)
- Play Store: `_grant_from_purchase`, reached only via `POST /transaction/playstore/verify`. There is no
  Play RTDN/Pub/Sub handler, so a renewal is reported only when the Android app re-verifies.

Not sent for admin grants, DT partner grants, or users with no stored `platform`/ids/`os_version`.
Runs in the background; any Meta failure is logged and never affects the payment. Transport errors,
429 and 5xx are retried twice (1s, 4s); Meta dedupes on `event_id`. Events are not persisted, so a
restart during the retry window loses one.

## What value is reported
- **App Store:** only `Production` transactions (the environment inside Apple's signed payload, not the
  client's string), so Sandbox/TestFlight/Xcode purchases are never reported. Value = Apple's own
  `price` (milliunits) in `currency`, so a free trial (price 0) reports nothing and a discount reports
  what was charged. If Apple gives no price but an `offerType` is set, nothing is reported.
- **Play Store:** nothing for `testPurchase`, nothing when the line item has an `offerDetails.offerId`
  (free trial / intro price; Play's response has no per-charge amount). Otherwise the plan's
  `recurringPrice` in its currency.
- **Payme/Click/Uzum:** the catalog `amount_sum`, always UZS, sent as `UZS` without conversion.
- Every value is sent in the currency it was charged in (Play/App Store can be any currency).
- These web checkouts are opened from the apps, so they are reported as app events with the user's
  stored IDs. A user who installed on a phone and pays on a laptop is still attributed to the phone.

## Payload
`action_source: app`, `user_data {madid, anon_id}`, `app_data {advertiser_tracking_enabled, extinfo}`,
`custom_data {value, currency}`. `extinfo` is exactly 16 items in Meta's fixed order:
`[i2|a2, ai.humblebee.wakil, app_version, app_build, os_version, "" x11]`. Meta marks index 0 (version)
and 4 (OS version) as required, so an event without a stored `os_version` is skipped.
The access token is sent in the POST body, not the URL. Logs contain status + Meta's error
code/message/`fbtrace_id` only, never the raw response body.

## Config (`.env`)
`META_CAPI_TOKEN` (required; empty disables reporting and logs one warning at the first purchase),
`META_DATASET_ID` (defaults to the production dataset), `META_GRAPH_API_VERSION` (Meta retires versions after ~2 years;
bump it), `META_CAPI_TEST_EVENT_CODE` (set only while QA-ing: server events don't show in Test Events
without it; remove after).

## Known limits
- Refunds/revocations (`REFUND`, `REVOKE`) cancel access but are **not** reversed in Meta: CAPI has no
  supported way to undo a Purchase. Upgrades/crossgrades report the new transaction's price.
- Only the last login's `platform`/ids are kept per user (last write wins across devices).
- Users who have not opened the updated app have no ad fields, so their purchases are skipped. Both
  apps re-sync on launch (iOS on every activation, Android on cold start), so this clears up as users
  update. Android sends `platform: "android"`, `att: 1`, and `os_version` = `Build.VERSION.RELEASE`.
