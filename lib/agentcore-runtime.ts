import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import * as path from 'node:path';
import { Runtime, AgentRuntimeArtifact } from '@aws-cdk/aws-bedrock-agentcore-alpha';
import { RedshiftServerless } from './redshift-serverless';

export interface AgentCoreRuntimeProps {
  sessionsTable: dynamodb.ITable;
  configTable: dynamodb.ITable;
  bedrockModelId: string;
  redshift: RedshiftServerless;
  sqlResultThreshold: number;
  enablePromptCache: boolean;
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
        BEDROCK_MODEL_ID: props.bedrockModelId,
        REDSHIFT_WORKGROUP_NAME: props.redshift.workgroupName,
        REDSHIFT_DATABASE: props.redshift.dbName,
        REDSHIFT_SECRET_ARN: props.redshift.agentSecret.secretArn,
        SQL_RESULT_THRESHOLD: String(props.sqlResultThreshold),
        ENABLE_PROMPT_CACHE: String(props.enablePromptCache),
      },
    });

    // --- プロジェクト固有権限 ---

    // DynamoDB
    props.sessionsTable.grantReadWriteData(this.runtime);
    props.configTable.grantReadData(this.runtime);

    // Bedrock モデル呼び出し
    this.runtime.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: ['*'],
    }));

    // Redshift Data API + Secrets Manager
    props.redshift.grantDataApi(this.runtime, props.redshift.agentSecret);
  }
}
