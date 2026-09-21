from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_run_ps1_parses_in_powershell():
    script = Path(__file__).resolve().parents[1] / "run.ps1"
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        return

    command = (
        "$errors = $null; "
        "[void][System.Management.Automation.Language.Parser]::ParseFile("
        f"'{script}', [ref]$null, [ref]$errors); "
        "if ($errors) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }"
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
