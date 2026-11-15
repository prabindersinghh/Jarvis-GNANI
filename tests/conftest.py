import pytest


def pytest_collection_modifyitems(config, items):
    if config.getoption("-m"):
        return
    skip = pytest.mark.skip(reason="live test: run with -m live")
    for it in items:
        if "live" in it.keywords:
            it.add_marker(skip)
