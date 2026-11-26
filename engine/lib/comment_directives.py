from __future__ import annotations

TS_DIRECTIVE = r"@ts-(?:ignore|expect-error|nocheck|check)"
_TOOL_DIRECTIVE_PATTERNS = (
    r"//\s*swift-tools-version:[ \t]*\d+(?:\.\d+){0,2}(?:-[a-z0-9]+)?$",
    r"//\s*swiftlint:(?:disable|enable)(?::(?:next|this|previous))?(?:\s+[a-z][a-z0-9_]*){0,5}$",
    r"//\s*(?-i:MARK):[ \t]*(?:-[ \t]*)?(?:[^.]|\.(?=\S)){0,40}$",
    r"//go:(?:build|generate|embed|linkname|noinline|nosplit|noescape|norace|nocheckptr|nowritebarrier|"
    r"nowritebarrierrec|yeswritebarrierrec|uintptrescapes|systemstack|notinheap|debug|wasmimport|"
    r"wasmexport|fix|cgo_[a-z_]+)(?=\s|$)",
    r"//nolint(?::[\w-]+(?:,[\w-]+)*)?$",
    r"//\s*eslint-(?:disable(?:-(?:next-)?line)?|enable)(?:\s+[\w@/-]+(?:\s*,\s*[\w@/-]+)*)?$",
    r"//\s*prettier-ignore$",
    r"///\s*<reference(?:\s+[\w-]+=(?:\"[^\"]*\"|'[^']*'|(?=\s)|(?=/>))){1,3}\s*/>$",
    r"//\s*" + TS_DIRECTIVE + "$",
    r"//\s*@(?:no)?flow(?:\s+(?:strict(?:-local)?|weak))?$",
)
_HASH_TOOL_DIRECTIVE_PATTERNS = (
    r"#\s*noqa(?::\s*[a-z]+\d+(?:[\s,]+[a-z]+\d+)*)?$",
    r"#\s*type:\s*ignore(?:\[[\w\s,.-]+\])?$",
    r"#\s*pragma:\s*(?:no\s+(?:cover|branch)|allowlist\s+(?:nextline-)?secret)$",
    r"#\s*ruff:\s*noqa(?::\s*[a-z]+\d+(?:[\s,]+[a-z]+\d+)*)?$",
    r"#\s*fmt:\s*(?:off|on|skip)$",
    r"#\s*pylint:\s*(?:disable|enable)(?:-next)?=[\w-]+(?:\s*,\s*[\w-]+)*$",
    r"#\s*pyright:\s*(?:basic|standard|strict|ignore(?:\[[\w\s,.-]+\])?)$",
    r"#\s*mypy:\s*[\w-]+(?:=[\w\"',.-]+)?$",
    r"#\s*isort:\s*(?:skip(?:_file)?|off|on|split)$",
    r"#\s*syntax=\S+$",
    r"#\s*escape=[\\`]$",
    r"#\s*check=[\w=,;]+$",
    r"#\s*shellcheck\s+(?:disable|enable|source|shell|source-path|external-sources)=\S+(?:\s+[a-z-]+=\S+)*$",
    r"#\s*(?:frozen_string_literal|warn_indent|shareable_constant_value):\s*[\w-]+$",
    r"#\s*typed:\s*(?:ignore|false|true|strict|strong)$",
    r"#\s*(?:>>>|<<<)\s*agent-discipline-watcher(?:\s*(?:>>>|<<<))?$",
)
