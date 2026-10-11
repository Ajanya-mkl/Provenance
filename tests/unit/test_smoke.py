import provenance


def test_package_version() -> None:
    assert provenance.__version__ == "0.1.0"
