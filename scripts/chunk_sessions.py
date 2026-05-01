#!/usr/bin/env python3
"""Split sessions.jsonl into N chunks for parallel subagent analysis."""
import argparse
import json
import math
from pathlib import Path
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="/tmp/recurring-task-extractor/sessions.jsonl")
    ap.add_argument("--out-dir", default="/tmp/recurring-task-extractor/chunks")
    ap.add_argument("--chunk-size", type=int, default=250,
                    help="Sessions per chunk (default 250)")
    ap.add_argument("--max-chunks", type=int, default=20,
                    help="Hard cap on number of chunks (default 20)")
    args = ap.parse_args()

    inp = Path(args.inp)
    if not inp.exists():
        print(f"[chunk] not found: {inp}", file=sys.stderr)
        sys.exit(1)

    sessions = []
    with inp.open(encoding="utf-8") as f:
        for line in f:
            try:
                sessions.append(json.loads(line))
            except Exception:
                continue
    total = len(sessions)
    if total == 0:
        print("[chunk] no sessions to chunk", file=sys.stderr)
        sys.exit(1)

    n_chunks = min(args.max_chunks, max(1, math.ceil(total / args.chunk_size)))
    chunk_size = math.ceil(total / n_chunks)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Clear old chunks
    for old in out_dir.glob("chunk_*.jsonl"):
        old.unlink()
    for old in out_dir.glob("findings_*.json"):
        old.unlink()

    paths = []
    for i in range(n_chunks):
        chunk = sessions[i * chunk_size : (i + 1) * chunk_size]
        if not chunk:
            continue
        p = out_dir / f"chunk_{i+1:02d}.jsonl"
        with p.open("w", encoding="utf-8") as f:
            for s in chunk:
                # Trim very long prompts to keep subagent context lean
                s2 = dict(s)
                if isinstance(s2.get("first_user_prompt"), str):
                    s2["first_user_prompt"] = s2["first_user_prompt"][:600]
                f.write(json.dumps(s2, ensure_ascii=False) + "\n")
        paths.append(p)

    print(json.dumps({
        "total_sessions": total,
        "n_chunks": len(paths),
        "chunk_size": chunk_size,
        "chunk_paths": [str(p) for p in paths],
        "findings_dir": str(out_dir),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
