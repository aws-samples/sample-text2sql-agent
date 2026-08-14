import * as cdk from 'aws-cdk-lib';
import * as path from 'node:path';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as apigw from 'aws-cdk-lib/aws-apigateway';
import { RedshiftServerless } from './redshift-serverless';
import { IRedshiftConnection } from './redshift-connection';
import { Cognito } from './cognito';
import { PublicRestApi } from './public-rest-api';
import { RegionalWaf } from './regional-waf';
import { Frontend } from './frontend';
import { RedshiftInitWorkflow } from './redshift-init-workflow';

/**
 * CSV アップロード機能 (Upload & Build) 一式の設定。
 * existingRedshift モードでは未指定にし、機能ごと無効化する
 * (既存 Redshift には COPY 用 IAM Role を関連付けられず、
 * ワークフローの DROP TABLE が既存データを破壊しうるため)。
 */
export interface CsvUploadProps {
  bucket: s3.IBucket;
  /** 既存バケットを参照しているか (true のときフロントエンドでローカルアップロードを無効化) */
  bucketIsExisting: boolean;
  redshiftAdminRoleArn: string;
  /** InitWorkflow が使う新規作成の Redshift (adminSecret が必要なため型を狭める) */
  redshift: RedshiftServerless;
}

export interface AdminBackendProps {
  allowOrigin: string;
  allowedCidrs: string[];
  configTable: dynamodb.ITable;
  bedrockModelId: string;
  redshift: IRedshiftConnection;
  /** CSV アップロード機能。未指定時は Upload & Build を無効化 (existingRedshift モード) */
  csvUpload?: CsvUploadProps;
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

    const existingRedshiftMode = props.csvUpload === undefined;

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
        ...(props.csvUpload ? {
          CSV_BUCKET_NAME: props.csvUpload.bucket.bucketName,
        } : {
          // existingRedshift モード: スキーマ自動生成用に Data API 接続情報を渡す
          EXISTING_REDSHIFT_MODE: 'true',
          REDSHIFT_WORKGROUP_NAME: props.redshift.workgroupName,
          REDSHIFT_DATABASE: props.redshift.dbName,
          REDSHIFT_SECRET_ARN: props.redshift.agentSecret.secretArn,
        }),
      },
    });

    // DynamoDB config テーブル読み書き
    props.configTable.grantReadWriteData(this.handler);

    // Bedrock 呼び出し権限
    this.handler.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['bedrock:InvokeModelWithResponseStream', 'bedrock:InvokeModel'],
        resources: ['*'],
      }),
    );

    if (props.csvUpload) {
      // S3 CSV バケット読み取り + PutObject (presigned URL 署名用)
      props.csvUpload.bucket.grantRead(this.handler);
      props.csvUpload.bucket.grantPut(this.handler);

      // Redshift Init Workflow (Step Functions)
      const initWorkflow = new RedshiftInitWorkflow(this, 'InitWorkflow', {
        configTable: props.configTable,
        redshift: props.csvUpload.redshift,
        csvBucket: props.csvUpload.bucket,
        redshiftAdminRoleArn: props.csvUpload.redshiftAdminRoleArn,
      });

      // adminwebbackend Lambda に Step Functions 実行権限を付与
      initWorkflow.stateMachine.grantStartExecution(this.handler);
      initWorkflow.stateMachine.grantRead(this.handler);

      // ステートマシン ARN を環境変数で渡す
      this.handler.addEnvironment('INIT_WORKFLOW_STATE_MACHINE_ARN', initWorkflow.stateMachine.stateMachineArn);
    } else {
      // existingRedshift モード: スキーマ自動生成 (information_schema 参照) 用に
      // 読み取り専用ユーザーの Secret で Data API を実行できるようにする
      props.redshift.grantDataApi(this.handler, props.redshift.agentSecret);
    }

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
    // existingRedshift モード用 (通常モードでは 400 を返す)
    this.api.addResource('GET', ['admin', 'redshift', 'tables'], this.handler, authorizer, stream);
    this.api.addResource('POST', ['admin', 'redshift', 'generate-schema'], this.handler, authorizer, stream);
    this.api.addResource('POST', ['admin', 'redshift', 'apply'], this.handler, authorizer, stream);

    // Frontend
    this.frontend = new Frontend(this, 'Frontend', {
      appName: 'admin',
      webAclArn: props.webAclArn,
      buildEnvironments: {
        VITE_APP_API_ENDPOINT: this.api.url,
        VITE_APP_USER_POOL_ID: this.cognito.userPool.userPoolId,
        VITE_APP_USER_POOL_CLIENT_ID: this.cognito.userPoolClient.userPoolClientId,
        VITE_APP_EXISTING_REDSHIFT_MODE: existingRedshiftMode ? 'true' : 'false',
        ...(props.csvUpload ? {
          VITE_APP_CSV_BUCKET_NAME: props.csvUpload.bucket.bucketName,
          VITE_APP_CSV_BUCKET_IS_EXISTING: props.csvUpload.bucketIsExisting ? 'true' : 'false',
        } : {}),
      },
    });
  }
}
