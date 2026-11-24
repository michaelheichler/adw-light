from __future__ import annotations

from enum import StrEnum
from typing import NamedTuple

try:
    from ._captures import EXCEPTION
    from ._expectations import ASSERTED
except ImportError:
    from test_rules._captures import EXCEPTION
    from test_rules._expectations import ASSERTED

JS = frozenset({"javascript"})
JVM = frozenset({"java", "kotlin", "scala", "groovy"})
JAVA = frozenset({"java"})
KOTLIN = frozenset({"kotlin"})
CSHARP = frozenset({"csharp"})
PHP = frozenset({"php"})
RUBY = frozenset({"ruby"})
GO = frozenset({"go"})
BASH = frozenset({"bash"})
RUST = frozenset({"rust"})
SWIFT = frozenset({"swift"})
NATIVE = frozenset({"c", "cpp"})
CPP = frozenset({"cpp"})
SCALA = frozenset({"scala"})
DART = frozenset({"dart"})
ELIXIR = frozenset({"elixir"})
LUA = frozenset({"lua"})


class Order(StrEnum):
    EXPECTED_FIRST = "expected_first"
    ACTUAL_FIRST = "actual_first"
    MATCHER = "matcher"
    LAST = "last"
    NONE = "none"


class Skip(StrEnum):
    NOTHING = ""
    TESTING_HANDLE = "testing_handle"
    JUNIT4_MESSAGE = "junit4_message"


class Row(NamedTuple):
    framework: str
    names: frozenset[str]
    order: Order
    languages: frozenset[str]
    roots: frozenset[str] = frozenset()
    kind: str = ASSERTED
    skip: Skip = Skip.NOTHING
    bare: bool = False
    pattern: bool = False
    guarded: bool = False


def _names(text: str) -> frozenset[str]:
    return frozenset(text.split())


def _go_variants(text: str) -> frozenset[str]:
    names = text.split()
    return frozenset(names + [name + "f" for name in names])


