from importlib import metadata, resources

import pytest

from mnfst import VERSION


def test_runtime_version_matches_distribution_metadata():
    try:
        distribution_version = metadata.version("mnfst")
    except metadata.PackageNotFoundError:
        pytest.skip("distribution metadata requires an installed package")
    assert VERSION == distribution_version


def test_typing_marker_is_packaged():
    assert resources.files("mnfst").joinpath("py.typed").is_file()


def test_bootstrap_is_packaged():
    assert resources.files("mnfst").joinpath("_bootstrap", "sitecustomize.py").is_file()


def test_console_script_is_declared():
    try:
        distribution = metadata.distribution("mnfst")
    except metadata.PackageNotFoundError:
        pytest.skip("distribution metadata requires an installed package")
    scripts = {ep.name: ep.value for ep in distribution.entry_points
               if ep.group == "console_scripts"}
    assert scripts.get("mnfst") == "mnfst.__main__:main"
