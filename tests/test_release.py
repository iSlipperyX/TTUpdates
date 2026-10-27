"""release.py's safety checks: it must build and tag exactly the code that is on GitHub."""

import subprocess

import pytest

import release


def run(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def clone(tmp_path, monkeypatch):
    (tmp_path / "tokmanager").mkdir()
    (tmp_path / "tokmanager" / "config.py").write_text('VERSION = "1.0.0"\n', encoding="utf-8")
    (tmp_path / "README.md").write_text("hi\n", encoding="utf-8")
    run(tmp_path, "init", "-q")
    run(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    run(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    monkeypatch.setattr(release, "ROOT", tmp_path)
    return tmp_path


def test_clean_clone_reports_its_commit(clone):
    head, changed = release.local_commit()
    assert head and len(head) == 40 and changed == []


def test_version_bump_alone_does_not_count_as_a_change(clone):
    (clone / "tokmanager" / "config.py").write_text('VERSION = "1.1.0"\n', encoding="utf-8")
    assert release.local_commit()[1] == []


def test_other_uncommitted_changes_are_reported(clone):
    (clone / "README.md").write_text("changed\n", encoding="utf-8")
    (clone / "untracked.txt").write_text("not ours\n", encoding="utf-8")
    assert release.local_commit()[1] == ["README.md"]


def test_not_a_clone(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "ROOT", tmp_path)
    monkeypatch.setattr(release, "git", lambda *a: None)
    assert release.local_commit() == (None, [])


def test_notes_file_for_the_version_is_found(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "NOTES_DIR", tmp_path)
    (tmp_path / "v1.1.0.md").write_text("## New\n", encoding="utf-8")
    assert release.default_notes("1.1.0") == tmp_path / "v1.1.0.md"
    assert release.default_notes("1.2.0") is None


def test_this_versions_notes_are_in_the_repo():
    # Every release after 1.0.0 ships with hand-written notes, shown in the app's "What's new".
    version = release.current_version()
    if version != "1.0.0":
        assert release.default_notes(version), f"release-notes/v{version}.md is missing"


class FakeResponse:
    def __init__(self, status, data=None):
        self.status_code, self._data = status, data or {}

    def json(self):
        return self._data


def test_token_that_cannot_create_releases_fails_before_building(monkeypatch):
    gh = release.GitHub("me/repo", "token")
    monkeypatch.setattr(gh, "call", lambda method, path, ok=(200, 201), **kw: FakeResponse(403))
    with pytest.raises(SystemExit, match="Contents: Read and write"):
        gh.create_draft("v1.2.0", "abc", "TikTok Manager 1.2.0", "notes")


def test_failed_build_deletes_the_draft(clone, monkeypatch):
    calls = []

    class FakeGitHub:
        def __init__(self, repo, token):
            pass

        def check_access(self):
            return "main"

        def latest_version(self):
            return "1.0.0"

        def tag_exists(self, tag):
            return False

        def branch_head(self, branch):
            return release.local_commit()[0]

        def ensure_not_empty(self, branch):
            pass

        def create_draft(self, *a):
            calls.append("draft")
            return {"id": 1}

        def delete_draft(self, rel):
            calls.append("deleted")

        def publish(self, rel, files):
            calls.append("published")

    def failing_run(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(release, "GitHub", FakeGitHub)
    config = clone / "tokmanager" / "config.py"
    config.write_text('VERSION = "1.0.0"\nupdate_repo: str = "me/repo"\n', encoding="utf-8")
    monkeypatch.setattr(release, "CONFIG", config)
    monkeypatch.setattr(release.subprocess, "run", failing_run)
    monkeypatch.setattr(release, "local_commit", lambda: ("a" * 40, []))
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setattr("sys.argv", ["release.py", "1.1.0", "--notes", "x", "--repo", "me/repo"])
    with pytest.raises(subprocess.CalledProcessError):
        release.main()
    assert calls == ["draft", "deleted"]
