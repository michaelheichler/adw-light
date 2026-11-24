import unittest

from t11_language_home import (
    DENY, GO_RAW, HASH_LINE, LIBRARY_NEEDED, LIBRARY_READY, LINE, NESTED_TEMPLATE, HookHome, comment_findings,
)


JS_TEMPLATE = ("help.js", NESTED_TEMPLATE, 2)
GO_STRING = ("main.go", GO_RAW, 4)
RUST_RAW = ("lib.rs", 'pub const HELP: &str = r#"\n' + LINE + '\n"#;\n', 2)
CPP_RAW = ("help.cpp", 'const char* help = R"(\n' + LINE + '\n)";\n', 2)
RUBY_PERCENT = ("help.rb", "HELP = %q{\n" + HASH_LINE + "\n}\n", 2)
RUBY_HEREDOC = ("doc.rb", "HELP = <<~TXT\n" + HASH_LINE + "\nTXT\n", 2)
SHELL_HEREDOC = ("help.sh", "cat <<EOF\n" + HASH_LINE + "\nEOF\n", 2)
SWIFT_EXTENDED = ("Help.swift", 'let help = #"""\n' + LINE + '\n"""#\n', 2)
KOTLIN_TEMPLATE = ("Help.kt", 'val help = "${"\n' + LINE + '\n"}"\n', 2)
KOTLIN_NAME = ("Names.kt", "fun `a # Now we loop over items`() {}\n", 1)
SHELL_EXPANSION = ("expand.sh", "echo ${name:- # Now we loop over items}\n", 1)
SVELTE_SCRIPT = ("Help.svelte", '<script lang="ts">\n' + NESTED_TEMPLATE + "</script>\n<div></div>\n", 3)
HTML_SCRIPT = ("help.html", "<html><body>\n<script>\n" + NESTED_TEMPLATE + "</script>\n</body></html>\n", 4)
VUE_SCRIPT = ("Help.vue", '<template><div></div></template>\n<script lang="ts">\n' + NESTED_TEMPLATE + "</script>\n", 4)
REAL_SHELL_COMMENT = ("real.sh", 'echo "${path# }" # now we loop\n', 1)
HALF_WRITTEN_GO = ("half.go", GO_RAW + "func main( {\n", 4)
PHP_ATTRIBUTE = ("src/Checks.php", "<?php\n#[CoversClass(Calculator::class)] final class CalculatorTest extends TestCase {}\n")
TEST_GATE = (("test_write_gate", 1),)
FSTRING_LINES = 'name = "x"\nlabel = f"{f"{name}"} ' + HASH_LINE + '"\n'
PYTHON_FSTRING = ("label.py", FSTRING_LINES)
PYTHON_FSTRING_AND_COMMENT = ("label.py", FSTRING_LINES + HASH_LINE + "\nvalue = 1\n", 3)


def python_comment_findings(line):
    return (("added_comment", line),) + comment_findings(line)


@unittest.skipUnless(LIBRARY_READY, LIBRARY_NEEDED)
class ExactLayerReadsLiteralsAsText(unittest.TestCase):
    def exact_hook(self):
        return HookHome(self).link_library().link_grammars()

    def allowed(self, fixture):
        relative, content, _ = fixture
        hook = self.exact_hook()
        return hook.verdict(relative, content), hook.precision_rows()

    def test_nested_javascript_template_text_is_not_a_comment(self):
        self.assertEqual(self.allowed(JS_TEMPLATE), ((None, ()), []))

    def test_go_raw_string_text_is_not_a_comment(self):
        self.assertEqual(self.allowed(GO_STRING), ((None, ()), []))

    def test_rust_raw_string_text_is_not_a_comment(self):
        self.assertEqual(self.allowed(RUST_RAW), ((None, ()), []))

    def test_cpp_raw_string_text_is_not_a_comment(self):
        self.assertEqual(self.allowed(CPP_RAW), ((None, ()), []))

    def test_ruby_percent_literal_text_is_not_a_comment(self):
        self.assertEqual(self.allowed(RUBY_PERCENT), ((None, ()), []))

    def test_ruby_heredoc_text_is_not_a_comment(self):
        self.assertEqual(self.allowed(RUBY_HEREDOC), ((None, ()), []))

    def test_shell_heredoc_text_is_not_a_comment(self):
        self.assertEqual(self.allowed(SHELL_HEREDOC), ((None, ()), []))

    def test_swift_extended_delimiter_text_is_not_a_comment(self):
        self.assertEqual(self.allowed(SWIFT_EXTENDED), ((None, ()), []))

    def test_kotlin_nested_template_text_is_not_a_comment(self):
        self.assertEqual(self.allowed(KOTLIN_TEMPLATE), ((None, ()), []))

    def test_kotlin_backtick_function_name_is_not_a_comment(self):
        self.assertEqual(self.allowed(KOTLIN_NAME), ((None, ()), []))

    def test_shell_parameter_expansion_word_is_not_a_comment(self):
        self.assertEqual(self.allowed(SHELL_EXPANSION), ((None, ()), []))

    def test_vue_script_body_gets_its_own_parse(self):
        self.assertEqual(self.allowed(VUE_SCRIPT), ((None, ()), []))

    def test_svelte_script_body_gets_its_own_parse(self):
        self.assertEqual(self.allowed(SVELTE_SCRIPT), ((None, ()), []))

    def test_html_script_body_gets_its_own_parse(self):
        self.assertEqual(self.allowed(HTML_SCRIPT), ((None, ()), []))

    def test_a_real_shell_comment_after_an_expansion_still_denies(self):
        relative, content, line = REAL_SHELL_COMMENT
        self.assertEqual(self.exact_hook().verdict(relative, content), (DENY, comment_findings(line)))

    def test_half_written_file_keeps_the_lexer_findings(self):
        relative, content, line = HALF_WRITTEN_GO
        self.assertEqual(self.exact_hook().verdict(relative, content), (DENY, comment_findings(line)))

    def test_php_attribute_marks_the_file_as_a_test(self):
        self.assertEqual(self.exact_hook().verdict(*PHP_ATTRIBUTE), (DENY, TEST_GATE))


