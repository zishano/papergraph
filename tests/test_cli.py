import argparse
import subprocess
import sys

import pytest

from papergraph.cli import clean_cli_args, run


def test_cli_help_and_invalid_bounds():
    help_result = subprocess.run([sys.executable, "-m", "papergraph", "--help"], capture_output=True, text=True)
    assert help_result.returncode == 0
    assert "resolve" in help_result.stdout and "search" in help_result.stdout
    result = subprocess.run([sys.executable, "-m", "papergraph", "search", "--seed", "W1", "--depth", "-1"],
                            capture_output=True, text=True)
    assert result.returncode == 1
    assert not result.stdout and "depth" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_removes_zero_width_copy_paste_characters():
    hidden = "\u200b--source"
    assert clean_cli_args([hidden, "scholar", "\u2060--keywords"])[0] == "--source"
    assert clean_cli_args([hidden, "scholar", "\u2060--keywords"])[2] == "--keywords"


async def test_cli_invalid_config_precedes_network(monkeypatch):
    def no_http(*args, **kwargs):
        pytest.fail("Invalid config must not create an HTTP client")
    monkeypatch.setattr("papergraph.cli.httpx.AsyncClient", no_http)
    with pytest.raises(ValueError):
        await run(argparse.Namespace(command="search", seed=["W1"], depth=4))
