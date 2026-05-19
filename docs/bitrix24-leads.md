# Bitrix24 Lead Creation

When a new user is created with a phone number, the backend creates a Bitrix24
CRM lead through the `crm.lead.add` webhook method.

## Configuration

Set the incoming webhook base URL in `.env`:

```env
BITRIX24_WEBHOOK_URL=https://your-domain.bitrix24.com/rest/USER_ID/WEBHOOK_CODE/
```

Optional settings:

```env
BITRIX24_LEAD_TITLE="Wakil platform"
BITRIX24_LEAD_SOURCE_ID=WEB
BITRIX24_LEAD_SOURCE_DESCRIPTION="Wakil platforma"
BITRIX24_LEAD_ASSIGNED_BY_ID=1
BITRIX24_TIMEOUT_SECONDS=10
```

`BITRIX24_WEBHOOK_URL` can be either the webhook base URL or a full `crm.lead.add` URL.

## Behavior

- Users without `phone_number` are skipped until their phone number is added.
- Existing users are not duplicated.
- The returned Bitrix lead ID is stored on the user as `bitrix24_lead_id`.
- Bitrix failures are logged and do not block user creation.

## Manual Check

Send a test lead with:

```bash
.venv/bin/python scripts/check_bitrix24_lead.py
```

The script generates random test values and fetches the created lead back.
Set `SEND_TO_BITRIX = False` inside the script to print the payload without
sending it.
