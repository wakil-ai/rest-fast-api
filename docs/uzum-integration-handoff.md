# Uzum Integration — Handoff & Conversation State

> **Last updated:** 2026-06-16
> **Status:** Uzum replied — they CANNOT pin amounts per planId on their side; we now return the amount in `/check` response (in sums). Deeplink confirmed, testable on prod.
>
> This document captures conversation state and open items. For the technical
> implementation reference (endpoints, error codes, request/response shapes),
> see [`uzum-integration.md`](./uzum-integration.md).

---

## TL;DR — Where things stand

| Item | State |
|---|---|
| Backend integration (5 webhooks: check/create/confirm/reverse/status) | ✅ Implemented, live on `dev-backend.wakil.ai` |
| HTTP 400 auth-failure fix (was 401) | ✅ Live on dev-backend, verified via curl 2026-06-07 |
| New 7-plan catalog (basic/standard/premium daily + standard/pro monthly/yearly) | ✅ Merged to `dev` branch, live on dev-backend |
| `/check` returns `data.amount.value` in sums | ✅ Implemented 2026-06-16 (this branch) — needs deploy to dev-backend |
| Last message from Uzum team | Confirmed model: amount returned in `/check`; deeplink supported, test on prod |
| Production credentials | ❌ Not yet — Uzum will issue real values once integration is approved |
| Frontend "Pay with Uzum" button + deeplink | ❌ Not started — waiting on Uzum to share the deeplink URL format |

---

## Repo / branch reality (read this first if returning later)

- **Real GitHub remote:** `wakil-ai/rest-fast-api` (note: this was renamed from `wakil-ai/rest-api` at some point in late May/early June 2026).
- **Local origin URL** was updated on 2026-06-07 to point at the new repo. If you cloned before that date, run:
  ```bash
  git remote set-url origin git@github.com:wakil-ai/rest-fast-api.git
  git fetch --all --prune
  ```
- `feat/uzum-payment-integration` branch was deleted on the remote after merge (normal cleanup). The HTTP 400 fix landed before that deletion.
- `feat/new-daily-tariffs` was also merged into `dev` and the branch is gone.
- **Production (`main`) does NOT yet have the Uzum integration.** Everything lives on `dev` and deploys only to `dev-backend.wakil.ai`. When prod-ready, separate PR `dev → main` is required.

---

## The 7-plan catalog (current state)

Defined in `app/services/payments/base.py:26-69` (the `_subscription_catalog` dict) with prices in `app/core/config.py:301-309`.

