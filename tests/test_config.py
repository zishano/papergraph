import os

import pytest

from papergraph.config import load_project_env


def test_load_env_supports_quotes_and_preserves_shell_values(tmp_path, monkeypatch):
    source = tmp_path / ".env"
    source.write_text("A_TEST_KEY='from file'\nexport B_TEST_KEY=plain\n", encoding="utf-8")
    monkeypatch.setenv("A_TEST_KEY", "from shell")
    monkeypatch.delenv("B_TEST_KEY", raising=False)
    assert load_project_env(source) == source
    assert os.environ["A_TEST_KEY"] == "from shell"
    assert os.environ["B_TEST_KEY"] == "plain"


def test_invalid_env_line_is_explicit(tmp_path):
    source = tmp_path / ".env"
    source.write_text("not valid\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 1"):
        load_project_env(source)
