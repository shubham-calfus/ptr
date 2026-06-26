"""Utility package for the test runner.

Keep this package import-light. Runtime helpers import submodules from here
inside worker processes that may not have optional report/storage dependencies.
"""

__all__: list[str] = []
