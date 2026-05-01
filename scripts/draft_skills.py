#!/usr/bin/env python3
"""Render recurring-task clusters as candidate lists or generated SKILL.md files."""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

EXISTING_SKILLS_DIR = Path.home() / ".claude" / "skills"
EXISTING_COMMANDS_DIR = Path.home() / ".claude" / "commands"

EMAIL_RE = re.compile(r"\b[\w._%+-]+@[\w.-]+\.[a-zA-Z]{2,}\b")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\-() ]{8,}\d)(?!\d)")
URL_RE = re.compile(r"https?://[^\s)）>]+")
HANDLE_RE = re.compile(r"(?<![\w.])@[A-Za-z0-9_]{2,20}")
SLACK_MENTION_RE = re.compile(r"@[^\s:：,、，]+")
NAME_WITH_SUFFIX_RE = re.compile(r"([一-鿿]{2,5})(さん|様|くん|君)")
JA_NAME_SEPARATOR_RE = re.compile(r"[一-鿿]{2,5}(?=｜)")
JA_NAME_ROMAN_RE = re.compile(r"[一-鿿]{2,5}\s+[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?")
COMPANY_RE = re.compile(r"株式会社[^\s、。，]+|[A-Za-z0-9_-]+様_連携用")
PRIVATE_TERMS_ENV = "RECURRING_TASK_EXTRACTOR_PRIVATE_TERMS"
TOKEN_RE = re.compile(
    r"(?i)\b(?:bearer|authorization|api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+"
)
FIELD_REPLACEMENTS = [
    (re.compile(r"(?m)^(氏名|名前|お名前)\s*[:：]\s*.+$"), r"\1: <name>"),
    (re.compile(r"(?m)^(会社名|法人名|組織名)\s*[:：]\s*.+$"), r"\1: <company>"),
    (re.compile(r"(?m)^(メールアドレス|メール|Email|E-mail)\s*[:：]\s*.+$"), r"\1: <email>"),
    (re.compile(r"(?m)^(電話番号|TEL|Tel|Phone)\s*[:：]\s*.+$"), r"\1: <phone>"),
]

CATEGORY_RULES = [
    {
        "key": "email_response",
        "match": ("メール", "返信", "問い合わせ", "受信トレイ", "gmail", "mail", "inquiry"),
        "name": "inquiry-reply-drafter",
        "title": "問い合わせ・メール返信ドラフト",
        "description": "問い合わせメールや受信文面を読み、文脈確認、返信方針、送信前確認用の返信ドラフトを作る。",
    },
    {
        "key": "x_post",
        "match": ("投稿", "ポスト", "twitter", "tweet", "x投稿", "typefully", "スレッド"),
        "name": "x-post-drafter",
        "title": "X投稿・スレッド作成",
        "description": "素材や調査結果をX向けの投稿文、スレッド、投稿前確認ペイロードに変換する。",
    },
    {
        "key": "research",
        "match": ("調査", "検索", "リサーチ", "調べ", "research", "websearch", "find"),
        "name": "research-brief-maker",
        "title": "調査ブリーフ作成",
        "description": "与えられた問いや素材について、必要な検索、出典確認、要点整理、次アクション提示を行う。",
    },
    {
        "key": "invoice",
        "match": ("請求", "入金", "領収", "invoice", "stripe", "売上"),
        "name": "invoice-status-checker",
        "title": "請求書・入金状況確認",
        "description": "請求書、領収書、入金予定、支払期限を確認し、未対応や確認事項を整理する。",
    },
    {
        "key": "meeting_notes",
        "match": ("議事", "mtg", "meeting", "transcript", "会議", "ネクストアクション"),
        "name": "meeting-notes-builder",
        "title": "議事録・ネクストアクション整理",
        "description": "会議ログや文字起こしから、決定事項、論点、TODO、担当者、期限を整理する。",
    },
    {
        "key": "article",
        "match": ("記事", "執筆", "メディア", "seo", "原稿", "ライティング", "zenn", "qiita"),
        "name": "article-drafting-workflow",
        "title": "記事・メディア原稿作成",
        "description": "記事テーマ、参考資料、SEO条件をもとに構成、本文、ファクトチェック、納品形式を作る。",
    },
    {
        "key": "slide",
        "match": ("資料", "スライド", "営業資料", "marp", "presentation", "slides"),
        "name": "slide-material-builder",
        "title": "資料・スライド作成",
        "description": "目的、対象者、素材から資料構成、スライド本文、確認観点を作成する。",
    },
    {
        "key": "training",
        "match": ("研修", "講座", "カリキュラム", "教材", "training"),
        "name": "training-material-helper",
        "title": "研修・講座設計補助",
        "description": "研修や講座の対象、ゴール、教材素材を整理し、構成案、演習案、次アクションを作る。",
    },
]

