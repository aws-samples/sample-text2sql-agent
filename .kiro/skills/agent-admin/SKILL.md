---
name: agent-admin
description: Agent の system_prompt、Knowledge の参照・変更、Agent の新規作成に使う。
---

# Admin

このスキルは Agent 設定（system_prompt, db_schema, Knowledge）を CLI から参照・変更するためのものです。Admin UI と同じ操作の一部を、同梱の Python スクリプト経由で実行できます。

## 前提条件

- ターミナルに AWS credentials が設定されていること
- `uv` (Python tool manager) がインストールされていること

各スクリプトは PEP 723 のインラインメタデータで `boto3` を宣言しているため、`uv run` が初回実行時に依存を自動解決します。`pyproject.toml` や venv のセットアップは不要です。

## 初回セットアップ

このスキルを初めて使うとき、ユーザーに以下を聞いてください:

1. Agent 設定テーブル名
   - 例: `DwhAgentStack-DatabaseConfigTableXXXXXX-YYYYYY`
   - CDK デプロイ出力や AWS コンソールから確認できる
2. AWS リージョン
   - 例: `ap-northeast-1`

一度教えてもらったら、以降の会話ではそのまま使い回してください。以下のコマンド例では `$TABLE` / `$REGION` をそれぞれの値で置き換えてください。

## スクリプト一覧

すべて `.kiro/skills/agent-admin/scripts/` 配下にあります。Kiro が実行するときはワークスペース相対パスで呼び出してください。

| スクリプト | 目的 |
|---|---|
| `list_agents.py` | Agent 一覧を取得 |
| `create_agent.py` | 新規 Agent を作成 |
| `get_agent.py` | 1 Agent の全属性（system_prompt, db_schema, Knowledge, メタ情報）を取得 |
| `update_system_prompt.py` | system_prompt を差し替え |
| `upsert_knowledge.py` | Knowledge エントリ 1 件を追加または差し替え |
| `delete_knowledge.py` | Knowledge エントリ 1 件を name で削除 |

各スクリプトは成功時 stdout に JSON を返し、失敗時 stderr にメッセージを出して非ゼロで終了します。成功時だけ `json.loads(stdout)` してください。

## 用語対応

UI・スクリプト・このドキュメントではすべて **Knowledge** と呼びます。

## 入力ファイルの作成ルール（重要）

system_prompt や Knowledge エントリのように改行・引用符を含みうるテキストを渡すときは、必ず **Kiro の native `fsWrite` ツール** で一時ファイルを作成し、そのパスを `--*-file` 引数に渡してください。

- `echo ... >`, `cat <<EOF >`, ヒアドキュメントなどのシェル経由のファイル作成は **使わない**
- `fsWrite` はシェルを介さないので、クォート・エスケープ・EOF マーカーの問題が原理的に発生しない
- スクリプトは inline 文字列引数や stdin を受け付けない（ファイルパス渡しのみ）

## 操作

### Agent 一覧取得

```bash
uv run .kiro/skills/agent-admin/scripts/list_agents.py \
  --table-name "$TABLE" --region "$REGION"
```

### Agent の新規作成

ユーザーが新しい Agent を追加したいと言ったら:

1. `agent_name`（表示名）と `system_prompt` をユーザの要望にあわせて決定し、system_prompt を `fsWrite` で一時ファイルに書き出す
2. 以下を実行

```bash
# system_prompt をファイルから読ませる場合
uv run .kiro/skills/agent-admin/scripts/create_agent.py \
  --table-name "$TABLE" --region "$REGION" \
  --agent-name "売上分析 Agent" \
  --system-prompt-file ./tmp/new_prompt.txt

# system_prompt を空で作る場合
uv run .kiro/skills/agent-admin/scripts/create_agent.py \
  --table-name "$TABLE" --region "$REGION" \
  --agent-name "売上分析 Agent"
```

`db_schema` は空で作成されます。Agent 実行時に `db_schema` が空の場合、Agent Runtime が自動的に `"default"` Agent の `db_schema` をフォールバックとして使用します。

### Agent の現状を見る

```bash
# default Agent
uv run .kiro/skills/agent-admin/scripts/get_agent.py \
  --table-name "$TABLE" --region "$REGION"

# 指定した Agent
uv run .kiro/skills/agent-admin/scripts/get_agent.py \
  --table-name "$TABLE" --region "$REGION" --id 3f2c1d4e-...
```

