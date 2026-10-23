from agent_hover import states

# The StopFailure example from the Claude Code hooks reference.
RATE_LIMIT = {"session_id": "abc123", "hook_event_name": "StopFailure", "error": "rate_limit",
              "error_details": "429 Too Many Requests", "last_assistant_message": "API Error: Rate limit reached"}


def test_rate_limit_is_waiting_with_the_error_text():
    assert states.map_event("claude", RATE_LIMIT) == ("waiting", "usage limit")
    assert states.question_text(RATE_LIMIT) == "API Error: Rate limit reached\n429 Too Many Requests"


def test_other_stop_failures():
    assert states.map_event("claude", {**RATE_LIMIT, "error": "billing_error"}) == ("waiting", "usage limit")
    assert states.map_event("claude", {**RATE_LIMIT, "error": "authentication_failed"})[0] == "waiting"
    assert states.map_event("claude", {**RATE_LIMIT, "error": "overloaded"}) == ("done", "error")
    assert states.map_event("claude", {"hook_event_name": "StopFailure"}) == ("done", "error")


def test_quota_notifications():
    def note(kind):
        return states.map_event("claude", {"hook_event_name": "Notification", "notification_type": kind,
                                           "message": "Usage limit reset. Press Enter to continue."})
    assert note("quota_auto_resume_fired") == ("working", "resumed")
    assert note("quota_auto_resume_stale") == ("waiting", "usage limit")
    assert note("quota_auto_resume_disabled") == ("done", "usage limit")
