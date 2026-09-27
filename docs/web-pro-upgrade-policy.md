# Web Pro upgrade grants

Status: implemented in PR #210 fixes. Date: 2026-09-27. Owner: B2C backend.

## Issued invoice terms

New invoices record `subscription_upgrade_policy: full_price_v1`. An eligible
Standard-to-Pro upgrade pays the quoted Pro amount without unused-time proration,
starts a new Pro period, and carries only the unused Standard credits. Campaign
pricing still applies when the quote is created.

An unversioned invoice keeps its captured amount and the previous grant timing
rules, even when paid after deployment. Reopening it returns the saved quote.
Do not backfill the new policy onto existing pending invoices or recalculate their
prices. If reverting the checkout changes, retain the version-aware grant reader
for invoices already issued under the new policy.

## Callback retries

Recurring web grants use a stable provider/order key in the subscription's
`applied_payment_grants` array. The credit update and insertion of this key share
one conditional write. Updating the invoice's `subscription_applied` flag happens
afterward; retrying a failed invoice update does not regrant credits or restore
credits already spent. Keep these keys when modifying subscriptions and do not
expire them while invoices can be replayed.

The write uses MongoDB's [single-document atomicity](https://www.mongodb.com/docs/manual/core/write-operations-atomicity/)
and [aggregation update pipelines](https://www.mongodb.com/docs/manual/tutorial/update-documents-with-aggregation-pipeline/).
A unique `user_id` index is required; index creation failure leaves the invoice
unapplied for retry. App Store/Google Play grant paths and daily-pass lots retain
their existing behavior.

## Verification

`PYTHONPATH=src python -m pytest tests/test_web_payment_grant_idempotency.py tests/test_full_price_pro_upgrades.py tests/test_playstore_renewal_idempotency.py tests/test_daily_pass_multiple_purchases.py`

These use in-memory fakes and make no provider calls. Cover concurrent duplicates,
retry after grant/before invoice update, later purchases, concurrent spending,
new and expired subscriptions, literal identifiers, index failure, and legacy
invoice reopening/payment. The aggregation fake does not replace a staging smoke
test against the deployed MongoDB version.
