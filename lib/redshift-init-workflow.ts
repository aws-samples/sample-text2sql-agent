import * as cdk from 'aws-cdk-lib';
import * as path from 'node:path';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as tasks from 'aws-cdk-lib/aws-stepfunctions-tasks';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { PythonFunction } from '@aws-cdk/aws-lambda-python-alpha';
import { RedshiftServerless } from './redshift-serverless';

export interface RedshiftInitWorkflowProps {
  configTable: dynamodb.ITable;
  redshift: RedshiftServerless;
  csvBucket: s3.IBucket;
  redshiftAdminRoleArn: string;
}

/**
 * Redshift テーブル初期化ワークフロー (Step Functions)
 *
 * StartBuild → Wait → CheckAndFinalize → Choice (loop or end)
 */
export class RedshiftInitWorkflow extends Construct {
  readonly stateMachine: sfn.StateMachine;

  constructor(scope: Construct, id: string, props: RedshiftInitWorkflowProps) {
    super(scope, id);

    const entry = path.join(__dirname, '../lambda/redshiftinitworkflow');

    const commonEnv = {
      REDSHIFT_WORKGROUP_NAME: props.redshift.workgroupName,
      REDSHIFT_DATABASE: props.redshift.dbName,
      REDSHIFT_SECRET_ARN: props.redshift.adminSecret.secretArn,
      REDSHIFT_AGENT_SECRET_ARN: props.redshift.agentSecret.secretArn,
      REDSHIFT_ADMIN_ROLE_ARN: props.redshiftAdminRoleArn,
      CSV_BUCKET_NAME: props.csvBucket.bucketName,
      CONFIG_TABLE_NAME: props.configTable.tableName,
    };

    // 共通 IAM Role（describe_statement は execute_statement と同じプリンシパルが必要）
    const sharedRole = new iam.Role(this, 'SharedLambdaRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });

    // Lambda 1: StartBuild
    const startBuildFn = new PythonFunction(this, 'StartBuildFn', {
      entry,
      runtime: lambda.Runtime.PYTHON_3_14,
      architecture: lambda.Architecture.ARM_64,
      index: 'handlers.py',
      handler: 'handle_start_build',
      timeout: cdk.Duration.minutes(15),
      memorySize: 512,
      environment: commonEnv,
      role: sharedRole,
    });

    // Lambda 2: CheckAndFinalize
    const checkAndFinalizeFn = new PythonFunction(this, 'CheckAndFinalizeFn', {
      entry,
      runtime: lambda.Runtime.PYTHON_3_14,
      architecture: lambda.Architecture.ARM_64,
      index: 'handlers.py',
      handler: 'handle_check_and_finalize',
      timeout: cdk.Duration.minutes(5),
      memorySize: 512,
      environment: commonEnv,
      role: sharedRole,
    });

    // 共通 Role に権限付与
    props.configTable.grantReadWriteData(sharedRole);
    props.redshift.grantDataApi(sharedRole, props.redshift.adminSecret);
    props.redshift.agentSecret.grantRead(sharedRole);

    // CSV バケットのリージョンを解決するため HeadBucket を許可
    // (クロスリージョン COPY 対応: Lambda のリージョンではなくバケットの実リージョンを使うため)
    // 注: HeadBucket API に必要な IAM アクションは s3:ListBucket
    sharedRole.addToPrincipalPolicy(new iam.PolicyStatement({
      actions: ['s3:ListBucket'],
      resources: [props.csvBucket.bucketArn],
    }));

    // Step Functions 定義
    const startBuildTask = new tasks.LambdaInvoke(this, 'StartBuild', {
      lambdaFunction: startBuildFn,
      outputPath: '$.Payload',
    });

    const waitState = new sfn.Wait(this, 'WaitForCopy', {
      time: sfn.WaitTime.duration(cdk.Duration.seconds(10)),
    });

    const checkAndFinalizeTask = new tasks.LambdaInvoke(this, 'CheckAndFinalize', {
      lambdaFunction: checkAndFinalizeFn,
      outputPath: '$.Payload',
    });

    const choiceState = new sfn.Choice(this, 'AllDone?')
      .when(sfn.Condition.booleanEquals('$.all_done', true), new sfn.Succeed(this, 'Done'))
      .otherwise(waitState);

    const definition = startBuildTask
      .next(waitState)
      .next(checkAndFinalizeTask)
      .next(choiceState);

    this.stateMachine = new sfn.StateMachine(this, 'StateMachine', {
      definitionBody: sfn.DefinitionBody.fromChainable(definition),
      timeout: cdk.Duration.hours(2),
    });
  }
}
