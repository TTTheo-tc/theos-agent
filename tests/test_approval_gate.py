"""Tests for ApprovalGate — risk-aware human escape hatch."""

import asyncio

from src.agent.approval import (
    ApprovalGate,
    ApprovalRequest,
    ApprovalResponse,
    RiskLevel,
)


async def test_auto_approve_low():
    gate = ApprovalGate(auto_approve_levels={RiskLevel.LOW})
    resp = await gate.check("read_file", {"path": "/tmp/x"}, RiskLevel.LOW)
    assert resp.approved is True


async def test_block_high_without_callback():
    gate = ApprovalGate(auto_approve_levels={RiskLevel.LOW})
    resp = await gate.check("bash", {"command": "rm -rf /"}, RiskLevel.HIGH)
    assert resp.approved is False
    assert "no approval callback" in resp.reason


async def test_callback_invoked():
    invocations: list[ApprovalRequest] = []

    async def cb(req: ApprovalRequest) -> ApprovalResponse:
        invocations.append(req)
        return ApprovalResponse(approved=True, reason="human approved")

    gate = ApprovalGate(callback=cb, auto_approve_levels={RiskLevel.LOW})
    resp = await gate.check(
        "bash", {"command": "git push"}, RiskLevel.HIGH, reason="push to remote"
    )
    assert resp.approved is True
    assert resp.reason == "human approved"
    assert len(invocations) == 1
    assert invocations[0].tool_name == "bash"
    assert invocations[0].risk_level == RiskLevel.HIGH


async def test_callback_denies():
    async def cb(req: ApprovalRequest) -> ApprovalResponse:
        return ApprovalResponse(approved=False, reason="too dangerous")

    gate = ApprovalGate(callback=cb)
    resp = await gate.check("bash", {"command": "rm -rf /"}, RiskLevel.CRITICAL)
    assert resp.approved is False
    assert resp.reason == "too dangerous"


async def test_timeout():
    async def slow_cb(req: ApprovalRequest) -> ApprovalResponse:
        await asyncio.sleep(10)
        return ApprovalResponse(approved=True)

    gate = ApprovalGate(callback=slow_cb, timeout=0.05)
    resp = await gate.check("bash", {}, RiskLevel.HIGH)
    assert resp.approved is False
    assert "timed out" in resp.reason


async def test_disabled_gate_passes_everything():
    gate = ApprovalGate(enabled=False)
    resp = await gate.check("bash", {"command": "rm -rf /"}, RiskLevel.CRITICAL)
    assert resp.approved is True


async def test_modified_args():
    async def cb(req: ApprovalRequest) -> ApprovalResponse:
        return ApprovalResponse(
            approved=True,
            modified_args={"command": "echo 'sanitized'"},
            reason="args modified",
        )

    gate = ApprovalGate(callback=cb)
    resp = await gate.check("bash", {"command": "dangerous"}, RiskLevel.MEDIUM)
    assert resp.approved is True
    assert resp.modified_args == {"command": "echo 'sanitized'"}


async def test_auto_approve_multiple_levels():
    gate = ApprovalGate(auto_approve_levels={RiskLevel.LOW, RiskLevel.MEDIUM})
    resp_low = await gate.check("t", {}, RiskLevel.LOW)
    resp_med = await gate.check("t", {}, RiskLevel.MEDIUM)
    resp_high = await gate.check("t", {}, RiskLevel.HIGH)
    assert resp_low.approved is True
    assert resp_med.approved is True
    assert resp_high.approved is False
