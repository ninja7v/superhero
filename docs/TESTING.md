# Testing and CI

```bash
uv run pytest                          # everything, about ten seconds, no camera needed
uv run pytest tests/test_engine.py     # one area
```

## What a change runs

`.github/scripts/ci_plan.py` reads the diff of the whole pull request (every commit, not just
the last) and decides what to run. Each run's summary page shows its decision and why.

Lint runs whenever a Python file changed, and in every full run.

| Change | Tests | Operating systems | Bundle |
|---|---|---|---|
| Only `*.md` files or files under `docs/` | – | – | – |
| A module under `src/superhero/` | the test files that import it, directly or indirectly | Linux, Windows, macOS | – |
| Pure logic: engine, commands, AI planner, face gestures, audio endpointing | as above | Linux | – |
| An import added or removed | as above | as above | yes |
| `packaging/`, `LICENSE`, `NOTICE` | `test_notices.py` | all three | yes |
| A UI asset or other data file in `src/superhero/` | the tests of its package | all three | yes |
| Test fixtures | all | all three | – |
| `pyproject.toml`, `uv.lock`, `tests/conftest.py`, `.github/` | all | all three | yes |
| Anything not listed | all | all three | yes |

Imports are followed both ways. For example, a change to `platform/displays.py` runs every
test that uses a display, including through the engine and the app. A deleted module still
selects the tests that import it, so they fail on the pull request rather than later.

Nothing runs on a push to `main`. On the 1st and 15th of each month, and on demand (*Run
workflow*), every test runs on all three systems and the bundles are built; these runs are the
safety net under the pull-request selection. In branch protection, require the single **ci-ok** check: it passes when every job
the plan chose passed, and skipped jobs do not count against it.

The "pure logic" list is `PORTABLE` in `ci_plan.py`. Add a file to it only if its behaviour
cannot depend on the OS: no files, sockets, native libraries or real timing. Where it picks
something per platform (a shortcut, a prompt), its tests must force each platform value.

## Platform matrix

| | Linux X11 | Linux Wayland | Windows | macOS |
|---|---|---|---|---|
| Logic and Qt tests (offscreen) | CI | CI (same Linux job) | CI | CI |
| Input backend loads, knows every key | CI, under Xvfb | – | CI | skipped without Accessibility |
| Real pointer movement | – | – | CI (moves, never clicks) | – |
| Portal stream matching, keysyms, key release on failure | – | CI, pure logic | – | – |
| Portal consent, restore token, GlobalShortcuts | manual | manual | – | – |
| Accessibility, camera, microphone permissions | – | – | – | manual |
| Clicks and typing into real apps | manual | manual | manual | manual |
| Snapping logic, window descriptions | CI, fake tree | CI, fake tree | CI, fake tree | CI, fake tree |
| Window list, element under a point | manual | window list manual | manual | manual |
| Bundle builds and starts (`doctor`) | CI | (same bundle) | CI | CI |

CI runners have no camera, microphone, compositor or user, so a green run shows that the code
and the bundle are sound. It does not show that a platform works. That takes the
[bring-up checklist](PLATFORMS.md#bring-up-checklist) on real hardware, plus:

- **Wayland**: the RemoteDesktop consent dialog appears once and is not shown again after a
  restart (restore token); Ctrl+Alt+Esc is registered through GlobalShortcuts and fires while
  another app has focus.
- **macOS**: the Accessibility, Camera and Microphone prompts appear for the bundled `.app`;
  after a refusal, `doctor` reports input control as *blocked*.
- **Windows**: clicks land where you look on a scaled (125–150%) display and on a second
  monitor. Also check an elevated (administrator) window: Windows blocks injection into it.

## Safety rules for tests

`tests/conftest.py` enforces these for every test, even one that forgets its fake:

- No real input: pynput clicks, keys and pointer moves raise. Use a fake `InputBackend`
  (`FakeInput` in `test_engine.py`).
- No D-Bus session bus, so no portal dialog can open.
- No network beyond this machine. A stand-in Ollama on `127.0.0.1` is fine (see `test_ai.py`),
  and models are never downloaded.

The one exception is the `real_input` marker. It lets a test move (never click with) the real
pointer, and only when `SUPERHERO_ALLOW_REAL_INPUT=1` is set, which CI does only on its
throwaway Windows runner.
