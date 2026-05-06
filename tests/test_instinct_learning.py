from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_node(
    script: str,
    args: list[str],
    *,
    workspace: Path,
    stdin: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> str:
    env = {**os.environ, "THEOS_WORKSPACE": str(workspace), **(extra_env or {})}
    proc = subprocess.run(
        ["node", script, *args],
        input=stdin,
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )
    return proc.stdout


def test_reflect_post_task_uses_real_task_context(tmp_path: Path) -> None:
    payload = {
        "session_key": "cli:test",
        "user_message": "帮我分析这个 repo 的架构",
        "response": "我先读了 src/agent/loop.py，再总结架构。",
        "tools_used": ["read_file", "grep"],
        "usage": {"input_tokens": 12, "output_tokens": 34},
        "duration_ms": 78.9,
        "routing_domains": ["coding/general", "coding/automation"],
        "selected_primary": "coding/general",
        "artifacts": ["src/agent/loop.py", "tests/test_loop.py"],
        "tests": ["tests/test_loop.py"],
        "reflector_active": False,
    }

    _run_node(
        "instinct/scripts/reflect.js",
        ["--mode", "post-task"],
        workspace=tmp_path,
        stdin=json.dumps(payload, ensure_ascii=False),
    )

    events_dir = tmp_path / "memory" / "instinct" / "events"
    event_file = next(events_dir.glob("*.json"))
    event = json.loads(event_file.read_text(encoding="utf-8"))

    assert event["request"]["raw"] == payload["user_message"]
    assert event["request"]["demand_class"] == "analysis"
    assert event["generation"]["tools_used"] == ["read_file", "grep"]
    assert event["routing"]["domains"] == ["coding/general", "coding/automation"]
    assert event["routing"]["selected_primary"] == "coding/general"
    assert event["outcome"]["cost_hint"]["tool_calls"] == 2
    assert event["outcome"]["usage"] == {"input_tokens": 12, "output_tokens": 34}
    assert event["outcome"]["artifacts"] == ["src/agent/loop.py", "tests/test_loop.py"]
    assert event["outcome"]["tests"] == ["tests/test_loop.py"]


def test_reflex_reads_recent_events_as_gotchas(tmp_path: Path) -> None:
    payload = {
        "session_key": "cli:test",
        "user_message": "帮我分析一个股票量化策略",
        "response": "建议先做回测，再控制风险。",
        "tools_used": ["web_search"],
        "reflector_active": True,
    }

    _run_node(
        "instinct/scripts/reflect.js",
        ["--mode", "post-task"],
        workspace=tmp_path,
        stdin=json.dumps(payload, ensure_ascii=False),
    )

    output = _run_node(
        "instinct/scripts/reflex.js",
        ["帮我分析一个股票量化策略"],
        workspace=tmp_path,
    )

    assert "历史易错点" in output
    assert "最近相关任务" in output


def test_reflect_post_task_writes_lessons_and_candidates_even_when_reflector_flag_is_true(
    tmp_path: Path,
) -> None:
    payload = {
        "session_key": "cli:test",
        "user_message": "帮我重构 auth 模块",
        "response": "When changing auth code, always update the related tests.",
        "tools_used": ["read_file"],
        "reflector_active": True,
    }

    _run_node(
        "instinct/scripts/reflect.js",
        ["--mode", "post-task"],
        workspace=tmp_path,
        stdin=json.dumps(payload, ensure_ascii=False),
    )

    lessons_dir = tmp_path / "memory" / "instinct" / "lessons"
    candidates_path = tmp_path / "memory" / "instinct" / "rules" / "CANDIDATES.md"

    assert any(lessons_dir.glob("*.md"))
    assert candidates_path.exists()
    assert "always update the related tests" in candidates_path.read_text(encoding="utf-8").lower()


def test_reflect_post_task_marks_explicit_failed_status(tmp_path: Path) -> None:
    payload = {
        "session_key": "cli:test",
        "user_message": "帮我继续改这个工具系统",
        "response": "I reached the maximum number of tool call iterations (60) without completing the task.",
        "status": "failed",
        "tools_used": ["read_file", "edit_file"],
        "reflector_active": False,
    }

    _run_node(
        "instinct/scripts/reflect.js",
        ["--mode", "post-task"],
        workspace=tmp_path,
        stdin=json.dumps(payload, ensure_ascii=False),
    )

    events_dir = tmp_path / "memory" / "instinct" / "events"
    event_file = next(events_dir.glob("*.json"))
    event = json.loads(event_file.read_text(encoding="utf-8"))

    assert event["outcome"]["status"] == "failed"
    assert event["verification"]["has_issues"] is True
    assert event["verification"]["issue_types"] == ["failed"]


def test_evolve_decays_stale_rules_out_of_active(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory" / "instinct"
    events_dir = memory_dir / "events"
    rules_dir = memory_dir / "rules"
    events_dir.mkdir(parents=True)
    rules_dir.mkdir(parents=True)

    stale_rule = "建议先做回测，再控制风险。"
    old_event = {
        "timestamp": "2025-01-01T00:00:00Z",
        "task_id": "task-old",
        "request": {"demand_class": "analysis"},
        "generalization": {"transferable_rules": [stale_rule], "confidence": 0.9},
    }
    (events_dir / "old.json").write_text(
        json.dumps(old_event, ensure_ascii=False), encoding="utf-8"
    )
    (rules_dir / "ACTIVE.md").write_text(
        "# Active Rules\n\n- [R-old] 建议先做回测，再控制风险。\n",
        encoding="utf-8",
    )

    output = _run_node("instinct/scripts/evolve.js", [], workspace=tmp_path)

    active_text = (rules_dir / "ACTIVE.md").read_text(encoding="utf-8")
    archive_text = (rules_dir / "ARCHIVE.md").read_text(encoding="utf-8")
    assert "Decaying 1 stale rule" in output
    assert stale_rule not in active_text
    assert stale_rule in archive_text


def test_evolve_respects_min_interval(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory" / "instinct"
    events_dir = memory_dir / "events"
    rules_dir = memory_dir / "rules"
    events_dir.mkdir(parents=True)
    rules_dir.mkdir(parents=True)

    event = {
        "timestamp": "2026-03-07T00:00:00Z",
        "task_id": "task-now",
        "request": {"demand_class": "analysis"},
        "generalization": {"transferable_rules": ["建议先做回测，再控制风险。"], "confidence": 0.9},
    }
    (events_dir / "recent.json").write_text(json.dumps(event, ensure_ascii=False), encoding="utf-8")
    (rules_dir / "index.json").write_text(
        json.dumps(
            {"last_evolved": datetime.now(timezone.utc).isoformat()},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    output = _run_node(
        "instinct/scripts/evolve.js",
        [],
        workspace=tmp_path,
        extra_env={"INSTINCT_EVOLVE_MIN_INTERVAL_SECONDS": "3600"},
    )

    assert "Skipping evolve" in output


def test_evolve_temporal_decay_is_stable_across_repeated_runs(tmp_path: Path) -> None:
    events_dir = tmp_path / "memory" / "instinct" / "events"
    rules_dir = tmp_path / "memory" / "instinct" / "rules"
    events_dir.mkdir(parents=True)
    rules_dir.mkdir(parents=True)

    # Use dates relative to "now" so the test stays within the evolve.js
    # RECENCY_DAYS=14 promotion window regardless of when it runs.
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    rule_last_seen = (now - timedelta(days=24)).strftime("%Y-%m-%d")
    rule_review_after = (now + timedelta(days=36)).strftime("%Y-%m-%d")
    event_recent = (now - timedelta(days=2)).replace(microsecond=0)

    active_path = rules_dir / "ACTIVE.md"
    active_path.write_text(
        "# Active Rules\n\n"
        "- [R-auth] 当修改 auth 相关代码时，必须同时更新对应的测试用例  "
        f"<!-- scope:risk_warn domains:auth boost:0 class:adaptive conf:0.8 "
        f"base_conf:0.8 last_seen:{rule_last_seen} review_after:{rule_review_after} -->\n",
        encoding="utf-8",
    )

    # Force evolve.js to rewrite ACTIVE.md by providing a promotable new rule.
    for idx in range(3):
        ts = (event_recent + timedelta(hours=idx)).isoformat().replace("+00:00", "Z")
        event = {
            "timestamp": ts,
            "task_id": f"task-{idx}",
            "request": {"demand_class": "analysis"},
            "routing": {"domains": ["coding/analysis"]},
            "generalization": {
                "transferable_rules": ["When analyzing a repo, always read the main loop first."],
                "confidence": 0.9,
            },
        }
        (events_dir / f"event-{idx}.json").write_text(
            json.dumps(event, ensure_ascii=False), encoding="utf-8"
        )

    keep_event = {
        "timestamp": (event_recent + timedelta(hours=9)).isoformat().replace("+00:00", "Z"),
        "task_id": "task-keep-auth",
        "request": {"demand_class": "analysis"},
        "routing": {"domains": ["coding/auth"]},
        "generalization": {
            "transferable_rules": ["当修改 auth 相关代码时，必须同时更新对应的测试用例"],
            "confidence": 0.8,
        },
    }
    (events_dir / "keep-auth.json").write_text(
        json.dumps(keep_event, ensure_ascii=False), encoding="utf-8"
    )

    # Run 1: new rules enter probation.
    # Run 2: probation rules may promote to active (three-stage pipeline).
    # Run 3+: output must stabilise — idempotent from here.
    _run_node("instinct/scripts/evolve.js", [], workspace=tmp_path)
    _run_node("instinct/scripts/evolve.js", [], workspace=tmp_path)
    stabilised_text = active_path.read_text(encoding="utf-8")
    _run_node("instinct/scripts/evolve.js", [], workspace=tmp_path)
    third_text = active_path.read_text(encoding="utf-8")

    # After stabilisation, repeated runs on the same day must be identical.
    assert stabilised_text == third_text

    # Decay is working: conf < base_conf for the adaptive rule.
    import re

    conf_match = re.search(r" conf:([\d.]+)", stabilised_text)
    base_match = re.search(r"base_conf:([\d.]+)", stabilised_text)
    assert conf_match and base_match, "expected conf and base_conf in ACTIVE.md"
    assert float(conf_match.group(1)) < float(base_match.group(1))

    # New metadata fields are present
    assert "demoted_reason:" in stabilised_text
    assert "verification_support_count:" in stabilised_text
    assert "user_adopted_count:" in stabilised_text
