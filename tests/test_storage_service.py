"""Tests for Google Cloud Storage credential configuration."""

from unittest.mock import MagicMock, patch

import pytest

from core.config import settings
from services.storage_service import StorageService


def _clear_service_account_settings(monkeypatch):
    for name in (
        "GOOGLE_PROJECT_ID",
        "GOOGLE_PRIVATE_KEY_ID",
        "GOOGLE_PRIVATE_KEY",
        "GOOGLE_CLIENT_EMAIL",
        "GOOGLE_CLIENT_ID",
    ):
        monkeypatch.setattr(settings, name, None)


def test_credentials_from_environment(monkeypatch):
    values = {
        "GOOGLE_PROJECT_ID": "project",
        "GOOGLE_PRIVATE_KEY_ID": "key-id",
        "GOOGLE_PRIVATE_KEY": "line-one\\nline-two",
        "GOOGLE_CLIENT_EMAIL": "svc@example.iam.gserviceaccount.com",
        "GOOGLE_CLIENT_ID": "client-id",
    }
    for name, value in values.items():
        monkeypatch.setattr(settings, name, value)

    expected = MagicMock()
    with patch(
        "services.storage_service.service_account.Credentials.from_service_account_info",
        return_value=expected,
    ) as factory:
        assert StorageService._credentials_from_environment() is expected

    info = factory.call_args.args[0]
    assert info["project_id"] == "project"
    assert info["private_key"] == "line-one\nline-two"
    assert info["client_email"] == values["GOOGLE_CLIENT_EMAIL"]


def test_credentials_from_environment_returns_none_for_adc(monkeypatch):
    _clear_service_account_settings(monkeypatch)
    # A project ID is also useful with Workload Identity / ADC and does not mean
    # that individual service-account credentials were partially configured.
    monkeypatch.setattr(settings, "GOOGLE_PROJECT_ID", "project")
    assert StorageService._credentials_from_environment() is None


def test_credentials_from_environment_rejects_partial_config(monkeypatch):
    _clear_service_account_settings(monkeypatch)
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_EMAIL", "svc@example.com")

    with pytest.raises(ValueError, match="GOOGLE_PRIVATE_KEY"):
        StorageService._credentials_from_environment()


def test_public_bucket_setting_is_gone():
    """Not hasattr(): extra="allow" on Settings keeps a stray env var alive as an
    instance attribute long after the field is deleted, so the assertion has to be
    on the class or it passes for the wrong reason in exactly the deployments that
    still set the variable."""
    from core.config import Settings

    assert "GCS_PUBLIC_BUCKET_NAME" not in Settings.model_fields


def test_uploads_and_deletes_take_no_bucket_argument():
    """One bucket. A per-call target was only ever the public avatar bucket."""
    import inspect

    for method in (StorageService.upload_file, StorageService.permanently_delete_file):
        assert "bucket_name" not in inspect.signature(method).parameters
