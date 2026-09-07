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
