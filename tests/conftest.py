import pytest

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: marks tests requiring "
        "real API calls (deselect with -m "
        "'not integration')"
    )
    config.addinivalue_line(
        "markers",
        "regression: marks weekly regression tests"
    )
