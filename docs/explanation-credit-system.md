# The Credit System

How WakilAI controls API usage through a daily credit pool, per-assistant costs, promo codes, and subscriptions.

**Source:** [src/services/rate_limit_service.py](../src/services/rate_limit_service.py), [src/core/config.py](../src/core/config.py)

---

## Why credits instead of request limits?

Simple request-per-day limits treat a one-word question the same as a deep-research contract analysis. Credits allow the same limit (100/day by default) to represent different consumption depending on which assistant the user picks.

A user asking 10 general questions uses 100 credits. The same user asking 4 contract analysis questions also uses 100 credits (4 × 25). Both are fair use; both hit the same wall.

---

## Credit costs per assistant

| Assistant | Request cost |
|-----------|-------------|
| `main` (general law) | 10 credits |
| `tax` | 20 credits |
| `court` (all types) | 25 credits |
| `contract_analyzer` | 25 credits |
| `criminal_court` | 25 credits |

Costs are configurable via environment variables:

```env
DAILY_CREDITS_LIMIT=100
CREDIT_COST_MAIN_ASSISTANT=10
CREDIT_COST_SOLIQ_ASSISTANT=20
CREDIT_COST_SUD_ASSISTANT=25
CREDIT_COST_SHARTNOMA_ASSISTANT=25
```

---

## How credits are checked and deducted

**File:** [src/services/rate_limit_service.py](../src/services/rate_limit_service.py) — `check_and_decrement_credits()`

Every chat request goes through this sequence:

1. **Verify user exists.** If `user_id` isn't in the `users` MongoDB collection, the request is rejected (`return False`).

2. **Determine effective daily limit.** The limit is calculated in this priority order:

   ```
   base_limit = subscription_daily  (if active subscription exists)
             OR settings.DAILY_CREDITS_LIMIT  (default: 100)

   base_limit += daily_pass_bonus  (if active day-pass exists)

   if user has promo:
       if promo_credit_limit is None:
           return unlimited access (-1)
       else:
           base_limit += promo_credit_limit
   ```

3. **Read today's usage.** Query the `creditusage` collection for `{user_id, date: "YYYY-MM-DD"}`.

4. **Check sufficiency.** If `daily_limit - credits_used < credit_cost`, return `(False, credits_remaining, daily_limit)`. The API raises `InsufficientCreditsException` (HTTP 429).

5. **Deduct.** Increment `credits_used` by `credit_cost` via an atomic MongoDB `$inc`.

6. **Return.** `(True, new_credits_remaining, daily_limit)` — the API proceeds with the request.

**Fail-open policy:** if an unexpected exception occurs during credit checking, the request is allowed through with the default limit. This prevents a MongoDB hiccup from blocking all users, at the cost of potentially over-serving credits during outages.

---

## The MongoDB document

One document per user per day in the `creditusage` collection:

```json
{
  "_id": "...",
  "user_id": "user_abc",
  "date": "2026-05-21",
  "credits_used": 45,
  "created_at": "2026-05-21T08:00:00Z",
  "updated_at": "2026-05-21T14:23:00Z"
}
```

Credits reset daily: when no document exists for today, the user starts at 0 used.

---

## Subscriptions

Subscriptions override the default `DAILY_CREDITS_LIMIT`. An active subscription sets `daily_credits` to a higher value (e.g. 300 for the Standard plan).

**Active subscription check:**
- Reads from `subscriptions` collection via `SubscriptionStorage.get_subscription(user_id)`.
- Validates `end_ms > now_ms`.
- If `end_ms` has passed, the subscription is expired and the default limit applies.

### Subscription tiers (Payme prices in UZS)

| Plan | Daily credits | Monthly price | Yearly price |
|------|-------------|---------------|-------------|
| Free (default) | 100 | — | — |
| Day pass | 300 | 15,000 sum | — |
| Standard | (set per plan) | 300,000 sum | 3,000,000 sum |
| Pro | (set per plan) | 600,000 sum | 6,000,000 sum |
| Test | (set per plan) | 10,000 sum | 20,000 sum |

Prices are configurable via `PAYME_SUBSCRIPTION_*` env vars. See [Reference: Environment Variables](reference-environment-variables.md).

---

## Day passes

Day passes (`daily_subscriptions` collection) add a `daily_pass_bonus` on top of the base limit:

```
effective_daily_limit = base_limit + daily_pass_bonus
```

A user with Standard plan (300/day) who buys a day pass adds the pass bonus. Day passes expire by `end_ms`.

---

## Promo codes

Promo codes can:
1. **Grant unlimited access** — when `promo_credit_limit` is `None`, the user bypasses all credit checks. The request always returns `True`.
2. **Add bonus credits** — when `promo_credit_limit` is an integer, it's added to the effective daily limit.

Promo codes are assigned per-user in the `user-promos` collection. See [docs/promo-codes.md](promo-codes.md) for management endpoints.

---

## Checking a user's current credit status

```bash
GET /api/v2/history/users/{user_id}/rate-limit

Response:
{
  "remaining_credits": 65,
  "effective_daily_credit_limit": 100,
  "today_credits_used": 35,
  "uses_combined_credit_pool": true
}
```

Returns `-1` for `remaining_credits` and `effective_daily_credit_limit` when the user has unlimited access via promo.

---

## Admin: reset a user's credits

Credits can be reset by deleting today's `creditusage` document:

```bash
# Direct MongoDB operation (no API endpoint currently)
db.creditusage.deleteOne({ user_id: "user_abc", date: "2026-05-21" })
```

Or by blocking/unblocking the user, which is a separate operation from credit management.

---

## Related

- [Reference: Environment Variables](reference-environment-variables.md) — credit limits and costs
- [Reference: Error Codes](reference-error-codes.md) — `InsufficientCreditsException`
- [How-To: Configure Payments](howto-configure-payments.md) — subscription setup
- [Docs: Promo Codes](promo-codes.md)
- [Docs: Rate Limiting](rate-limiting.md)
