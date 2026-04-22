---
name: agent-chat
description: Agent に自然言語で質問を送り、データを SQL で分析した結果を返す。
---

# Agent チャット

このスキルは DWH（データウェアハウス）のデータを自然言語で分析するためのものです。Amazon Bedrock AgentCore Runtime 上で動作する Agent に質問を送り、データを SQL で分析した結果を返します。

Admin UI やブラウザ版の Agent UI と同等のチャット操作を、同梱の Python スクリプト経由で CLI から実行できます。

## 前提条件

- ターミナルに AWS credentials が設定されていること
- `uv` (Python tool manager) がインストールされていること


各スクリプトは PEP 723 のインラインメタデータで `boto3` を宣言しているため、`uv run` が初回実行時に依存を自動解決します。`pyproject.toml` や venv のセットアップは不要です。

## 初回セットアップ

このスキルを初めて使うとき、ユーザーに以下を聞いてください:

1. AgentCore Runtime ARN
   - 例: `arn:aws:bedrock:ap-northeast-1:<AccountId12桁>:agent-runtime/dwh_agent`
   - CDK デプロイ出力や AWS コンソールから確認できる
2. AWS リージョン
   - 例: `ap-northeast-1`
   - ARN からも推測可能

一度教えてもらったら、以降の会話ではそのまま使い回してください。以下のコマンド例では `$RUNTIME_ARN` / `$REGION` をそれぞれの値で置き換えてください。

Agent ID はデフォルトで `"default"` を使用します。ユーザーが別の Agent を指定したい場合のみ Agent ID を聞いてください。

## スクリプト一覧


| スクリプト | 目的 |
|---|---|
| `.kiro/skills/agent-chat/scripts/chat.py` | 質問を送り、AgentCore Runtime のレスポンスを 1 回の呼び出しで構造化 JSON として返す |

レスポンスは内部で SSE を読み取り、トークン連結・ツール呼び出し抽出・チャート情報の集約までやります。Kiro は中間ファイルを扱う必要も、SSE を自前でパースする必要もありません。

## 入力ファイルの作成ルール（重要）

プロンプト JSON ファイルは必ず **Kiro の native `fsWrite` ツール** で作成してください。

- `echo ... >`, `cat <<EOF >`, ヒアドキュメントなどのシェル経由のファイル作成は **使わない**
- 非 ASCII 文字（日本語を含むプロンプト、タイトル）は `fsWrite` ならそのまま書ける

## 実行手順

ユーザーの質問を受け取ったら:

### 1. payload JSON をファイルに書き出す

`fsWrite` で `./tmp/dwh-payload.json` を作成します。内容例:

```json
{
  "prompt": "先月の売上上位 5 商品は？",
  "user_id": "kiro-user",
  "agent_id": "default",
}
```

- `prompt` (必須): エージェントへの質問文
- `user_id` (任意): 省略時は呼び出し側が自由に決めてよい。`kiro-user` などを推奨
- `agent_id` (任意): 省略時は AgentCore Runtime が `"default"` をフォールバックとして使用


### 2. Agent を呼び出す

```bash
uv run .kiro/skills/agent-chat/scripts/chat.py \
  --runtime-arn "$RUNTIME_ARN" \
  --region "$REGION" \
  --session-id "<SESSION_ID>" \
  --prompt-file ./tmp/dwh-payload.json
```

- `--session-id` は 33 文字以上の文字列。例: `kiro-dwh-session-<YYYYmmddHHMMSS>-<8桁hex>` (33 文字以上になる)

成功時 stdout は 1 つの JSON で以下の形:

```json
{
  "session_id": "...",
  "text": "<連結された回答テキスト>",
  "tool_uses": [
    {"tool": "_redshift_query", "description": "...", "sql": "..."}
  ],
  "charts": [
    {"title": "..."}
  ],
  "errors": [],
  "elapsed_seconds": <実行にかかった秒数>
}
```

### 3. ユーザーへ回答する

- `text` をそのままユーザーへの回答として使う
- `tool_uses` に `_redshift_query` があれば「SQL を実行しました（説明: ...）」と要約を補足する
- `charts` が空でなければ「チャート「...」が生成されました。チャートはブラウザの Agent UI でのみ表示可能です」と伝える
- `errors` が空でなければその内容を伝える

## セッション管理

- 同じ会話を継続したいときは `--session-id` を同じ値で呼び出す
- 新しい話題に切り替えるときは別のセッション ID を生成する（33 文字以上を維持）
- 異なる `agent_id` に切り替えたり、異なる会話を開始する場合は新しいセッション ID を使うこと

## エラーハンドリング

- 成功時: exit 0, stdout に JSON
- 失敗時: 非ゼロ exit, stderr に原因メッセージ, stdout は空

失敗時は stderr の内容をそのまま読めば原因がわかる形で返します。
