"""Hook runner for pre/post chat lifecycle hooks.

Hooks are executables (any language) in a configured directory:

  pre-chat   — receives user message on stdin, stdout is injected as hidden context
  post-chat  — receives JSON with task context on stdin, fire-and-forget

Example .hook/pre-chat (bash):
    #!/usr/bin/env bash
    node "$(dirname "$0")/../.instinct/scripts/reflex.js" "$(cat)"

Example .hook/post-chat (bash):
    #!/usr/bin/env bash
    ERROR=$(cat | python3 -c "import json,sys; print(json.load(sys.stdin).get('error',''))")
    [ -n "$ERROR" ] && node "$(dirname "$0")/../.instinct/scripts/reflect.js" \\
        --domain error --gotcha "$ERROR"
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from loguru import logger


class HookRunner:
    """Runs lifecycle hook scripts from a directory."""

    PRE_CHAT = "pre-chat"
    POST_CHAT = "post-chat"

    def __init__(self, hooks_dir: Path | None) -> None:
        self.hooks_dir = hooks_dir

    def _hook(self, name: str) -> Path | None:
        """Return hook path if it exists and is executable, else None."""
        if not self.hooks_dir or not self.hooks_dir.is_dir():
            return None
        p = self.hooks_dir / name
        if p.exists() and os.access(p, os.X_OK):
            return p
        return None

    async def run_pre_chat(self, user_message: str, workspace: Path | None = None) -> str | None:
        """Run pre-chat hook. Returns output to inject as hidden context, or None."""
        hook = self._hook(self.PRE_CHAT)
        if not hook:
            return None
        try:
            out = await self._run(hook, stdin=user_message, timeout=10, workspace=workspace)
            return out.strip() or None
        except asyncio.TimeoutError:
            logger.warning("pre-chat hook timed out (>10s), skipping")
            return None
        except Exception as e:
            logger.warning("pre-chat hook failed: {}", e)
            return None

    async def run_post_chat(
        self,
        session_key: str,
        response: str | None = None,
        error: str | None = None,
        status: str | None = None,
        user_message: str | None = None,
        tools_used: list[str] | None = None,
        usage: dict[str, int] | None = None,
        duration_ms: float | None = None,
        routing_domains: list[str] | None = None,
        selected_primary: str | None = None,
        artifacts: list[str] | None = None,
        tests: list[str] | None = None,
        workspace: Path | None = None,
        reflector_active: bool = False,
    ) -> None:
        """Run post-chat hook. Fire-and-forget (errors are logged, not raised)."""
        hook = self._hook(self.POST_CHAT)
        if not hook:
            return
        payload = self._post_chat_payload(
            session_key=session_key,
            response=response,
            error=error,
            status=status,
            user_message=user_message,
            tools_used=tools_used,
            usage=usage,
            duration_ms=duration_ms,
            routing_domains=routing_domains,
            selected_primary=selected_primary,
            artifacts=artifacts,
            tests=tests,
            reflector_active=reflector_active,
        )
        try:
            await self._run(hook, stdin=payload, timeout=15, workspace=workspace)
        except asyncio.TimeoutError:
            logger.warning("post-chat hook timed out (>15s)")
        except Exception as e:
            logger.warning("post-chat hook failed: {}", e)

    @staticmethod
    def _post_chat_payload(
        *,
        session_key: str,
        response: str | None,
        error: str | None,
        status: str | None,
        user_message: str | None,
        tools_used: list[str] | None,
        usage: dict[str, int] | None,
        duration_ms: float | None,
        routing_domains: list[str] | None,
        selected_primary: str | None,
        artifacts: list[str] | None,
        tests: list[str] | None,
        reflector_active: bool,
    ) -> str:
        return json.dumps(
            {
                "session_key": session_key,
                "response": response,
                "error": error,
                "status": status,
                "user_message": user_message,
                "tools_used": tools_used or [],
                "usage": usage or {},
                "duration_ms": duration_ms,
                "routing_domains": routing_domains or [],
                "selected_primary": selected_primary,
                "artifacts": artifacts or [],
                "tests": tests or [],
                "reflector_active": reflector_active,
            }
        )

    @staticmethod
    def _hook_env(workspace: Path | None = None) -> dict[str, str]:
        env = {**os.environ}
        if workspace:
            env["ARIESCLAW_WORKSPACE"] = str(workspace)
        return env

    @staticmethod
    async def _run(
        script: Path,
        stdin: str,
        timeout: float,
        workspace: Path | None = None,
    ) -> str:
        proc = await asyncio.create_subprocess_exec(
            str(script),
            cwd=str(script.parent),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=HookRunner._hook_env(workspace),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(stdin.encode()), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            raise

        if proc.returncode != 0:
            err = stderr.decode().strip() if stderr else ""
            if err:
                logger.debug("hook stderr: {}", err)
            raise RuntimeError(f"hook exited with status {proc.returncode}")
        return stdout.decode()
