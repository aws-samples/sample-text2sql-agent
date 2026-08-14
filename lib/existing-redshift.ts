import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';
import { IRedshiftConnection, grantRedshiftDataApi } from './redshift-connection';

export interface ExistingRedshiftServerlessProps {
  /** 既存 Redshift Serverless の Workgroup 名 */
  workgroupName: string;
  /** 接続先データベース名 */
  database: string;
  /**
   * 読み取り専用 DB ユーザーの認証情報 Secret の完全 ARN (サフィックス付き)。
   * Secret は {"username": "...", "password": "..."} 形式であること。
   * DB ユーザーと Secret はこのスタックの外で事前に作成する (docs/MANUAL_DEPLOYMENT.md 参照)。
   */
  agentSecretArn: string;
}

/**
 * 既存の Redshift Serverless Workgroup を参照する (existingRedshift モード)。
 *
 * 何も作成しない (VPC / Namespace / Workgroup / Secret はすべて既存参照)。
 * 既存の Redshift リソース自体への変更も一切行わない。
 *
 * 制約:
 * - Workgroup / Secret はスタックと同一アカウント・同一リージョンであること
 *   (Data API とその IAM ポリシーがリージョナルであるため)
 * - DB ユーザーは SELECT のみの読み取り専用であること
 *   (デプロイ時に RedshiftReadonlyValidation カスタムリソースで検証される)
 */
export class ExistingRedshiftServerless extends Construct implements IRedshiftConnection {
  readonly workgroupName: string;
  readonly dbName: string;
  readonly agentSecret: secretsmanager.ISecret;

  constructor(scope: Construct, id: string, props: ExistingRedshiftServerlessProps) {
    super(scope, id);

    const stack = cdk.Stack.of(this);

    // 同一アカウント・同一リージョン制約の検証 (synth 時)
    const arn = cdk.Arn.split(props.agentSecretArn, cdk.ArnFormat.COLON_RESOURCE_NAME);
    if (!cdk.Token.isUnresolved(stack.region) && arn.region && arn.region !== stack.region) {
      throw new Error(
        `existingRedshift.secretArn のリージョン (${arn.region}) がスタックのリージョン (${stack.region}) と一致しません。` +
        '既存 Redshift モードはスタックと同一リージョンのみサポートします。',
      );
    }
    if (!cdk.Token.isUnresolved(stack.account) && arn.account && arn.account !== stack.account) {
      throw new Error(
        `existingRedshift.secretArn のアカウント (${arn.account}) がスタックのアカウント (${stack.account}) と一致しません。` +
        '既存 Redshift モードはスタックと同一アカウントのみサポートします。',
      );
    }
    // fromSecretCompleteArn はサフィックス付き完全 ARN を要求する。
    // サフィックス無し ARN を渡すと IAM ポリシーのリソース指定が実際の Secret と一致せず
    // 実行時に AccessDenied になるため、ここで弾く。
    if (!/-[A-Za-z0-9]{6}$/.test(props.agentSecretArn)) {
      throw new Error(
        'existingRedshift.secretArn には Secrets Manager の完全 ARN (末尾 6 文字のサフィックス付き) を指定してください。' +
        `指定値: ${props.agentSecretArn}`,
      );
    }

    this.workgroupName = props.workgroupName;
    this.dbName = props.database;
    this.agentSecret = secretsmanager.Secret.fromSecretCompleteArn(
      this, 'AgentSecret', props.agentSecretArn,
    );
  }

  grantDataApi(grantee: iam.IGrantable, secret: secretsmanager.ISecret): void {
    grantRedshiftDataApi(this, grantee, secret);
  }
}
