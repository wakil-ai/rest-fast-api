from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.chains.chat_chain import ChatChain
from app.chains.court_classifier import CourtClassifier, CourtRoutingDecision
from app.core.assistants import AssistantConfig


def test_court_classifier_maps_expected_labels():
    classifier = CourtClassifier.__new__(CourtClassifier)

    assert classifier._map_response_to_assistant("criminal") == "criminal_court"
    assert classifier._map_response_to_assistant("civil") == "civil_court"
    assert classifier._map_response_to_assistant("economic") == "economic_court"
    assert (
        classifier._map_response_to_assistant("administrative")
        == "administrative_court"
    )


def test_court_classifier_returns_none_for_non_label_response():
    classifier = CourtClassifier.__new__(CourtClassifier)

    assert classifier._map_response_to_assistant(
        "I'm optimized only for court-related questions."
    ) is None


def test_public_assistants_hide_internal_court_variants():
    public_assistants = AssistantConfig.get_public_assistants()

    assert "court" in public_assistants
    assert "administrative_court" not in public_assistants
    assert "criminal_court" not in public_assistants
    assert "civil_court" not in public_assistants
    assert "economic_court" not in public_assistants


@pytest.mark.asyncio
async def test_chat_chain_resolves_court_to_specific_assistant():
    chat_chain = ChatChain.__new__(ChatChain)
    route_query = AsyncMock(
        return_value=CourtRoutingDecision(assistant_name="civil_court")
    )
    setattr(chat_chain, "court_classifier", SimpleNamespace(route_query=route_query))

    routing_decision = await ChatChain._resolve_court_routing(
        chat_chain,
        assistant="court",
        query="Nikohdan ajrashish bo'yicha da'vo bermoqchiman.",
        chat_history="",
        file_context="",
    )

    assert routing_decision.assistant_name == "civil_court"
    assert routing_decision.out_of_scope_message is None
    route_query.assert_awaited_once()


@pytest.mark.asyncio
async def test_chat_chain_keeps_non_court_assistant_unchanged():
    chat_chain = ChatChain.__new__(ChatChain)
    route_query = AsyncMock()
    setattr(chat_chain, "court_classifier", SimpleNamespace(route_query=route_query))

    routing_decision = await ChatChain._resolve_court_routing(
        chat_chain,
        assistant="tax",
        query="QQS haqida savol bor.",
        chat_history="",
        file_context="",
    )

    assert routing_decision.assistant_name == "tax"
    assert routing_decision.out_of_scope_message is None
    route_query.assert_not_called()


def test_court_classifier_treats_non_label_as_direct_response():
    classifier = CourtClassifier.__new__(CourtClassifier)

    decision = classifier._build_routing_decision(
        "I'm optimized only for court-related questions."
    )

    assert decision.assistant_name is None
    assert decision.out_of_scope_message == "I'm optimized only for court-related questions."


def test_court_classifier_places_file_context_before_chat_history():
    prompt = CourtClassifier._build_user_prompt(
        query="Analyze this",
        chat_history="Earlier we discussed something unrelated.",
        uploaded_file_context="This file contains a civil court claim.",
    )

    query_index = prompt.index("User query:")
    file_index = prompt.index("Uploaded file context:")
    history_index = prompt.index("Chat history:")

    assert query_index < file_index < history_index


@pytest.mark.asyncio
async def test_chat_chain_returns_out_of_scope_message_for_court_assistant():
    chat_chain = ChatChain.__new__(ChatChain)
    route_query = AsyncMock(
        return_value=CourtRoutingDecision(
            out_of_scope_message="I'm optimized only for court-related questions."
        )
    )
    setattr(chat_chain, "court_classifier", SimpleNamespace(route_query=route_query))

    result = await ChatChain.generate_answer(
        chat_chain,
        user_id="u1",
        message_id="",
        query="Please write a contract for me",
        chat_history=None,
        stream=False,
        file_ids=None,
        assistant="court",
        model_name=None,
    )

    assert result == "I'm optimized only for court-related questions."
