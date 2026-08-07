from __future__ import annotations

import sys
from pathlib import Path

# Make the package importable when tests are run from the service directory
# without an editable install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sysml_backend.services.opencode_client import _extract_sysml_content  # noqa: E402
from sysml_backend.services.repository_importer import (  # noqa: E402
    _name_from_url,
    _role_directory,
    _safe_name,
)
from sysml_backend.services.workspace import slugify  # noqa: E402
from sysml_backend.utils.env import _parse_env_line  # noqa: E402
from sysml_backend.utils.mapping import pick  # noqa: E402

# ---- env parsing -----------------------------------------------------------


def test_parse_env_line_strips_quotes_and_comments():
    assert _parse_env_line('FOO="bar"') == ("FOO", "bar")
    assert _parse_env_line("FOO = bar ") == ("FOO", "bar")
    assert _parse_env_line("# comment") == (None, "")
    assert _parse_env_line("noequals") == (None, "")


# ---- slug / safe name ------------------------------------------------------


def test_slug_normalizes():
    assert slugify("Example System!") == "example-system"
    assert slugify("  A__B  ") == "a__b"
    assert slugify("!!!") == ""


def test_safe_name_and_name_from_url():
    assert _safe_name("weird/name*.git") == "weird-name-.git"
    assert (
        _name_from_url("https://github.com/example/identity-service.git")
        == "identity-service"
    )
    assert _name_from_url("git@github.com:org/platform-apps.git") == "platform-apps"


def test_role_directory_mapping():
    assert _role_directory("argo") == "argo"
    assert _role_directory("docs") == "docs"
    # source/service/unknown sit directly under repos/ (no doubled subdir).
    assert _role_directory("source") == ""
    assert _role_directory("anything-else") == ""


# ---- mapping.pick ----------------------------------------------------------


def test_pick_prefers_first_present_non_none():
    assert pick({"remoteUrl": "x"}, "remote_url", "remoteUrl") == "x"
    assert pick({"remote_url": "a", "remoteUrl": "b"}, "remote_url", "remoteUrl") == "a"
    assert (
        pick({"remote_url": None, "remoteUrl": "b"}, "remote_url", "remoteUrl") == "b"
    )
    assert pick({}, "a", "b", default=0) == 0


# ---- OpenCode SysML extraction --------------------------------------------


def _assistant(text: str) -> list[dict]:
    return [{"type": "assistant", "parts": [{"type": "text", "text": text}]}]


def test_extract_sysml_fenced():
    response = _assistant("Here you go:\n```sysml\npackage P { }\n```")
    assert _extract_sysml_content(response) == "package P { }"


def test_extract_sysml_bare_with_preamble():
    response = _assistant(
        "Sure, here is the model:\n\npackage P {\n  part def A { }\n}"
    )
    extracted = _extract_sysml_content(response)
    assert extracted is not None and extracted.startswith("package P")
    assert extracted.rstrip().endswith("}")


def test_extract_sysml_none_when_no_model():
    assert _extract_sysml_content(_assistant("I could not find any overlays.")) is None
