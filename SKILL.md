---
name: recurring-task-extractor
description: 自分のClaude Codeログから3回以上繰り返している作業を洗い出し、実用できるエージェントスキルを生成する。「定期作業洗い出し」「ログから繰り返し作業」「スキル化候補出して」「この作業をスキル化して」と言われたら起動。2モード搭載 (keyword=高速30秒・無料 / semantic=並列サブエージェント・3-5分・$1〜$3)。出力は候補リスト、生成SKILL.md、実インストールはユーザー承認後
---

# Recurring Task Extractor

過去のClaude Codeセッションログから、繰り返している作業を抽出して実用スキルを生成する「メタスキル」。

重要: `keyword` モードの自動生成はテンプレートベースの fallback。実用スキルを作る本命は、代表セッションを集めてAIに書かせる `AI-backed generation`。

## 2つの動作モード

| モード | 仕組み | 速度 | コスト | 精度 |
|--------|--------|------|--------|------|
| `keyword` | キーワード共起カウント | ~30秒 | 0 | 粗（言い回しの揺れに弱い） |
| `semantic` | 並列サブエージェントが意味で束ねる | ~3-5分 | $1〜$3 | 高（「請求書出して」と「インボイス送って」を同一視） |

迷ったら `semantic` 推奨。月1回の棚卸しなら十分元取れる。

## いつ起動するか

- 「最近よくやってる作業を自動化したい」
- 「自分のログ見て、スキル化できそうなやつ出して」
- 月次棚卸し

## 共通: Phase 1 ログスキャン

両モード共通の前処理。直近30日のセッションから「ユーザーの最初の依頼」と「使ったツール一覧」を抽出。

```bash
python3 ~/.claude/skills/recurring-task-extractor/scripts/scan_logs.py \
  --days 30 \
  --out /tmp/recurring-task-extractor/sessions.jsonl
```

オプション: `--days N`（直近N日、デフォ30）/ `--all`（全期間）

---

## モードA: `keyword`（高速・無料）

### Phase 2: クラスタリング

```bash
python3 ~/.claude/skills/recurring-task-extractor/scripts/cluster_tasks.py \
  --in /tmp/recurring-task-extractor/sessions.jsonl \
  --min-count 5 \
  --top-n 25 \
  --out /tmp/recurring-task-extractor/clusters.json
```

### Phase 3: 候補リスト表示

```bash
python3 ~/.claude/skills/recurring-task-extractor/scripts/draft_skills.py \
  --in /tmp/recurring-task-extractor/clusters.json \
  --mode list
```

---

## モードB: `semantic`（並列サブエージェント）

### Phase 2: チャンク分割

```bash
python3 ~/.claude/skills/recurring-task-extractor/scripts/chunk_sessions.py \
  --in /tmp/recurring-task-extractor/sessions.jsonl \
  --chunk-size 250 \
  --max-chunks 15 \
  --out-dir /tmp/recurring-task-extractor/chunks
```

出力: `chunk_01.jsonl` ... `chunk_15.jsonl`

### Phase 3: 並列サブエージェント分析（**Claude Code側でAgent toolを使う**）

このスキルが起動した時点で、Claude（あなた）は**全チャンクを並列でAgent toolにディスパッチ**します。

各サブエージェント (`subagent_type: general-purpose`) に**全く同じ指示テンプレート**で投げる。チャンクパスだけ置換:

```text
あなたは過去Claude Codeセッションログを分析するアナリストです。

入力: {CHUNK_PATH}

このJSONLファイルには {N} 件のセッション要約が入っています（各行 JSON: session_id, first_user_prompt, tools, cwd, timestamp）。

タスク:
1. 全セッションを読み、「同じ目的の作業を繰り返し依頼している」パターンを意味で抽出する
2. 単発・1回限りの作業はスキップ
3. 2件以上の類似セッションがあるパターンのみ採用
4. パターンごとに以下のJSONを返す:

{
  "patterns": [
    {
      "name": "問い合わせ返信ドラフト",
      "description": "問い合わせフォームから来たメールに対して返信文を考える作業",
      "trigger_phrases": ["これ返信考えて", "問い合わせどう答える", "コレ返信"],
      "sample_session_ids": ["xxx", "yyy", "zzz"],
      "approximate_count": 8,
      "suggested_skill_name": "inquiry-reply-drafter",
      "suggested_tools": ["Bash", "Read", "WebSearch"]
    }
  ]
}

出力先: {FINDINGS_PATH}（JSONを書き込み）

書き込み完了したら "OK: written N patterns to {FINDINGS_PATH}" とだけ返す。本文での説明は不要。
```

`{FINDINGS_PATH}` は `/tmp/recurring-task-extractor/chunks/findings_NN.json`（NNはチャンク番号）。

**重要**: 全チャンクを**1メッセージ内で全て同時にAgent tool callする**（並列実行）。順次実行すると遅い。

### Phase 4: 統合（Claudeが直接マージ）

