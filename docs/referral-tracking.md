# Referral Tracking

This feature counts which external websites send users to the WakilAI web app.

## Frontend Flow

External websites should link to the app with a `from` query parameter:

```text
https://chat.wakil.ai/?from=advice.uz
```

When the app loads, read the `from` value and call the backend once:

```http
POST /api/v2/referrals/track?from=advice.uz
```

The backend normalizes the value to a hostname and increments that source count in MongoDB.

## JavaScript Example

```js
const params = new URLSearchParams(window.location.search);
const source = params.get("from");

if (source) {
  await fetch(`${API_BASE_URL}/api/v2/referrals/track?from=${encodeURIComponent(source)}`, {
    method: "POST",
  });
}
```

## Response

```json
{
  "success": true,
  "source": "advice.uz",
  "total_count": 12
}
```

## Rules For Frontend Developers

- Use only the website hostname as `from`, for example `advice.uz`.
- Do not send full page URLs unless unavoidable. The backend will normalize `https://www.advice.uz/some-page` to `advice.uz`.
- Call the endpoint only once when the app first opens from a shared link.
- If the same user refreshes the page with `?from=advice.uz`, it will count again.
- The tracking endpoint is public and does not require an API key.

## Admin Stats

Referral counts can be checked with the existing API key:

```http
GET /api/v2/referrals/stats?limit=100
```

Response:

```json
{
  "sources": [
    {
      "source": "advice.uz",
      "total_count": 12,
      "first_seen_at": "2026-04-21T10:00:00Z",
      "last_seen_at": "2026-04-21T11:30:00Z"
    }
  ]
}
```

## Database

Collection: `referral_sources`

```js
{
  "source": "advice.uz",
  "total_count": 12,
  "first_seen_at": ISODate("2026-04-21T10:00:00Z"),
  "last_seen_at": ISODate("2026-04-21T11:30:00Z")
}
```
