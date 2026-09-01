"""Plan-mode enforcement in TaskRuntime._build_tools().

The gateway enforces plan mode at routing time (test_plan_mode_routing.py).
This test suite verifies that the engine runtime mirrors that enforcement
for non-gateway entry points (CLI, channel bot, subagent, etc.).
"""

from __future__ import annotations

import pytest

from agentos.engine.runtime import TurnRunner
from agentos.plan_mode import PLAN_MODE_TOOL_ALLOW, get_plan_mode_store, reset_plan_mode_store
from agentos.tools.registry import ToolRegistry
from agentos.tools.types import (
    CallerKind,
    InteractionMode,
    ToolContext,
    ToolSpec,
)


async def _handler() -> str:
    return "ok"


def _spec(name: str) -> ToolSpec:
    return ToolSpec(name=name, description=f"{name} tool", parameters={})


@pytest.fixture(autouse=True)
def _fresh_store() -> None:
    """Each test starts and ends with a clean plan-mode store."""
    reset_plan_mode_store()
    yield
    reset_plan_mode_store()


# ---- helpers ------------------------------------------------------------


def _runner_for_tool_names(
    names: frozenset[str] = frozenset(
        {"read_file", "write_file", "exec_command", "web_search", "web_fetch", "exit_plan_mode"}
    ),
) -> TurnRunner:
    registry = ToolRegistry()
    for name in names:
        registry.register(_spec(name), _handler)
    return TurnRunner(
        provider_selector=None,
        tool_registry=registry,
        session_manager=object(),
        config=object(),
    )


# ---- plan mode ON: CLI/web/channel turns get narrowed -------------------


class TestPlanModeNarrowsNonBackgroundTools:
    def test_cli_turn_gets_plan_allowlist_when_mode_on(self) -> None:
        key = "agent:main:cli-plan"
        get_plan_mode_store().enable(key)
        ctx = ToolContext(
            caller_kind=CallerKind.CLI,
            interaction_mode=InteractionMode.INTERACTIVE,
            session_key=key,
            allowed_tools=None,
        )
        tool_defs, _handler_fn = _runner_for_tool_names()._build_tools(ctx)
        names = {t.name for t in tool_defs}
        assert names == set(PLAN_MODE_TOOL_ALLOW) & {
            "read_file",
            "web_search",
            "web_fetch",
            "exit_plan_mode",
        }
        assert "write_file" not in names
        assert "exec_command" not in names

    def test_channel_turn_gets_plan_allowlist_when_mode_on(self) -> None:
        key = "telegram:dm:u1"
        get_plan_mode_store().enable(key)
        ctx = ToolContext(
            caller_kind=CallerKind.CHANNEL,
            interaction_mode=InteractionMode.UNATTENDED,
            session_key=key,
            allowed_tools=None,
        )
        tool_defs, _handler_fn = _runner_for_tool_names()._build_tools(ctx)
        names = {t.name for t in tool_defs}
        assert "read_file" in names
        assert "write_file" not in names
        assert "exec_command" not in names

    def test_web_turn_gets_plan_allowlist_when_mode_on(self) -> None:
        key = "agent:main:web-plan"
        get_plan_mode_store().enable(key)
        ctx = ToolContext(
            caller_kind=CallerKind.WEB,
            interaction_mode=InteractionMode.INTERACTIVE,
            session_key=key,
            allowed_tools=None,
        )
        tool_defs, _handler_fn = _runner_for_tool_names()._build_tools(ctx)
        names = {t.name for t in tool_defs}
        assert "write_file" not in names
        assert "exec_command" not in names

    def test_plan_mode_intersects_existing_allowlist(self) -> None:
        """When ctx.allowed_tools is already set, plan mode intersects."""
        key = "agent:main:narrow"
        get_plan_mode_store().enable(key)
        ctx = ToolContext(
            caller_kind=CallerKind.WEB,
            interaction_mode=InteractionMode.INTERACTIVE,
            session_key=key,
            allowed_tools={"read_file", "write_file", "exec_command", "exit_plan_mode"},
        )
        tool_defs, _handler_fn = _runner_for_tool_names()._build_tools(ctx)
        names = {t.name for t in tool_defs}
        # write_file and exec_command are in the original allowlist but not in
        # PLAN_MODE_TOOL_ALLOW, so they must be removed after intersection.
        assert "read_file" in names
        assert "exit_plan_mode" in names
        assert "write_file" not in names
        assert "exec_command" not in names


