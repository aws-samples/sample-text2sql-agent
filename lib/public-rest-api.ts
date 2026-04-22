import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import * as apigw from 'aws-cdk-lib/aws-apigateway';

export interface PublicRestApiProps {
  allowOrigins: string[];
}

/**
 * REST API (API Gateway)
 * WAF は外部の RegionalWaf.associate() で関連付ける
 */
export class PublicRestApi extends Construct {
  readonly restApi: apigw.RestApi;
  readonly url: string;

  constructor(scope: Construct, id: string, props: PublicRestApiProps) {
    super(scope, id);

    this.restApi = new apigw.RestApi(this, id, {
      deployOptions: { stageName: 'api' },
      endpointTypes: [apigw.EndpointType.REGIONAL],
      defaultCorsPreflightOptions: {
        allowOrigins: props.allowOrigins,
        allowMethods: apigw.Cors.ALL_METHODS,
      },
    });

    // 4xx/5xx レスポンスにも CORS ヘッダーを付加
    this.restApi.addGatewayResponse('Gwr4xx', {
      type: apigw.ResponseType.DEFAULT_4XX,
      responseHeaders: {
        'Access-Control-Allow-Origin': props.allowOrigins.map((o) => `'${o}'`).join(','),
      },
    });
    this.restApi.addGatewayResponse('Gwr5xx', {
      type: apigw.ResponseType.DEFAULT_5XX,
      responseHeaders: {
        'Access-Control-Allow-Origin': props.allowOrigins.map((o) => `'${o}'`).join(','),
      },
    });

    this.url = this.restApi.url;
  }

  /**
   * REST API にリソース/メソッドを追加
   */
  addResource(
    method: string,
    path: string[],
    fn: lambda.IFunction,
    authorizer?: apigw.IAuthorizer,
    options?: { responseTransferMode?: apigw.ResponseTransferMode },
  ): void {
    const resource = this.restApi.root.resourceForPath(path.join('/'));
    resource.addMethod(
      method,
      new apigw.LambdaIntegration(fn, {
        allowTestInvoke: false,
        ...(options?.responseTransferMode && { responseTransferMode: options.responseTransferMode }),
        ...(options?.responseTransferMode === apigw.ResponseTransferMode.STREAM && {
          timeout: cdk.Duration.seconds(900),
        }),
      }),
      {
        authorizer,
        authorizationType: authorizer?.authorizationType,
      },
    );

    // Lambda ごとに1回だけ Permission を付与
    const permId = 'ApiGwInvokePermission';
    if (!fn.node.tryFindChild(permId)) {
      fn.addPermission(permId, {
        principal: new iam.ServicePrincipal('apigateway.amazonaws.com'),
        sourceArn: this.restApi.arnForExecuteApi('*', '/*', '*'),
      });
    }
  }
}
