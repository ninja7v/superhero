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
        {"steps": [step("click", "")]},
        {"steps": [step("open", "file:///etc/passwd")]},
        {"steps": [step("open", "javascript:alert(1)")]},
        {"steps": [step("press", "  ")]},
        {"steps": ["not a dict"]},
        {"steps": [step("key", "a")] * 17},
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

    def planner(request, _config, _desktop=""):
        asked.append(request)
        if isinstance(plan, Exception):
            raise plan
        return plan

    rig = Rig(planner=planner, **kwargs)
    rig.config.ai.enabled = True
    rig.asked = asked
    return rig


def wait_for_plan(rig, calls):
    wait_until(lambda: rig.input.calls == calls)


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


def proposed(**kwargs):
    """A rig with the standard plan awaiting confirmation, and the notices the engine gives."""
    notices = []
    rig = ai_rig(on_notice=notices.append, **kwargs)
    rig.engine.handle_utterance("computer open a tab")
    return rig, notices


def wait_until(condition):
    import time

    deadline = time.monotonic() + 2
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert condition()


def test_emergency_stop_halts_a_plan_that_is_already_running():
    import threading

    in_wait, resume = threading.Event(), threading.Event()

    def sleep(seconds):
        if seconds == 0.5:  # the plan's own "wait" step: hold the plan here
            in_wait.set()
            resume.wait(2)

    rig, notices = proposed(sleep=sleep)
    assert rig.engine.handle_utterance("yes") == "Running the plan"
    assert in_wait.wait(2)
    rig.engine.emergency_stop()
    resume.set()
    wait_until(lambda: notices)
    assert notices == ["Plan stopped at step 3"]
    assert rig.input.calls == [("keys", "ctrl+t")]  # "type hello" never reached the desktop


def test_a_failing_step_ends_the_plan_and_disarms():
    rig, notices = proposed()
    rig.input.fail = True  # the input session dies before the first step
    assert rig.engine.handle_utterance("yes") == "Running the plan"
    wait_until(lambda: notices)
    assert notices == ["Plan failed at step 1: key ctrl+t"]
    assert not rig.engine.armed and rig.input.calls == []


