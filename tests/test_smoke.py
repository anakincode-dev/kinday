"""Smoke test that keeps the test suite non-empty until real tests arrive."""

import kinday


def test_package_exposes_version() -> None:
    assert kinday.__version__
