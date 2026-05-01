#!/usr/bin/env python3
"""Cluster sessions by trigger keywords + tool signatures, find recurring tasks."""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Stop words to ignore in keyword extraction
STOP_KANJI = {
    "今", "後", "前", "中", "時", "日", "月", "年", "回", "個", "件", "件目",
    "私", "俺", "自分", "君", "彼",
    "事", "物", "者", "場合", "場所", "感じ", "状態",
    "確認", "実行", "出力", "表示", "情報", "内容", "作成", "以下", "対応",
    "結果", "問題", "必要", "可能", "重要", "詳細", "全体", "全部", "一部",
    "今回", "今日", "明日", "昨日", "本日", "現在", "最新", "最近", "今後",
    "時間", "返信", "添付", "送信", "削除", "追加", "変更", "編集",
}
STOP_KATAKANA = {
    "ファイル", "フォルダ", "コード", "メッセージ", "テキスト", "リスト",
    "テーブル", "セル", "データ", "ユーザー", "アカウント", "メモ",
    "プロジェクト", "オプション", "クエリ", "アプリ", "サービス",
    "システム", "ページ", "サイト", "コマンド", "プロンプト", "リクエスト",
    "アクセス", "アクション", "クライアント", "サーバー", "リソース", "コンテンツ",
}
STOP_ALPHA = {
    "the", "and", "for", "with", "that", "this", "you", "have", "from",
    "what", "how", "can", "are", "was", "were", "but", "not",
    "all", "any", "use", "using", "claude", "code", "file", "files",
    "ok", "yes", "no", "ai", "url", "api", "json", "txt",
    "see", "want", "need", "make", "made", "get", "got", "let",
    "today", "now", "test", "run", "user", "search", "bash", "agent",
    "google", "gemini", "websearch", "python", "python3",
    "node", "npm", "git", "github", "tldv", "slack", "notion", "command",
    "skill", "skills", "tools", "tool", "input", "output", "result", "results",
    "users", "library", "cloudstorage", "googledrive",
    "ide", "opened", "may", "related", "current", "task",
    "drive", "share", "doc", "docs",
    "image", "images", "png", "jpg", "pdf", "csv", "html", "css",
    "true", "false", "null", "none", "self", "main", "auto",
    "show", "list", "create", "delete", "update", "add", "fix",
    "claude-data", "config", "settings", "https",
}

# Slash command at start of line (real /command), not embedded path
SLASH_CMD_RE = re.compile(r"(?:^|\s)/([a-zA-Z][a-zA-Z0-9-]{2,30})", re.MULTILINE)
URL_RE = re.compile(r"https?://\S+")
EMAIL_RE = re.compile(r"\b[\w._%+-]+@[\w.-]+\.[a-zA-Z]{2,}\b")
DOMAIN_RE = re.compile(r"\b[a-zA-Z0-9-]+\.(?:com|co\.jp|jp|io|ai|net|org|app)\b")
PATH_RE = re.compile(r"/[\w./-]{3,}")
HEX_RE = re.compile(r"\b[0-9a-f]{8,}\b")
DATE_RE = re.compile(r"\b\d{4}[-/年]?\d{1,2}[-/月]?\d{0,2}日?\b|\b\d{1,2}[:時]\d{2}\b")

# Japanese keyword extraction: 2-4 char kanji compounds, katakana words
KANJI_NGRAM_RE = re.compile(r"[一-鿿]{2,5}")
KATAKANA_RE = re.compile(r"[ァ-ヺー]{3,}")
ALPHA_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9-]{2,20}")

# Action verbs that often signal a recurring workflow when followed/preceded by a noun
ACTION_KANJI = {
    "投稿", "返信", "送信", "発行", "請求", "議事", "要約", "翻訳",
    "提案", "契約", "見積", "資料", "図解", "動画", "音声", "画像",
    "リサーチ", "リライト", "校正", "確認", "添削", "整理", "整形",
    "Slack", "Gmail", "Notion", "Linear", "PR TIMES",
}

PROMPT_LIMIT_FOR_KEYWORDS = 800


