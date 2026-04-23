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
| `csvInputBucketName` | 既存 CSV バケット名 (省略時は新規作成) | 未設定 |
| `sqlResultThreshold` | Agent SQL 結果の行数上限 | `200` |
| `enablePromptCache` | Bedrock Prompt Cache の有効/無効 | `true` |
| `stackPrefix` | スタック名のプレフィックス。同一 AWS アカウントに複数環境をデプロイする場合に指定 (例: `Dev`, `Stg`, `TeamA`, `TeamB`) | `""` (空文字) |
| `testAgentUser` | Agent UserPool に自動作成するテストユーザー名。空文字の場合は作成しない。メールアドレスではなくユーザー名とすること。パスワードは CDK が自動生成して Secrets Manager に保管する | `""` (空文字) |
| `testAdminUser` | Admin UserPool に自動作成するテストユーザー名。空文字の場合は作成しない。メールアドレスではなくユーザー名とすること。パスワードは CDK が自動生成して Secrets Manager に保管する | `""` (空文字) |

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