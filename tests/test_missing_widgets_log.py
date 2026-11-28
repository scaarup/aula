"""The missing-widget error is logged once, not on every update."""

import logging

from custom_components.aula.client import Client


def test_missing_widget_error_logged_once(caplog):
    client = Client.__new__(Client)  # no network or login needed for this method
    with caplog.at_level(logging.DEBUG, logger="custom_components.aula.client"):
        for _ in range(3):
            client._log_missing_widgets("ugeplan", "no widgets for %s", "ugeplan")
        client._log_missing_widgets("mu_opgaver", "no widgets for %s", "mu")
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    debugs = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert [r.getMessage() for r in errors] == ["no widgets for ugeplan", "no widgets for mu"]
    assert len(debugs) == 2
