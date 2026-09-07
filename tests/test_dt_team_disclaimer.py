"""The DT-team disclaimer must never stand in for an answer.

Regression: when a turn failed, the model's answer was empty but the disclaimer
was appended and streamed anyway. The chat then showed the English notice on its
own, and because the client treats any streamed text as a successful answer, the
failure was masked -- no error toast, no Sentry report, and the disclaimer-only
text was persisted as if it were a real reply.
"""

from services.chat_service import ChatService


def test_disclaimer_appended_to_a_real_answer():
    out = ChatService.append_dt_team_disclaimer("javob matni", is_dt_team_request=True)

    assert out.startswith("javob matni")
    assert len(out) > len("javob matni")


def test_disclaimer_not_added_to_an_empty_answer():
    assert ChatService.append_dt_team_disclaimer("", is_dt_team_request=True) == ""


def test_disclaimer_not_added_to_a_whitespace_only_answer():
    """Whitespace is still a failed turn -- and would render as a lone notice."""
    assert ChatService.append_dt_team_disclaimer("   \n", is_dt_team_request=True) == "   \n"


def test_disclaimer_skipped_for_non_dt_requests():
    assert ChatService.append_dt_team_disclaimer("javob", is_dt_team_request=False) == "javob"
