import * as cdk from 'aws-cdk-lib';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';

export interface CognitoProps {
  /**
   * UserPool に作るテストユーザーの username。
   * 空文字 / undefined の場合はユーザーを作らない。
   * パスワードは Secrets Manager で自動生成・保管される。
   */
  testUsername?: string;
}

export class Cognito extends Construct {
  readonly userPool: cognito.UserPool;
  readonly userPoolClient: cognito.UserPoolClient;
  readonly testUserSecret?: secretsmanager.Secret;

  constructor(scope: Construct, id: string, props: CognitoProps = {}) {
    super(scope, id);

    this.userPool = new cognito.UserPool(this, 'UserPool', {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      signInAliases: {
        username: true,
        email: true,
      },
      standardAttributes: {
        email: { required: true, mutable: true },
      },
      autoVerify: { email: true },
      passwordPolicy: {
        minLength: 8,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: true,
      },
      selfSignUpEnabled: false,
    });

    this.userPoolClient = new cognito.UserPoolClient(this, 'UserPoolClient', {
      userPool: this.userPool,
      authFlows: {
        userPassword: true,
        userSrp: true,
      },
    });

    // ========================================
    // テストユーザー作成 (開発用)
    // ========================================
    if (props.testUsername) {
      this.testUserSecret = this.createTestUser(props.testUsername);
    }
  }

  /**
   * テストユーザーを作成し、パスワードを Secrets Manager で自動生成して保管する。
   * Lambda ベースの Custom Resource で Secrets Manager の値を読み、
   * AdminSetUserPassword を Permanent=true で呼び、初回強制変更なしで即ログイン可能にする。
   */
  private createTestUser(username: string): secretsmanager.Secret {
    // 1. パスワードを Secrets Manager で自動生成
    const secret = new secretsmanager.Secret(this, 'TestUserSecret', {
      description: `Test user credentials for Cognito UserPool (${username})`,
      generateSecretString: {
        secretStringTemplate: JSON.stringify({ username }),
        generateStringKey: 'password',
        passwordLength: 16,
        requireEachIncludedType: true,
        // シェル・コピペで事故りやすい文字を除外
        excludeCharacters: ' "\'\\/@`$',
      },
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // 2. UserPool にユーザー作成 (招待メール抑制、email 検証済み)
    const cfnUser = new cognito.CfnUserPoolUser(this, 'TestUser', {
      userPoolId: this.userPool.userPoolId,
      username,
      messageAction: 'SUPPRESS',
      userAttributes: [
        { name: 'email', value: `${username}@example.com` },
        { name: 'email_verified', value: 'true' },
      ],
    });

    // 3. Custom Resource Lambda (Python / boto3 ランタイム同梱、依存なし)
    const handler = new lambda.Function(this, 'SetPasswordHandler', {
      runtime: lambda.Runtime.PYTHON_3_14,
      handler: 'index.handler',
      timeout: cdk.Duration.minutes(5),
      code: lambda.Code.fromInline(`
import json
import urllib.request
import boto3

def send(event, status, reason=""):
    body = json.dumps({
        "Status": status,
        "Reason": reason or f"See CW Logs: {event.get('LogicalResourceId')}",
        "PhysicalResourceId": event.get("PhysicalResourceId") or event["LogicalResourceId"],
        "StackId": event["StackId"],
        "RequestId": event["RequestId"],
        "LogicalResourceId": event["LogicalResourceId"],
        "Data": {},
    }).encode("utf-8")
    req = urllib.request.Request(
        event["ResponseURL"], data=body, method="PUT",
        headers={"Content-Type": "", "Content-Length": str(len(body))},
    )
    urllib.request.urlopen(req).read()

def handler(event, _ctx):
    print("event:", json.dumps(event))
    try:
        if event["RequestType"] == "Delete":
            send(event, "SUCCESS")
            return
        props = event["ResourceProperties"]
        user_pool_id = props["UserPoolId"]
        username = props["Username"]
        secret_arn = props["SecretArn"]

        sm = boto3.client("secretsmanager")
        secret = json.loads(sm.get_secret_value(SecretId=secret_arn)["SecretString"])

        cognito = boto3.client("cognito-idp")
        cognito.admin_set_user_password(
            UserPoolId=user_pool_id,
            Username=username,
            Password=secret["password"],
            Permanent=True,
        )
        send(event, "SUCCESS")
    except Exception as e:
        print("error:", repr(e))
        send(event, "FAILED", reason=str(e))
`),
    });

    handler.addToRolePolicy(new iam.PolicyStatement({
      actions: ['cognito-idp:AdminSetUserPassword'],
      resources: [this.userPool.userPoolArn],
    }));
    secret.grantRead(handler);

    // 4. CustomResource を Lambda 直結 (cr.Provider は不要)
    const setPassword = new cdk.CustomResource(this, 'SetTestUserPassword', {
      serviceToken: handler.functionArn,
      properties: {
        UserPoolId: this.userPool.userPoolId,
        Username: username,
        SecretArn: secret.secretArn,
      },
    });
    setPassword.node.addDependency(cfnUser);
    setPassword.node.addDependency(secret);

    // Secret 情報を出力 (パスワード本体は出さない)
    new cdk.CfnOutput(this, 'TestUserSecretArn', { value: secret.secretArn });

    return secret;
  }
}