# ---- plan mode OFF: tools stay unrestricted -----------------------------


class TestPlanModeOffNoEffect:
    def test_cli_turn_is_unrestricted_when_mode_off(self) -> None:
        ctx = ToolContext(
            caller_kind=CallerKind.CLI,
            interaction_mode=InteractionMode.INTERACTIVE,
            session_key="agent:main:cli-no-plan",
            allowed_tools=None,
        )
        tool_defs, _handler_fn = _runner_for_tool_names()._build_tools(ctx)
        names = {t.name for t in tool_defs}
        assert "read_file" in names
        assert "write_file" in names
        assert "exec_command" in names

    def test_session_without_plan_mode_is_unaffected(self) -> None:
        """Session never enabled plan mode — full tool surface."""
        ctx = ToolContext(
            caller_kind=CallerKind.WEB,
            interaction_mode=InteractionMode.INTERACTIVE,
            session_key="agent:main:never-plan",
            allowed_tools=None,
        )
        tool_defs, _handler_fn = _runner_for_tool_names()._build_tools(ctx)
        names = {t.name for t in tool_defs}
        assert "write_file" in names
        assert "exec_command" in names


# ---- background turns CRON/SUBAGENT are NEVER narrowed ------------------


class TestBackgroundTurnsSkipPlanMode:
    def test_cron_turn_never_narrowed(self) -> None:
        """Cron keeps its own allowlist; plan mode must not touch it."""
        key = "cron:job-1"
        get_plan_mode_store().enable(key)
        ctx = ToolContext(
            caller_kind=CallerKind.CRON,
            interaction_mode=InteractionMode.UNATTENDED,
            session_key=key,
            allowed_tools={"read_file", "write_file", "web_fetch"},
        )
        tool_defs, _handler_fn = _runner_for_tool_names()._build_tools(ctx)
        names = {t.name for t in tool_defs}
        assert "write_file" in names  # would be stripped by plan mode if applied
        assert "exit_plan_mode" not in names  # not in cron's own allowlist

    def test_subagent_turn_never_narrowed(self) -> None:
        """Subagent keeps its own surface."""
        key = "agent:main:sub-plan"
        get_plan_mode_store().enable(key)
        ctx = ToolContext(
            caller_kind=CallerKind.SUBAGENT,
            interaction_mode=InteractionMode.UNATTENDED,
            session_key=key,
            allowed_tools=None,
        )
        tool_defs, _handler_fn = _runner_for_tool_names()._build_tools(ctx)
        names = {t.name for t in tool_defs}
        assert "write_file" in names  # would be stripped if plan mode applied
        assert "exec_command" in names


# ---- edge cases ---------------------------------------------------------


class TestEdgeCases:
    def test_no_session_key_skips_plan_check(self) -> None:
        """Without a session key there is nothing to check."""
        get_plan_mode_store().enable("some-other-session")
        ctx = ToolContext(
            caller_kind=CallerKind.CLI,
            interaction_mode=InteractionMode.INTERACTIVE,
            session_key=None,
            allowed_tools=None,
        )
        tool_defs, _handler_fn = _runner_for_tool_names()._build_tools(ctx)
        names = {t.name for t in tool_defs}
        assert "write_file" in names

    def test_plan_tools_in_allowlist_when_mode_on(self) -> None:
        """All tools in PLAN_MODE_TOOL_ALLOW that we registered should show."""
        key = "agent:main:allow-check"
        get_plan_mode_store().enable(key)
        ctx = ToolContext(
            caller_kind=CallerKind.WEB,
            interaction_mode=InteractionMode.INTERACTIVE,
            session_key=key,
            allowed_tools=None,
        )
        tool_defs, _handler_fn = _runner_for_tool_names()._build_tools(ctx)
        names = {t.name for t in tool_defs}
        # These are in both PLAN_MODE_TOOL_ALLOW and our registry
        for allowed in ("read_file", "web_search", "web_fetch", "exit_plan_mode"):
            assert allowed in names
