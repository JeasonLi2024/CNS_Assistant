"""Pytest wrapper for the smoke test script."""

from scripts.smoke_test import main


def test_smoke_tools() -> None:
    main()

