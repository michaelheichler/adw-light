import unittest

from in_memory_hook import ALLOWED, DENY, PRINCIPLE, InMemoryHookCase, blocked


CITATION_RULE = "test_principle_citation"
MADE_UP = "A test should always be written in green ink before lunch."
LUA_TEST = "spec/order_spec.lua"
ELIXIR_TEST = "test/order_test.exs"
SCALA_TEST = "src/test/scala/OrderTest.scala"
PHP_TEST = "tests/OrderTest.php"
RUBY_TEST = "spec/order_test.rb"
DART_TEST = "test/x_test.dart"
HACK_TEST = "test/x_test.hh"
NARRATION = "increment the counter by one"


def lines(*body):
    return "".join(line + "\n" for line in body)


def cited(marker, quote):
    return (marker + " Principle: " + quote,) if quote else ()


def busted(quote):
    return lines('describe("order", function()', *cited("  --", quote), '  it("ships", function()',
                 '    assert(order.status() == "ok")', "  end)", "end)")


def exunit(quote):
    return lines("defmodule OrderTest do", "  use ExUnit.Case", *cited("  #", quote), '  test "ships" do',
                 '    assert Order.status() == "ok"', "  end", "end")


def scala_junit(quote):
    return lines("import org.junit.Test", "class OrderTest {", *cited("  //", quote), "  @Test def ships(): Unit = {",
                 '    assertEquals(order.status(), "ok")', "  }", "}")


def phpunit(quote, marker="    #"):
    return lines("<?php", "use PHPUnit\\Framework\\TestCase;", "class OrderTest extends TestCase {",
                 *cited(marker, quote), "    public function testShips(): void {",
                 "        $this->assertSame('ok', status());", "    }", "}")


def rspec(marker, quote):
    return lines('describe "order" do', *cited("  " + marker, quote), '  it "ships" do',
                 '    expect(order.status).to eq("ok")', "  end", "end")


def broken_exunit():
    return lines("defmodule OrderTest do", '  test "ships" do (((')


def dart_test(comment):
    return lines("import 'package:test/test.dart';", "void main() {", "  test('ships', () {", "    // " + comment,
                 "    expect(status(), equals('ok'));", "  });", "}")


def hack_test(comment):
    return lines("<?hh", "class OrderTest {", "  // Principle: " + PRINCIPLE, "  public function testShips(): void {",
                 "    // " + comment, "    expect(status())->toBeSame('ok');", "  }", "}")


class CitationMarkersPerLanguage(InMemoryHookCase):
    language = True

    def citation_line_verdict(self, relative, content, line):
        decision, rows = self.exact_verdict(relative, content)
        return decision, tuple(row for row in rows if row[1] == line)

    def test_lua_test_with_a_dash_citation_passes(self):
        self.assertEqual(self.exact_verdict(LUA_TEST, busted(PRINCIPLE)), ALLOWED)

    def test_lua_test_without_a_citation_is_denied_for_citation(self):
        self.assertEqual(self.exact_verdict(LUA_TEST, busted(None)), blocked((CITATION_RULE, 2)))

    def test_lua_test_quoting_a_sentence_outside_the_skill_is_denied_for_citation(self):
        self.assertEqual(self.exact_verdict(LUA_TEST, busted(MADE_UP)), blocked((CITATION_RULE, 3)))

    def test_elixir_hash_citation_line_raises_no_row(self):
        self.assertEqual(self.citation_line_verdict(ELIXIR_TEST, exunit(PRINCIPLE), 3), ALLOWED)

    def test_scala_slash_citation_line_raises_no_row(self):
        self.assertEqual(self.citation_line_verdict(SCALA_TEST, scala_junit(PRINCIPLE), 3), ALLOWED)

    def test_elixir_test_quoting_a_sentence_outside_the_skill_is_denied_for_citation(self):
        decision, rows = self.exact_verdict(ELIXIR_TEST, exunit(MADE_UP))
        self.assertEqual((decision, (CITATION_RULE, 4) in rows), (DENY, True))

    def test_scala_test_quoting_a_sentence_outside_the_skill_is_denied_for_citation(self):
        decision, rows = self.exact_verdict(SCALA_TEST, scala_junit(MADE_UP))
        self.assertEqual((decision, (CITATION_RULE, 4) in rows), (DENY, True))

    def test_php_test_with_a_hash_citation_passes(self):
        self.assertEqual(self.exact_verdict(PHP_TEST, phpunit(PRINCIPLE)), ALLOWED)

    def test_php_test_with_a_double_slash_citation_passes(self):
        self.assertEqual(self.exact_verdict(PHP_TEST, phpunit(PRINCIPLE, "    //")), ALLOWED)

    def test_ruby_test_quoting_a_made_up_sentence_on_a_dash_line_is_denied_for_citation(self):
        decision, rows = self.exact_verdict(RUBY_TEST, rspec("--", MADE_UP))
        self.assertEqual((decision, {rule for rule, _ in rows}), (DENY, {CITATION_RULE}))

    def test_broken_elixir_test_without_units_is_denied_for_citation(self):
        self.assertEqual(self.exact_verdict(ELIXIR_TEST, broken_exunit()), blocked((CITATION_RULE, 2)))

    def test_clean_elixir_test_with_a_real_quote_passes(self):
        self.assertEqual(self.exact_verdict(ELIXIR_TEST, exunit(PRINCIPLE)), ALLOWED)

    def test_narrating_comment_in_a_dart_test_is_read_as_a_code_comment(self):
        decision, rows = self.exact_verdict(DART_TEST, dart_test(NARRATION))
        self.assertEqual((decision, ("what_comment", 4) in rows), (DENY, True))

    def test_narrating_comment_in_a_hack_test_is_read_as_a_code_comment(self):
        self.assertEqual(self.exact_verdict(HACK_TEST, hack_test(NARRATION)), blocked(("what_comment", 5)))


if __name__ == "__main__":
    unittest.main()