def extract_keywords(text: str) -> list[str]:
    """Extract candidate trigger keywords from a user prompt."""
    # Truncate very long prompts; the head usually carries the intent
    t = text[:PROMPT_LIMIT_FOR_KEYWORDS]

    # Strip noise sources
    t = URL_RE.sub(" ", t)
    t = EMAIL_RE.sub(" ", t)
    t = DOMAIN_RE.sub(" ", t)
    t = HEX_RE.sub(" ", t)
    t = DATE_RE.sub(" ", t)

    # Slash commands BEFORE removing paths
    slash_cmds = []
    for m in SLASH_CMD_RE.findall(text[:PROMPT_LIMIT_FOR_KEYWORDS]):
        low = m.lower()
        if low in STOP_ALPHA:
            continue
        slash_cmds.append(f"/{low}")

    t = PATH_RE.sub(" ", t)

    keywords = list(slash_cmds)

    # Kanji compounds
    for m in KANJI_NGRAM_RE.findall(t):
        if m not in STOP_KANJI:
            keywords.append(m)

    # Katakana words
    for m in KATAKANA_RE.findall(t):
        if m not in STOP_KATAKANA:
            keywords.append(m)

    # Alpha words (lowercase, deduped per session)
    seen_alpha = set()
    for m in ALPHA_RE.findall(t):
        low = m.lower()
        if low in STOP_ALPHA:
            continue
        if low in seen_alpha:
            continue
        seen_alpha.add(low)
        keywords.append(low)

    return keywords

def normalize_tool_signature(tools: list[str]) -> str:
    """Compress tool sequence into a coarse signature (top-3 most-used tools)."""
    if not tools:
        return "(no tools)"
    counts = Counter(tools).most_common(3)
    return " + ".join(f"{name}" for name, _ in counts)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="/tmp/recurring-task-extractor/sessions.jsonl")
    ap.add_argument("--min-count", type=int, default=3)
    ap.add_argument("--out", default="/tmp/recurring-task-extractor/clusters.json")
    ap.add_argument("--top-n", type=int, default=50, help="Top N keywords to inspect")
    args = ap.parse_args()

    sessions = []
    with open(args.inp, encoding="utf-8") as f:
        for line in f:
            try:
                sessions.append(json.loads(line))
            except Exception:
                continue
    print(f"[cluster] loaded {len(sessions)} sessions", file=sys.stderr)

    # Build keyword -> session indices
    kw_to_sessions: dict[str, list[int]] = defaultdict(list)
    for idx, s in enumerate(sessions):
        prompt = s.get("first_user_prompt", "")
        seen_in_session = set()
        for kw in extract_keywords(prompt):
            if kw in seen_in_session:
                continue
            seen_in_session.add(kw)
            kw_to_sessions[kw].append(idx)

    # Filter to keywords appearing in >= min-count distinct sessions
    candidates = [
        (kw, idxs) for kw, idxs in kw_to_sessions.items() if len(idxs) >= args.min_count
    ]
    candidates.sort(key=lambda x: -len(x[1]))

    # Drop subsumed keywords: if kw_a's sessions are mostly a superset of kw_b's, prefer kw_a
    # Simple greedy: keep top N by count, then prune ones whose sessions are >=80% covered by an already-kept one
    kept: list[tuple[str, list[int]]] = []
    for kw, idxs in candidates[: args.top_n * 3]:
        idx_set = set(idxs)
        subsumed = False
        for k_kw, k_idxs in kept:
            k_set = set(k_idxs)
            overlap = len(idx_set & k_set)
            if overlap / max(1, len(idx_set)) >= 0.8 and len(k_set) >= len(idx_set):
                subsumed = True
                break
        if not subsumed:
            kept.append((kw, idxs))
        if len(kept) >= args.top_n:
            break

    # Build cluster output
    clusters = []
    for kw, idxs in kept:
        sample_sessions = [sessions[i] for i in idxs[:5]]
        all_tools = []
        cwds = Counter()
        for i in idxs:
            all_tools.extend(sessions[i].get("tools", []))
            cwd = sessions[i].get("cwd")
            if cwd:
                cwds[cwd] += 1
        clusters.append({
            "trigger": kw,
            "count": len(idxs),
            "tool_signature": normalize_tool_signature(all_tools),
            "top_tools": [t for t, _ in Counter(all_tools).most_common(5)],
            "top_cwds": [c for c, _ in cwds.most_common(3)],
            "samples": [
                {
                    "session_id": s.get("session_id"),
                    "path": s.get("path"),
                    "prompt": s.get("first_user_prompt", "")[:200],
                    "timestamp": s.get("timestamp"),
                    "cwd": s.get("cwd"),
                }
                for s in sample_sessions
            ],
        })

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(clusters, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[cluster] {len(clusters)} candidate clusters → {out_path}", file=sys.stderr)

if __name__ == "__main__":
    main()