def load_existing_names() -> set[str]:
    names = set()
    for base in (EXISTING_SKILLS_DIR, EXISTING_COMMANDS_DIR):
        if not base.exists():
            continue
        for p in base.iterdir():
            if p.is_dir():
                names.add(p.name.lower())
            elif p.suffix == ".md":
                names.add(p.stem.lower())
    return names

def overlap_existing(trigger: str, existing: set[str]) -> list[str]:
    """Return names of existing skills/commands that may overlap with this trigger."""
    t = trigger.lower().lstrip("/")
    hits = []
    for name in existing:
        if t in name or name in t:
            hits.append(name)
    return sorted(set(hits))[:5]

def slugify(trigger: str) -> str:
    s = trigger.lstrip("/").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s or f"recurring-task-{hashlib.sha1(trigger.encode('utf-8')).hexdigest()[:8]}"

def one_line(text: str, limit: int = 220) -> str:
    """Collapse text for frontmatter / candidate display."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"

def load_private_terms() -> list[str]:
    """Load user-specific private terms to mask from an optional env var."""
    raw = os.environ.get(PRIVATE_TERMS_ENV, "")
    return [term.strip() for term in raw.split(",") if term.strip()]

def redact_sensitive(text: str) -> str:
    """Remove private data from log-derived examples before rendering."""
    if not text:
        return ""
    redacted = text
    home = Path.home()
    redacted = redacted.replace(str(home), "~")
    if home.name:
        redacted = redacted.replace(home.name, "<user>")
    redacted = TOKEN_RE.sub("<secret>", redacted)
    redacted = EMAIL_RE.sub("<email>", redacted)
    redacted = PHONE_RE.sub(mask_phone_candidate, redacted)
    redacted = URL_RE.sub("<url>", redacted)
    redacted = SLACK_MENTION_RE.sub("<mention>", redacted)
    redacted = HANDLE_RE.sub("<handle>", redacted)
    redacted = NAME_WITH_SUFFIX_RE.sub(r"<name>\2", redacted)
    redacted = JA_NAME_SEPARATOR_RE.sub("<name>", redacted)
    redacted = JA_NAME_ROMAN_RE.sub("<name>", redacted)
    redacted = COMPANY_RE.sub("<company>", redacted)
    for term in load_private_terms():
        redacted = redacted.replace(term, "<private>")
    for pattern, repl in FIELD_REPLACEMENTS:
        redacted = pattern.sub(repl, redacted)
    return redacted.strip()

def mask_phone_candidate(match: re.Match) -> str:
    candidate = match.group(0)
    digits = re.sub(r"\D", "", candidate)
    if len(digits) < 10:
        return candidate
    if re.match(r"20\d{2}[-/]\d{1,2}[-/]\d{1,2}", candidate):
        return candidate
    return "<phone>"

def safe_sample(prompt: str, limit: int = 180) -> str:
    return one_line(redact_sensitive(prompt), limit=limit)

def semantic_meta(cluster: dict) -> dict:
    meta = cluster.get("_semantic")
    return meta if isinstance(meta, dict) else {}

def cluster_text(cluster: dict) -> str:
    sample_text = " ".join(str(s.get("prompt", "")) for s in cluster.get("samples", []))
    meta = semantic_meta(cluster)
    return " ".join([
        str(cluster.get("trigger", "")),
        str(meta.get("description", "")),
        " ".join(str(x) for x in meta.get("trigger_phrases", []) if isinstance(x, str)),
        sample_text,
    ]).lower()

def rule_matches(rule: dict, text: str) -> bool:
    return any(word.lower() in text for word in rule["match"])

def infer_category(cluster: dict) -> dict:
    meta = semantic_meta(cluster)
    priority_text = " ".join([
        str(cluster.get("trigger", "")),
        str(meta.get("description", "")),
        " ".join(str(x) for x in meta.get("trigger_phrases", []) if isinstance(x, str)),
    ]).lower()
    for rule in CATEGORY_RULES:
        if rule_matches(rule, priority_text):
            return rule
    text = cluster_text(cluster)
    for rule in CATEGORY_RULES:
        if rule_matches(rule, text):
            return rule
    trigger = str(cluster.get("trigger", "task"))
    return {
        "key": "generic",
        "match": (),
        "name": slugify(trigger),
        "title": trigger,
        "description": f"{trigger} に関連する反復作業を、過去ログの実例に沿って再現する。",
    }

def generated_skill_name(cluster: dict) -> str:
    meta = semantic_meta(cluster)
    suggested = str(meta.get("suggested_skill_name", "")).strip()
    if suggested:
        return slugify(suggested)
    return infer_category(cluster)["name"]

def render_trigger_phrases(cluster: dict) -> list[str]:
    meta = semantic_meta(cluster)
    phrases = []
    for phrase in meta.get("trigger_phrases", []):
        if isinstance(phrase, str):
            phrases.append(safe_sample(phrase, limit=80))
    phrases.append(safe_sample(str(cluster.get("trigger", "")), limit=80))
    for sample in cluster.get("samples", [])[:4]:
        prompt = sample.get("prompt", "")
        if prompt:
            phrases.append(safe_sample(prompt, limit=80))
    deduped = []
    seen = set()
    for phrase in phrases:
        if not phrase or phrase in seen:
            continue
        seen.add(phrase)
        deduped.append(phrase)
    return deduped[:6]

def category_workflow(category_key: str) -> tuple[list[str], str, list[str]]:
    """Return workflow steps, output format, and extra safety rules."""
    workflows = {
        "email_response": (
            [
                "依頼文から送信元、相手の目的、期待されている返答、期限、添付やURLの有無を整理する。",
                "必要に応じて過去資料、関連メール、Web情報を確認し、事実と未確認事項を分ける。",
                "返信方針を1-3行で示し、相手に送る本文とは分離して書く。",
                "敬語、温度感、次アクション、日程候補、確認事項を含む返信ドラフトを作る。",
                "外部送信が必要な場合は、宛先、件名、本文、添付を明示して、送信前にユーザーの明示承認を待つ。",
            ],
            "返信方針 / 件名案 / 本文案 / 確認事項 / 送信前チェック",
            ["外部メール送信は実行しない。送信する場合は明示承認後のみ。"],
        ),
        "x_post": (
            [
                "素材の主張、一次情報、数字、固有名詞、引用元URLを確認する。",
                "投稿の目的を、速報、解説、体験談、思想、比較、まとめのどれかに分類する。",
                "ユーザーの文体ルールや既存の投稿スキルがある場合は先に読む。",
                "1投稿またはスレッドとして、フック、具体例、判断、締めを作る。",
                "公開前に投稿先、本文、画像、引用URLをまとめて提示し、明示承認を待つ。",
            ],
            "投稿方針 / X本文 / スレッド分割 / ファクトチェック結果 / 投稿前確認ペイロード",
            ["X、Typefullyなど外部投稿は明示承認後のみ。"],
        ),
        "research": (
            [
                "依頼の問い、調査対象、期待する粒度、納期、使い道を整理する。",
                "新しい情報や変わりやすい情報はWeb検索し、公式情報や一次ソースを優先する。",
                "調査メモを、事実、推測、未確認、判断に分けて整理する。",
                "ユーザーが意思決定できるように、結論、根拠、リスク、次アクションを短くまとめる。",
                "出典URL、確認日時、未確認点を残す。",
            ],
            "結論 / 根拠 / 詳細メモ / リスク・不明点 / 次アクション / 参照元",
            ["古い可能性がある情報は、必ず現在情報を確認してから断定する。"],
        ),
        "invoice": (
            [
                "対象期間、取引先、確認したい区分（請求済み、未請求、入金済み、未入金）を確認する。",
                "指定フォルダや既存の命名規則に従って、請求書、領収書、入金管理ファイルを探す。",
                "金額、支払期限、ステータス、ファイルパス、未確認事項を一覧化する。",
                "不足ファイル、二重計上の疑い、期限超過、要確認相手を分けて報告する。",
                "財務・法務ファイルは勝手に変更せず、編集が必要な場合は変更案を先に提示する。",
            ],
            "サマリー / 明細表 / 未入金・期限超過 / 不足資料 / 要確認事項",
            ["財務ファイルの編集や外部送付は、必ず明示承認後に行う。"],
        ),
        "meeting_notes": (
            [
                "会議名、日時、参加者、関連資料、文字起こしの場所を特定する。",
                "発言ログから決定事項、論点、未決事項、担当者、期限を抽出する。",
                "冗長な会話を削り、業務で使える粒度の議事録に整える。",
                "ネクストアクションは、担当、期限、依存関係、確認先を明示する。",
                "外部共有する前に、クライアント名、個人情報、社内事情が含まれていないか確認する。",
            ],
            "会議概要 / 決定事項 / 議論内容 / TODO / 未決事項 / 共有前注意",
            ["クライアント共有資料にする場合は、公開範囲を確認する。"],
        ),
        "article": (
            [
                "記事の目的、媒体、読者、キーワード、文字数、納品形式を確認する。",
                "既存記事や競合記事を確認し、被り、独自性、一次情報を整理する。",
                "構成案を作り、見出しごとの主張、根拠、具体例を決める。",
                "本文を作成し、数字、固有名詞、引用、リンクをファクトチェックする。",
                "納品前にトーン、表記、SEO観点、重複、公開リスクを確認する。",
            ],
            "記事方針 / 構成案 / 本文 / ファクトチェック / 修正メモ",
            ["第三者記事の表現をそのまま流用しない。出典と自分の説明を分ける。"],
        ),
        "slide": (
            [
                "資料の目的、相手、利用シーン、制約、ブランドルールを確認する。",
                "既存資料や近い事例を探し、構成、トーン、デザインの前例を把握する。",
                "全体ストーリー、各スライドのメッセージ、必要な図解や表を決める。",
                "スライド本文または生成用Markdownを作成し、表現の過不足を確認する。",
                "最終確認として、誤字、情報密度、クライアント可視情報、画像権利を点検する。",
            ],
            "資料方針 / スライド構成 / 各ページ本文 / 図解案 / 確認事項",
            ["クライアント名や非公開情報を外部資料に入れる前に確認する。"],
        ),
        "training": (
            [
                "対象者、前提知識、到達目標、時間、形式、演習有無を確認する。",
                "既存教材や過去の研修資料を探し、再利用できる部分を整理する。",
                "講座構成を、導入、概念、デモ、演習、まとめの流れで設計する。",
                "各パートの講師メモ、演習指示、想定質問、補足資料を作る。",
                "受講者に見せる情報と社内運用メモを分ける。",
            ],
            "講座ゴール / カリキュラム / 各章の内容 / 演習案 / 講師メモ / TODO",
            ["クライアント固有の数字や事例は、共有可否を確認してから使う。"],
        ),
        "generic": (
            [
                "依頼文から目的、成果物、制約、参照すべきファイルやURLを整理する。",
                "過去サンプルと主要cwdを手がかりに、既存の手順、関連スキル、近い成果物を探す。",
                "作業手順を短く提示し、必要な読み取り、検索、編集、検証を実行する。",
                "成果物、判断理由、未確認事項、次アクションを分けて返す。",
                "外部公開、送信、財務・法務・クライアント可視の操作は、実行前に承認を取る。",
            ],
            "結論 / 実施内容 / 成果物 / 未確認事項 / 次アクション",
            [],
        ),
    }
    return workflows.get(category_key, workflows["generic"])

def bullet_block(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)

def numbered_block(items: list[str]) -> str:
    return "\n".join(f"{i}. {item}" for i, item in enumerate(items, 1))

def category_related_resources(category_key: str) -> list[str]:
    resources = {
        "email_response": [
            "`gmail:gmail` や Gmail 系コネクタが使える場合は、対象メールの原文・スレッド・添付有無を確認する。",
            "`gmail-rules`、`email-template-generator`、既存の返信テンプレがある場合は先に読む。",
            "問い合わせフォーム由来のメールは、事業種別・会社名・相手の温度感・次回アクションを必ず分ける。",
        ],
        "x_post": [
            "ユーザー固有の文体スキルや投稿ルールがある場合は先に読み、このスキルは素材整理・投稿前ペイロード化に使う。",
            "`x-api`、`typefully-cli`、投稿先ごとの文体・承認ルールがある場合は投稿前に確認する。",
            "外部投稿は必ず本文・投稿先・画像・引用URLを提示してから承認を取る。",
        ],
        "research": [
            "`research-vault`、`ai-news-research`、`article-router` など近い調査系スキルがある場合は先に確認する。",
            "新しい情報、価格、仕様、CEO/組織情報、法務・金融・医療系はWeb確認を前提にする。",
            "公式ドキュメント、一次ソース、GitHub、論文、企業発表を優先し、まとめ記事だけで断定しない。",
        ],
        "invoice": [
            "チームや個人の経理フォルダ、請求書フォルダ、入金管理ファイルを第一候補にする。",
            "`moneyforward`、`moneyforward-invoice`、`finance-status` がある場合は関連手順を確認する。",
            "請求書・領収書・入金情報は編集より先に一覧化し、変更案を提示してから進める。",
        ],
        "meeting_notes": [
            "`mtg-actions`、`notion-meeting-to-tasks`、議事録テンプレがある場合は先に確認する。",
            "文字起こし、Slack、Notion、Drive資料が混在することを前提に、出典ごとに情報を分ける。",
            "外部共有用と社内運用メモを分けて作る。",
        ],
        "article": [
            "媒体別の執筆スキル、編集ルール、納品テンプレートがある場合は必ず先に読む。",
            "GSC、既存記事、競合記事、一次情報を分けて扱う。",
            "納品先の表記ルール、禁止表現、リンク方針、画像要否を確認する。",
        ],
        "slide": [
            "`company-slide-recreator`、`ppt-creator`、`frontend-slides` など既存資料系スキルがある場合は先に読む。",
            "既存スライド、ブランドカラー、フォント、ロゴ、表記ルールを先に確認する。",
            "構成作成、本文作成、デザイン反映、レンダリング確認を別工程として扱う。",
        ],
        "training": [
            "`AI研修事業マスターコンテキスト` や研修教材フォルダを先に確認する。",
            "受講者の職種、AI習熟度、演習環境、講義時間、成果物を最初に固める。",
            "クライアント固有事例は共有可否を確認し、汎用教材と社内メモを分ける。",
        ],
        "generic": [
            "既存スキルと重複していないか確認し、近いスキルがあればそちらを優先する。",
            "過去サンプルの cwd と主要ツールを手がかりに、前例を探してから実行する。",
        ],
    }
    return resources.get(category_key, resources["generic"])

def category_execution_details(category_key: str) -> list[str]:
    details = {
        "email_response": [
            "受信文面を `相手情報 / 用件 / 期待される返答 / 期限 / 添付・URL / 未確認事項` に分解する。",
            "返信前に、相手が求めているものが `日程調整 / 見積 / 資料請求 / 相談 / クレーム / 内部連絡` のどれかを分類する。",
            "本文は最初から完成形で書かず、まず `返信方針` を出してから本文案にする。",
            "日程候補、資料添付、金額、契約条件などの具体情報は、確認済みかどうかを明記する。",
            "送信用本文には余計な分析を入れず、社内向けの判断メモと分離する。",
        ],
        "x_post": [
            "元ネタから `何が新しいか / 誰に効くか / 自分の判断 / 具体例 / 注意点` を抜き出す。",
            "最初に構文を決める。体験談、速報、思想、解説、比較、まとめでフックの作り方を変える。",
            "数字、料金、リリース日、会社名、モデル名、API仕様は投稿前に一次ソースで確認する。",
            "スレッド化する場合は、1ツイート目に結論と読む理由、2ツイート目以降に具体、最後に判断を置く。",
            "投稿前確認ペイロードには `投稿先 / 本文 / 画像 / 引用URL / 公開範囲 / 未確認事項` を入れる。",
        ],
        "research": [
            "まず調査質問を1文で書き換え、今回答える範囲と答えない範囲を決める。",
            "検索は `公式 / GitHub・論文 / ニュース / 比較・評判 / 日本語情報` の順に見る。",
            "出典ごとに、事実、主張、推測、古い可能性がある情報を分けてメモする。",
            "結論を先に出し、その後に根拠、反証、リスク、次アクションを置く。",
            "ユーザーが次に動けるように、調査結果を `やる / 保留 / やらない / 追加確認` に落とす。",
        ],
        "invoice": [
            "対象期間と対象フォルダを確認し、ファイル名、金額、取引先、期限、状態を表で抜く。",
            "売上側と経費側を混ぜない。請求書、領収書、入金証跡、支払予定を別列で扱う。",
            "同じ取引先・同じ金額・近い日付のファイルは重複候補として印を付ける。",
            "不足している場合は `見つからない` と書き、勝手に推測で補完しない。",
            "編集や移動が必要な場合は、操作前に対象ファイルパスと変更理由を出す。",
        ],
        "meeting_notes": [
            "文字起こしを `決定事項 / 論点 / TODO / 未決 / 雑談・背景` に分ける。",
            "TODOは必ず `担当 / 期限 / 成果物 / 依存関係` を書く。分からないものは未確認にする。",
            "クライアント共有用は表現を整え、社内判断や懸念は別セクションに逃がす。",
            "長い会話は要約し、意思決定に関係する発言だけ残す。",
            "次回MTGで確認すべき質問を最後にまとめる。",
        ],
        "article": [
            "最初に `読者 / 検索意図 / 狙うKW / 媒体 / 文字数 / 納品形式` を固定する。",
            "競合記事は構成の被りを見るために使い、表現は流用しない。",
            "見出しごとに、主張、根拠、具体例、読者の次アクションを置く。",
            "AIツールや新機能は、公式・料金・対応地域・制限を確認する。",
            "納品前に、媒体トーン、重複、リンク、固有名詞、古い情報をチェックする。",
        ],
        "slide": [
            "最初に `誰が / 何を判断するための資料か` を1文で決める。",
            "1スライド1メッセージにし、本文、図解、補足を混ぜない。",
            "既存資料のトーン、余白、色、見出し粒度を見てから作る。",
            "テキスト量が多い場合は、スライド本文と発話メモに分ける。",
            "レンダリング後に、文字切れ、重なり、画像欠け、リンク切れを確認する。",
        ],
        "training": [
            "対象者の前提知識を、未経験、日常利用、業務利用、開発利用に分けて難易度を決める。",
            "各章に `説明 / デモ / 演習 / 振り返り` を置く。",
            "演習は受講者の業務に近い題材にし、ツール操作だけで終わらせない。",
            "講師メモには、詰まりやすい点、想定質問、代替説明を入れる。",
            "社内運用メモと受講者配布資料を分ける。",
        ],
        "generic": [
            "依頼文を成果物単位に分解し、必要な読み取り・検索・編集・検証を並べる。",
            "過去サンプルから共通する入力、出力、判断基準を探す。",
            "外部公開やファイル編集が絡む場合は、操作前に確認する。",
        ],
    }
    return details.get(category_key, details["generic"])

def category_output_template(category_key: str) -> str:
    templates = {
        "email_response": """```text
返信方針:
-

件名案:

本文案:
〇〇様

確認事項:
-

送信前チェック:
- 宛先:
- 添付:
- 外部送信承認: 未承認
```""",
        "x_post": """```text
投稿方針:
- 構文:
- 狙い:
- 未確認ファクト:

投稿本文:
1.

投稿前確認:
- 投稿先:
- 引用URL:
- 画像:
- 公開範囲:
- 承認: 未承認
```""",
        "research": """```text
結論:

根拠:
1.
2.

詳細メモ:
- 事実:
- 推測:
- 未確認:

リスク:

次アクション:

参照元:
- URL / 確認日 / 何を確認したか
```""",
        "invoice": """```text
サマリー:

明細:
| 区分 | 取引先 | 金額 | 期限 | 状態 | ファイル | 確認事項 |
|---|---|---:|---|---|---|---|

不足・要確認:
-

操作提案:
- 実行前承認: 未承認
```""",
        "meeting_notes": """```text
会議概要:
- 日時:
- 参加者:
- 目的:

決定事項:
-

議論内容:
-

TODO:
| 担当 | 内容 | 期限 | 依存関係 |
|---|---|---|---|

未決事項:
-
```""",
        "article": """```text
記事方針:
- 読者:
- 検索意図:
- キーワード:

構成:
H1:
H2:

本文:

ファクトチェック:
- 確認済み:
- 未確認:

修正メモ:
```""",
        "slide": """```text
資料方針:
- 目的:
- 読み手:
- 判断してほしいこと:

スライド構成:
1.

各ページ本文:
- title:
- message:
- visual:

確認事項:
```""",
        "training": """```text
講座ゴール:

対象者:

カリキュラム:
| 時間 | 内容 | デモ | 演習 |
|---|---|---|---|

講師メモ:

TODO:
```""",
        "generic": """```text
結論:

実施内容:

成果物:

未確認事項:

次アクション:
```""",
    }
    return templates.get(category_key, templates["generic"])

def category_quality_gates(category_key: str) -> list[str]:
    gates = {
        "email_response": [
            "本文だけでなく、返信方針と確認事項が分かれている。",
            "送信先、件名、添付、日程候補、外部送信承認状態が明示されている。",
            "未確認の約束、金額、日程、契約条件を断定していない。",
        ],
        "x_post": [
            "1ツイート目だけで何の話か、なぜ読むべきか分かる。",
            "数字・固有名詞・仕様は確認済みか、未確認として明示されている。",
            "投稿先と公開範囲、引用URL、画像有無、承認状態が明示されている。",
        ],
        "research": [
            "結論が先にあり、根拠と未確認事項が分かれている。",
            "一次ソースまたは公式情報を優先している。",
            "次アクションが意思決定に使える粒度になっている。",
        ],
        "invoice": [
            "金額、期限、状態、ファイルパスが表で追える。",
            "不足・重複・期限超過が分かる。",
            "財務ファイルの編集や送付を勝手に実行していない。",
        ],
        "meeting_notes": [
            "決定事項、TODO、未決事項が分かれている。",
            "TODOに担当、期限、成果物がある。",
            "外部共有用に出せない内部事情が混ざっていない。",
        ],
        "article": [
            "読者、検索意図、主張、根拠が対応している。",
            "競合記事の表現をそのまま使っていない。",
            "古い情報や料金・仕様が確認済みになっている。",
        ],
        "slide": [
            "1スライド1メッセージになっている。",
            "既存デザインやブランドルールと矛盾していない。",
            "レンダリング確認で文字切れ・重なりがない。",
        ],
        "training": [
            "対象者の前提知識と講座ゴールが対応している。",
            "説明だけでなく演習と振り返りがある。",
            "受講者向け資料と社内メモが分かれている。",
        ],
        "generic": [
            "成果物、判断、未確認事項が分かれている。",
            "外部公開・送信・編集の承認状態が明示されている。",
        ],
    }
    return gates.get(category_key, gates["generic"])

def category_failure_modes(category_key: str) -> list[str]:
    failures = {
        "email_response": [
            "相手に送る本文に、社内向けの分析や未確認メモを混ぜる。",
            "ユーザー承認なしに送信済みの体裁で進める。",
            "相手の依頼目的を読まず、一般的な丁寧メールだけを返す。",
        ],
        "x_post": [
            "元ネタの言い回しを近い形で使う。",
            "ファクト未確認のまま数字や公式発表風の表現を書く。",
            "投稿前確認ペイロードなしで公開処理に進む。",
        ],
        "research": [
            "検索結果の上位まとめ記事だけで結論を出す。",
            "事実と推測を混ぜる。",
            "結局何をすればよいかが分からない調査メモで終わる。",
        ],
        "invoice": [
            "売上と経費を同じ表で曖昧に扱う。",
            "見つからないファイルを推測で存在扱いする。",
            "財務ファイルを確認なしに移動・編集する。",
        ],
        "meeting_notes": [
            "文字起こしの要約だけでTODOに落ちていない。",
            "担当者や期限が不明なまま完了扱いにする。",
            "社内向けの懸念を外部共有文に混ぜる。",
        ],
        "article": [
            "構成だけ作って、読者の検索意図がない。",
            "競合記事の焼き直しになる。",
            "媒体ルールや納品形式を確認しない。",
        ],
        "slide": [
            "情報を詰め込みすぎて1枚のメッセージがぼける。",
            "デザイン確認なしに完成扱いにする。",
            "発話メモとスライド本文を混ぜる。",
        ],
        "training": [
            "講義だけで演習がない。",
            "対象者の前提知識に対して難しすぎる。",
            "受講者配布資料に社内運用メモを混ぜる。",
        ],
        "generic": [
            "過去ログのサンプルをそのまま再利用する。",
            "薄い一般論で終わる。",
        ],
    }
    return failures.get(category_key, failures["generic"])

def cmd_list(args):
    clusters = json.loads(Path(args.inp).read_text(encoding="utf-8"))
    existing = load_existing_names()
    print(f"\n# 定期作業候補（{len(clusters)}件 / トリガー出現回数の多い順）\n")
    for i, c in enumerate(clusters, 1):
        skill_name = generated_skill_name(c)
        overlaps = overlap_existing(skill_name, existing) + overlap_existing(c["trigger"], existing)
        overlaps = sorted(set(overlaps))[:5]
        overlap_note = f" / 既存重複候補: {', '.join(overlaps)}" if overlaps else ""
        category = infer_category(c)
        print(
            f"## {i}. 【{c['trigger']}】 {c['count']}回 / "
            f"生成名: {skill_name} / 種別: {category['title']} / "
            f"主要ツール: {c['tool_signature']}{overlap_note}"
        )
        for s in c["samples"][:3]:
            prompt = safe_sample(s.get("prompt", ""), limit=140)
            print(f"   - 「{prompt}」")
        if c.get("top_cwds"):
            print(f"   cwd: {safe_sample(c['top_cwds'][0], limit=80)}")
        print()
    print("→ 採用したい番号を指定してください（例: \"2,5,9\"）。次のコマンド:")
    print(f"   python3 {Path(__file__).resolve()} --in {args.inp} --mode generate --pick 2,5,9 --out-dir /tmp/recurring-task-extractor/generated/")

def render_skill_md(
    cluster: dict,
    name_override: str | None = None,
    include_safety: bool = False,
) -> str:
    """Build a usable SKILL.md from a cluster."""
    trigger = str(cluster["trigger"])
    category = infer_category(cluster)
    category_key = category["key"]
    name = name_override or generated_skill_name(cluster)
    samples = cluster.get("samples", [])[:5]
    tools = cluster.get("top_tools", [])
    cwds = cluster.get("top_cwds", [])
    meta = semantic_meta(cluster)
    steps, output_format, extra_safety = category_workflow(category_key)
    related_block = bullet_block(category_related_resources(category_key))
    detail_block = numbered_block(category_execution_details(category_key))
    output_template = category_output_template(category_key)
    quality_block = bullet_block(category_quality_gates(category_key))
    failure_block = bullet_block(category_failure_modes(category_key))

    trigger_block = "\n".join(f"- 「{p}」" for p in render_trigger_phrases(cluster))
    sample_block = "\n".join(
        f"- {one_line(str(s.get('timestamp', '')), limit=32)} / 「{safe_sample(s.get('prompt', ''), limit=180)}」"
        for s in samples
    )
    tools_block = ", ".join(tools) if tools else "(detected none)"
    cwd_block = "\n".join(f"- `{safe_sample(c, limit=140)}`" for c in cwds) if cwds else "(no consistent cwd)"
    workflow_block = "\n".join(f"{i}. {step}" for i, step in enumerate(steps, 1))
    safety_lines = [
        "ログ由来の個人情報、メールアドレス、電話番号、トークン、非公開URLは出力前にマスクする。",
        "既存ファイルを編集する前に、対象ファイルと変更内容を明確にする。",
        "外部公開、送信、投稿、クライアント可視の操作は、明示承認後のみ実行する。",
        "不確かな事実、日付、数値、料金、仕様は確認済み情報と推測を分ける。",
    ] + extra_safety
    safety_block = "\n".join(f"- {line}" for line in safety_lines)
    safety_section = f"""
