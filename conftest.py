import pytest
from django.test import SimpleTestCase, TransactionTestCase


def pytest_collection_modifyitems(items):
    """Add suite markers without replacing any explicit test markers."""
    for item in items:
        test_class = getattr(item, "cls", None)
        if test_class is not None and issubclass(test_class, SimpleTestCase):
            marker = "integration" if issubclass(test_class, TransactionTestCase) else "unit"
        else:
            marker = "integration"
        item.add_marker(getattr(pytest.mark, marker))