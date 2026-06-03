# File uploading limitation for the free users.

## Problem

Currently, free users can upload files via file upload endpoints.

Business requirement: **only paid** users can upload files.

## Goal

Implement file-upload access control so:
 * Free users are blocked from uploading files (message files + project files).
 * Paid users can upload as usual.
 * Frontend clearly communicates restriction and upgrade path.
 * Backend remains the source of truth (cannot be bypassed from client).

## Scope

### In scope

 * Backend authorization check for upload endpoints.
 * Standardized error response when user is not eligible.
 * Frontend gating (disable/hide upload actions for free users).
 * Frontend error handling for backend denial.
 * Tests (unit/integration + UI behavior).

### Out of scope

 * Payment flow redesign.
 * Changing subscription plan definitions.
 * Retroactive migration of old files.

---

## Current code touchpoints (backend)

From this repo, upload paths are here:

 * `app/api/v2/history/files.py` → `POST /files` (`upload_file_for_message`)
 * `app/api/v2/history/projects.py` → project upload endpoint(s), including `upload_project_file`
 * Upload service logic in `app/services/file_management.py`
 * Subscription/credit logic in `app/services/rate_limit_service.py` and `payment/subscription` services.

Observation: upload handlers currently call file manager directly and do not enforce paid-plan upload entitlement.

---

## Backend tasks

1. **Define entitlement rule**
    * Add a reusable check like `can_upload_files(user_id)` (service/dependency).
    * Rule: free tier => false, paid tier => true.
    * Use existing subscription source of truth (same area used by payment/subscription logic).
2. **Enforce on all upload entrypoints**
    * Block at API layer before file processing starts:
        * message file upload endpoint
        * project file upload endpoint
    * Return `403 Forbidden` (or `402/403` per product convention, pick one and keep consistent).
3. **Standardize response contract**
    * Example:
        ```json
        {
          "code": "FILE_UPLOAD_REQUIRES_PAID_PLAN",
          "message": "File upload is available for paid plans only.",
          "upgrade_required": true
        }
        ```

    * Document this in API docs/reference.
4. **Telemetry/logging**
    * Log denied attempts (user_id, endpoint, timestamp) without sensitive payloads.
5. **Backend tests**
    * Free user → upload denied.
    * Paid user → upload allowed.
    * Regression: existing non-upload endpoints unaffected.

---

## Frontend tasks

1. **Plan status detection**
    * Use existing subscription/status API used in app (or add one stable flag if needed):
        * derive `can_upload_files` boolean.
2. **UI gating**
    * Disable or hide upload controls for free users.
    * Show lock/tooltip/CTA: “Upgrade to upload files.”
3. **Error fallback**
    * If user still triggers upload (stale state or race), handle backend `FILE_UPLOAD_REQUIRES_PAID_PLAN` gracefully:
        * show upgrade modal/toast
        * do not show generic failure.
4. **Frontend tests**
    * Free user: upload UI gated.
    * Paid user: upload UI enabled.
    * Denial response displays correct upgrade message.

---

## Acceptance criteria

 - [ ] Free users cannot upload files through any upload endpoint.
 - [ ] Paid users can upload normally.
 - [ ] Backend returns consistent machine-readable error code for denied upload.
 - [ ] Frontend hides/disables upload for free users and shows upgrade UX.
 - [ ] Frontend handles backend denial gracefully (no generic error).
 - [ ] Automated tests cover free vs paid behavior.

---

## QA scenarios

1. Free user tries message upload → blocked + upgrade prompt.
2. Free user tries project upload → blocked + upgrade prompt.
3. Paid user uploads same files successfully.
4. API direct call (Postman/cURL) as free user still blocked.
5. Existing chat flow without file remains unaffected.

