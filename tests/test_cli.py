import os
import subprocess
import sys

import pytest

from mnfst.__main__ import (
    bootstrap_directory,
    environment_with_bootstrap,
    main,
)


def test_bootstrap_directory_ships_sitecustomize():
    bootstrap = bootstrap_directory()
    assert os.path.isfile(os.path.join(bootstrap, "sitecustomize.py"))


def test_environment_prepends_bootstrap_and_keeps_existing():
    env = environment_with_bootstrap({"PYTHONPATH": "/app/libs"})
    assert env["PYTHONPATH"] == bootstrap_directory() + os.pathsep + "/app/libs"


def test_environment_without_existing_pythonpath():
    assert environment_with_bootstrap({})["PYTHONPATH"] == bootstrap_directory()


def test_help_prints_usage(capsys):
    assert main(["--help"]) is None
    assert "mnfst run" in capsys.readouterr().out


def test_no_arguments_prints_usage(capsys):
    assert main([]) is None
    assert "mnfst run" in capsys.readouterr().out


def test_version_prints_version(capsys):
    from mnfst import VERSION

    assert main(["--version"]) is None
    assert capsys.readouterr().out.strip() == VERSION


def test_unknown_command_exits_2(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["fly"])
    assert exc.value.code == 2
    assert "unknown command" in capsys.readouterr().err


def test_run_without_command_exits_2(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["run"])
    assert exc.value.code == 2


def test_missing_command_exits_127(monkeypatch, capsys):
    def explode(command, argv, env):
        raise FileNotFoundError(command)

    monkeypatch.setattr("mnfst.__main__.os.execvpe", explode)
    monkeypatch.setenv("MNFST_KEY", "mnfx_cli")
    with pytest.raises(SystemExit) as exc:
        main(["run", "definitely-not-a-real-command"])
    assert exc.value.code == 127
    assert "command not found" in capsys.readouterr().err


def test_exec_env_carries_bootstrap(monkeypatch):
    captured = {}

    def capture(command, argv, env):
        captured["command"] = command
        captured["argv"] = argv
        captured["env"] = env

    monkeypatch.setattr("mnfst.__main__.os.execvpe", capture)
    monkeypatch.setenv("MNFST_KEY", "mnfx_cli")
    main(["run", "--", "uvicorn", "main:app"])
    assert captured["argv"] == ["uvicorn", "main:app"]
    assert captured["env"]["PYTHONPATH"].split(os.pathsep)[0] == bootstrap_directory()


def test_run_without_key_warns_once(monkeypatch, capsys):
    monkeypatch.delenv("MNFST_KEY", raising=False)
    monkeypatch.setattr("mnfst.__main__.os.execvpe", lambda *a: None)
    main(["run", "true"])
    assert "MNFST_KEY" in capsys.readouterr().err


def _run(code, *, key):
    env = dict(os.environ)
    if key is None:
        env.pop("MNFST_KEY", None)
    else:
        env["MNFST_KEY"] = key
    env["MNFST_URL"] = "http://first.test"
    return subprocess.run(
        [sys.executable, "-m", "mnfst", "run", sys.executable, "-c", code],
        capture_output=True, text=True, env=env, timeout=30,
    )


def test_run_instruments_the_child_process():
    code = ("from mnfst.outbound import installed_config\n"
            "cfg = installed_config()\n"
            "print('installed' if cfg and cfg.api_key == 'mnfx_cli' else 'inert')")
    proc = _run(code, key="mnfx_cli")
    assert proc.returncode == 0, proc.stderr
    assert "installed" in proc.stdout


def test_run_without_key_is_uninstrumented_and_quiet():
    code = ("from mnfst.outbound import installed_config\n"
            "print('inert' if installed_config() is None else 'installed')")
    proc = _run(code, key=None)
    assert proc.returncode == 0, proc.stderr
    assert "inert" in proc.stdout
    # `mnfst run` warns once up front; the child must not warn again.
    assert proc.stderr.count("MNFST_KEY") == 1


def test_run_propagates_the_command_exit_code():
    proc = subprocess.run(
        [sys.executable, "-m", "mnfst", "run", sys.executable, "-c", "raise SystemExit(3)"],
        capture_output=True, text=True, env=dict(os.environ), timeout=30,
    )
    assert proc.returncode == 3