`get_agent.py` は 1 回の呼び出しで `agent_name`, `system_prompt`, `db_schema`, `knowledge`（配列）, `created_at`, `updated_at` をすべて返します。`--field` のような絞り込みはありません（必要な部分だけ Kiro 側で取り出してください）。

**db_schema フォールバックに注意**: 対象 Agent の `db_schema` が空のとき、`get_agent.py` は自動的に `id="default"` の `db_schema` を返し、出力 JSON に `db_schema_source: "default"` を付けます（通常は `"self"`）。Agent Runtime と同じ挙動です。今見ている `db_schema` が自前のものか default 由来かは `db_schema_source` で判別してください。

### system_prompt の更新

1. `fsWrite` で新しい system_prompt を一時ファイルに書く（例: `./tmp/new_prompt.txt`）
2. 以下を実行

```bash
uv run .kiro/skills/agent-admin/scripts/update_system_prompt.py \
  --table-name "$TABLE" --region "$REGION" \
  --id 3f2c1d4e-... --prompt-file ./tmp/new_prompt.txt
```

`--id` は Agent の ID です。省略時は `default` を対象にします。

### Knowledge の追加 / 差し替え

Knowledge エントリは以下の形式の Markdown 文字列です:

```
---
name: <kebab-case の識別子>
description: <説明と、どの様な会話でこのknowledgeを使うかについて簡潔に>
---
<Markdown 形式の指示内容>
```

1. `fsWrite` で 1 エントリ分を一時ファイルに書く（例: `./tmp/k1.md`）
2. 以下を実行

```bash
uv run .kiro/skills/agent-admin/scripts/upsert_knowledge.py \
  --table-name "$TABLE" --region "$REGION" \
  --id 3f2c1d4e-... --knowledge-file ./tmp/k1.md
```

`--id` は Agent の ID です。省略時は `default` を対象にします。

動作:

- ファイル冒頭の **frontmatter**（`---` で挟まれた YAML メタデータブロック）から `name` を抽出
- 既存の Knowledge に同じ `name` があれば **その場で差し替え**、なければ **末尾に追加**
- 出力 JSON の `action` が `"updated"` か `"added"` かで結果がわかる
- description には、簡潔な説明とともに、どのような会話でこの Knowledge を使うかについても簡潔に記述すること。

制約:

- `name` は空でない文字列であること（空白のみも不可）
- ファイルには 1 エントリだけ書く（複数エントリを `---` で連結したファイルは非対応）
- 複数エントリを追加したいときは、エントリごとにファイルを書いて `upsert_knowledge.py` を順に呼ぶ

### Knowledge の削除

```bash
uv run .kiro/skills/agent-admin/scripts/delete_knowledge.py \
  --table-name "$TABLE" --region "$REGION" \
  --id 3f2c1d4e-... --name k1
```

`--id` は Agent の ID です。省略時は `default` を対象にします。
`--name` は削除対象の frontmatter の `name` と完全一致する必要があります。存在しなければエラー終了します。

### db_schema の更新

⛔ **db_schema の変更は厳禁。このスキルからは変更手段を提供していません。**

db_schema は Admin UI の CSV アップロード → AI 分析 → Apply フローによってのみ管理されます。CLI やスクリプトから db_schema を変更すると、Redshift 上の実テーブル定義との不整合が発生し、Agent が誤った SQL を生成する原因となります。

ユーザーから db_schema の変更を依頼された場合:

- db_schema の変更は Admin UI からのみ行えることを説明する
- CSV の再アップロードと Apply を案内する
- このスキルでは db_schema の参照（`get_agent.py` 経由）のみ可能

## 注意事項

- **db_schema は絶対に変更しないこと（厳禁）。参照のみ可。**
- 設定の変更は即座に Agent の動作に反映される（次回の /chat リクエストから）
- 変更前に `get_agent.py` で現在の値を確認することを推奨する
- 入力が必要な長文（system_prompt, Knowledge の本文）は必ず `fsWrite` で一時ファイルに書いてから `--*-file` で渡すこと

## エラーハンドリング

- 成功時: exit 0, stdout に JSON
- 失敗時: 非ゼロ exit, stderr に原因メッセージ, stdout は空

失敗時は stderr の内容をそのまま読めば原因がわかる形で返します。`get_agent.py` で fallback が必要な状況で `default` Agent が存在しなかった場合は、stderr に警告を出しつつ exit 0 で返します（`db_schema_source: "default"`, `db_schema: ""`）。Kiro は stdout の `db_schema_source` を見れば判別できます。
