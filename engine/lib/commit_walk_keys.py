from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from .commit_functions import Function
    from .commit_inputs import Command, Input
except ImportError:
    from commit_functions import Function
    from commit_inputs import Command, Input

if TYPE_CHECKING:
    from .commit_command import _Walk


def _command_key(command: Command | None) -> tuple[object, ...] | None:
    if command is None:
        return None
    return tuple(command.words), frozenset(command.inputs.items()), tuple(command.outputs)


def _process_key(payload: str, child: _Walk) -> tuple[object, ...]:
    functions = tuple(sorted((name, function.body) for name, function in child.functions.items()))
    alternates = tuple(sorted((name, tuple(function.body for function in bodies)) for name, bodies in child.alternates.items()))
    return (
        payload, str(child.cwd), child.shell, functions, alternates, tuple(sorted(child.exported_functions)),
        child.relocated, child.unseen_writes, child.run.git_configured, bool(child.touched), child.created,
        child.initialized,
    )


def _function_key(walk: _Walk, function: Function, inputs: dict[int, Input], args: list[str]) -> tuple[object, ...]:
    return (
        function, tuple(args), walk.cwd, frozenset(walk.variables.items()), frozenset(walk.functions.items()),
        frozenset(walk.aliases.items()), frozenset(walk.exported_functions), frozenset(walk.alternates.items()),
        walk.function_stack, walk.depth, walk.relocated, walk.unseen_writes, walk.physical,
        walk.shell, frozenset(inputs.items()), walk.run.git_configured, bool(walk.touched), walk.created,
        walk.initialized,
    )


def _left_state(walk: _Walk) -> tuple[object, ...]:
    return (
        walk.cwd, walk.functions, walk.aliases, walk.exported_functions, walk.alternates,
        walk.relocated, walk.physical, walk.fds if walk.fds_replaced else None, walk.created,
        walk.initialized,
    )


def _variable_changes(walk: _Walk, entry: dict[str, str]) -> dict[str, str]:
    return {name: value for name, value in walk.variables.items()
            if name not in walk.local_variables and entry.get(name) != value}
