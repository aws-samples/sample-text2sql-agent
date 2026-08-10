# デプロイ手順

## 前提条件

- AWS CLI
- Node.js 20~
- npm
- Docker CLI

## 1. CDK 依存インストール

```bash
npm ci
```

## 2. cdk.json のパラメータ確認

`cdk.json` の `context` セクションで以下を確認・変更:

| パラメータ | 説明 | デフォルト |
|-----------|------|-----------|
| `allowOrigin` | CORS 許可オリジン | `*` |
| `allowedCidrs` | WAF IP 制限 (CIDR) | `["0.0.0.0/1", "128.0.0.0/1"]` (全許可) |
| `bedrockModelId` | Bedrock モデル ID | `global.anthropic.claude-sonnet-4-6` |
| `csvInputBucketName` | 既存 CSV バケット名 (省略時は新規作成)。既存バケットを指定した場合、Admin UI のローカルアップロード機能は無効化され、「既存 S3 パス指定」のみ利用可能になります（ブラウザ直 PUT に必要な CORS 設定を行わない前提のため） | 未設定 |
| `sqlResultThreshold` | Agent SQL 結果の行数上限 | `200` |
| `enablePromptCache` | Bedrock Prompt Cache の有効/無効 | `true` |
| `stackPrefix` | スタック名のプレフィックス。同一 AWS アカウントに複数環境をデプロイする場合に指定 (例: `Dev`, `Stg`, `TeamA`, `TeamB`) | `""` (空文字) |
| `testAgentUser` | Agent UserPool に自動作成するテストユーザー名。空文字の場合は作成しない。メールアドレスではなくユーザー名とすること。パスワードは CDK が自動生成して Secrets Manager に保管する | `""` (空文字) |
| `testAdminUser` | Admin UserPool に自動作成するテストユーザー名。空文字の場合は作成しない。メールアドレスではなくユーザー名とすること。パスワードは CDK が自動生成して Secrets Manager に保管する | `""` (空文字) |
| `existingRedshift` | 既存の Redshift Serverless を参照する場合に指定 ([existingRedshift モード](#appendix-既存-redshift-serverless-を利用する-existingredshift-モード) 参照) | 未設定 (新規作成) |

アクセス元IPアドレスを縛るには `allowedCidrs` を実際の IP 範囲に制限してください。


## 3. CDK Bootstrap (初回のみ)

初めて CDK を利用する場合、bootstrap が必要です

```bash
npm run cdk bootstrap
```

## 4. デプロイ

```bash
npm run cdk -- deploy --all
```

2 つのスタックがデプロイされます:
- `DwhAgentWafStack` (us-east-1) — CloudFront 用 WAF
- `DwhAgentStack` (デフォルトリージョン) — 全リソース

デプロイ完了後、以下の出力値を確認:

```
DwhAgentStack.AgentFrontendUrl = https://dxxxxx.cloudfront.net
DwhAgentStack.AgentCognitoUserPoolId = ap-northeast-1_XXXXX
DwhAgentStack.AdminFrontendUrl = https://dyyyy.cloudfront.net
DwhAgentStack.AdminCognitoUserPoolId = ap-northeast-1_YYYYY
DwhAgentStack.CsvBucketName = dwhagentstack-csvstoragecsvinputbucketXXXXX-XXXXX
```

## 5. Cognito ユーザー作成

Agent 用と Admin 用で別々の UserPool です。ユーザー作成には 2 通りの方法があります。

### 方法 A: テストユーザーを CDK で自動作成（開発環境向け）

`cdk.json` の `testAgentUser` / `testAdminUser` に username を指定してデプロイすると、
それぞれの UserPool にテストユーザーが自動作成されます。
パスワードは CDK が自動生成して Secrets Manager に保管します。

```json
"testAgentUser": "agentuser",
"testAdminUser": "adminuser"
```

デプロイ後、以下の出力値を確認:

```
DwhAgentStack.AgentBackendCognitoTestUserSecretName = ...
DwhAgentStack.AdminBackendCognitoTestUserSecretName = ...
```

Secrets Manager からパスワードを取得:

```bash
aws secretsmanager get-secret-value \
  --secret-id <TestUserSecretName> \
  --query SecretString --output text | jq -r .password
```

> **注意**: この機能は開発環境専用です。本番では `testAgentUser` / `testAdminUser` を
> 空文字のままにしてください。

### 方法 B: AWS CLI で手動作成

```bash
# Agent UserPool にユーザー作成
aws cognito-idp admin-create-user \
  --user-pool-id <AgentCognitoUserPoolId> \
  --username <username> \
  --temporary-password '<temoprary password>' \
  --user-attributes Name=email,Value=<email>

# Admin UserPool にユーザー作成
aws cognito-idp admin-create-user \
  --user-pool-id <AdminCognitoUserPoolId> \
  --username <admin_username> \
  --temporary-password '<temoprary password>' \
  --user-attributes Name=email,Value=<admin_email>
```

## 6. Bedrock AgentCore Observability の有効化

[マネジメントコンソール](https://console.aws.amazon.com/cloudwatch/home#/gen-ai-observability/agent-core/agents)を開き、Configure をクリックして、Transaction Search を有効化してください。Trace indexing rate は開発環境では 100% にすることもお勧めです。

## 7. 動作確認

[OPERATION.md](./OPERATION.md) を参照してください。


## Appendix: スタック削除

```bash
npm run cdk -- destroy --all
```

## Appendix: 既存 Redshift Serverless を利用する (existingRedshift モード)

すでにデータが入っている Redshift Serverless がある場合、Redshift を新規作成せずに
既存の Workgroup を参照してデプロイできます。

### 通常モードとの違い

| 項目 | 通常モード | existingRedshift モード |
|------|-----------|------------------------|
| Redshift Serverless | 新規作成 (専用 VPC 含む) | 既存 Workgroup を参照 (一切変更しない) |
| CSV アップロード (Upload & Build) | あり | なし (既存データに問い合わせるのみ) |
| CSV ダウンロード (UNLOAD) | あり | なし (UNLOAD 用 IAM Role を既存 Namespace に関連付けないため) |
| スキーマ定義 | CSV から AI が自動生成 | **既存テーブルから AI が自動生成** (Admin UI の Tables & Build タブ) |
| DB 接続ユーザー | CDK が自動作成 | **事前に手動作成した読み取り専用ユーザー** |

接続はすべて Redshift Data API 経由のため、既存 Redshift の VPC 設定に関わらず動作します。
ただし **Workgroup はスタックと同一アカウント・同一リージョンにあること** が前提です。

### 1. 読み取り専用 DB ユーザーの作成 (リポジトリ外での事前準備)

既存 Redshift に管理者権限で接続し (クエリエディタ v2 など)、エージェント用の
読み取り専用ユーザーを作成します:

```sql
CREATE USER agent_readonly PASSWORD '<strong-password>';

-- エージェントに使わせたいスキーマ / テーブルにのみ SELECT を許可
GRANT USAGE ON SCHEMA public TO agent_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO agent_readonly;
```

> **重要**: このユーザーの GRANT が、エージェントがアクセスできる範囲の
> **ハードな境界**になります。Admin UI のテーブル選択はプロンプトに載せる
> テーブルを絞るソフトな制御であり、DB レベルの制御はこの GRANT が担います。
> SELECT 以外 (INSERT / UPDATE / DELETE / CREATE 等) の権限は付与しないでください。

### 2. Secrets Manager にシークレットを作成

```bash
aws secretsmanager create-secret \
  --name dwh-agent-readonly \
  --secret-string '{"username": "agent_readonly", "password": "<strong-password>"}'
```

出力される ARN (末尾 6 文字のサフィックス付き完全 ARN) を控えてください。

### 3. cdk.json の設定

```json
"existingRedshift": {
  "workgroupName": "my-existing-workgroup",
  "database": "dev",
  "secretArn": "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:dwh-agent-readonly-AbC123"
}
```

3 つの値をすべて指定してください (一部のみの指定は synth エラーになります)。

### 4. デプロイ

通常どおり `npm run cdk -- deploy --all` を実行します。

デプロイ中に **読み取り専用検証** (カスタムリソース) が実行されます。指定した
DB ユーザーが以下のいずれかに該当する場合、デプロイは失敗しロールバックします:

- superuser である
- いずれかのユーザースキーマに CREATE 権限を持つ
- いずれかのユーザーテーブルに INSERT / UPDATE / DELETE 権限を持つ

これは既存データを保護するためのガードレールです。失敗した場合は
CloudFormation イベントのエラーメッセージで違反内容を確認し、DB ユーザーの
権限を修正して再デプロイしてください。

### 5. スキーマ生成と Agent 作成

1. Admin UI にログインし、**Tables & Build** タブを開く
2. エージェントに使わせるテーブルを選択して「AI 分析を開始」
   - カラム定義 (information_schema) とサンプルデータ (先頭 20 行) を AI が分析し、
     テーブル・カラムの説明とシステムプロンプトを生成します
   - サンプルデータは説明生成のために Bedrock に送信されます
3. 生成結果を確認・編集して「Agent を作成」
4. Agent UI からチャットを開始

テーブルの組み合わせを変えた Agent を複数作成することもできます。
