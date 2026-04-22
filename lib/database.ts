import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import { Construct } from 'constructs';

/**
 * DynamoDB テーブル: sessions (会話セッション) + config (システム設定)
 */
export class Database extends Construct {
  readonly sessionsTable: dynamodb.Table;
  readonly configTable: dynamodb.Table;

  constructor(scope: Construct, id: string) {
    super(scope, id);

    // sessions テーブル
    this.sessionsTable = new dynamodb.Table(this, 'SessionsTable', {
      partitionKey: { name: 'user_id', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'session_id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // config テーブル (system_prompt, db_schema, skills)
    this.configTable = new dynamodb.Table(this, 'ConfigTable', {
      partitionKey: { name: 'id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
  }
}