## 安全ルール

{safety_block}
""" if include_safety else ""

    description_source = meta.get("description") or category["description"]
    description = (
        f"{description_source} 過去ログで{cluster['count']}回検出された反復作業。"
        f"「{trigger}」などの依頼で起動。"
    )
    description = one_line(redact_sensitive(description), limit=260)
    yaml_name = json.dumps(name, ensure_ascii=False)
    yaml_description = json.dumps(description, ensure_ascii=False)

    md = f"""---
name: {yaml_name}
description: {yaml_description}
---

# {category['title']}

過去のClaude Codeセッションログから生成された実用スキル。
このスキルは、同種の依頼を受けたときに最初から手順を再現できるようにする。

## 目的

{category['description']}

過去ログ上の検出回数: {cluster['count']}回

## いつ起動するか

次のような依頼で起動する:

{trigger_block}

## 入力として確認すること

- 最終的な成果物の形式
- 対象期間、対象ファイル、対象URL、対象サービス
- 外部公開、送信、投稿、クライアント共有が含まれるか
- 既存の関連スキル、テンプレート、近い過去成果物があるか
- 未確認の数値、日付、仕様、固有名詞があるか

## 先に確認する関連リソース

{related_block}

## 実行手順

{workflow_block}

## 詳細プレイブック

