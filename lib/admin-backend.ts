import * as cdk from 'aws-cdk-lib';
import * as path from 'node:path';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as apigw from 'aws-cdk-lib/aws-apigateway';
import { RedshiftServerless } from './redshift-serverless';
import { Cognito } from './cognito';
import { PublicRestApi } from './public-rest-api';
import { RegionalWaf } from './regional-waf';
import { Frontend } from './frontend';
import { RedshiftInitWorkflow } from './redshift-init-workflow';

export interface AdminBackendProps {
  allowOrigin: string;
  allowedCidrs: string[];
  configTable: dynamodb.ITable;
  bedrockModelId: string;
  redshift: RedshiftServerless;
  csvBucket: s3.IBucket;
  redshiftAdminRoleArn: string;
  regionalWaf: RegionalWaf;
  webAclArn: string;
  /** UserPool に作るテストユーザー名 (空の場合は作らない) */
  testUsername?: string;
}

/**
 * Admin 系リソース一式
 * Cognito + Lambda + API Gateway + WAF + Frontend
 */
export class AdminBackend extends Construct {
  readonly handler: lambda.DockerImageFunction;
  readonly cognito: Cognito;
  readonly api: PublicRestApi;
  readonly frontend: Frontend;

  constructor(scope: Construct, id: string, props: AdminBackendProps) {
    super(scope, id);

    // Cognito
    this.cognito = new Cognito(this, 'Cognito', {
      testUsername: props.testUsername,
    });

    // Lambda
    this.handler = new lambda.DockerImageFunction(this, 'Handler', {
      code: lambda.DockerImageCode.fromImageAsset(
        path.join(__dirname, '../lambda/adminwebbackend'),
        { platform: cdk.aws_ecr_assets.Platform.LINUX_ARM64 },
      ),
      architecture: lambda.Architecture.ARM_64,
      timeout: cdk.Duration.minutes(15),
      memorySize: 2048,
      tracing: lambda.Tracing.ACTIVE,
      environment: {
        ALLOW_ORIGIN: props.allowOrigin,
        CONFIG_TABLE_NAME: props.configTable.tableName,
        BEDROCK_MODEL_ID: props.bedrockModelId,
        CSV_BUCKET_NAME: props.csvBucket.bucketName,
      },
    });

    // DynamoDB config テーブル読み書き
    props.configTable.grantReadWriteData(this.handler);

    // S3 CSV バケット読み取り + PutObject (presigned URL 署名用)
    props.csvBucket.grantRead(this.handler);
    props.csvBucket.grantPut(this.handler);

    // Bedrock 呼び出し権限
    this.handler.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['bedrock:InvokeModelWithResponseStream', 'bedrock:InvokeModel'],
        resources: ['*'],
      }),
    );

    // Redshift Init Workflow (Step Functions)
    const initWorkflow = new RedshiftInitWorkflow(this, 'InitWorkflow', {
      configTable: props.configTable,
      redshift: props.redshift,
      csvBucket: props.csvBucket,
      redshiftAdminRoleArn: props.redshiftAdminRoleArn,
    });

    // adminwebbackend Lambda に Step Functions 実行権限を付与
    initWorkflow.stateMachine.grantStartExecution(this.handler);
    initWorkflow.stateMachine.grantRead(this.handler);

    // ステートマシン ARN を環境変数で渡す
    this.handler.addEnvironment('INIT_WORKFLOW_STATE_MACHINE_ARN', initWorkflow.stateMachine.stateMachineArn);

    // REST API
    this.api = new PublicRestApi(this, 'Api', {
      allowOrigins: [props.allowOrigin],
    });
    props.regionalWaf.associate('AdminApiWaf', this.api.restApi);

    const authorizer = new apigw.CognitoUserPoolsAuthorizer(this, 'Authorizer', {
      cognitoUserPools: [this.cognito.userPool],
    });

    const stream = { responseTransferMode: apigw.ResponseTransferMode.STREAM };
    this.api.addResource('GET', ['admin', 'agents'], this.handler, authorizer, stream);
    this.api.addResource('POST', ['admin', 'agents'], this.handler, authorizer, stream);
    this.api.addResource('GET', ['admin', 'agents', '{agentId}'], this.handler, authorizer, stream);
    this.api.addResource('PUT', ['admin', 'agents', '{agentId}'], this.handler, authorizer, stream);
    this.api.addResource('DELETE', ['admin', 'agents', '{agentId}'], this.handler, authorizer, stream);
    this.api.addResource('GET', ['admin', 'config'], this.handler, authorizer, stream);
    this.api.addResource('POST', ['admin', 'knowledge'], this.handler, authorizer, stream);
    this.api.addResource('POST', ['admin', 'presigned-urls'], this.handler, authorizer, stream);
    this.api.addResource('POST', ['admin', 'list-csv'], this.handler, authorizer, stream);
    this.api.addResource('POST', ['admin', 'analyze'], this.handler, authorizer, stream);
    this.api.addResource('POST', ['admin', 'apply'], this.handler, authorizer, stream);
    this.api.addResource('POST', ['admin', 'apply-status'], this.handler, authorizer, stream);
    this.api.addResource('GET', ['admin', 'system-prompt'], this.handler, authorizer, stream);
    this.api.addResource('PUT', ['admin', 'system-prompt'], this.handler, authorizer, stream);

    // Frontend
    this.frontend = new Frontend(this, 'Frontend', {
      appName: 'admin',
      webAclArn: props.webAclArn,
      buildEnvironments: {
        VITE_APP_API_ENDPOINT: this.api.url,
        VITE_APP_USER_POOL_ID: this.cognito.userPool.userPoolId,
        VITE_APP_USER_POOL_CLIENT_ID: this.cognito.userPoolClient.userPoolClientId,
      },
    });
  }
}
