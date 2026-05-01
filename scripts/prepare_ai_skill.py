#!/usr/bin/env python3
"""Build an AI-generation brief for turning a recurring-task cluster into a real skill."""
import argparse
import json
import sys
from pathlib import Path

from draft_skills import (
    generated_skill_name,
    infer_category,
    load_existing_names,
    overlap_existing,
    safe_sample,
)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_sessions(path: Path) -> list[dict]:
    sessions = []
    if not path.exists():
        return sessions
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                sessions.append(json.loads(line))
            except Exception:
                continue
    return sessions


def find_session(sample: dict, sessions_by_id: dict[str, dict], sessions: list[dict]) -> dict | None:
    sid = sample.get("session_id")
    if sid and sid in sessions_by_id:
        return sessions_by_id[sid]

    prompt = str(sample.get("prompt", "")).strip()
    if not prompt:
        return None
    for session in sessions:
        first = str(session.get("first_user_prompt", ""))
        if first.startswith(prompt[:80]) or prompt.startswith(first[:80]):
            return session
    return None


def representative_sessions(cluster: dict, sessions: list[dict], max_samples: int) -> list[dict]:
    sessions_by_id = {str(s.get("session_id")): s for s in sessions if s.get("session_id")}
    reps = []
    seen = set()
    for sample in cluster.get("samples", []):
        session = find_session(sample, sessions_by_id, sessions)
        if not session:
            continue
        sid = session.get("session_id")
        if sid in seen:
            continue
        seen.add(sid)
        reps.append({
            "session_id": sid,
            "path": session.get("path") or sample.get("path"),
            "timestamp": session.get("timestamp") or sample.get("timestamp"),
            "cwd": session.get("cwd") or sample.get("cwd"),
            "first_user_prompt_sanitized": safe_sample(
                session.get("first_user_prompt") or sample.get("prompt", ""),
                limit=500,
            ),
            "tools": session.get("tools", [])[:20],
        })
        if len(reps) >= max_samples:
            break
    return reps


def render_prompt(brief: dict) -> str:
    session_lines = []
    for i, session in enumerate(brief["representative_sessions"], 1):
        session_lines.append(
            "\n".join([
                f"### Session {i}",
                f"- id: `{session.get('session_id')}`",
                f"- path: `{session.get('path')}`",
                f"- timestamp: `{session.get('timestamp')}`",
                f"- cwd: `{session.get('cwd')}`",
                f"- tools: {', '.join(session.get('tools') or [])}",
                f"- first prompt: {session.get('first_user_prompt_sanitized')}",
            ])
        )
    sessions_block = "\n\n".join(session_lines) if session_lines else "(no representative sessions found)"

    overlaps = ", ".join(brief["existing_overlap"]) if brief["existing_overlap"] else "(none detected)"

    return f"""# AI Skill Generation Brief

You are creating a real Codex/Claude skill from recurring Claude Code usage logs.

This is the AI-backed path. Do not merely fill a generic template. Use the representative sessions as evidence, infer the user's real workflow, and write a lean `SKILL.md` that would actually improve future runs.

## Target

- skill name: `{brief["skill_name"]}`
- category: {brief["category_title"]}
- detected trigger: {brief["trigger"]}
- detected count: {brief["count"]}
- top tools: {', '.join(brief["top_tools"])}
- top cwds: {', '.join(brief["top_cwds"])}
- existing overlap: {overlaps}
- target file: `{brief["target_skill_md"]}`

## Representative Sessions

Read the session JSONL files if they are available and you need more detail. Prefer extracting actual successful steps, tool choices, file locations, checks, and output shape from the logs.

{sessions_block}

## Writing Rules

- Create only the files needed for the skill. Usually that means `SKILL.md` only.
- Keep `SKILL.md` concise. Good skills are not long policy documents.
- Do not include a generic "safety rules" section by default.
- Include approval gates only when the workflow genuinely sends, posts, edits financial/legal records, or exposes external/client-visible content.
- If an existing skill overlaps, say when to use the existing skill instead of duplicating it.
- Use concrete workflow steps, not broad advice.
- Include trigger examples, inputs to collect, actual execution flow, output shape, and quality checks.
- Use placeholders for private data: `<name>`, `<company>`, `<email>`, `<phone>`, `<url>`, `<token>`.
- Do not copy raw private log text into the final skill unless it is masked and materially useful.

## Required Output

Write a complete `SKILL.md` to the target file. It must have valid frontmatter:

```yaml
---
name: {brief["skill_name"]}
description: ...
---
```

The description is the main trigger surface, so include realistic trigger phrases. The body should be a practical operating guide for the repeated task, not a retrospective report about this analysis.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clusters", default="/tmp/recurring-task-extractor/clusters.json")
    ap.add_argument("--sessions", default="/tmp/recurring-task-extractor/sessions.jsonl")
    ap.add_argument("--pick", required=True, type=int, help="1-indexed cluster number")
    ap.add_argument("--out-dir", default="/tmp/recurring-task-extractor/ai-briefs")
    ap.add_argument("--max-samples", type=int, default=8)
    args = ap.parse_args()

    clusters = load_json(Path(args.clusters))
    if args.pick < 1 or args.pick > len(clusters):
        print(f"--pick out of range: {args.pick}", file=sys.stderr)
        sys.exit(2)

    cluster = clusters[args.pick - 1]
    sessions = load_sessions(Path(args.sessions))
    category = infer_category(cluster)
    skill_name = generated_skill_name(cluster)
    existing = load_existing_names()
    overlap = sorted(set(
        overlap_existing(skill_name, existing) + overlap_existing(cluster.get("trigger", ""), existing)
    ))[:8]

    out_dir = Path(args.out_dir) / skill_name
    out_dir.mkdir(parents=True, exist_ok=True)
    target_dir = out_dir / "generated" / skill_name
    target_dir.mkdir(parents=True, exist_ok=True)
    target_skill_md = target_dir / "SKILL.md"

    brief = {
        "skill_name": skill_name,
        "category_key": category["key"],
        "category_title": category["title"],
        "trigger": cluster.get("trigger"),
        "count": cluster.get("count"),
        "top_tools": cluster.get("top_tools", []),
        "top_cwds": [safe_sample(c, limit=160) for c in cluster.get("top_cwds", [])],
        "existing_overlap": overlap,
        "target_skill_md": str(target_skill_md),
        "representative_sessions": representative_sessions(cluster, sessions, args.max_samples),
    }

    (out_dir / "brief.json").write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "PROMPT.md").write_text(render_prompt(brief), encoding="utf-8")
    print(json.dumps({
        "brief": str(out_dir / "brief.json"),
        "prompt": str(out_dir / "PROMPT.md"),
        "target_skill_md": str(target_skill_md),
        "representative_session_count": len(brief["representative_sessions"]),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