{detail_block}

## 出力フォーマット

{output_format}

## 出力テンプレート

{output_template}

## 主要ツール

過去のセッションで使われていたツール: {tools_block}

## 主な作業場所

過去ログで多かったcwd:

{cwd_block}

## ログ由来のサンプル

サンプルは個人情報をマスクした参考例。内容をそのまま外部に出さない。

{sample_block}

{safety_section}
## 品質ゲート

{quality_block}

## よくある失敗

{failure_block}

## 完了条件

- 依頼された成果物が指定形式で出ている
- 事実、推測、未確認事項が分かれている
- 外部送信や投稿が必要な場合、実行前確認ペイロードが提示されている
- 参照ファイル、参照URL、変更ファイルが分かる
- 次アクションがある場合は担当、期限、確認先が明記されている
"""
    return md

def install_generated_skill(skill_dir: Path, force: bool = False) -> Path:
    target = EXISTING_SKILLS_DIR / skill_dir.name
    if target.exists():
        if not force:
            raise FileExistsError(f"{target} already exists (use --force to overwrite)")
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    EXISTING_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copytree(skill_dir, target)
    return target

def cmd_generate(args):
    clusters = json.loads(Path(args.inp).read_text(encoding="utf-8"))
    picks = [int(x.strip()) for x in args.pick.split(",") if x.strip()]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written_dirs = []
    used_names = set()

    for n in picks:
        if n < 1 or n > len(clusters):
            print(f"[generate] skip invalid pick: {n}", file=sys.stderr)
            continue
        c = clusters[n - 1]
        name = generated_skill_name(c)
        if name in used_names:
            name = f"{name}-{hashlib.sha1(str(c.get('trigger', n)).encode('utf-8')).hexdigest()[:6]}"
        used_names.add(name)
        skill_dir = out_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            render_skill_md(c, name_override=name, include_safety=args.include_safety),
            encoding="utf-8",
        )
        written_dirs.append(skill_dir)
        print(f"[generate] wrote {skill_dir / 'SKILL.md'}", file=sys.stderr)
        if args.install:
            try:
                target = install_generated_skill(skill_dir, force=args.force)
                print(f"[install] copied to {target}", file=sys.stderr)
            except FileExistsError as exc:
                print(f"[install] skipped: {exc}", file=sys.stderr)

    print(f"\n→ 生成結果を確認:")
    print(f"   ls {out_dir}/")
    print(f"   cat {out_dir}/<name>/SKILL.md")
    if not args.install:
        print(f"\n→ 採用するなら、確認後に明示的にインストール:")
        print(f"   python3 {Path(__file__).resolve()} --in {args.inp} --mode generate --pick {args.pick} --out-dir {out_dir} --install")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="/tmp/recurring-task-extractor/clusters.json")
    ap.add_argument("--mode", choices=["list", "draft", "generate"], default="list",
                    help="draft is kept as a backward-compatible alias of generate")
    ap.add_argument("--pick", help="Comma-separated cluster numbers to generate (1-indexed)")
    ap.add_argument("--out-dir", default="/tmp/recurring-task-extractor/generated/")
    ap.add_argument("--install", action="store_true",
                    help="Copy generated skills into ~/.claude/skills after writing them")
    ap.add_argument("--force", action="store_true",
                    help="Allow --install to overwrite an existing skill directory")
    ap.add_argument("--include-safety", action="store_true",
                    help="Include an explicit safety-rules section in generated SKILL.md")
    args = ap.parse_args()

    if args.mode == "list":
        cmd_list(args)
    else:
        if not args.pick:
            print("--pick is required in draft/generate mode (e.g. --pick 1,3,5)", file=sys.stderr)
            sys.exit(2)
        cmd_generate(args)

if __name__ == "__main__":
    main()
