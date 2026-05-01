#!/usr/bin/env python3
"""Scan Claude Code JSONL session logs and extract first-user-prompt + tool-sequence per session."""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta

LOG_DIRS = [
    Path.home() / ".claude" / "projects",
    Path.home() / "claude-data" / "projects",
]

SKIP_TAGS = (
    "<observed_from_primary_session>",
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<system-reminder>",
    "<ide_opened_file>",
    "<ide_selection>",
    "<local-command-stdout>",
    "Caveat:",
    "Base directory for this skill:",
    "[Request interrupted",
)

TAG_STRIP_RE = re.compile(r"<ide_opened_file>.*?</ide_opened_file>", re.DOTALL)
TAG_STRIP_RE2 = re.compile(r"<ide_selection>.*?</ide_selection>", re.DOTALL)
SYS_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)


def clean_prompt(text: str) -> str:
    """Strip IDE/system tags out of a user prompt before analysis."""
    cleaned = TAG_STRIP_RE.sub("", text)
    cleaned = TAG_STRIP_RE2.sub("", cleaned)
    cleaned = SYS_REMINDER_RE.sub("", cleaned)
    return cleaned.strip()


def is_real_user_prompt(text: str) -> bool:
    if not text:
        return False
    cleaned = clean_prompt(text)
    if len(cleaned) < 8:
        return False
    for tag in SKIP_TAGS:
        if cleaned.startswith(tag):
            return False
    if cleaned.startswith("Hello memory agent"):
        return False
    if cleaned.startswith("/Users/") and "\n" not in cleaned:
        return False
    return True

def extract_text(content):
    """Return text from a user message content (str or list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text", ""))
        return "\n".join(parts)
    return ""

def scan_session(jsonl_path: Path, max_lines: int = 200):
    """Extract metadata from one JSONL file."""
    first_user = None
    tools = []
    cwd = None
    timestamp = None
    try:
        with jsonl_path.open(encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i > max_lines and first_user:
                    break
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if cwd is None:
                    cwd = obj.get("cwd")
                if timestamp is None and obj.get("timestamp"):
                    timestamp = obj["timestamp"]
                t = obj.get("type")
                if t == "user" and first_user is None:
                    text = extract_text(obj.get("message", {}).get("content", ""))
                    if is_real_user_prompt(text):
                        first_user = clean_prompt(text)[:500]
                elif t == "assistant":
                    msg_content = obj.get("message", {}).get("content", [])
                    if isinstance(msg_content, list):
                        for c in msg_content:
                            if isinstance(c, dict) and c.get("type") == "tool_use":
                                tools.append(c.get("name", "?"))
    except Exception:
        return None
    if not first_user:
        return None
    return {
        "session_id": jsonl_path.stem,
        "path": str(jsonl_path),
        "cwd": cwd,
        "timestamp": timestamp,
        "first_user_prompt": first_user,
        "tools": tools[:30],
        "tool_count": len(tools),
    }

def collect_files(days: int | None):
    cutoff = None if days is None else time.time() - days * 86400
    files = []
    for base in LOG_DIRS:
        if not base.exists():
            continue
        for p in base.rglob("*.jsonl"):
            try:
                if cutoff and p.stat().st_mtime < cutoff:
                    continue
                files.append(p)
            except OSError:
                continue
    return files

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--all", action="store_true", help="Scan entire log history")
    ap.add_argument("--out", default="/tmp/recurring-task-extractor/sessions.jsonl")
    ap.add_argument("--exclude-cwd", action="append", default=[],
                    help="Skip sessions whose cwd contains this substring (repeatable)")
    args = ap.parse_args()

    days = None if args.all else args.days
    files = collect_files(days)
    print(f"[scan] {len(files)} JSONL files to inspect", file=sys.stderr)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    with out_path.open("w", encoding="utf-8") as out:
        for i, p in enumerate(files):
            if i % 1000 == 0 and i > 0:
                print(f"[scan] processed {i}/{len(files)}", file=sys.stderr)
            # exclude observer / memory rebuild sessions
            if "observer-sessions" in str(p) or "memory-rebuild" in str(p):
                continue
            data = scan_session(p)
            if not data:
                continue
            cwd = data.get("cwd") or ""
            skip = False
            for ex in args.exclude_cwd:
                if ex in cwd:
                    skip = True
                    break
            if skip:
                continue
            out.write(json.dumps(data, ensure_ascii=False) + "\n")
            written += 1
    print(f"[scan] {written} sessions written to {out_path}", file=sys.stderr)

if __name__ == "__main__":
    main()
