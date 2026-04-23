import * as cdk from 'aws-cdk-lib';
import * as path from 'node:path';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as apigw from 'aws-cdk-lib/aws-apigateway';
import { RedshiftServerless } from './redshift-serverless';
import { AgentCoreRuntime } from './agentcore-runtime';
import { Cognito } from './cognito';
import { PublicRestApi } from './public-rest-api';
import { RegionalWaf } from './regional-waf';
import { Frontend } from './frontend';

export interface AgentBackendProps {
  allowOrigin: string;
  allowedCidrs: string[];
  sessionsTable: dynamodb.ITable;
  configTable: dynamodb.ITable;
  bedrockModelId: string;
  redshift: RedshiftServerless;
  regionalWaf: RegionalWaf;
  webAclArn: string;
  sqlResultThreshold: number;
  enablePromptCache: boolean;
  /** UserPool に作るテストユーザー名 (空の場合は作らない) */
  testUsername?: string;
}

/**
 * Agent 系リソース一式
 * AgentCore Runtime + Cognito + Lambda (プロキシ) + API Gateway + WAF + Frontend
 */
export class AgentBackend extends Construct {
  readonly handler: lambda.DockerImageFunction;
  readonly cognito: Cognito;
  readonly api: PublicRestApi;
  readonly frontend: Frontend;
  readonly agentCoreRuntime: AgentCoreRuntime;

  constructor(scope: Construct, id: string, props: AgentBackendProps) {
    super(scope, id);

    // AgentCore Runtime (Strands Agent をホスト)
    this.agentCoreRuntime = new AgentCoreRuntime(this, 'AgentCoreRuntime', {
      sessionsTable: props.sessionsTable,
      configTable: props.configTable,
      bedrockModelId: props.bedrockModelId,
      redshift: props.redshift,
      sqlResultThreshold: props.sqlResultThreshold,
      enablePromptCache: props.enablePromptCache,
    });

    // Cognito
    this.cognito = new Cognito(this, 'Cognito', {
      testUsername: props.testUsername,
    });

    // Lambda (セッション管理 API + AgentCore invoke プロキシ)
    this.handler = new lambda.DockerImageFunction(this, 'Handler', {
      code: lambda.DockerImageCode.fromImageAsset(
        path.join(__dirname, '../lambda/agentwebbackend'),
        { platform: cdk.aws_ecr_assets.Platform.LINUX_ARM64 },
      ),
      architecture: lambda.Architecture.ARM_64,
      timeout: cdk.Duration.minutes(15),
      memorySize: 2048,
      environment: {
        ALLOW_ORIGIN: props.allowOrigin,
        SESSIONS_TABLE_NAME: props.sessionsTable.tableName,
        CONFIG_TABLE_NAME: props.configTable.tableName,
        AGENTCORE_RUNTIME_ARN: this.agentCoreRuntime.runtime.agentRuntimeArn,
      },
    });

    // DynamoDB sessions 権限 (sessions API で使用)
    props.sessionsTable.grantReadWriteData(this.handler);

    // DynamoDB config 権限 (agents API で使用)
    props.configTable.grantReadData(this.handler);

    // AgentCore Runtime invoke 権限
    this.agentCoreRuntime.runtime.grantInvokeRuntime(this.handler);

    // REST API
    this.api = new PublicRestApi(this, 'Api', {
      allowOrigins: [props.allowOrigin],
    });
    props.regionalWaf.associate('AgentApiWaf', this.api.restApi);

    const authorizer = new apigw.CognitoUserPoolsAuthorizer(this, 'Authorizer', {
      cognitoUserPools: [this.cognito.userPool],
    });

    const stream = { responseTransferMode: apigw.ResponseTransferMode.STREAM };
    this.api.addResource('POST', ['chat'], this.handler, authorizer, stream);
    this.api.addResource('GET', ['agents'], this.handler, authorizer, stream);
    this.api.addResource('GET', ['sessions'], this.handler, authorizer, stream);
    this.api.addResource('GET', ['sessions', '{id}'], this.handler, authorizer, stream);
    this.api.addResource('DELETE', ['sessions', '{id}'], this.handler, authorizer, stream);

    // Frontend
    this.frontend = new Frontend(this, 'Frontend', {
      appName: 'agent',
      webAclArn: props.webAclArn,
      buildEnvironments: {
        VITE_APP_API_ENDPOINT: this.api.url,
        VITE_APP_USER_POOL_ID: this.cognito.userPool.userPoolId,
        VITE_APP_USER_POOL_CLIENT_ID: this.cognito.userPoolClient.userPoolClientId,
      },
    });
  }
}
