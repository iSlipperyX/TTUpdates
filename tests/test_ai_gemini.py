"""Claude or Gemini (Settings → AI): Gemini answers through a stand-in for Google's API, Claude through a
stand-in for the anthropic package, and a failing AI hands over to the other one. No real keys."""

import io
import json
import os
import sys
import types
import urllib.error
import urllib.request

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tokmanager import ai, gemini, jobs  # noqa: E402
from tokmanager.config import Settings  # noqa: E402
from tokmanager.store import Store  # noqa: E402
from test_ai_hashtags import account  # noqa: E402
from test_ready import clipmonster_folder  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))


class Google:
    """Stands in for urllib's urlopen: answers each streamGenerateContent call with `reply` as SSE."""

    def __init__(self, reply: dict, fail: urllib.error.HTTPError | None = None):
        self.reply, self.fail, self.requests = reply, fail, []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        if self.fail:
            raise self.fail
        if req.get_method() == "GET":
            return io.BytesIO(json.dumps({"displayName": "Gemini 3.8 Flash"}).encode())
        text = json.dumps(self.reply)
        chunks = [{"candidates": [{"index": 0, "content": {"parts": [{"text": text[:5]}]}}]},
                  {"candidates": [{"index": 0, "content": {"parts": [{"text": text[5:]}]}, "finishReason": "STOP"}],
                   "modelVersion": "gemini-3.8-flash-002"}]
        return io.BytesIO(b"".join(b"data: " + json.dumps(c).encode() + b"\r\n\r\n" for c in chunks))

    def body(self, i=-1) -> dict:
        return json.loads(self.requests[i].data)


@pytest.fixture
def google(monkeypatch):
    g = Google({"hashtags": ["Jynxzi", "fyp", "rainbowsix", "clutch", "gaming"]})
    monkeypatch.setattr(urllib.request, "urlopen", g)
    monkeypatch.setattr(gemini, "RETRY_WAITS", ())
    return g


@pytest.fixture
def claude(monkeypatch):
    """The anthropic package, failing like a busy server: records each request."""
    calls = []

    class Busy(Exception):
        status_code = 529

    class Messages:
        def create(self, **kw):
            calls.append(kw)
            raise Busy("overloaded")

    class Anthropic:
        def __init__(self, api_key, max_retries=0, **kw):
            self.messages = Messages()

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=Anthropic, APIError=Busy))
    return calls


def test_hashtags_with_gemini(tmp_path, google):
    store = Store(tmp_path / "lib.json")
    item = store.add_paths([clipmonster_folder(tmp_path)])[0]
    s = Settings(ai_provider="gemini", gemini_api_key=" AIza-test ", ai_hashtag_count=3)
    got = jobs.ai_hashtags(store, item.id, s, account())
    assert got.hashtags == "#Jynxzi #rainbowsix #clutch"  # spam and the account's own tags dropped
    req = google.requests[0]
    assert req.full_url.endswith("/models/gemini-3.8-flash:streamGenerateContent?alt=sse")
    assert req.get_header("X-goog-api-key") == "AIza-test"
    body = google.body()
    assert body["systemInstruction"]["parts"][0]["text"] == ai.HASHTAG_SYSTEM
    assert "The account posts: Rainbow Six streamer clips" in body["contents"][0]["parts"][0]["text"]
    assert body["generationConfig"]["responseSchema"]["properties"]["hashtags"]["type"] == "ARRAY"


def test_claude_failing_hands_over_to_gemini(tmp_path, google, claude):
    store = Store(tmp_path / "lib.json")
    item = store.add_paths([clipmonster_folder(tmp_path)])[0]
    s = Settings(ai_api_key="sk-ant-x", gemini_api_key="AIza-test", ai_hashtag_count=2)
    got = jobs.ai_hashtags(store, item.id, s, account())
    assert len(claude) == 1 and len(google.requests) == 1 and got.hashtags == "#Jynxzi #rainbowsix"
    with pytest.raises(ai.AIError, match="servers are busy"):  # falling back turned off
        ai.suggest_hashtags(Settings(ai_api_key="sk-ant-x", gemini_api_key="AIza-test", ai_fallback=False), item)


def test_both_failing_says_why_for_each(tmp_path, monkeypatch, claude):
    err = urllib.error.HTTPError("u", 429, "x", {}, io.BytesIO(json.dumps({"error": {
        "code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded for the free tier, per day"}}).encode()))
    monkeypatch.setattr(urllib.request, "urlopen", Google({}, fail=err))
    store = Store(tmp_path / "lib.json")
    item = store.add_paths([clipmonster_folder(tmp_path)])[0]
    with pytest.raises(ai.AIError, match=r"^Claude: Anthropic's servers are busy .* Gemini: .*quota is used up"):
        ai.suggest_hashtags(Settings(ai_api_key="sk-ant-x", gemini_api_key="AIza-test"), item)


