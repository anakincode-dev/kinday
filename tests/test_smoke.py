"""Smoke test that keeps the test suite non-empty until real tests arrive."""

import femevmen


def test_package_exposes_version() -> None:
    assert femevmen.__version__