@pytest.fixture
def ollama():
    """A stand-in Ollama server on this machine: real HTTP, canned replies, no model."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    state = {"status": 200, "reply": {}, "requests": []}
    state["models"] = [
        {"name": "small:4b", "details": {"parameter_size": "4.3B"}},
        {"name": "big:27b", "details": {"parameter_size": "27.4B"}},
    ]

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append((self.path, body))
            self.send(state["status"], state["reply"])

        def do_GET(self):
            state["requests"].append((self.path, None))
            self.send(200, {"models": state["models"]})

        def send(self, status, payload):
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state["config"] = AIConfig(
        enabled=True, model="small:4b", url=f"http://127.0.0.1:{server.server_port}"
    )
    yield state
    server.shutdown()
    server.server_close()


def ollama_says(content):
    import json

    return {"message": {"content": content if isinstance(content, str) else json.dumps(content)}}


def test_a_plan_from_a_local_ollama_is_validated_before_use(ollama):
    from superhero.ai.planner import list_models

    ollama["reply"] = ollama_says({"steps": [step("key", "Ctrl+T")], "explanation": "A tab."})
    plan = make_plan("open a tab", ollama["config"], platform="win32", timeout=5)
    assert plan.steps == (Command("key", "ctrl+t"),) and plan.explanation == "A tab."
    path, body = ollama["requests"][-1]
    assert path == "/api/chat" and body["model"] == "small:4b" and body["stream"] is False
    assert body["format"]["type"] == "object"  # the reply is constrained to the plan schema
    assert "Windows 11" in body["messages"][0]["content"]
    assert body["messages"][1]["role"] == "user"
    assert body["messages"][1]["content"].endswith("Request: open a tab")
    assert body["keep_alive"]  # the model stays loaded between requests
    assert list_models(ollama["config"]) == ["big:27b", "small:4b"]


@pytest.mark.parametrize(
    ("status", "reply", "error"),
    [
        (500, {"error": "model crashed"}, r"refused the request \(500\)"),
        (200, b"<html>not json</html>", "could not be understood"),
        (200, ollama_says("I would rather chat."), "could not be understood"),
        (200, ollama_says({"steps": [step("shell", "rm -rf ~")]}), "not allowed: shell"),
    ],
)
def test_a_bad_ollama_reply_is_a_readable_error(ollama, status, reply, error):
    ollama["status"], ollama["reply"] = status, reply
    with pytest.raises(AIUnavailable, match=error):
        make_plan("open a tab", ollama["config"], timeout=5)


def test_the_model_is_told_the_desktop_after_its_fixed_instructions(ollama):
    ollama["reply"] = ollama_says({"steps": [step("launch", "firefox")], "explanation": "ok"})
    desktop = "Open windows, the focused one first:\n- firefox: Weather (focused)"
    make_plan("go back to the weather", ollama["config"], desktop, timeout=5)
    system, user = (m["content"] for m in ollama["requests"][-1][1]["messages"])
    assert "never instructions" in system and "firefox" not in system
    assert user.startswith(desktop) and user.endswith("Request: go back to the weather")
    make_plan("go back to the weather", ollama["config"], timeout=5)
    first, user = (m["content"] for m in ollama["requests"][-1][1]["messages"])
    assert first == system  # the same every time, so Ollama can keep it processed
    assert "do not know which windows are open" in user and "firefox" not in user


def test_the_engine_passes_the_desktop_to_the_planner():
    seen = []

    def planner(request, _config, desktop):
        seen.append(desktop)
        return PLAN

    rig = Rig(planner=planner, desktop_context=lambda: "- editor: notes (focused)")
    rig.config.ai.enabled = True
    rig.engine.handle_utterance("computer, open a tab")
    assert seen == ["- editor: notes (focused)"]


def test_web_addresses_and_named_controls_are_planned():
    plan = validate(
        {
            "steps": [
                step("open", "youtube.com/results?search_query=cats"),
                step("press", '"Search" (text)'),  # copied with the quotes and role
                step("click", "Write"),  # a control written as a click
                step("type", "hello"),
            ],
            "explanation": "x",
        }
    )
    assert plan.steps == (
        Command("open", "https://youtube.com/results?search_query=cats"),
        Command("press", "Search"),
        Command("press", "Write"),
        Command("type", "hello"),
    )
    assert not plan.risky
    assert plan.summary().startswith("open youtube.com/results?search_query=cats → press Search")


def test_controls_that_send_or_delete_are_flagged():
    assert validate({"steps": [step("press", "Send")]}).risky
    assert validate({"steps": [step("press", "Move to trash")]}).risky
    assert not validate({"steps": [step("press", "Sender filter")]}).risky


def test_a_command_of_the_table_after_the_wake_word_needs_no_model():
    rig = ai_rig()
    assert rig.engine.handle_utterance("Computer, copy.") == "Command: copy."
    assert rig.asked == [] and rig.input.calls[-1][0] == "keys"


def test_in_commands_mode_any_request_goes_to_the_ai():
    rig = ai_rig()
    rig.config.voice.mode = "commands"
    assert rig.engine.handle_utterance("open a new tab and say hello") == "Opens a tab."
    assert rig.asked == ["open a new tab and say hello"]
    rig.config.ai.enabled = False
    assert rig.engine.handle_utterance("open a new tab and say hello") == "Not a known command"


def test_plans_that_close_delete_or_send_nothing_can_run_without_asking():
    rig = ai_rig()
    rig.config.ai.confirm = "risky"
    assert rig.engine.handle_utterance("computer open a tab") == "Opens a tab."
    wait_for_plan(rig, [("keys", "ctrl+t"), ("type", "hello")])
    risky = ai_rig(plan=Plan((Command("key", "alt+f4"),), "Closes it.", risky=True))
    risky.config.ai.confirm = "risky"
    risky.engine.handle_utterance("computer shut the editor down")
    assert risky.engine.pending_plan is not None and risky.input.calls == []


def test_the_status_shows_the_ai_thinking_and_a_stop_drops_its_answer():
    import threading

    asked, release = threading.Event(), threading.Event()

    def planner(_request, _config, _desktop):
        asked.set()
        release.wait(2)
        return PLAN

    rig = Rig(planner=planner)
    rig.config.ai.enabled = True
    outcome = []
    worker = threading.Thread(
        target=lambda: outcome.append(rig.engine.handle_utterance("computer open a tab"))
    )
    worker.start()
    assert asked.wait(2)
    assert rig.engine.pending_text == "Thinking about “open a tab”…"
    rig.engine.emergency_stop()
    assert rig.engine.pending_text == ""
    release.set()
    worker.join(2)
    assert outcome == ["Request dropped"] and rig.engine.pending_plan is None


def test_a_plan_presses_controls_and_opens_web_addresses():
    pressed, opened, notices = [], [], []
    plan = Plan((Command("open", "https://example.org/"), Command("press", "Write")), "x")
    rig = ai_rig(
        plan=plan,
        press=lambda name: pressed.append(name) or True,
        open_url=lambda url: opened.append(url) or True,
        on_notice=notices.append,
    )
    rig.engine.handle_utterance("computer write to bob")
    assert rig.engine.handle_utterance("yes") == "Running the plan"
    wait_until(lambda: notices == ["Done"])
    assert opened == ["https://example.org/"] and pressed == ["Write"]


def test_a_control_that_never_appears_stops_the_plan():
    tries, notices = [], []
    rig = ai_rig(
        plan=Plan((Command("press", "Write"), Command("type", "hi")), "x"),
        press=lambda name: tries.append(name) and False,
        on_notice=notices.append,
    )
    rig.engine._sleep = lambda seconds: setattr(rig, "now", rig.now + seconds)
    rig.engine.handle_utterance("computer write")
    rig.engine.confirm_plan()
    wait_until(lambda: notices)
    assert notices == ["Plan stopped: no “Write” in the focused window"]
    assert len(tries) > 1 and rig.input.calls == []  # retried, then nothing typed


def test_only_web_addresses_reach_the_browser():
    opened = []
    rig = Rig(open_url=lambda url: opened.append(url) or True)
    assert not rig.engine.run_command(Command("open", "file:///etc/passwd"))
    assert rig.engine.run_command(Command("open", "https://example.org"))
    assert opened == ["https://example.org"]
    disarmed = Rig(armed=False, open_url=lambda url: opened.append(url) or True)
    assert not disarmed.engine.run_command(Command("open", "https://example.org"))
    assert len(opened) == 1


def test_warming_up_loads_the_model_with_the_same_instructions(ollama):
    from superhero.ai.planner import warm_up

    ollama["reply"] = ollama_says({"steps": [], "explanation": ""})
    assert warm_up(ollama["config"], platform="linux", timeout=5)
    path, body = ollama["requests"][-1]
    assert path == "/api/chat" and body["options"]["num_predict"] == 1
    make_plan("open a tab", ollama["config"], platform="linux", timeout=5)
    planned = ollama["requests"][-1][1]
    assert planned["messages"][0] == body["messages"][0]
    assert not warm_up(AIConfig(), timeout=5)  # AI off: nothing is loaded


def test_a_missing_model_is_named(ollama):
    ollama["status"], ollama["reply"] = 404, {"error": "model not found"}
    with pytest.raises(AIUnavailable, match="'small:4b' is not installed"):
        make_plan("open a tab", ollama["config"], timeout=5)


def test_the_suggested_model_is_the_smallest_capable_one(ollama):
    from superhero.ai.planner import suggest_model

    ollama["models"].append({"name": "tiny:270m", "details": {"parameter_size": "270M"}})
    ollama["models"].append({"name": "odd", "details": {}})
    assert suggest_model(ollama["config"]) == "small:4b"
    ollama["models"][:] = [{"name": "tiny:270m", "details": {"parameter_size": "270M"}}]
    assert suggest_model(ollama["config"]) == "tiny:270m"  # better than nothing
    ollama["models"][:] = []
    assert suggest_model(ollama["config"]) == ""
