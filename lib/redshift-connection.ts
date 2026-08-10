import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';

/**
 * Redshift Serverless への Data API 接続情報の抽象。
 *
 * 実装は 2 つ:
 * - RedshiftServerless: Namespace / Workgroup を新規作成する (デフォルト)
 * - ExistingRedshiftServerless: 既存の Workgroup を参照する (existingRedshift モード)
 *
 * 本プロジェクトの Redshift 接続はすべて Data API (WorkgroupName + SecretArn) 経由で、
 * VPC 内通信を必要としないため、この 3 つの情報だけで接続が完結する。
 */
export interface IRedshiftConnection {
  /** Data API の WorkgroupName に渡す Workgroup 名 */
  readonly workgroupName: string;
  /** Data API の Database に渡すデータベース名 */
  readonly dbName: string;
  /** Agent (読み取り専用 DB ユーザー) の認証情報 Secret ({username, password}) */
  readonly agentSecret: secretsmanager.ISecret;
  /** Data API 実行権限 + 指定 Secret の読み取り権限を付与する */
  grantDataApi(grantee: iam.IGrantable, secret: secretsmanager.ISecret): void;
}

/**
 * Data API 権限を付与する (Secrets Manager 認証用)。
 *
 * - ExecuteStatement / BatchExecuteStatement → workgroup ARN でスコープ
 * - CancelStatement / DescribeStatement / GetStatementResult / ListStatements
 *   → リソースレベル制限非対応のため Resource: "*"（AWS 公式ドキュメント準拠）
 * - secretsmanager:GetSecretValue → 指定 Secret のみ
 *
 * GetCredentials は Secrets Manager 認証では不要なため付与しない。
 */
export function grantRedshiftDataApi(
  scope: Construct,
  grantee: iam.IGrantable,
  secret: secretsmanager.ISecret,
): void {
  const stack = cdk.Stack.of(scope);
  const workgroupArn = `arn:aws:redshift-serverless:${stack.region}:${stack.account}:workgroup/*`;

  grantee.grantPrincipal.addToPrincipalPolicy(
    new iam.PolicyStatement({
      actions: [
        'redshift-data:ExecuteStatement',
        'redshift-data:BatchExecuteStatement',
      ],
      resources: [workgroupArn],
    }),
  );

  grantee.grantPrincipal.addToPrincipalPolicy(
    new iam.PolicyStatement({
      actions: [
        'redshift-data:CancelStatement',
        'redshift-data:DescribeStatement',
        'redshift-data:GetStatementResult',
        'redshift-data:ListStatements',
      ],
      resources: ['*'],
    }),
  );

  // 指定された Secret のみ読み取り可能
  secret.grantRead(grantee);
}
