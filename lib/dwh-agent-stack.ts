import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { Database } from './database';
import { RedshiftServerless } from './redshift-serverless';
import { CsvStorage } from './csv-storage';
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

    const redshiftAdminRole = new iam.Role(this, 'RedshiftAdminRole', {
      assumedBy: new iam.ServicePrincipal('redshift.amazonaws.com'),
      description: 'Role for Redshift Serverless to read CSV from S3 via COPY command',
    });
    csvStorage.bucket.grantRead(redshiftAdminRole);

    const redshift = new RedshiftServerless(this, 'Redshift', {
      adminRole: redshiftAdminRole,
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
      bedrockModelId: props.bedrockModelId,
      redshift,
      regionalWaf,
      webAclArn: props.webAclArn,
      sqlResultThreshold: props.sqlResultThreshold,
      enablePromptCache: props.enablePromptCache,
      testUsername: props.testAgentUser,
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
    new cdk.CfnOutput(this, 'ConfigTableName', {
      value: database.configTable.tableName,
    });
    new cdk.CfnOutput(this, 'AgentCoreRuntimeArn', {
      value: agentBackend.agentCoreRuntime.runtime.agentRuntimeArn,
    });
  }
}