class FallbackLexerStillScansLiterals(unittest.TestCase):
    def denied(self, fixture):
        relative, content, line = fixture
        return HookHome(self).verdict(relative, content), (DENY, comment_findings(line))

    def test_nested_javascript_template_falls_back_to_a_deny(self):
        self.assertEqual(*self.denied(JS_TEMPLATE))

    def test_go_raw_string_falls_back_to_a_deny(self):
        self.assertEqual(*self.denied(GO_STRING))

    def test_rust_raw_string_falls_back_to_a_deny(self):
        self.assertEqual(*self.denied(RUST_RAW))

    def test_cpp_raw_string_falls_back_to_a_deny(self):
        self.assertEqual(*self.denied(CPP_RAW))

    def test_ruby_percent_literal_falls_back_to_a_deny(self):
        self.assertEqual(*self.denied(RUBY_PERCENT))

    def test_ruby_heredoc_falls_back_to_a_deny(self):
        self.assertEqual(*self.denied(RUBY_HEREDOC))

    def test_shell_heredoc_falls_back_to_a_deny(self):
        self.assertEqual(*self.denied(SHELL_HEREDOC))

    def test_swift_extended_delimiter_falls_back_to_a_deny(self):
        self.assertEqual(*self.denied(SWIFT_EXTENDED))

    def test_kotlin_nested_template_falls_back_to_a_deny(self):
        self.assertEqual(*self.denied(KOTLIN_TEMPLATE))

    def test_kotlin_backtick_name_falls_back_to_a_deny(self):
        self.assertEqual(*self.denied(KOTLIN_NAME))

    def test_shell_parameter_expansion_falls_back_to_a_deny(self):
        self.assertEqual(*self.denied(SHELL_EXPANSION))

    def test_vue_script_body_falls_back_to_a_deny(self):
        self.assertEqual(*self.denied(VUE_SCRIPT))

    def test_svelte_script_body_falls_back_to_a_deny(self):
        self.assertEqual(*self.denied(SVELTE_SCRIPT))

    def test_html_script_body_falls_back_to_a_deny(self):
        self.assertEqual(*self.denied(HTML_SCRIPT))

    def test_half_written_file_falls_back_to_a_deny(self):
        self.assertEqual(*self.denied(HALF_WRITTEN_GO))

    def test_php_attribute_is_not_seen_as_a_test(self):
        self.assertEqual(HookHome(self).verdict(*PHP_ATTRIBUTE), (None, ()))


class PythonKeepsItsOwnParser(unittest.TestCase):
    def test_hash_inside_a_nested_fstring_is_not_a_comment(self):
        self.assertEqual(HookHome(self).verdict(*PYTHON_FSTRING), (None, ()))

    def test_real_comment_beside_a_nested_fstring_denies_on_its_line(self):
        relative, content, line = PYTHON_FSTRING_AND_COMMENT
        self.assertEqual(HookHome(self).verdict(relative, content), (DENY, python_comment_findings(line)))


if __name__ == "__main__":
    unittest.main()
