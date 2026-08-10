import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { Database } from './database';
import { RedshiftServerless } from './redshift-serverless';
import { ExistingRedshiftServerless } from './existing-redshift';
import { RedshiftReadonlyValidation } from './redshift-readonly-validation';
import { IRedshiftConnection } from './redshift-connection';
import { CsvStorage } from './csv-storage';
import { CsvDownloadStorage } from './csv-download-storage';
import { AgentBackend } from './agent-backend';
import { AdminBackend, CsvUploadProps } from './admin-backend';
import { RegionalWaf } from './regional-waf';

/** 既存 Redshift Serverless を参照する場合の設定 (cdk.json の existingRedshift context) */
export interface ExistingRedshiftProps {
  /** 既存 Workgroup 名 */
  workgroupName: string;
  /** データベース名 */
  database: string;
  /** 読み取り専用 DB ユーザーの Secret 完全 ARN ({username, password} 形式) */
  secretArn: string;
}

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
  /**
   * 既存の Redshift Serverless を参照する場合に指定 (existingRedshift モード)。
   * 指定時は Redshift / CSV バケット / COPY・UNLOAD 用 IAM Role を新規作成せず、
   * CSV アップロード (Upload & Build) と CSV ダウンロード (UNLOAD) 機能を無効化する。
   * 代わりに Admin UI で既存テーブルからスキーマを自動生成できる。
   */
  existingRedshift?: ExistingRedshiftProps;
}

export class DwhAgentStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: DwhAgentStackProps) {
    super(scope, id, props);

    // ========================================
    // 共通リソース
    // ========================================

    const database = new Database(this, 'Database');

    const regionalWaf = new RegionalWaf(this, 'RegionalWaf', {
      allowedCidrs: props.allowedCidrs,
      allowedIpv6Cidrs: props.allowedIpv6Cidrs,
    });

    // ========================================
    // Redshift (新規作成 or 既存参照)
    // ========================================

    let redshift: IRedshiftConnection;
    let csvUpload: CsvUploadProps | undefined;
    let downloadBucket: CsvDownloadStorage | undefined;
    let redshiftUnloadRoleArn: string | undefined;
    let csvStorage: CsvStorage | undefined;

    if (props.existingRedshift) {
      // --- existingRedshift モード: 既存 Workgroup を参照 ---
      const existing = new ExistingRedshiftServerless(this, 'ExistingRedshift', {
        workgroupName: props.existingRedshift.workgroupName,
        database: props.existingRedshift.database,
        agentSecretArn: props.existingRedshift.secretArn,
      });
      redshift = existing;

      // ガードレール: 提供された DB ユーザーが読み取り専用でなければデプロイを失敗させる
      new RedshiftReadonlyValidation(this, 'ReadonlyValidation', {
        redshift: existing,
      });
    } else {
      // --- デフォルト: Redshift Serverless + CSV 系リソースを新規作成 ---
      csvStorage = new CsvStorage(this, 'CsvStorage', {
        existingBucketName: props.csvInputBucketName,
        allowOrigin: props.allowOrigin,
      });

      // CSV ダウンロード機能用バケット
      downloadBucket = new CsvDownloadStorage(this, 'CsvDownloadStorage', {
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
      downloadBucket.bucket.grantReadWrite(redshiftUnloadRole);
      // UNLOAD 専用 Role の ARN を明示指定する。
      // `IAM_ROLE default` には頼らず、SQL 内で常に Role ARN を埋め込む。
      // これにより Namespace の defaultIamRoleArn 設定の如何に関わらず、
      // 必ず unloadRole で UNLOAD が実行される。
      redshiftUnloadRoleArn = redshiftUnloadRole.roleArn;

      const created = new RedshiftServerless(this, 'Redshift', {
        adminRole: redshiftAdminRole,
        unloadRole: redshiftUnloadRole,
      });
      redshift = created;

      csvUpload = {
        bucket: csvStorage.bucket,
        bucketIsExisting: csvStorage.isExisting,
        redshiftAdminRoleArn: redshiftAdminRole.roleArn,
        redshift: created,
      };
    }

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
      downloadBucket: downloadBucket?.bucket,
      redshiftUnloadIamRoleArn: redshiftUnloadRoleArn,
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
      csvUpload,
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
    if (csvStorage) {
      new cdk.CfnOutput(this, 'CsvBucketName', {
        value: csvStorage.bucket.bucketName,
      });
    }
    if (downloadBucket) {
      new cdk.CfnOutput(this, 'CsvDownloadBucketName', {
        value: downloadBucket.bucket.bucketName,
      });
    }
    new cdk.CfnOutput(this, 'ConfigTableName', {
      value: database.configTable.tableName,
    });
    new cdk.CfnOutput(this, 'AgentCoreRuntimeArn', {
      value: agentBackend.agentCoreRuntime.runtime.agentRuntimeArn,
    });
  }
}