ASSERTJ_ROOTS = _names("assertThat then assertThatThrownBy assertThatExceptionOfType assertThatCode Assertions BDDAssertions softly")
HAMCREST_ROOTS = _names("Matchers CoreMatchers Is StringContains")
GMOCK_ROOTS = _names("testing")
CATCH_ROOTS = _names("Catch Matchers")
FRAMEWORKS: tuple[Row, ...] = (
    Row("MUnit", _names("assertEquals assertNotEquals assertNoDiff"), Order.NONE, SCALA),
    Row("JUnit", _names("assertEquals assertNotEquals assertSame assertNotSame assertArrayEquals assertIterableEquals"),
        Order.EXPECTED_FIRST, JVM, skip=Skip.JUNIT4_MESSAGE),
    Row("JUnit", _names("assertLinesMatch"), Order.EXPECTED_FIRST, JVM, pattern=True, skip=Skip.JUNIT4_MESSAGE),
    Row("AssertJ", _names(
        "isEqualTo isNotEqualTo contains doesNotContain startsWith endsWith containsOnlyOnce isEqualToIgnoringCase "
        "isEqualToIgnoringWhitespace isEqualToNormalizingWhitespace isEqualToNormalizingNewlines containsIgnoringCase "
        "containsSequence hasToString containsExactly"), Order.MATCHER, JVM, ASSERTJ_ROOTS),
    Row("AssertJ", _names("matches containsPattern"), Order.MATCHER, JVM, ASSERTJ_ROOTS, pattern=True),
    Row("AssertJ", _names(
        "hasMessage hasMessageContaining hasMessageStartingWith hasMessageEndingWith withMessage withMessageContaining "
        "hasRootCauseMessage"), Order.MATCHER, JVM, ASSERTJ_ROOTS, EXCEPTION),
    Row("AssertJ", _names("hasMessageMatching withMessageMatching"), Order.MATCHER, JVM, ASSERTJ_ROOTS, EXCEPTION, pattern=True),
    Row("Hamcrest", _names(
        "equalTo is containsString startsWith endsWith equalToIgnoringCase equalToIgnoringWhiteSpace "
        "equalToCompressingWhiteSpace containsStringIgnoringCase hasToString stringContainsInOrder"),
        Order.MATCHER, JVM, HAMCREST_ROOTS, bare=True),
    Row("Hamcrest", _names("matchesPattern matchesRegex"), Order.MATCHER, JVM, HAMCREST_ROOTS, bare=True, pattern=True),
    Row("Kotest", _names(
        "shouldBe shouldNotBe shouldContain shouldNotContain shouldStartWith shouldEndWith shouldContainIgnoringCase "
        "shouldBeEqualIgnoringCase shouldContainOnlyOnce"), Order.LAST, KOTLIN),
    Row("Kotest", _names("shouldMatch shouldContainRegex"), Order.LAST, KOTLIN, pattern=True),
    Row("Kotest", _names("shouldHaveMessage"), Order.LAST, KOTLIN, kind=EXCEPTION),
    Row("xUnit", _names(
        "Equal NotEqual StrictEqual NotStrictEqual Contains DoesNotContain StartsWith EndsWith AreEqual AreNotEqual "
        "AreSame AreEqualIgnoringCase"), Order.EXPECTED_FIRST, CSHARP,
        _names("Assert ClassicAssert StringAssert CollectionAssert")),
    Row("xUnit", _names("Matches DoesNotMatch IsMatch"), Order.EXPECTED_FIRST, CSHARP,
        _names("Assert ClassicAssert StringAssert"), pattern=True),
    Row("NUnit", _names(
        "EqualTo Contain StartWith EndWith Substring StringContaining StringStarting StringEnding"),
        Order.MATCHER, CSHARP, _names("Is Does Contains Has Text")),
    Row("NUnit", _names("Match Matches StringMatching"), Order.MATCHER, CSHARP, _names("Is Does Has Text"), pattern=True),
    Row("PHPUnit", _names(
        "assertEquals assertSame assertNotEquals assertNotSame assertStringContainsString "
        "assertStringNotContainsString assertStringStartsWith assertStringEndsWith assertStringStartsNotWith "
        "assertStringEndsNotWith assertStringContainsStringIgnoringCase assertEqualsIgnoringCase assertContains"),
        Order.EXPECTED_FIRST, PHP),
    Row("PHPUnit", _names("assertMatchesRegularExpression assertDoesNotMatchRegularExpression assertRegExp"),
        Order.EXPECTED_FIRST, PHP, pattern=True),
    Row("PHPUnit", _names("expectExceptionMessage"), Order.MATCHER, PHP, kind=EXCEPTION),
    Row("PHPUnit", _names("expectExceptionMessageMatches"), Order.MATCHER, PHP, kind=EXCEPTION, pattern=True),
    Row("Minitest", _names("assert_equal refute_equal assert_same refute_same"), Order.EXPECTED_FIRST, RUBY),
    Row("Minitest", _names("assert_match refute_match"), Order.EXPECTED_FIRST, RUBY, pattern=True),
    Row("Minitest", _names("assert_includes refute_includes"), Order.ACTUAL_FIRST, RUBY),
    Row("Minitest", _names("must_equal wont_equal must_include wont_include must_be_same_as"), Order.MATCHER, RUBY),
    Row("Minitest", _names("must_match wont_match"), Order.MATCHER, RUBY, pattern=True),
    Row("RSpec", _names("eq eql equal include start_with end_with have_content have_text"), Order.MATCHER, RUBY, bare=True),
    Row("RSpec", _names("match"), Order.MATCHER, RUBY, bare=True, pattern=True),
    Row("RSpec", _names("raise_error"), Order.LAST, RUBY, kind=EXCEPTION, bare=True),
    Row("Bats", _names("assert_output refute_output assert_line refute_line assert_stderr refute_stderr"), Order.LAST, BASH),
    Row("Bats", _names("assert_regex refute_regex"), Order.LAST, BASH, pattern=True),
    Row("Bats", _names("assert_equal assert_not_equal"), Order.ACTUAL_FIRST, BASH),
    Row("Go testing", _names("Contains HasPrefix HasSuffix EqualFold"), Order.ACTUAL_FIRST, GO,
        _names("strings bytes"), guarded=True),
    Row("Go testing", _names("DeepEqual"), Order.NONE, GO, _names("reflect"), guarded=True),
    Row("testify", _go_variants("Equal NotEqual EqualValues NotEqualValues Exactly Same NotSame JSONEq YAMLEq"),
        Order.EXPECTED_FIRST, GO, skip=Skip.TESTING_HANDLE),
    Row("testify", _go_variants("Regexp NotRegexp"), Order.EXPECTED_FIRST, GO, skip=Skip.TESTING_HANDLE, pattern=True),
    Row("testify", _go_variants("Contains NotContains"), Order.ACTUAL_FIRST, GO, skip=Skip.TESTING_HANDLE),
    Row("testify", _go_variants("EqualError ErrorContains"), Order.ACTUAL_FIRST, GO, kind=EXCEPTION, skip=Skip.TESTING_HANDLE),
    Row("Jest", _names(
        "toBe toEqual toStrictEqual toContain toHaveTextContent toHaveText toContainText toHaveValue "
        "toHaveDisplayValue toHaveTitle toHaveAccessibleName toHaveAccessibleDescription toHaveBeenCalledWith "
        "toHaveBeenLastCalledWith toHaveBeenNthCalledWith"), Order.MATCHER, JS, _names("expect")),
    Row("Jest", _names("toMatch"), Order.MATCHER, JS, _names("expect"), pattern=True),
    Row("Jest", _names("toThrow toThrowError"), Order.MATCHER, JS, _names("expect"), EXCEPTION),
    Row("Chai", _names("equal equals eql include includes contain contains string"), Order.MATCHER, JS,
        _names("expect should")),
    Row("Chai", _names("match matches"), Order.MATCHER, JS, _names("expect should"), pattern=True),
    Row("Node assert", _names(
        "equal strictEqual deepEqual deepStrictEqual notEqual notStrictEqual include notInclude"),
        Order.ACTUAL_FIRST, JS, _names("assert")),
    Row("Node assert", _names("match doesNotMatch"), Order.ACTUAL_FIRST, JS, _names("assert"), pattern=True),
    Row("Rust", _names("assert_eq assert_ne"), Order.NONE, RUST),
    Row("XCTest", _names("XCTAssertEqual XCTAssertNotEqual"), Order.NONE, SWIFT),
    Row("Nimble", _names("equal contain beginWith endWith"), Order.MATCHER, SWIFT, bare=True),
    Row("Nimble", _names("match"), Order.MATCHER, SWIFT, bare=True, pattern=True),
    Row("GoogleTest", _names(
        "EXPECT_EQ ASSERT_EQ EXPECT_NE ASSERT_NE EXPECT_STREQ ASSERT_STREQ EXPECT_STRNE ASSERT_STRNE "
        "EXPECT_STRCASEEQ ASSERT_STRCASEEQ EXPECT_STRCASENE ASSERT_STRCASENE"), Order.NONE, NATIVE),
    Row("gMock", _names("HasSubstr StartsWith EndsWith StrEq StrNe StrCaseEq StrCaseNe Eq"), Order.MATCHER, CPP,
        GMOCK_ROOTS, bare=True),
    Row("gMock", _names("ContainsRegex MatchesRegex"), Order.MATCHER, CPP, GMOCK_ROOTS, bare=True, pattern=True),
    Row("Catch2", _names("ContainsSubstring StartsWith EndsWith Equals"), Order.MATCHER, CPP, CATCH_ROOTS, bare=True),
    Row("Catch2", _names("Matches"), Order.MATCHER, CPP, CATCH_ROOTS, bare=True, pattern=True),
    Row("Catch2", _names("REQUIRE_THROWS_WITH CHECK_THROWS_WITH"), Order.LAST, CPP, kind=EXCEPTION),
    Row("Unity", _names("TEST_ASSERT_EQUAL_STRING TEST_ASSERT_EQUAL_STRING_MESSAGE"), Order.NONE, NATIVE),
    Row("CUnit", _names("CU_ASSERT_STRING_EQUAL CU_ASSERT_STRING_NOT_EQUAL CU_ASSERT_STRING_EQUAL_FATAL"),
        Order.NONE, NATIVE),
    Row("cmocka", _names("assert_string_equal assert_string_not_equal"), Order.NONE, NATIVE),
    Row("package:test", _names("expect"), Order.ACTUAL_FIRST, DART),
    Row("package:test", _names("equals contains startsWith endsWith equalsIgnoringCase equalsIgnoringWhitespace"),
        Order.MATCHER, DART, bare=True),
    Row("package:test", _names("matches"), Order.MATCHER, DART, bare=True, pattern=True),
    Row("ExUnit", _names("assert_raise"), Order.NONE, ELIXIR, kind=EXCEPTION),
    Row("busted", _names("equal equals same are_equal are_same is_equal"), Order.NONE, LUA, _names("assert")),
    Row("busted", _names("match matches"), Order.NONE, LUA, _names("assert"), pattern=True),
    Row("LuaUnit", _names("assertEquals assertNotEquals assertStrContains assertStrIContains assertNotStrContains"),
        Order.NONE, LUA),
    Row("LuaUnit", _names("assertStrMatches"), Order.NONE, LUA, pattern=True),
)


def _rooted(row: Row, parts: list[str]) -> bool:
    if len(parts) == 1:
        return row.bare or not row.roots
    return not row.roots or parts[0] in row.roots


def framework_row(language: str, parts: list[str]) -> Row | None:
    if not parts:
        return None
    tail = parts[-1]
    for row in FRAMEWORKS:
        if language in row.languages and tail in row.names and _rooted(row, parts):
            return row
    return None
