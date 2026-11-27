import unittest

from in_memory_hook import ALLOWED, DENY, PRINCIPLE, InMemoryHookCase, blocked


CITATION_RULE = "test_principle_citation"
WORDING_RULE = "wording_assertion"
MADE_UP = "A test should always be written in green ink before lunch."
SENTENCE = "'An English sentence here'"
DART_TEST = "test/order_test.dart"
WIDGET_TEST = "test/order_widget_test.dart"
DART_SOURCE = "lib/order.dart"
TEST_IMPORT = "import 'package:test/test.dart';"
FLUTTER_IMPORT = "import 'package:flutter_test/flutter_test.dart';"


def lines(*body):
    return "".join(line + "\n" for line in body)


def cited(quote, indent="  "):
    return (indent + "// Principle: " + quote,) if quote else ()


def dart_test(quote, check="expect(status(), equals('ok'));"):
    return lines(TEST_IMPORT, "void main() {", *cited(quote), "  test('ships', () {", "    " + check, "  });", "}")


def widget_test(quote):
    return lines(FLUTTER_IMPORT, "void main() {", *cited(quote), "  testWidgets('ships', (tester) async {",
                 "    expect(find.byKey(orderKey), findsOneWidget);", "  });", "}")


def sibling_group():
    return lines(TEST_IMPORT, "void main() {", "  group('order', () {", *cited(PRINCIPLE, "    "),
                 "    test('ships', () {", "      expect(status(), equals('ok'));", "    });",
                 "    test('bills', () {", "      expect(total(), equals(42));", "    });", "  });", "}")


def hooks_around_cited_test():
    return lines(TEST_IMPORT, "void main() {", "  setUp(() {", "    reset();", "  });", "  tearDown(() {",
                 "    reset();", "  });", *cited(PRINCIPLE), "  test('ships', () {",
                 "    expect(status(), equals('ok'));", "  });", "}")


def prefixed_test():
    return lines("import 'package:test/test.dart' as t;", "void main() {", "  t.test('ships', () {",
                 "    t.expect(status(), t.equals('ok'));", "  });", "}")


def member_test_inside_cited_test():
    return lines(TEST_IMPORT, "void main() {", *cited(PRINCIPLE), "  test('ships', () {",
                 "    foo.test('inner', () {", "      bar();", "    });", "    expect(status(), equals('ok'));",
                 "  });", "}")


def production_source():
    return lines("void check(Order order) {", "  test('ships', () {", "    expect(order.status(), " + SENTENCE + ");",
                 "  });", "}")


class DartTestUnits(InMemoryHookCase):
    language = True

    def test_dart_test_without_a_citation_is_denied_for_citation(self):
        self.assertEqual(self.exact_verdict(DART_TEST, dart_test(None)), blocked((CITATION_RULE, 3)))

    def test_dart_test_quoting_a_sentence_outside_the_skill_is_denied_for_citation(self):
        decision, rows = self.exact_verdict(DART_TEST, dart_test(MADE_UP))
        self.assertEqual((decision, (CITATION_RULE, 4) in rows), (DENY, True))

    def test_dart_test_with_a_verbatim_quote_above_the_call_passes(self):
        self.assertEqual(self.exact_verdict(DART_TEST, dart_test(PRINCIPLE)), ALLOWED)

    def test_flutter_widget_test_without_a_citation_is_denied_for_citation(self):
        self.assertEqual(self.exact_verdict(WIDGET_TEST, widget_test(None)), blocked((CITATION_RULE, 3)))

    def test_flutter_widget_test_with_a_verbatim_quote_passes(self):
        self.assertEqual(self.exact_verdict(WIDGET_TEST, widget_test(PRINCIPLE)), ALLOWED)

    def test_dart_expect_on_a_bare_sentence_is_denied_for_wording(self):
        content = dart_test(PRINCIPLE, "expect(status(), " + SENTENCE + ");")
        self.assertEqual(self.exact_verdict(DART_TEST, content), blocked((WORDING_RULE, 5)))

    def test_dart_equals_matcher_on_a_sentence_is_denied_for_wording(self):
        content = dart_test(PRINCIPLE, "expect(status(), equals(" + SENTENCE + "));")
        self.assertEqual(self.exact_verdict(DART_TEST, content), blocked((WORDING_RULE, 5)))

    def test_dart_contains_matcher_on_a_sentence_is_denied_for_wording(self):
        content = dart_test(PRINCIPLE, "expect(status(), contains(" + SENTENCE + "));")
        self.assertEqual(self.exact_verdict(DART_TEST, content), blocked((WORDING_RULE, 5)))

    def test_dart_starts_with_matcher_on_a_sentence_is_denied_for_wording(self):
        content = dart_test(PRINCIPLE, "expect(status(), startsWith(" + SENTENCE + "));")
        self.assertEqual(self.exact_verdict(DART_TEST, content), blocked((WORDING_RULE, 5)))

    def test_dart_matches_matcher_on_a_sentence_is_denied_for_wording(self):
        content = dart_test(PRINCIPLE, "expect(status(), matches(" + SENTENCE + "));")
        self.assertEqual(self.exact_verdict(DART_TEST, content), blocked((WORDING_RULE, 5)))

    def test_dart_equals_matcher_on_an_id_token_passes(self):
        content = dart_test(PRINCIPLE, "expect(orderId(), equals('ORD-42'));")
        self.assertEqual(self.exact_verdict(DART_TEST, content), ALLOWED)

    def test_dart_equals_matcher_on_a_number_passes(self):
        content = dart_test(PRINCIPLE, "expect(total(), equals(42));")
        self.assertEqual(self.exact_verdict(DART_TEST, content), ALLOWED)

    def test_citation_above_the_first_test_in_a_group_leaves_the_sibling_denied(self):
        self.assertEqual(self.exact_verdict(DART_TEST, sibling_group()), blocked((CITATION_RULE, 8)))

    def test_uncited_set_up_and_tear_down_around_a_cited_test_pass(self):
        self.assertEqual(self.exact_verdict(DART_TEST, hooks_around_cited_test()), ALLOWED)

    def test_uncited_test_through_an_import_prefix_is_denied_for_citation(self):
        self.assertEqual(self.exact_verdict(DART_TEST, prefixed_test()), blocked((CITATION_RULE, 3)))

    def test_member_test_call_inside_a_cited_test_is_not_a_unit(self):
        self.assertEqual(self.exact_verdict(DART_TEST, member_test_inside_cited_test()), ALLOWED)

    def test_dart_production_file_with_test_and_expect_calls_raises_no_rows(self):
        self.assertEqual(self.exact_verdict(DART_SOURCE, production_source()), ALLOWED)


if __name__ == "__main__":
    unittest.main()
