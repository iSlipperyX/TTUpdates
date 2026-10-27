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
