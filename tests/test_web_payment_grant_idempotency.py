import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.payments.base import BasePaymentService
from services.subscription_storage import SubscriptionStorage

NOW = 1_800_000_000_000
DAY = 86_400_000
QUOTE = {'tier': 'pro', 'period': 'monthly', 'days': 30, 'total_credits': 18_000,
         'daily_credits': 0, 'amount_sum': 300_000}
STANDARD = {'user_id': 'u1', 'tier': 'standard', 'period': 'monthly',
            'provider': 'click', 'credits_remaining': 4_000, 'total_credits': 6_000,
            'start_ms': NOW - 10 * DAY, 'end_ms': NOW + 20 * DAY}


def evaluate(value, document):
    if isinstance(value, str) and value.startswith('$'):
        return document.get(value[1:])
    if isinstance(value, list):
        return [evaluate(item, document) for item in value]
    if not isinstance(value, dict):
        return value
    operator, args = next(iter(value.items()))
    if operator == '$literal':
        return deepcopy(args)
    if operator == '$cond':
        condition, yes, no = args
        return evaluate(yes if evaluate(condition, document) else no, document)
    values = evaluate(args, document)
    if operator == '$ifNull':
        return values[0] if values[0] is not None else values[1]
    if operator == '$add':
        return sum(values)
    if operator == '$max':
        return max(values)
    if operator == '$gt':
        return values[0] > values[1]
    if operator == '$eq':
        return values[0] == values[1]
    if operator == '$and':
        return all(values)
    if operator == '$setUnion':
        return list(dict.fromkeys(item for group in values for item in group))
    raise AssertionError(f'Unexpected aggregation operator: {operator}')


def matches(document, query):
    for key, value in query.items():
        current = document.get(key)
        if isinstance(value, dict):
            if '$gt' in value and not (current is not None and current > value['$gt']):
                return False
            if '$ne' in value and (value['$ne'] in current if isinstance(current, list) else current == value['$ne']):
                return False
        elif current != value:
            return False
    return True


class Collection:
    # Async boundaries before reads/writes reproduce callback interleaving;
    # each individual update evaluates against the current document atomically.
    def __init__(self, document):
        self.document = deepcopy(document)
        self.before_grant = None
        self.create_index = AsyncMock()

    async def find_one(self, query):
        result = deepcopy(self.document) if self.document and matches(self.document, query) else None
        await asyncio.sleep(0)
        return result

    async def update_one(self, query, update, upsert=False):
        await asyncio.sleep(0)
        if self.document is None and upsert:
            self.document = {**query, **deepcopy(update.get('$setOnInsert', {}))}
        if self.document is not None and matches(self.document, query):
            self.document.update(deepcopy(update.get('$set', {})))
        return SimpleNamespace(modified_count=1)

    async def find_one_and_update(self, query, update, return_document=None, **kwargs):
        await asyncio.sleep(0)
        if self.before_grant:
            action, self.before_grant = self.before_grant, None
            action(self.document)
        if not self.document or not matches(self.document, query):
            return None
        if isinstance(update, list):
            for stage in update:
                self.document.update({k: evaluate(v, self.document)
                                      for k, v in stage['$set'].items()})
        else:
            self.document.update(deepcopy(update.get('$set', {})))
        return deepcopy(self.document)


def service_for(*, policy='full_price_v1', existing=STANDARD):
    collection = Collection(existing)
    storage = SubscriptionStorage.__new__(SubscriptionStorage)
    storage.ensure_indexes = AsyncMock()
    storage.subscriptions_collection = 'subscriptions'
    storage.daily_subscriptions_collection = 'daily_subscriptions'
    storage.mongo_handler = SimpleNamespace(db={'subscriptions': collection})
    async def read(_):
        return await collection.find_one({'user_id': 'u1'})
    storage.get_subscription = AsyncMock(side_effect=read)
    invoice = {'order_id': 'order-1', 'user_id': 'u1', 'provider': 'payme',
               'amount_sum': 300_000, 'subscription': deepcopy(QUOTE),
               'subscription_applied': False}
    if policy:
        invoice['subscription_upgrade_policy'] = policy
    service = BasePaymentService.__new__(BasePaymentService)
    service.provider = 'payme'
    service.invoices_collection = 'invoices'
    service.subscription_storage = storage
    service.get_user_by_id = AsyncMock(return_value={'_id': 'u1'})
    service.db_handler = MagicMock()
    async def invoice_read(*_):
        snapshot = deepcopy(invoice)
        await asyncio.sleep(0)
        return snapshot
    async def invoice_write(_, query, fields):
        invoice.update(fields)
    service.db_handler.find_one = AsyncMock(side_effect=invoice_read)
    service.db_handler.update_one = AsyncMock(side_effect=invoice_write)
    return service, collection, invoice


async def finalize(service, order='order-1', now=NOW):
    await service._finalize_subscription_invoice(order_id=order, transaction_id='tx-' + order, now_ms=now)


