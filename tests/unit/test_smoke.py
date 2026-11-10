import provenance


def test_package_version() -> None:
    assert provenance.__version__ == "1.0.0"
