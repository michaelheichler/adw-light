import unittest

from t17_home import DENY, LIBRARY_NEEDED, LIBRARY_READY, VAGUE_TODO, WELL_FORMED_TODO, source, verdict


ALLOWED = (0, None, ())


def python_stub(marker, raised="raise NotImplementedError"):
    return source("def export(rows):", "    # " + marker, "    " + raised)


class PythonStubCarriesTodo(unittest.TestCase):
    def test_stub_raising_not_implemented_with_a_well_formed_todo_is_allowed(self):
        self.assertEqual(verdict(self, "src/mod.py", python_stub(WELL_FORMED_TODO)), ALLOWED)

    def test_method_stub_raising_not_implemented_with_an_argument_is_allowed(self):
        content = source(
            "class Report:",
            "    def export(self, rows):",
            "        # " + WELL_FORMED_TODO,
            '        raise NotImplementedError("later")',
        )
        self.assertEqual(verdict(self, "src/mod.py", content), ALLOWED)

    def test_well_formed_todo_on_the_line_above_a_stub_is_allowed(self):
        content = source("# " + WELL_FORMED_TODO, "def export(rows):", "    raise NotImplementedError")
        self.assertEqual(verdict(self, "src/mod.py", content), ALLOWED)

    def test_stub_with_a_vague_todo_is_denied_as_an_added_comment_on_its_line(self):
        self.assertEqual(verdict(self, "src/mod.py", python_stub(VAGUE_TODO)), (0, DENY, (("added_comment", 2),)))

    def test_well_formed_todo_in_a_function_that_returns_is_denied_as_an_added_comment(self):
        content = python_stub(WELL_FORMED_TODO, raised="return rows")
        self.assertEqual(verdict(self, "src/mod.py", content), (0, DENY, (("added_comment", 2),)))

    def test_well_formed_todo_in_a_function_that_raises_another_error_is_denied(self):
        content = python_stub(WELL_FORMED_TODO, raised="raise ValueError")
        self.assertEqual(verdict(self, "src/mod.py", content), (0, DENY, (("added_comment", 2),)))

    def test_well_formed_todo_after_a_stub_ends_is_denied_on_its_own_line(self):
        content = source("def export(rows):", "    raise NotImplementedError", "", "", "# " + WELL_FORMED_TODO, "VALUE = 1")
        self.assertEqual(verdict(self, "src/mod.py", content), (0, DENY, (("added_comment", 5),)))


class BraceStubCarriesTodo(unittest.TestCase):
    def test_javascript_stub_throwing_not_implemented_with_a_well_formed_todo_is_allowed(self):
        content = source("function exportRows() {", "  // " + WELL_FORMED_TODO, '  throw new Error("not implemented");', "}")
        self.assertEqual(verdict(self, "src/mod.js", content), ALLOWED)


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class LayerStubCarriesTodo(unittest.TestCase):
    def test_java_stub_throwing_unsupported_operation_with_a_well_formed_todo_is_allowed(self):
        content = source(
            "class Report {",
            "    void export() {",
            "        // " + WELL_FORMED_TODO,
            '        throw new UnsupportedOperationException("not implemented");',
            "    }",
            "}",
        )
        self.assertEqual(verdict(self, "src/Report.java", content, exact=True), ALLOWED)


if __name__ == "__main__":
    unittest.main()