@pytest.mark.asyncio
async def test_duplicate_web_callbacks_grant_once_and_preserve_carryover():
    service, collection, invoice = service_for()
    await asyncio.gather(finalize(service), finalize(service))
    assert collection.document['credits_remaining'] == 22_000
    assert collection.document['total_credits'] == 22_000
    assert collection.document['credit_carryover'] == 4_000
    assert collection.document['end_ms'] == NOW + 30 * DAY
    assert invoice['subscription_applied'] is True


@pytest.mark.asyncio
async def test_retry_after_grant_before_invoice_mark_does_not_restore_spent_credits():
    service, collection, invoice = service_for()
    normal_write = service.db_handler.update_one.side_effect
    service.db_handler.update_one.side_effect = RuntimeError('invoice write unavailable')
    with pytest.raises(RuntimeError):
        await finalize(service)
    collection.document['credits_remaining'] -= 100
    service.db_handler.update_one.side_effect = normal_write
    await finalize(service, now=NOW + DAY)
    assert invoice['subscription_applied'] is True
    assert collection.document['credits_remaining'] == 21_900
    assert collection.document['end_ms'] == NOW + 30 * DAY


@pytest.mark.asyncio
async def test_old_retry_after_another_purchase_keeps_latest_grant_and_balance():
    service, collection, invoice = service_for()
    await finalize(service)
    first_invoice = deepcopy(invoice)
    invoice.update(order_id='order-2', subscription_applied=False)
    await finalize(service, order='order-2', now=NOW + DAY)
    before_retry = deepcopy(collection.document)
    invoice.clear()
    invoice.update(first_invoice, subscription_applied=False)
    await finalize(service, now=NOW + 2 * DAY)
    assert collection.document == before_retry


@pytest.mark.asyncio
async def test_chat_debit_between_snapshot_and_grant_is_preserved():
    service, collection, _ = service_for()
    collection.before_grant = lambda document: document.update(credits_remaining=3_900)
    await finalize(service)
    assert collection.document['credits_remaining'] == 21_900
    assert collection.document['credit_carryover'] == 3_900


@pytest.mark.asyncio
@pytest.mark.parametrize('existing', [None, {**STANDARD, 'end_ms': NOW - 1}])
async def test_new_or_expired_subscriber_gets_only_purchased_credits(existing):
    service, collection, _ = service_for(existing=existing)
    await asyncio.gather(finalize(service), finalize(service))
    assert collection.document['credits_remaining'] == 18_000
    assert collection.document['end_ms'] == NOW + 30 * DAY


@pytest.mark.asyncio
async def test_legacy_prorated_invoice_keeps_issued_price_and_legacy_period_policy():
    service, collection, invoice = service_for(policy=None)
    invoice['amount_sum'] = 200_000
    invoice['subscription'].update(amount_sum=200_000, full_amount_sum=300_000,
                                   upgrade_credit_sum=100_000, upgrade_from_tier='standard')
    await finalize(service)
    assert invoice['amount_sum'] == 200_000
    assert collection.document['amount_sum'] == 200_000
    assert collection.document['start_ms'] == STANDARD['end_ms']
    assert collection.document['end_ms'] == STANDARD['end_ms'] + 30 * DAY
    assert collection.document['credit_carryover'] == 0


def test_new_invoice_records_upgrade_policy_version():
    service, _, _ = service_for()
    invoice = service._build_invoice_document(order_id='new', user_id='u1', amount_sum=300_000,
        callback_url='https://example.test', purpose='subscription', quote=QUOTE, now_ms=NOW)
    assert invoice.get('subscription_upgrade_policy') == 'full_price_v1'


@pytest.mark.asyncio
async def test_reopening_legacy_checkout_keeps_its_quote_and_price():
    service, _, invoice = service_for(policy=None)
    invoice.update(status='pending', amount_sum=200_000, callback_url='https://example.test/result')
    invoice['subscription'].update(amount_sum=200_000, upgrade_credit_sum=100_000)
    original = deepcopy(invoice)
    service.ensure_invoice_indexes = AsyncMock()
    service._get_subscription_quote = MagicMock(return_value=deepcopy(QUOTE))
    service.validate_subscription_eligibility = AsyncMock()
    service.build_payment_link = AsyncMock(return_value='https://example.test/checkout')
    service.db_handler.insert_one = AsyncMock()
    response = await service.init_payment(amount_sum=None, user_id='u1',
        callback_url='https://example.test/result', order_id='order-1',
        subscription_tier='pro', subscription_period='monthly')
    assert response['amount_sum'] == 200_000
    assert invoice == original
    assert service.build_payment_link.await_args.kwargs['amount_sum'] == 200_000
    service.db_handler.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_payment_identifiers_are_literal_values():
    service, collection, invoice = service_for()
    invoice['order_id'] = '$credits_remaining'
    await finalize(service, order='$credits_remaining')
    assert collection.document['order_id'] == '$credits_remaining'
    assert collection.document['credits_remaining'] == 22_000


@pytest.mark.asyncio
async def test_unique_index_failure_leaves_invoice_unapplied():
    service, collection, invoice = service_for()
    collection.create_index.side_effect = RuntimeError('unique index unavailable')
    with pytest.raises(RuntimeError):
        await finalize(service)
    assert collection.document == STANDARD
    assert invoice['subscription_applied'] is False
