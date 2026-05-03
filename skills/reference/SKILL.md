---
name: reference
description: "Clone GitHub repos into workspace/reference/ as code references, then implement features based on the cloned source."
metadata: {"theos":{"emoji":"📚","requires":{"bins":["git"]}}}
---

# Reference Skill

Clone GitHub repositories into the workspace `reference/` directory as read-only code references, then implement features based on the cloned source.

## When to use

Use this skill when the user:
- Shares a GitHub repo URL and wants to reproduce or adapt its code
- Says "reference this repo", "clone this for reference", "look at this repo and implement..."
- Wants to study a repo's implementation and port logic into TheOS

## Workflow

1. **Clone**: Clone the repo into `~/.theos/workspace/reference/<repo-name>/`
   ```bash
   git clone --depth 1 <github-url> ~/.theos/workspace/reference/<repo-name>
   ```
   - Use `--depth 1` for a shallow clone (saves space, only need source)
   - If the directory already exists, skip cloning and inform the user

2. **Explore**: Read and understand the cloned repo's structure and key files
   - Identify the entry points, core modules, and relevant logic
   - Focus on the parts the user asked about

3. **Implement**: Based on the user's request, adapt the reference code into the current codebase
   - Do NOT copy-paste blindly; adapt to existing project conventions and architecture
   - Reference the original file paths when explaining what was adapted

4. **Cleanup** (optional): When the user says they're done with a reference repo:
   ```bash
   rm -rf ~/.theos/workspace/reference/<repo-name>
   ```

## List references

Show all currently cloned references:
```bash
ls ~/.theos/workspace/reference/
```

## Notes

- The `reference/` directory is for temporary study only, not committed anywhere
- Always use shallow clones to save disk space
- When multiple repos are referenced, keep them organized by repo name
