from app import version


def test_version_defaults_are_set() -> None:
    assert version.VERSION
    assert version.COMMIT
