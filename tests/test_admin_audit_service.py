"""The audit trail is the only record that a manual grant ever happened.

There is no invoice behind these changes, so an entry that goes missing means
revenue that cannot be reconciled.
"""

import pytest

from core.config import settings
from services.admin_audit_service import (
    ACTION_GRANT,
    RESULT_SUCCESS,
    AdminActionAlreadyRecorded,
    AdminAuditService,
)
from tests.admin_fakes import FakeCollection, FakeMongoHandler

REQUEST_ID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def audit():
    collection = FakeCollection()
    handler = FakeMongoHandler(**{settings.ADMIN_AUDIT_LOGS_COLLECTION: collection})

    service = AdminAuditService.__new__(AdminAuditService)
    service.mongo_handler = handler
    service.collection_name = settings.ADMIN_AUDIT_LOGS_COLLECTION
    service._indexes_ready = False
    return service, collection


async def claim(service, request_id: str = REQUEST_ID) -> str:
    return await service.claim(
        request_id=request_id,
        action=ACTION_GRANT,
        target_user_id="7470310475",
        operator="mirodilbek",
        reason="paid via external account, receipt 4471",
        params={"tier": "standard", "period": "monthly"},
        source_ip="10.0.0.1",
        user_agent="admin-panel",
    )


async def test_claim_records_the_actor_and_reason(audit):
    service, collection = audit

    await claim(service)

    entry = collection.documents[0]
    assert entry["result"] == "in_progress"
    assert entry["actor"]["operator"] == "mirodilbek"
    assert entry["actor"]["operator_source"] == "header"
    assert entry["actor"]["source_ip"] == "10.0.0.1"
    assert entry["reason"].startswith("paid via external account")


async def test_key_fingerprint_is_recorded_and_is_not_the_key(audit):
    service, collection = audit

    await claim(service)

    fingerprint = collection.documents[0]["actor"]["key_fingerprint"]
    assert len(fingerprint) == 12
    assert fingerprint != settings.SUPER_ADMIN_API_KEY
    assert settings.SUPER_ADMIN_API_KEY not in fingerprint


async def test_replaying_a_request_id_raises_with_the_original(audit):
    service, collection = audit
    await claim(service)

    with pytest.raises(AdminActionAlreadyRecorded) as excinfo:
        await claim(service)

    assert excinfo.value.existing["request_id"] == REQUEST_ID
    # The guard must prevent a second claim, not merely report one.
    assert len(collection.documents) == 1


async def test_a_different_request_id_is_accepted(audit):
    service, collection = audit

    await claim(service)
    await claim(service, request_id="99999999-2222-3333-4444-555555555555")

    assert len(collection.documents) == 2


async def test_finalize_stores_before_and_after(audit):
    service, collection = audit
    event_id = await claim(service)

    await service.finalize(
        event_id,
        result=RESULT_SUCCESS,
        before={"computed": {"credits_remaining": 0}},
        after={"computed": {"credits_remaining": 6000}},
    )

    entry = collection.documents[0]
    assert entry["result"] == RESULT_SUCCESS
    assert entry["before"]["computed"]["credits_remaining"] == 0
    assert entry["after"]["computed"]["credits_remaining"] == 6000
    assert entry["duration_ms"] is not None


async def test_finalize_records_a_failure_with_its_code(audit):
    service, collection = audit
    event_id = await claim(service)

    await service.finalize(
        event_id, result="error", error_code="SUBSCRIPTION_NOT_FOUND"
    )

    assert collection.documents[0]["result"] == "error"
    assert collection.documents[0]["error_code"] == "SUBSCRIPTION_NOT_FOUND"


async def test_finalize_never_raises_when_the_write_fails(audit):
    """A broken audit write must not mask the outcome of the action itself."""
    service, collection = audit
    event_id = await claim(service)

    async def boom(*args, **kwargs):
        raise RuntimeError("mongo down")

    collection.find_one_and_update = boom

    assert await service.finalize(event_id, result=RESULT_SUCCESS) is None


async def test_list_entries_filters_and_paginates(audit):
    service, _ = audit
    await claim(service)
    await claim(service, request_id="99999999-2222-3333-4444-555555555555")

    matched = await service.list_entries(user_id="7470310475")
    assert matched["total"] == 2

    missed = await service.list_entries(user_id="someone-else")
    assert missed["total"] == 0

    by_operator = await service.list_entries(operator="mirodilbek")
    assert by_operator["total"] == 2

    page = await service.list_entries(limit=1, offset=1)
    assert len(page["items"]) == 1
    assert page["total"] == 2
