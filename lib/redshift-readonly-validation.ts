import * as cdk from 'aws-cdk-lib';
import * as path from 'node:path';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as cr from 'aws-cdk-lib/custom-resources';
import { PythonFunction } from '@aws-cdk/aws-lambda-python-alpha';
import { Construct } from 'constructs';
import { IRedshiftConnection } from './redshift-connection';

export interface RedshiftReadonlyValidationProps {
  redshift: IRedshiftConnection;
}

/**
 * 既存 Redshift の DB ユーザーが読み取り専用であることをデプロイ時に検証するカスタムリソース。
 *
 * existingRedshift モードのガードレール。提供された Secret のユーザーが
 * superuser / スキーマ CREATE 権限 / テーブル書き込み権限のいずれかを持っていた場合、
 * カスタムリソースが失敗し CloudFormation デプロイ全体が失敗・ロールバックする。
 *
 * Create / Update 時のみ検証する (Workgroup / Secret ARN の変更時に再実行される)。
 */
export class RedshiftReadonlyValidation extends Construct {
  constructor(scope: Construct, id: string, props: RedshiftReadonlyValidationProps) {
    super(scope, id);

    const handler = new PythonFunction(this, 'Handler', {
      entry: path.join(__dirname, '../lambda/redshiftreadonlyvalidator'),
      runtime: lambda.Runtime.PYTHON_3_14,
      architecture: lambda.Architecture.ARM_64,
      index: 'handler.py',
      handler: 'on_event',
      timeout: cdk.Duration.minutes(5),
      memorySize: 256,
    });

    props.redshift.grantDataApi(handler, props.redshift.agentSecret);

    const provider = new cr.Provider(this, 'Provider', {
      onEventHandler: handler,
    });

    new cdk.CustomResource(this, 'Validation', {
      serviceToken: provider.serviceToken,
      resourceType: 'Custom::RedshiftReadonlyValidation',
      properties: {
        WorkgroupName: props.redshift.workgroupName,
        Database: props.redshift.dbName,
        SecretArn: props.redshift.agentSecret.secretArn,
      },
    });
  }
}
