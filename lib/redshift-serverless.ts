import * as cdk from 'aws-cdk-lib';
import * as redshiftserverless from 'aws-cdk-lib/aws-redshiftserverless';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';

export interface RedshiftServerlessProps {
  /** Admin用 IAM Role (COPY コマンドで S3 からデータロード) */
  adminRole: iam.IRole;
}



/**
 * Redshift Serverless (Public, Data API 経由接続)
 * L2 construct が存在しないため L1 (CfnNamespace, CfnWorkgroup) を使用
 */
export class RedshiftServerless extends Construct {
  readonly namespace: redshiftserverless.CfnNamespace;
  readonly workgroup: redshiftserverless.CfnWorkgroup;
  readonly adminSecret: secretsmanager.Secret;
  readonly agentSecret: secretsmanager.Secret;
  readonly workgroupName: string;
  readonly namespaceName: string;
  readonly dbName = 'dwh';

  constructor(scope: Construct, id: string, props: RedshiftServerlessProps) {
    super(scope, id);

    const prefix = cdk.Stack.of(this).stackName.toLowerCase();
    this.namespaceName = `${prefix}-dwh-agent-ns`;
    this.workgroupName = `${prefix}-dwh-agent-wg`;

    // Admin credentials (SUPERUSER)
    this.adminSecret = new secretsmanager.Secret(this, 'AdminSecret', {
      description: 'Redshift Serverless admin credentials (SUPERUSER)',
      generateSecretString: {
        secretStringTemplate: JSON.stringify({ username: 'admin' }),
        generateStringKey: 'password',
        excludePunctuation: true,
        passwordLength: 32,
      },
    });

    // Agent readonly credentials (SELECT のみ)
    // ※ DB ユーザー作成は apply ワークフロー (Step Functions) で自動実行される
    this.agentSecret = new secretsmanager.Secret(this, 'AgentSecret', {
      description: 'Redshift Serverless agent_readonly credentials (SELECT only)',
      generateSecretString: {
        secretStringTemplate: JSON.stringify({ username: 'agent_readonly' }),
        generateStringKey: 'password',
        excludePunctuation: true,
        passwordLength: 32,
      },
    });

    // Namespace
    this.namespace = new redshiftserverless.CfnNamespace(this, 'Namespace', {
      namespaceName: this.namespaceName,
      dbName: this.dbName,
      adminUsername: 'admin',
      adminUserPassword: this.adminSecret.secretValueFromJson('password').unsafeUnwrap(),
      iamRoles: [props.adminRole.roleArn],
      defaultIamRoleArn: props.adminRole.roleArn,
    });

    // Workgroup (Data API 経由のみ接続するため publiclyAccessible: false)
    // Data API は AWS 内部の HTTP エンドポイント経由で通信するため、
    // JDBC/ODBC 用の public IP は不要
    this.workgroup = new redshiftserverless.CfnWorkgroup(this, 'Workgroup', {
      workgroupName: this.workgroupName,
      namespaceName: this.namespaceName,
      publiclyAccessible: false,
      baseCapacity: 8, // 最小 RPU
    });
    this.workgroup.addDependency(this.namespace);
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
  grantDataApi(grantee: iam.IGrantable, secret: secretsmanager.ISecret): void {
    const workgroupArn = `arn:aws:redshift-serverless:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:workgroup/*`;

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
}
