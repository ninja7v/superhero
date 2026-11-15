import pytest

from superhero.ai.planner import AIUnavailable, Plan, is_local, make_plan, validate
from superhero.config import AIConfig
from superhero.interaction.commands import Command
from tests.test_engine import Rig


def step(kind, value):
    return {"kind": kind, "value": value}


def test_valid_plan_becomes_commands():
    plan = validate(
        {
            "steps": [
                step("launch", "firefox"),
                step("wait", "1"),
                step("type", "weather"),
                step("key", "Enter"),
                step("click", "double"),
                step("scroll", "down"),
            ],
            "explanation": "Searches the weather.",
        }
    )
    assert plan.steps == (
        Command("launch", "firefox"),
        Command("wait", "1.0"),
        Command("type", "weather"),
        Command("key", "enter"),
        Command("action", "double_click"),
        Command("scroll", "down"),
    )
    assert not plan.risky and "launch firefox" in plan.summary()


def test_formats_real_models_produce_are_accepted():
    # qwen3.5 uses "type" for the kind and a one-key shorthand; gemma4 garbles wait values.
    plan = validate(
        {
            "steps": [{"type": "launch", "value": "gedit"}, {"wait": "1"}, step("wait", "],")],
            "explanation": "",
        }
    )
    assert plan.steps == (
        Command("launch", "gedit"),
        Command("wait", "1.0"),
        Command("wait", "1.0"),
    )


@pytest.mark.parametrize(
    "bad",
    [
        "just text",
        {"explanation": "no steps key"},
        {"steps": [step("shell", "rm -rf ~")]},
        {"steps": [step("key", "ctrl+hyper")]},
        {"steps": [step("key", "")]},
        {"steps": [step("type", "x" * 501)]},
        {"steps": [step("launch", "a\nb")]},
        {"steps": [step("scroll", "sideways")]},
        {"steps": [step("click", "middle")]},
        {"steps": ["not a dict"]},
        {"steps": [step("key", "a")] * 11},
    ],
)
def test_anything_outside_the_vocabulary_is_rejected(bad):
    with pytest.raises(AIUnavailable):
        validate(bad)


def test_closing_and_deleting_chords_are_flagged():
    assert validate({"steps": [step("key", "alt+f4")]}).risky
    assert validate({"steps": [step("key", "Shift + Delete")]}).risky
    assert not validate({"steps": [step("key", "ctrl+c")]}).risky


def test_waits_are_clamped():
    assert validate({"steps": [step("wait", "999")]}).steps == (Command("wait", "3.0"),)


def test_off_by_default_and_remote_needs_consent():
    assert not AIConfig().enabled
    with pytest.raises(AIUnavailable, match="turned off"):
        make_plan("open a tab", AIConfig())
    remote = AIConfig(enabled=True, model="m", url="https://api.example.com")
    assert not is_local(remote.url) and is_local("http://localhost:11434")
    with pytest.raises(AIUnavailable, match="remote AI is not allowed"):
        make_plan("open a tab", remote)


def test_unreachable_server_is_a_readable_error():
    config = AIConfig(enabled=True, model="m", url="http://127.0.0.1:9")
    with pytest.raises(AIUnavailable, match="cannot be reached"):
        make_plan("open a tab", config, timeout=2)


PLAN = Plan(
    (Command("key", "ctrl+t"), Command("wait", "0.5"), Command("type", "hello")), "Opens a tab."
)


def ai_rig(plan=PLAN, **kwargs):
    asked = []

    def planner(request, _config):
        asked.append(request)
        if isinstance(plan, Exception):
            raise plan
        return plan

    rig = Rig(planner=planner, **kwargs)
    rig.config.ai.enabled = True
    rig.asked = asked
    return rig


def wait_for_plan(rig, calls):
    import time

    deadline = time.monotonic() + 2
    while rig.input.calls != calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert rig.input.calls == calls


def test_wake_word_proposes_a_plan_but_runs_nothing():
    rig = ai_rig()
    assert rig.engine.handle_utterance("Computer, open a new tab and say hello.") == "Opens a tab."
    assert rig.asked == ["open a new tab and say hello."]
    assert rig.input.calls == []
    assert "key ctrl+t" in rig.engine.pending_text


def test_wink_confirms_instead_of_clicking():
    rig = ai_rig()
    rig.engine.handle_utterance("computer open a tab")
    rig.look()
    rig.wink()  # left click binding
    wait_for_plan(rig, [("keys", "ctrl+t"), ("type", "hello")])
    assert rig.engine.pending_plan is None


def test_saying_yes_confirms_and_no_cancels():
    rig = ai_rig()
    rig.engine.handle_utterance("computer open a tab")
    assert rig.engine.handle_utterance("Yes.") == "Running the plan"
    wait_for_plan(rig, [("keys", "ctrl+t"), ("type", "hello")])
    rig.engine.handle_utterance("computer open a tab")
    assert rig.engine.handle_utterance("cancel") == "Plan cancelled"
    assert rig.engine.pending_plan is None and len(rig.input.calls) == 2


def test_right_wink_cancels():
    from fovea.events import Eye

    rig = ai_rig()
    rig.engine.handle_utterance("computer open a tab")
    rig.look()
    rig.wink(Eye.LEFT)  # the user's right eye on a mirrored frame: right click binding
    assert rig.engine.pending_plan is None and rig.input.calls == []


def test_unanswered_plan_expires():
    rig = ai_rig()
    rig.engine.handle_utterance("computer open a tab")
    rig.now += 13
    assert rig.engine.pending_plan is None
    rig.look()
    rig.wink()  # an ordinary click again
    assert ("button", "left", True) in rig.input.calls and ("keys", "ctrl+t") not in rig.input.calls


def test_without_wake_word_or_when_disabled_speech_is_dictated(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    rig = ai_rig()
    assert rig.engine.handle_utterance("open a new tab and say hello to everyone") == "Dictated"
    rig.config.ai.enabled = False
    assert rig.engine.handle_utterance("computer open a tab") == "Dictated"
    assert rig.asked == []


def test_ai_is_not_asked_while_disarmed_and_errors_are_reported():
    rig = ai_rig(armed=False)
    assert rig.engine.handle_utterance("computer open a tab") == "Turn control on first"
    assert rig.asked == []
    broken = ai_rig(plan=AIUnavailable("The AI server cannot be reached. Is Ollama running?"))
    assert "cannot be reached" in broken.engine.handle_utterance("computer open a tab")
    assert broken.engine.pending_plan is None


def test_empty_plan_reports_the_models_reason():
    rig = ai_rig(plan=Plan((), "I cannot see which windows are open."))
    assert (
        rig.engine.handle_utterance("computer close everything else")
        == "I cannot see which windows are open."
    )
    assert rig.engine.pending_plan is None


def test_emergency_stop_discards_a_pending_plan():
    rig = ai_rig()
    rig.engine.handle_utterance("computer open a tab")
    rig.engine.emergency_stop()
    assert rig.engine.pending_plan is None
    assert not rig.engine.confirm_plan()


def test_a_multi_word_wake_word_is_removed_whole():
    rig = ai_rig()
    rig.config.ai.wake_word = "Hey computer"
    rig.engine.handle_utterance("Hey, computer, open a new tab.")
    assert rig.asked == ["open a new tab."]