| planId | Price (so'm) | Amount (tiyin) | Tier | Period | Daily credits | Total credits | Uzbek label |
|---|---:|---:|---|---|---:|---:|---|
| `basic_daily` | 15,000 | 1,500,000 | basic | daily | 200 | 200 | Odatiy kunlik |
| `standard_daily` | 30,000 | 3,000,000 | standard | daily | 500 | 500 | Standart kunlik |
| `premium_daily` | 50,000 | 5,000,000 | premium | daily | 800 | 800 | Premium kunlik |
| `standard_monthly` | 300,000 | 30,000,000 | standard | monthly | uncapped | 6,000 | Standart oylik |
| `standard_yearly` | 3,000,000 | 300,000,000 | standard | yearly | uncapped | 72,000 | Standart yillik |
| `pro_monthly` | 600,000 | 60,000,000 | pro | monthly | uncapped | 12,000 | Pro oylik |
| `pro_yearly` | 6,000,000 | 600,000,000 | pro | yearly | uncapped | 144,000 | Pro yillik |

**Legacy compatibility:** `daily_daily` is still accepted as an alias for `basic_daily` via `_parse_plan_id` in `app/services/payments/uzum.py` (line ~79). Old Postman tests against `daily_daily` won't break.

**Upgrade logic:** Customer with active lower-rank daily pass (basic) can purchase a higher-rank one (standard or premium) — that's an upgrade. Same-rank or downgrade returns errorCode `10008`. Rank source: `app/core/subscription_tiers.py`.

**Test plans** (`test_monthly`, `test_yearly` — 10k/20k so'm) only exist when `DEBUG=True`.

---

## Dev-backend test credentials & artifacts

Stored in `.env` (gitignored) on local machine **and** on the dev-backend server. **These are placeholders** — must be rotated before going live.

```
UZUM_USERNAME=wakil_uzum
UZUM_PASSWORD=36Q6i-FpGhLZWWMRXeFcmwYVEHTv7SCdWufGyGPkkTc
UZUM_SERVICE_ID=101202
UZUM_TRANSACTIONS_COLLECTION=uzum_transactions
```

**Known-good test user (dev MongoDB):** `5904877504` — eligible for at least `standard_daily` as of 2026-06-07.

**Sample successful test transaction:** `627c256a-c88f-436c-adf3-7812e0e54646` (granted `daily_daily` to user `5904877504` on 2026-05-30 during initial integration testing).

---

## Verification curl commands (run anytime to confirm dev state)

### 1. Auth-failure returns HTTP 400 (not 401)

```bash
curl -i -X POST https://dev-backend.wakil.ai/api/v2/transaction/uzum/check \
  -u 'bad:creds' \
  -H 'Content-Type: application/json' \
  -d '{"serviceId": 101202, "timestamp": 1, "params": {}}'
```
Expect: `HTTP/2 400` + body `{"status":"FAILED","errorCode":"10001"}`.

### 2. New `standard_daily` plan is in the catalog

```bash
curl -i -X POST https://dev-backend.wakil.ai/api/v2/transaction/uzum/check \
  -u 'wakil_uzum:36Q6i-FpGhLZWWMRXeFcmwYVEHTv7SCdWufGyGPkkTc' \
  -H 'Content-Type: application/json' \
  -d '{"serviceId": 101202, "timestamp": 1, "params": {"userId": "5904877504", "planId": "standard_daily"}}'
```
Expect: `HTTP/2 200` + `status:"OK"` + the `data` block with `account.value` = user, `tariff.value` = `standard_daily`.

Both verified passing on 2026-06-07 23:38 UTC.

---

## Conversation timeline with Uzum team (Telegram, contact: Adxam)

**Phase 1 — Initial setup (late May 2026)**
- Confirmed Merchant API as the integration product (Adxam in chat: "serviceId, basic auth username/password — o'zizniki bo'ladi hozircha")
- Wakil.ai registered in Uzum catalog as serviceId `101202`
- We implemented and deployed to `dev-backend.wakil.ai`
- Sent test credentials + Postman instructions

**Phase 2 — Postman testing (early June 2026)**
- Uzum sent Postman requests with `params: { userId, planId }` — confirmed our chosen field names match what Uzum will send
- Uzum reported two issues:
  1. Auth-failure returned HTTP 401, spec requires HTTP 400 → **fixed**, deployed
  2. Asked: does the client enter the amount themselves? → **answered no**, amount fixed per plan

**Phase 3 — Catalog UX confirmation (2026-06-02 to 2026-06-07)**
- Uzum asked: "Are you assuming we'll wire fixed amounts per plan into a dropdown on our side?"
- Our answer: Yes, that's exactly the model
- Catalog was updated to 7 plans (was 5) — `basic_daily` rename + `standard_daily` + `premium_daily` added
- Sent updated 7-plan list with official Uzbek labels (from wakil.ai's own /billing page)
- Appended question: does Uzum support deeplink/QR to launch payment directly from a scanned link?

**Phase 4 — Amount-in-/check pivot (2026-06-15)**
- Uzum replied: they **cannot** pin fixed amounts per `planId` on their side
- Their actual model: their catalog only collects `userId` + `planId`, then they call our `/check`, and **we return the amount** under `data.amount.value` (in SUMS, string), which they display to the customer for confirmation
- Their example: `{"data": {"order_id": {"value": "6010"}, "amount": {"value": "79900"}}}`
- Crucial unit gotcha: `data.amount.value` in `/check` response is **in sums**, NOT tiyin. Every other amount in this protocol stays in tiyin.
- Deeplink: confirmed exists, testable on prod (URL format not yet shared)
- Backend change shipped (2026-06-16, this branch): `app/services/payments/uzum.py:check` now adds `data.amount.value` from `quote["amount_sum"]`

**Last message sent (2026-06-07):**
```
Да, именно — это идеальная модель. Клиент выбирает план из списка,
вы автоматически передаёте соответствующий planId и amount, мы
проверяем у себя.

Обновили каталог — теперь 7 планов. Подписи для UX в каталоге
(на узбекском, как у нас на сайте):

  • basic_daily       — Odatiy kunlik         (15 000 so'm)
  • standard_daily    — Standart kunlik       (30 000 so'm)
  • premium_daily     — Premium kunlik        (50 000 so'm)
  • standard_monthly  — Standart oylik        (300 000 so'm)
  • standard_yearly   — Standart yillik       (3 000 000 so'm)
  • pro_monthly       — Pro oylik             (600 000 so'm)
  • pro_yearly        — Pro yillik            (6 000 000 so'm)

В params в webhooks ждём, как и договаривались:
{ "userId": "<digits>", "planId": "<id из списка выше>" }

И отдельный вопрос: поддерживается ли в Uzum deeplink или QR для
запуска оплаты сразу в приложении? То есть можем ли мы у себя на
сайте сгенерировать ссылку/QR-код, по которому в приложении Uzum
открывается наш сервис (serviceId 101202) с уже заполненным
userId и planId? Это сильно улучшит UX — клиенту не придётся
искать wakil.ai в каталоге.
```

---

## Possible Uzum replies and how to handle each

| If Uzum says | What it means | Action |
|---|---|---|
| "Yes, deeplink supported" + format (e.g., `uzum://...?serviceId=...`) | Best case — we can render QR on `/billing` | Build a small frontend helper that builds the URL from `serviceId` + `userId` + `planId`, render as QR (e.g. `qrcode.vue`). **No backend changes needed.** |
| "Yes, but it's a separate Uzum product (Checkout / Dynamic QR)" | They'd push us to a different product | Push back — user previously instructed not to switch products. Keep Merchant API, accept the manual-catalog flow as the UX. |
| "No deeplink support for Merchant API" | We're stuck with catalog browsing | Accept it; frontend just shows "Open Uzum app → find wakil.ai → enter your user ID and pick a plan" |
| "Каталог настроен, можем запускать тесты заново" | They've wired the dropdown | Ask them to run full happy-path: /check → /create → /confirm with the new 7 plans. Verify in our MongoDB that subscriptions get granted. |

---

## Outstanding TODOs (in priority order)

### Pre-prod blockers
1. **Frontend "Pay with Uzum" button** on Nuxt billing page (`../frontend-nuxt/CheckoutBar.vue` pattern). UX depends on Uzum's deeplink reply — if deeplink supported, render QR + "open in Uzum app" button; otherwise instructional modal pointing user to the catalog manually.
2. **Production credentials** — request from Uzum once integration approved. Rotate `UZUM_USERNAME` / `UZUM_PASSWORD` / `UZUM_SERVICE_ID` on prod.
3. **Catalog activation on Uzum side** — they need to actually configure the dropdown with our 7 planIds before customers can buy anything.

### Polish
4. **Reverse semantics** — currently, `/reverse` on a CONFIRMED transaction is logged as a warning but doesn't auto-revoke the granted subscription. Decide whether this needs explicit handling or stays manual-review.
5. **Monitoring** — add metric/log alert for: high rate of `10007` (catalog mismatch), any `99999`, `/confirm` retries (Uzum retries up to 10× on 5xx).
6. **Idempotency stress test** — Uzum's spec allows retries; we proved idempotency works for single duplicate replay, but worth a load-test with many concurrent retries.

### Cleanup
7. **Sync `main` with `dev`** when ready for prod. Currently `main` is missing all Uzum work (and the 7-plan catalog rebrand).

---

## Files modified across this integration

Quick navigation map. Read these in order if onboarding fresh:

| File | Purpose |
|---|---|
| `app/services/payments/uzum.py` | Main service: 5 webhook handlers (check/create/confirm/reverse/status), plan parsing, amount validation |
| `app/api/v2/payment.py` | Route handlers (`/uzum/{check,create,confirm,reverse,status}`) + `_handle_uzum_webhook` wrapper |
| `app/models/payment.py` | `UzumResponseStatus`, `UzumTransactionState`, `UzumError`, `UzumServiceError`, request DTOs |
| `app/security/dependencies.py` | `verify_uzum_authorization` (Basic Auth, constant-time compare) |
| `app/core/config.py` | `UZUM_*` settings + `PAYME_SUBSCRIPTION_*_PRICE_SUM` (shared with Payme/Click) |
| `app/core/dependencies.py` | `get_uzum_service()` DI factory |
| `app/services/payments/base.py` | `_subscription_catalog` (7 plans), `_finalize_subscription_invoice` (subscription grant logic) |
| `app/core/subscription_tiers.py` | Daily-pass tier rank logic for upgrade enforcement |
| `docs/uzum-integration.md` | Technical reference (endpoints, error codes) — keep current with code |
| `docs/uzum-integration-handoff.md` | This file — conversation state & roadmap |

---

## Risk callouts / gotchas for next-session-you

1. **`UZUM_PASSWORD` in `.env` is a placeholder.** Never commit it. Never paste into a PR description. Rotate before prod.
2. **`main` branch is missing Uzum integration entirely.** A merge `dev → main` is a deliberate prod release, not a fast-forward — review carefully.
3. **My local git view of remote branches gets stale fast** since the repo was renamed (`rest-api` → `rest-fast-api`). If commands like `git branch -a` show old branch names, run `git fetch --prune`.
4. **The Uzum team's preferred language is Russian** for business communication, **Uzbek (Latin)** for customer-facing labels. Mirror that pattern in any future replies.
5. **Don't pivot to Uzum's other products (Checkout, Dynamic QR)** without explicit user approval — user was told by Uzum to use Merchant API specifically, and we've designed for that.
6. **HTTP status code on auth failure = 400, not 401**, per Uzum spec. The body `{status: "FAILED", errorCode: "10001"}` is the actual error signal; HTTP 400 is just a transport convention they enforce.
7. **Customer does NOT enter amount.** Amount is derived from planId on Uzum's side (via the dropdown they're configuring) and validated by us on `/create`. If `amount_tiyin / 100 != quote.amount_sum`, we return errorCode `10007`.

---

## Useful prompts to start a new chat with

If you want to pick up the thread cold, paste this into Claude:

> Read `docs/uzum-integration-handoff.md` and `docs/uzum-integration.md` — they capture our work on integrating Uzum Bank's Merchant API. We're awaiting a reply from the Uzum team to our last message (catalog confirmation + deeplink/QR question). Continue from where the handoff doc says we left off.
