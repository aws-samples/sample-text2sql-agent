import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';
import * as path from 'node:path';
import { Runtime, AgentRuntimeArtifact } from '@aws-cdk/aws-bedrock-agentcore-alpha';
import { RedshiftServerless } from './redshift-serverless';

export interface AgentCoreRuntimeProps {
  sessionsTable: dynamodb.ITable;
  configTable: dynamodb.ITable;
  filesTable: dynamodb.ITable;
  bedrockModelId: string;
  redshift: RedshiftServerless;
  sqlResultThreshold: number;
  enablePromptCache: boolean;
  /** CSV ダウンロード用バケット (UNLOAD 出力先 + presigned URL 発行) */
  downloadBucket: s3.IBucket;
  /** UNLOAD ... IAM_ROLE に渡す ARN ("default" の場合は文字列 "default") */
  redshiftUnloadIamRoleArn: string;
  /** presigned URL の有効期限秒。FilesTable の expires_at にも同値を使用 */
  downloadPresignTtlSeconds: number;
}

export class AgentCoreRuntime extends Construct {
  readonly runtime: Runtime;

  constructor(scope: Construct, id: string, props: AgentCoreRuntimeProps) {
    super(scope, id);

    // agent/ ディレクトリの Dockerfile からイメージをビルド
    const artifact = AgentRuntimeArtifact.fromAsset(
      path.join(__dirname, '../agent'),
      { file: 'Dockerfile' },
    );

    // Runtime (ECR, CodeBuild, Execution Role のベース権限は L2 が自動生成)
    this.runtime = new Runtime(this, 'Runtime', {
      agentRuntimeArtifact: artifact,
      environmentVariables: {
        AWS_REGION: cdk.Stack.of(this).region,
        SESSIONS_TABLE_NAME: props.sessionsTable.tableName,
        CONFIG_TABLE_NAME: props.configTable.tableName,
        FILES_TABLE_NAME: props.filesTable.tableName,
        BEDROCK_MODEL_ID: props.bedrockModelId,
        REDSHIFT_WORKGROUP_NAME: props.redshift.workgroupName,
        REDSHIFT_DATABASE: props.redshift.dbName,
        REDSHIFT_SECRET_ARN: props.redshift.agentSecret.secretArn,
        SQL_RESULT_THRESHOLD: String(props.sqlResultThreshold),
        ENABLE_PROMPT_CACHE: String(props.enablePromptCache),
        DOWNLOAD_BUCKET_NAME: props.downloadBucket.bucketName,
        DOWNLOAD_PRESIGN_TTL_SECONDS: String(props.downloadPresignTtlSeconds),
        REDSHIFT_UNLOAD_IAM_ROLE_ARN: props.redshiftUnloadIamRoleArn,
      },
    });

    // --- プロジェクト固有権限 ---

    // DynamoDB
    props.sessionsTable.grantReadWriteData(this.runtime);
    props.configTable.grantReadData(this.runtime);
    props.filesTable.grantReadWriteData(this.runtime);

    // Bedrock モデル呼び出し
    this.runtime.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: ['*'],
    }));

    // Redshift Data API + Secrets Manager
    props.redshift.grantDataApi(this.runtime, props.redshift.agentSecret);

    // CSV ダウンロード用バケット
    // - presigned URL を発行するため Read 権限が必要 (list_objects_v2 + GetObject)
    // - UNLOAD は Redshift の adminRole が書くので Runtime 自身の Write は不要
    props.downloadBucket.grantRead(this.runtime);
  }
}