全 findings_*.json をRead（並列）→ Claudeが意味で束ねる:
- `name` が類似なら同一パターン扱い（例: 「請求書発行」と「請求書作成」は同一）
- `approximate_count` を足し合わせ
- `sample_session_ids` を結合（重複除去）
- 統合結果を以下の形式で `/tmp/recurring-task-extractor/clusters.json` に書き出す（`draft_skills.py`互換）:

```json
[
  {
    "trigger": "問い合わせ返信ドラフト",
    "count": 12,
    "tool_signature": "Bash + Read + WebSearch",
    "top_tools": ["Bash","Read","WebSearch"],
    "top_cwds": ["..."],
    "samples": [
      {"prompt": "...", "timestamp": "...", "cwd": "..."}
    ],
    "_semantic": {
      "description": "...",
      "trigger_phrases": [...],
      "suggested_skill_name": "..."
    }
  }
]
```

### Phase 5: 候補リスト提示

```bash
python3 ~/.claude/skills/recurring-task-extractor/scripts/draft_skills.py \
  --in /tmp/recurring-task-extractor/clusters.json \
  --mode list
```

---

## 採用判定 → AI-backed generation（推奨）

ユーザーが「1番と3番を採用」のように指定したら:

```bash
python3 ~/.claude/skills/recurring-task-extractor/scripts/prepare_ai_skill.py \
  --clusters /tmp/recurring-task-extractor/clusters.json \
  --sessions /tmp/recurring-task-extractor/sessions.jsonl \
  --pick 1 \
  --out-dir /tmp/recurring-task-extractor/ai-briefs/
```

出力される:
- `brief.json`: 代表セッション、既存スキル重複、主要ツール、作業場所
- `PROMPT.md`: AIに渡す生成指示
- `generated/<skill-name>/SKILL.md`: AIが書く対象ファイル

Claude/Codexは `PROMPT.md` を読み、必要なら代表セッションJSONLを数件読んでから、`target_skill_md` に `SKILL.md` を書く。

このAI生成では、汎用テンプレではなく、過去セッションから以下を抽出して書く:
- 実際の起動文
- 実際に使っていたツール
- よく見る入力素材
- 成功した作業手順
- 出力の形
- 既存スキルとの棲み分け
- 失敗しやすい点

原則として、汎用的な「安全ルール」節は入れない。外部送信、投稿、財務・法務編集など実際に必要な場合だけ承認ゲートを短く入れる。

## 採用判定 → テンプレート生成（fallback）

AI-backed generationを使わずに素早く骨組みを出す場合:

```bash
python3 ~/.claude/skills/recurring-task-extractor/scripts/draft_skills.py \
  --in /tmp/recurring-task-extractor/clusters.json \
  --mode generate \
  --pick 1,3 \
  --out-dir /tmp/recurring-task-extractor/generated/
```

テンプレート生成されたスキルは `/tmp/recurring-task-extractor/generated/<name>/SKILL.md` に出力される。

生成される `SKILL.md` には以下が入る:
- 起動条件
- 目的
- 入力確認項目
- 先に確認する関連リソース
- 実行手順
- 詳細プレイブック
- 出力フォーマット
- 出力テンプレート
- 主要ツール
- 主な作業場所
- PIIマスク済みのログサンプル
- 品質ゲート
- よくある失敗
- 完了条件

`draft` モードは後方互換のため残っており、内部的には `generate` と同じ実用SKILL.mdを生成する。

明示的に安全ルール節も入れたい場合だけ `--include-safety` を付ける。

### インストール

レビュー後、ユーザーが採用を明示した場合だけ `~/.claude/skills/` へコピーする:

```bash
python3 ~/.claude/skills/recurring-task-extractor/scripts/draft_skills.py \
  --in /tmp/recurring-task-extractor/clusters.json \
  --mode generate \
  --pick 1,3 \
  --out-dir /tmp/recurring-task-extractor/generated/ \
  --install
```

既存スキルを上書きする必要がある場合のみ `--force` を付ける。

## 重要ルール

- **自動でSKILL.mdをインストールしない**（低品質スキル乱立防止）
- **ユーザー承認後のみ ~/.claude/skills/ へ実ファイル化**
- 既存スキルと重複してないか必ず確認（`ls ~/.claude/skills/`）
- セマンティックモードのコストはユーザーに事前告知（"$1〜$3 かかります、いいですか？"）
- ログ由来サンプルは個人情報、メールアドレス、電話番号、URL、トークン、Xハンドルをマスクしてから出す

## トラブルシュート

- セッション数が少ない（< 100）→ `keyword` モードで十分
- セマンティックでサブエージェントが timeout → `--chunk-size` を小さくして `--max-chunks` 増やす
- ストップワードが効いてない → `cluster_tasks.py` の `STOP_*` セットを編集

## 配布性メモ

このスキル本体はポータブル。公開・共有する場合は、ログ由来サンプルに含まれる固有名詞、ローカルパス、連絡先、URL、トークンを必ずマスクすること。
