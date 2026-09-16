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
        "GOOGLE_SERVICE_ACCOUNT_CLIENT_ID",
    ):
        monkeypatch.setattr(settings, name, None)


def test_credentials_from_environment(monkeypatch):
    values = {
        "GOOGLE_PROJECT_ID": "project",
        "GOOGLE_PRIVATE_KEY_ID": "key-id",
        "GOOGLE_PRIVATE_KEY": "line-one\\nline-two",
        "GOOGLE_CLIENT_EMAIL": "svc@example.iam.gserviceaccount.com",
        "GOOGLE_SERVICE_ACCOUNT_CLIENT_ID": "client-id",
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


def test_partial_identity_reports_service_account_client_id_name(monkeypatch):
    _clear_service_account_settings(monkeypatch)
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_EMAIL", "svc@example.com")

    with pytest.raises(ValueError, match="GOOGLE_SERVICE_ACCOUNT_CLIENT_ID"):
        StorageService._credentials_from_environment()
