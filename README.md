# Claude Skill: Recurring Task Extractor

Claude Codeの過去ログから、何度も頼んでいる反復作業を見つけて、再利用できるSkill候補に変換するメタスキルです。

たとえば、問い合わせ返信、X投稿作成、調査ブリーフ、請求書確認、議事録整理、記事作成のような「毎回似た流れで頼んでいる作業」をログから拾い、次回から呼び出せる `SKILL.md` の下書きにします。

## 何ができるか

- Claude CodeのローカルJSONLログを読み取る
  - `~/.claude/projects`
  - `~/claude-data/projects`
- 各セッションから、最初のユーザー依頼、作業ディレクトリ、使われたツール列を抽出する
- 何度も出てくる作業パターンを検出する
- 候補をランキング形式で表示する
- 候補から `SKILL.md` の下書きを生成する
- AI生成用の `PROMPT.md` / `brief.json` を作り、実ログを材料にしたSkill生成につなげる

## なぜ作ったか

AIエージェントを日常的に使っていると、会話ログには「自分が何度もAIに頼んでいる仕事の型」が溜まっていきます。

ただ、そのログは普通だと後から検索されるだけで、次回以降の能力にはなりません。

このスキルは、ログを単なる履歴としてではなく、次のSkillを作るための材料として扱います。

```text
使う
↓
ログが溜まる
↓
反復作業を見つける
↓
Skill候補にする
↓
次回から作業環境が少し育つ
```

## 動作モード

| モード | 仕組み | 速度 | コスト | 向いている用途 |
|---|---|---:|---:|---|
| `keyword` | キーワード共起で高速に束ねる | 約30秒 | 無料 | まず候補をざっと見る |
| `semantic` | チャンク分割して意味ベースで分析する | 約3〜5分 | 利用環境次第 | 精度高く棚卸しする |

まずは `keyword` で候補を見て、実用化したいものだけAI-backed generationに回すのがおすすめです。

## インストール

このリポジトリをcloneして、Claudeのskillsディレクトリに配置します。

```bash
git clone https://github.com/gonta223/claude-skill-recurring-task-extractor.git /tmp/claude-skill-recurring-task-extractor
mkdir -p ~/.claude/skills
cp -R /tmp/claude-skill-recurring-task-extractor ~/.claude/skills/recurring-task-extractor
```

開発中のリポジトリをそのまま参照したい場合は、シンボリックリンクでも使えます。

```bash
git clone https://github.com/gonta223/claude-skill-recurring-task-extractor.git
ln -s "$PWD/claude-skill-recurring-task-extractor" ~/.claude/skills/recurring-task-extractor
```

スキルとしての起動名は `recurring-task-extractor` です。

## クイックスタート

### 1. ログをスキャンする

```bash
python3 ~/.claude/skills/recurring-task-extractor/scripts/scan_logs.py \
  --days 30 \
  --out /tmp/recurring-task-extractor/sessions.jsonl
```

### 2. 反復作業をクラスタリングする

```bash
python3 ~/.claude/skills/recurring-task-extractor/scripts/cluster_tasks.py \
  --in /tmp/recurring-task-extractor/sessions.jsonl \
  --min-count 5 \
  --top-n 25 \
  --out /tmp/recurring-task-extractor/clusters.json
```

### 3. 候補一覧を見る

```bash
python3 ~/.claude/skills/recurring-task-extractor/scripts/draft_skills.py \
  --in /tmp/recurring-task-extractor/clusters.json \
  --mode list
```

出力例：

```text
# 定期作業候補（25件 / トリガー出現回数の多い順）

## 1. 【記事】 718回 / 生成名: article-drafting-workflow
## 2. 【メール】 275回 / 生成名: inquiry-reply-drafter
## 3. 【投稿】 333回 / 生成名: x-post-drafter
```

### 4. Skillの下書きを生成する

```bash
python3 ~/.claude/skills/recurring-task-extractor/scripts/draft_skills.py \
  --in /tmp/recurring-task-extractor/clusters.json \
  --mode generate \
  --pick 1,3 \
  --out-dir /tmp/recurring-task-extractor/generated/
```

生成されたファイルは次のような場所に出ます。

```text
/tmp/recurring-task-extractor/generated/<skill-name>/SKILL.md
```

### 5. 確認後にインストールする

生成されたSkillは必ず中身を確認してからインストールしてください。

```bash
python3 ~/.claude/skills/recurring-task-extractor/scripts/draft_skills.py \
  --in /tmp/recurring-task-extractor/clusters.json \
  --mode generate \
  --pick 1,3 \
  --out-dir /tmp/recurring-task-extractor/generated/ \
  --install
```

既存スキルを上書きする場合だけ `--force` を付けます。

## AI-backed generation

テンプレート生成は高速ですが、実用的なSkillを作る本命はAI-backed generationです。

指定した候補について、代表セッション、主要ツール、作業場所、既存Skillとの重複をまとめた生成ブリーフを作ります。

```bash
python3 ~/.claude/skills/recurring-task-extractor/scripts/prepare_ai_skill.py \
  --clusters /tmp/recurring-task-extractor/clusters.json \
  --sessions /tmp/recurring-task-extractor/sessions.jsonl \
  --pick 1 \
  --out-dir /tmp/recurring-task-extractor/ai-briefs/
```

出力されるもの：

- `brief.json`: 代表セッションや主要ツールをまとめた構造化データ
- `PROMPT.md`: AIに渡すSkill生成指示
- `generated/<skill-name>/SKILL.md`: 生成先のSkillファイル

`PROMPT.md` を読み、必要なら代表セッションのJSONLを確認してから、最終的な `SKILL.md` を書きます。

## プライバシー

このツールはローカルの会話ログを読みます。出力はデフォルトで非公開扱いにしてください。

生成時には、メールアドレス、電話番号、URL、Xハンドル、トークン、ホームディレクトリなどをマスクします。

追加でマスクしたい固有名詞がある場合は、環境変数で指定できます。

```bash
export RECURRING_TASK_EXTRACTOR_PRIVATE_TERMS="Your Name,Your Company,internal-project-code"
```

公開・共有する前に、必ず以下を確認してください。

- 個人名
- 会社名
- ローカルパス
- メールアドレス、電話番号、SNSハンドル
- APIキー、トークン、シークレット
- クライアント名や非公開の業務内容

## ファイル構成

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

## 必要環境

- Python 3.10以上
- Claude Codeのログ
  - `~/.claude/projects`
  - `~/claude-data/projects`

`keyword` モードでは、追加のPythonパッケージは不要です。

## ライセンス

MIT
