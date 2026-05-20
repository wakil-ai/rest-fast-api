# API v2 — Frontend migration (route cleanup)

Backend routes were reorganized so **frontend and DT clients no longer send `super_secret_admin_key`** in JSON bodies or query strings. Use the standard API headers instead.

## Headers (unchanged)

| Header | Config name | Used for |
| --- | --- | --- |
| `admin` | `API_KEY_NAME` | WakilAI web / admin tooling |
| `x-dt-team-api-key` | `DT_API_KEY_NAME` | DT team backend |
| `x-super-admin-key` | `SUPER_ADMIN_KEY_NAME` | Internal ops only (user create/block, Telegram admin) — **not required for the endpoints below** |

---

## Path changes (frontend)

Update client base paths from `/api/v2/admin/...` to the new locations.

### Rate limit / credits

| | Old | New |
| --- | --- | --- |
| **Method** | `GET` | `GET` |
| **Path** | `/api/v2/admin/rate-limit/{user_id}` | `/api/v2/history/users/rate-limit/{user_id}` |
| **Auth** | `admin` **or** `x-dt-team-api-key` | Same |
| **Body** | — | — |

Response shape is unchanged (`RateLimitResponse`).

**Example**

```http
GET /api/v2/history/users/rate-limit/user_abc123
admin: <API_KEY>
```

---

### Promo codes (assign / lookup / remove)

These are the endpoints most frontends and DT services call.

| Action | Old path | New path | Auth |
| --- | --- | --- | --- |
| Assign to user | `POST /api/v2/admin/promo-codes/assign` | `POST /api/v2/promo-codes/assign` | `admin` or `x-dt-team-api-key` |
| Remove from user | `DELETE /api/v2/admin/promo-codes/assign/{user_id}` | `DELETE /api/v2/promo-codes/assign/{user_id}` | Same |
| Get user's code | `GET /api/v2/admin/promo-codes/users/{user_id}` | `GET /api/v2/promo-codes/users/{user_id}` | Same |

Request/response bodies are unchanged (`UserPromoCode` for assign).

**Example — assign**

```http
POST /api/v2/promo-codes/assign
admin: <API_KEY>
Content-Type: application/json

{
  "user_id": "user_abc123",
  "promo_code": "WELCOME2026"
}
```

---

### Promo codes (admin CRUD)

Used by internal/admin tools, not typical end-user UI.

| Action | Old path | New path | Auth |
| --- | --- | --- | --- |
| Create | `POST /api/v2/admin/promo-codes` | `POST /api/v2/promo-codes` | `admin` header only |
| Get | `GET /api/v2/admin/promo-codes/{code}` | `GET /api/v2/promo-codes/{code}` | `admin` |
| Activate | `PATCH /api/v2/admin/promo-codes/{code}/activate` | `PATCH /api/v2/promo-codes/{code}/activate` | `admin` |
| Deactivate | `PATCH /api/v2/admin/promo-codes/{code}/deactivate` | `PATCH /api/v2/promo-codes/{code}/deactivate` | `admin` |

---

## Removed fields (no `super_secret_admin_key`)

Stop sending these fields; they are **ignored/removed** from the API models.

| Endpoint area | Removed field | Replacement |
| --- | --- | --- |
| Create promo code | `super_secret_admin_key` in body | `admin` header only |
| Save Telegram chats | `super_secret_admin_key` in body | `x-super-admin-key` header (backend ops) |
| List Telegram chats | `super_secret_admin_key` query param | `x-super-admin-key` header (backend ops) |

**Create promo — before**

```json
{
  "code": "WELCOME2026",
  "credit_amount": 50,
  "super_secret_admin_key": "..."
}
```

**Create promo — after**

```json
{
  "code": "WELCOME2026",
  "credit_amount": 50
}
```

```http
POST /api/v2/promo-codes
admin: <API_KEY>
```

**Save Telegram chats — after** (not a frontend route; ops/backoffice only)

```http
POST /api/v2/admin/telegram/save/chats
x-super-admin-key: <SUPER_ADMIN_API_KEY>
Content-Type: application/json

{
  "chat": { "chat_id": 123, "user_id": 456 }
}
```

---

## Admin routes (unchanged paths, stricter auth)

Telegram admin endpoints stayed under `/api/v2/admin` but now require **`x-super-admin-key` only** (no `super_secret_admin_key` in body/query).

| Method | Path | Auth |
| --- | --- | --- |
| `POST` | `/api/v2/admin/telegram/save/chats` | `x-super-admin-key` |
| `GET` | `/api/v2/admin/telegram/chats` | `x-super-admin-key` |

Typical **frontend apps do not call these**.

---

## Quick checklist for frontend

- [ ] Replace `GET .../admin/rate-limit/{user_id}` → `GET .../history/users/rate-limit/{user_id}`
- [ ] Replace `.../admin/promo-codes/...` → `.../promo-codes/...` (same suffix after prefix)
- [ ] Remove `super_secret_admin_key` from promo create payloads
- [ ] Keep using `admin` or `x-dt-team-api-key` for rate limit and promo assign/user endpoints
- [ ] Do **not** add `x-super-admin-key` unless integrating user block/create or Telegram admin tools

---

## Related docs

- Full endpoint list: [api-reference.md](api-reference.md)
- Promo code behavior: [promo-codes.md](promo-codes.md)
- Credits: [rate-limiting.md](rate-limiting.md)
