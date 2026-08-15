# How to Configure Payment Processing

WakilAI supports two Uzbek payment providers: **Payme/Paycom** and **Click**. This guide covers credentials setup, subscription plan configuration, and webhook verification.

## Prerequisites

- Merchant account with Payme and/or Click
- Access to `.env`
- Public HTTPS URL for callbacks (Payme and Click send webhook callbacks to your server)

---

## Steps — Payme/Paycom

### 1. Add Payme credentials to `.env`

```env
PAYME_MERCHANT_ID=your-merchant-id
PAYME_MERCHANT_KEY=your-merchant-key
PAYME_PAYMENT_LINK_BASE=https://checkout.paycom.uz/
```

The `PAYME_MERCHANT_KEY` is used for HTTP Basic Auth on incoming Payme callbacks. The API verifies `Authorization: Basic base64(merchant_id:key)` on every RPC call.

### 2. Configure fiscal settings (required for fiscalization)

```env
PAYME_FISCAL_RECEIPT_TITLE=Online Payment
PAYME_FISCAL_IKPU_CODE=your-ikpu-code
PAYME_FISCAL_PACKAGE_CODE=your-package-code
PAYME_FISCAL_VAT_PERCENT=15
PAYME_FISCAL_RECEIPT_TYPE=0
```

Contact your accountant for the correct IKPU and package codes. These are required by Uzbekistan's electronic fiscalization system.

### 3. Configure subscription prices (in UZS sum)

```env
PAYME_SUBSCRIPTION_DAILY_PRICE_SUM=15000
PAYME_SUBSCRIPTION_STANDARD_MONTHLY_PRICE_SUM=300000
PAYME_SUBSCRIPTION_STANDARD_YEARLY_PRICE_SUM=3000000
PAYME_SUBSCRIPTION_PRO_MONTHLY_PRICE_SUM=600000
PAYME_SUBSCRIPTION_PRO_YEARLY_PRICE_SUM=6000000
```

### 4. Optional: run a time-boxed discount campaign

Applies to every rail (Payme, Click, Uzum) — App Store pricing is set separately in App Store
Connect and is not affected. Off by default; enable with:

```env
SUBSCRIPTION_PROMO_ENABLED=true
SUBSCRIPTION_PROMO_PERCENT=35
SUBSCRIPTION_PROMO_STARTS_AT=            # optional; omit to start immediately
SUBSCRIPTION_PROMO_ENDS_AT=2026-09-01T00:00:00+05:00
SUBSCRIPTION_PROMO_TIERS=standard,pro    # daily-pass tiers (basic/standard/premium) excluded by default
SUBSCRIPTION_PROMO_PERIODS=monthly,yearly
```

The discount is evaluated live on every quote, so the campaign ends automatically once
`SUBSCRIPTION_PROMO_ENDS_AT` passes — no deploy or restart needed to turn it off. Prices round
down to the nearest 1,000 sum. See `src/core/subscription_promo.py`.

### 5. Register the callback URL in the Payme merchant cabinet

Payme will POST to `POST /api/v2/transaction/payme/` for all RPC calls (CheckPerformTransaction, CreateTransaction, PerformTransaction, CheckTransaction, SetFiscalData).

Set this in your Payme merchant dashboard:
```
https://api.yourdomain.com/api/v2/transaction/payme/
```

### 6. Test the integration

Initiate a checkout:

```bash
curl -X POST https://api.yourdomain.com/api/v2/transaction/payme/init \
  -H "admin: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user_abc",
    "amount": 300000,
    "plan": "standard_monthly"
  }'
```

Expected response includes a `payment_url` to redirect the user to checkout.

---

## Steps — Click

### 1. Add Click credentials to `.env`

```env
CLICK_MERCHANT_ID=12345
CLICK_SERVICE_ID=67890
CLICK_SECRET_KEY=your-click-secret
CLICK_MERCHANT_USER_ID=111
CLICK_PAYMENT_LINK_BASE=https://my.click.uz/services/pay
```

### 2. Register callback URLs in Click merchant cabinet

Click uses a prepare/complete callback flow:

- Prepare: `POST /api/v2/transaction/click/prepare`
- Complete: `POST /api/v2/transaction/click/complete`

Both must be HTTPS and publicly reachable.

### 3. Initiate a Click payment

```bash
curl -X POST https://api.yourdomain.com/api/v2/transaction/click/init \
  -H "admin: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user_abc",
    "amount": 300000
  }'
```

---

## Payment flow overview

```
Client → POST /payme/init          → backend creates invoice in MongoDB → returns checkout URL
User completes payment at Payme
Payme → POST /payme/ (RPC)         → backend verifies, updates transaction → grants subscription/credits
```

State machine for Payme transactions:
1. `created` — invoice exists in `payme_invoices`
2. `pending` — `CreateTransaction` received
3. `completed` — `PerformTransaction` received, subscription activated
4. `cancelled` — `CancelTransaction` received, refund initiated

---

## Viewing transactions

Transactions are stored in MongoDB:

- `payme_invoices` — one doc per initiated checkout
- `transactions` — one doc per completed/failed payment
- `subscriptions` — one doc per active subscription
- `payme_fiscal` — fiscal data from `SetFiscalData` callbacks

To check a user's subscription status:

```bash
GET /api/v2/transaction/payme/subscriptions/{user_id}
```

To list subscription catalog:

```bash
GET /api/v2/transaction/payme/subscriptions/catalog
```

---

## Verification

- [ ] `POST /payme/init` returns a URL with `paycom.uz/checkout/`
- [ ] Payme callback reaches `/api/v2/transaction/payme/` without 401
- [ ] `transactions` collection shows a completed record after test payment
- [ ] `subscriptions` collection shows an active record with correct `end_ms`
- [ ] `GET /api/v2/history/users/{user_id}/rate-limit` shows an increased daily limit

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| 401 on Payme callback | `PAYME_MERCHANT_KEY` doesn't match what's in your Payme dashboard |
| Subscription not activated after payment | Check `transactions` collection for `PerformTransaction` record; `payme.py` logs the full RPC payload |
| Fiscal error in Payme | IKPU or package code is wrong; contact your accountant |
| Click prepare fails with 400 | `CLICK_SECRET_KEY` HMAC mismatch; re-check the key in Click dashboard |

---

## Related

- [Explanation: Credit System](explanation-credit-system.md)
- [Reference: Environment Variables](reference-environment-variables.md)
- [Docs: Promo Codes](promo-codes.md)
