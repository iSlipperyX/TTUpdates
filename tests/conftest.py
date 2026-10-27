"""Shared test set-up."""

import pytest


@pytest.fixture(autouse=True)
def delete_windows():
    """Delete the windows a test made once it ends, while the QApplication is still alive.

    A window made without a parent (a page or dialog under test) can otherwise outlive the test
    in a reference cycle (a lambda slot holding `self`). PySide then frees it while shutting the
    QApplication down, after Qt has already deleted it, and the test run crashes on exit.
    """
    yield
    try:
        import shiboken6
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return
    app = QApplication.instance()
    if app is None:
        return
    for w in app.topLevelWidgets():
        if shiboken6.isValid(w) and w.parent() is None:
            shiboken6.delete(w)
