import json
from typing import NamedTuple

from t20_layer_home import DENY, NONE, PLAIN, LayerHome, decision_of


ASSERTION = "wording_assertion"
LOCATOR = "wording_ui_locator"
EXCEPTION = "wording_exception_match"
LOCATION = "literal_location"
LOCK = "wording_debt_lock"
WORDING_RULES = frozenset((ASSERTION, LOCATOR, EXCEPTION, LOCATION, "wording_log_assertion"))


def words(*parts):
    return " ".join(parts)


def quoted(text):
    return json.dumps(text)


def lines(*body):
    return "".join(line + "\n" for line in body)


class Outcome(NamedTuple):
    decision: str
    rows: tuple[tuple[str, int], ...]


def denial(rule, *at):
    return Outcome(DENY, tuple((rule, line) for line in at))


PASSED = Outcome(NONE, ())


class WordingHome(LayerHome):
    def outcome(self, case, relative, content, rules=WORDING_RULES, project=PLAIN):
        response = self.write(case, relative, content, project)
        rows = tuple(sorted({(row["rule"], row["line"]) for row in self.report_rows(case) if row["rule"] in rules}))
        return Outcome(decision_of(response), rows)

    def matches(self, case, rule):
        return tuple(sorted(str(row.get("match")) for row in self.report_rows(case) if row["rule"] == rule))
