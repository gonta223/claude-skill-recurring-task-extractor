# Recurring Task Extractor

Turn repeated Claude Code work logs into reusable skill candidates.

`recurring-task-extractor` scans local Claude Code JSONL session logs, finds workflows you keep asking for, and turns them into candidate `SKILL.md` files. The intent is to make your agent environment improve from its own usage history.

## What It Does

- Scans local Claude Code logs from:
  - `~/.claude/projects`
  - `~/claude-data/projects`
- Extracts each session's first real user request, cwd, and tool sequence.
- Finds recurring work patterns in two modes:
  - `keyword`: fast, free keyword-based clustering.
  - `semantic`: higher-quality chunking workflow designed for parallel subagent review.
- Renders a ranked candidate list.
- Generates draft skills either from a template fallback or via an AI-backed brief.
- Keeps installation explicit so low-quality generated skills do not silently enter your environment.

## Install

Clone the repo and place it in your Claude skills directory:

```bash
git clone https://github.com/gonta223/recurring-task-extractor.git
mkdir -p ~/.claude/skills
cp -R recurring-task-extractor ~/.claude/skills/recurring-task-extractor
```

If you prefer to develop from the clone:

```bash
ln -s "$PWD/recurring-task-extractor" ~/.claude/skills/recurring-task-extractor
```

## Quick Start

Scan recent logs:

```bash
python3 ~/.claude/skills/recurring-task-extractor/scripts/scan_logs.py \
  --days 30 \
  --out /tmp/recurring-task-extractor/sessions.jsonl
```

Cluster repeated work:

```bash
python3 ~/.claude/skills/recurring-task-extractor/scripts/cluster_tasks.py \
  --in /tmp/recurring-task-extractor/sessions.jsonl \
  --min-count 5 \
  --top-n 25 \
  --out /tmp/recurring-task-extractor/clusters.json
```

Show candidates:

```bash
python3 ~/.claude/skills/recurring-task-extractor/scripts/draft_skills.py \
  --in /tmp/recurring-task-extractor/clusters.json \
  --mode list
```

Generate draft skills:

```bash
python3 ~/.claude/skills/recurring-task-extractor/scripts/draft_skills.py \
  --in /tmp/recurring-task-extractor/clusters.json \
  --mode generate \
  --pick 1,3 \
  --out-dir /tmp/recurring-task-extractor/generated/
```

Install generated skills only after review:

```bash
python3 ~/.claude/skills/recurring-task-extractor/scripts/draft_skills.py \
  --in /tmp/recurring-task-extractor/clusters.json \
  --mode generate \
  --pick 1,3 \
  --out-dir /tmp/recurring-task-extractor/generated/ \
  --install
```

## AI-Backed Skill Generation

The template generator is useful for quick drafts, but the better path is AI-backed generation.

```bash
python3 ~/.claude/skills/recurring-task-extractor/scripts/prepare_ai_skill.py \
  --clusters /tmp/recurring-task-extractor/clusters.json \
  --sessions /tmp/recurring-task-extractor/sessions.jsonl \
  --pick 1 \
  --out-dir /tmp/recurring-task-extractor/ai-briefs/
```

This produces:

- `brief.json`: structured summary of representative sessions.
- `PROMPT.md`: prompt for generating a real skill from evidence.
- `generated/<skill-name>/SKILL.md`: target path for the generated skill.

Read `PROMPT.md`, inspect representative logs when needed, then write the final `SKILL.md`.

## Privacy Notes

This tool reads local conversation logs. Treat its output as private by default.

The generator masks common sensitive fields such as emails, phone numbers, URLs, handles, tokens, and local home paths. You can add your own terms to mask:

```bash
export RECURRING_TASK_EXTRACTOR_PRIVATE_TERMS="Your Name,Your Company,internal-project-code"
```

Before sharing generated candidates or generated skills publicly, review them for:

- Real names
- Company names
- Local paths
- Emails, phone numbers, handles
- Tokens, API keys, secrets
- Client-visible or confidential work details

## Repository Layout

```text
.
├── SKILL.md
├── agents/
│   └── openai.yaml
└── scripts/
    ├── chunk_sessions.py
    ├── cluster_tasks.py
    ├── draft_skills.py
    ├── prepare_ai_skill.py
    └── scan_logs.py
```

## Requirements

- Python 3.10+
- Claude Code logs in `~/.claude/projects` or `~/claude-data/projects`

No third-party Python packages are required for the default keyword mode.

## License

MIT
