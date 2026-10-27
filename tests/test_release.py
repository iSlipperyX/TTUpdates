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
    monkeypatch.setattr("sys.argv", ["release.py", "1.1.0", "--local", "--notes", "x", "--repo", "me/repo"])
    with pytest.raises(subprocess.CalledProcessError):
        release.main()
    assert calls == ["draft", "deleted"]


@pytest.fixture
def repo_pair(tmp_path, monkeypatch):
    """A clone of a bare 'GitHub' repo, set up like TTUpdates (config, notes)."""
    origin = tmp_path / "origin.git"
    run(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    run(tmp_path, "clone", "-q", str(origin), str(work))
    run(work, "checkout", "-q", "-b", "main")
    (work / "tokmanager").mkdir()
    config = work / "tokmanager" / "config.py"
    config.write_text('VERSION = "1.2.1"\nupdate_repo: str = "me/repo"\n', encoding="utf-8")
    (work / "release-notes").mkdir()
    (work / "release-notes" / "v1.3.0.md").write_text("## New\n", encoding="utf-8")
    for cmd in (("add", "."), ("commit", "-q", "-m", "init"), ("push", "-q", "-u", "origin", "main")):
        run(work, "-c", "user.email=t@t", "-c", "user.name=t", *cmd)
    run(work, "config", "user.email", "t@t")
    run(work, "config", "user.name", "t")
    monkeypatch.setattr(release, "ROOT", work)
    monkeypatch.setattr(release, "CONFIG", config)
    monkeypatch.setattr(release, "NOTES_DIR", work / "release-notes")
    return origin, work


def git_out(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True).stdout.strip()


def test_tag_release_bumps_tags_and_pushes(repo_pair, capsys):
    origin, work = repo_pair
    assert release.tag_release("1.3.0", "me/repo") == 0
    assert 'VERSION = "1.3.0"' in (work / "tokmanager" / "config.py").read_text(encoding="utf-8")
    assert git_out(origin, "log", "-1", "--format=%s", "main") == "Release 1.3.0"
    # The tag is on GitHub and points at the bump commit, so the build reports the right version.
    assert git_out(origin, "rev-parse", "v1.3.0^{commit}") == git_out(origin, "rev-parse", "main")
    assert "github.com/me/repo/actions" in capsys.readouterr().out


def test_tag_release_refuses_without_notes(repo_pair):
    with pytest.raises(SystemExit, match="release-notes/v1.4.0.md is missing"):
        release.tag_release("1.4.0", "me/repo")


def test_tag_release_refuses_a_clone_behind_github(repo_pair, tmp_path):
    origin, work = repo_pair
    other = tmp_path / "other"
    run(tmp_path, "clone", "-q", str(origin), str(other))
    (other / "x.txt").write_text("newer\n", encoding="utf-8")
    for cmd in (("add", "."), ("commit", "-q", "-m", "newer"), ("push", "-q")):
        run(other, "-c", "user.email=t@t", "-c", "user.name=t", *cmd)
    with pytest.raises(SystemExit, match="git pull"):
        release.tag_release("1.3.0", "me/repo")
    assert git_out(work, "tag") == ""  # nothing was tagged


def test_tag_release_refuses_a_tag_that_exists_on_github(repo_pair):
    origin, work = repo_pair
    run(work, "tag", "v1.3.0")
    run(work, "push", "-q", "origin", "v1.3.0")
    run(work, "tag", "-d", "v1.3.0")
    with pytest.raises(SystemExit, match="already exists on GitHub"):
        release.tag_release("1.3.0", "me/repo")


def test_tag_release_without_push_only_tags_here(repo_pair, capsys):
    origin, work = repo_pair
    assert release.tag_release("1.3.0", "me/repo", push=False) == 0
    assert git_out(work, "tag") == "v1.3.0" and git_out(origin, "tag") == ""
    assert "git push origin v1.3.0" in capsys.readouterr().out
