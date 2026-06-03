import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { Database } from './database';
import { RedshiftServerless } from './redshift-serverless';
import { CsvStorage } from './csv-storage';
import { CsvDownloadStorage } from './csv-download-storage';
import { AgentBackend } from './agent-backend';
import { AdminBackend } from './admin-backend';
import { RegionalWaf } from './regional-waf';

export interface DwhAgentStackProps extends cdk.StackProps {
  allowOrigin: string;
  allowedCidrs: string[];
  allowedIpv6Cidrs?: string[];
  webAclArn: string;
  bedrockModelId: string;
  /** 既存 CSV バケット名 (未指定時は新規作成) */
  csvInputBucketName?: string;
  sqlResultThreshold: number;
  /** Prompt Cache を有効にするか */
  enablePromptCache: boolean;
  /** Agent UserPool に作るテストユーザー名 (空の場合は作らない) */
  testAgentUser: string;
  /** Admin UserPool に作るテストユーザー名 (空の場合は作らない) */
  testAdminUser: string;
  /** CSV ダウンロードファイルの S3 保持日数 (default: 7) */
  csvDownloadExpirationDays?: number;
  /** CSV ダウンロード presigned URL の有効秒数 (default: 3600) */
  csvDownloadPresignTtlSeconds?: number;
}

export class DwhAgentStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: DwhAgentStackProps) {
    super(scope, id, props);

    // ========================================
    // 共通リソース
    // ========================================

    const database = new Database(this, 'Database');

    const csvStorage = new CsvStorage(this, 'CsvStorage', {
      existingBucketName: props.csvInputBucketName,
      allowOrigin: props.allowOrigin,
    });

    // CSV ダウンロード機能用バケット
    const csvDownloadStorage = new CsvDownloadStorage(this, 'CsvDownloadStorage', {
      expirationDays: props.csvDownloadExpirationDays ?? 7,
    });

    const redshiftAdminRole = new iam.Role(this, 'RedshiftAdminRole', {
      assumedBy: new iam.ServicePrincipal('redshift.amazonaws.com'),
      description: 'Role for Redshift Serverless to read CSV from S3 (COPY)',
    });
    csvStorage.bucket.grantRead(redshiftAdminRole);

    // UNLOAD 専用 Role (CSV ダウンロード機能用)
    // - agent_readonly が発行する SQL は _validate_sql_for_unload で UNLOAD キーワードを禁止しているが、
    //   万一バリデーションをすり抜けても IAM レベルで input バケットへのアクセスや
    //   攻撃者バケットへの書き込みを拒否できるよう、admin Role と権限を完全に分離する。
    // - download bucket への ReadWrite のみ付与 (Read は presigned URL 発行とは独立。
    //   Redshift が UNLOAD 完了確認に使う)。input bucket への権限は持たせない。
    const redshiftUnloadRole = new iam.Role(this, 'RedshiftUnloadRole', {
      assumedBy: new iam.ServicePrincipal('redshift.amazonaws.com'),
      description: 'Role for Redshift Serverless to UNLOAD CSV download files (least privilege)',
    });
    csvDownloadStorage.bucket.grantReadWrite(redshiftUnloadRole);

    const redshift = new RedshiftServerless(this, 'Redshift', {
      adminRole: redshiftAdminRole,
      unloadRole: redshiftUnloadRole,
    });

    const regionalWaf = new RegionalWaf(this, 'RegionalWaf', {
      allowedCidrs: props.allowedCidrs,
      allowedIpv6Cidrs: props.allowedIpv6Cidrs,
    });

    // ========================================
    // Agent 系 (Chat UI 用)
    // ========================================

    const agentBackend = new AgentBackend(this, 'AgentBackend', {
      allowOrigin: props.allowOrigin,
      allowedCidrs: props.allowedCidrs,
      sessionsTable: database.sessionsTable,
      configTable: database.configTable,
      filesTable: database.filesTable,
      bedrockModelId: props.bedrockModelId,
      redshift,
      regionalWaf,
      webAclArn: props.webAclArn,
      sqlResultThreshold: props.sqlResultThreshold,
      enablePromptCache: props.enablePromptCache,
      testUsername: props.testAgentUser,
      downloadBucket: csvDownloadStorage.bucket,
      // UNLOAD 専用 Role の ARN を明示指定する。
      // `IAM_ROLE default` には頼らず、SQL 内で常に Role ARN を埋め込む。
      // これにより Namespace の defaultIamRoleArn 設定の如何に関わらず、
      // 必ず unloadRole で UNLOAD が実行される。
      redshiftUnloadIamRoleArn: redshiftUnloadRole.roleArn,
      downloadPresignTtlSeconds: props.csvDownloadPresignTtlSeconds ?? 3600,
    });

    // ========================================
    // Admin 系 (管理 UI 用)
    // ========================================

    const adminBackend = new AdminBackend(this, 'AdminBackend', {
      allowOrigin: props.allowOrigin,
      allowedCidrs: props.allowedCidrs,
      configTable: database.configTable,
      bedrockModelId: props.bedrockModelId,
      redshift,
      csvBucket: csvStorage.bucket,
      csvBucketIsExisting: csvStorage.isExisting,
      redshiftAdminRoleArn: redshiftAdminRole.roleArn,
      regionalWaf,
      webAclArn: props.webAclArn,
      testUsername: props.testAdminUser,
    });

    // ========================================
    // Stack 出力
    // ========================================

    new cdk.CfnOutput(this, 'AgentFrontendUrl', {
      value: `https://${agentBackend.frontend.distribution.distributionDomainName}`,
    });
    new cdk.CfnOutput(this, 'AgentCognitoUserPoolId', {
      value: agentBackend.cognito.userPool.userPoolId,
    });
    new cdk.CfnOutput(this, 'AdminFrontendUrl', {
      value: `https://${adminBackend.frontend.distribution.distributionDomainName}`,
    });
    new cdk.CfnOutput(this, 'AdminCognitoUserPoolId', {
      value: adminBackend.cognito.userPool.userPoolId,
    });
    new cdk.CfnOutput(this, 'CsvBucketName', {
      value: csvStorage.bucket.bucketName,
    });
    new cdk.CfnOutput(this, 'CsvDownloadBucketName', {
      value: csvDownloadStorage.bucket.bucketName,
    });
    new cdk.CfnOutput(this, 'ConfigTableName', {
      value: database.configTable.tableName,
    });
    new cdk.CfnOutput(this, 'AgentCoreRuntimeArn', {
      value: agentBackend.agentCoreRuntime.runtime.agentRuntimeArn,
    });
  }
}
