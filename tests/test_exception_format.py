"""Тесты разворачивания ExceptionGroup / TaskGroup."""

import unittest

import httpx

try:
    ExceptionGroup
except NameError:  # pragma: no cover
    from exceptiongroup import ExceptionGroup

from assistant.lib import exception_format as fmt_mod


class TestExceptionFormat(unittest.TestCase):
    def test_unwrap_single_exception_group(self) -> None:
        inner = RuntimeError("bad token")
        group = ExceptionGroup("unhandled errors in a TaskGroup", [inner])
        self.assertIs(fmt_mod.unwrap_exception(group), inner)

    def test_format_exception_message_prefers_root(self) -> None:
        inner = httpx.ConnectError("connection refused")
        group = ExceptionGroup("unhandled errors in a TaskGroup", [inner])
        message = fmt_mod.format_exception_message(group)
        self.assertIn("connection refused", message)

    def test_format_exception_message_duck_typed_group(self) -> None:
        class FakeGroup(Exception):
            def __init__(self, message: str, exceptions: tuple[BaseException, ...]) -> None:
                super().__init__(message)
                self.exceptions = exceptions

        inner = RuntimeError("token expired")
        group = FakeGroup("unhandled errors in a TaskGroup (1 sub-exception)", (inner,))
        message = fmt_mod.format_exception_message(group)
        self.assertEqual(message, "token expired")