def test_rate_with_gemini_sends_the_frames(tmp_path, monkeypatch):
    g = Google({"appeal": 140, "hook": 70, "verdict": "Strong open", "caption": "He did what?",
                "hashtags": ["#gaming", " "], "ideas": ["Cut the intro"]})
    monkeypatch.setattr(urllib.request, "urlopen", g)
    monkeypatch.setattr(ai, "sample_frames", lambda path, duration, n: [b"\xff\xd8one", b"\xff\xd8two"])
    store = Store(tmp_path / "lib.json")
    item = store.add_paths([clipmonster_folder(tmp_path)])[0]
    logs = []
    data = ai.rate(item, Settings(ai_provider="gemini", gemini_api_key="AIza-test", gemini_model="gemini-4-argon"),
                   log=logs.append)
    assert data["appeal"] == 100 and data["hook"] == 70 and data["hashtags"] == ["gaming"]
    assert any(m.startswith("Asking gemini-4-argon to rate") for m in logs)
    parts = g.body()["contents"][0]["parts"]
    assert parts[0] == {"text": "Opening frame:"} and parts[1]["inlineData"]["mimeType"] == "image/jpeg"
    assert g.body()["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "medium"}


def test_bios_with_gemini(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", Google({"bios": ["Daily  R6 clutches 🎯", "x" * 200]}))
    bios = ai.suggest_bios(Settings(ai_provider="gemini", gemini_api_key="AIza-test"), "Rainbow Six clips")
    assert bios[0] == "Daily R6 clutches 🎯" and len(bios[1]) <= 80


def test_keys_order_and_clipmonsters_gemini_key(tmp_path):
    s = Settings()
    assert not ai.has_key(s) and ai.order(s) == []
    assert ai.order(Settings(ai_provider="gemini", ai_api_key="sk-ant-x")) == ["claude"]  # stands in
    assert ai.order(Settings(ai_provider="gemini", ai_api_key="sk-ant-x", ai_fallback=False)) == []
    cm = tmp_path / "appdata" / "ClipMonster"
    cm.mkdir(parents=True)
    (cm / "settings.json").write_text(json.dumps({"gemini_api_key": "AIza-cm", "ai_provider": "gemini"}),
                                      encoding="utf-8")
    assert ai.api_key(Settings(ai_provider="gemini"), "gemini") == "AIza-cm"
    assert ai.order(Settings(ai_provider="gemini")) == ["gemini"] and ai.has_key(Settings())
    assert ai.api_key(Settings(gemini_api_key="mine"), "gemini") == "mine"


def test_check_key_looks_the_model_up(google):
    assert ai.check_key(Settings(ai_provider="gemini", gemini_api_key="AIza-test")) == \
        "Gemini key works. Gemini 3.8 Flash is ready."
    assert google.requests[0].get_method() == "GET"
    with pytest.raises(ai.AIError, match="No Claude API key"):
        ai.check_key(Settings())


def test_settings_keep_the_choice_and_old_settings_use_claude(tmp_path):
    s = Settings(ai_provider="gemini", gemini_api_key="AIza-secret", gemini_model="gemini-4-argon")
    s.save()
    back = Settings.load()
    assert (back.ai_provider, back.gemini_api_key, back.gemini_model) == ("gemini", "AIza-secret", "gemini-4-argon")
    Settings.path().write_text(json.dumps({"ai_api_key": "sk-ant-old"}), encoding="utf-8")
    old = Settings.load()
    assert old.ai_provider == "claude" and old.ai_fallback and ai.order(old) == ["claude"]


def test_settings_ai_tab(monkeypatch):
    from PySide6.QtWidgets import QApplication

    from tokmanager.gui.settings_dialog import SettingsDialog

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(urllib.request, "urlopen", Google({}))
    s = Settings()
    dlg = SettingsDialog(s, tab="AI")
    dlg.ai_provider.setCurrentIndex(dlg.ai_provider.findData("gemini"))
    dlg.gemini_key.setText("  AIza-typed ")
    dlg.gemini_model.setCurrentIndex(dlg.gemini_model.findData("gemini-3.5-flash-lite"))
    dlg.ai_fallback.setChecked(False)
    dlg._test_keys()  # checks the key as typed, before saving
    assert dlg.key_result.text() == "✓ Gemini key works. Gemini 3.8 Flash is ready."
    dlg.apply()
    assert (s.ai_provider, s.gemini_api_key, s.gemini_model, s.ai_fallback) == \
        ("gemini", "AIza-typed", "gemini-3.5-flash-lite", False)
