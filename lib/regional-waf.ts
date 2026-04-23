import * as cdk from 'aws-cdk-lib';
import * as waf from 'aws-cdk-lib/aws-wafv2';
import { Construct } from 'constructs';

export interface RegionalWafProps {
  allowedCidrs: string[];
  allowedIpv6Cidrs?: string[];
}

/**
 * Regional WAF (IP制限) — 複数の API Gateway で共有可能
 */
export class RegionalWaf extends Construct {
  readonly webAclArn: string;

  constructor(scope: Construct, id: string, props: RegionalWafProps) {
    super(scope, id);

    const ipv4Set = new waf.CfnIPSet(this, 'IPSet', {
      name: cdk.Stack.of(this).stackName + 'IPSet',
      ipAddressVersion: 'IPV4',
      scope: 'REGIONAL',
      addresses: props.allowedCidrs,
    });

    // IPv6 IPSet（CIDRが指定されている場合のみ有効なルールを追加）
    const ipv6Cidrs = props.allowedIpv6Cidrs ?? [];
    const ipv6Set = new waf.CfnIPSet(this, 'IPSetV6', {
      name: cdk.Stack.of(this).stackName + 'IPSetV6',
      ipAddressVersion: 'IPV6',
      scope: 'REGIONAL',
      addresses: ipv6Cidrs,
    });

    // IPv4 と IPv6 を OR 条件で許可するルール
    const ipMatchStatement: waf.CfnWebACL.StatementProperty = ipv6Cidrs.length > 0
      ? {
          orStatement: {
            statements: [
              { ipSetReferenceStatement: { arn: ipv4Set.attrArn } },
              { ipSetReferenceStatement: { arn: ipv6Set.attrArn } },
            ],
          },
        }
      : { ipSetReferenceStatement: { arn: ipv4Set.attrArn } };

    const webAcl = new waf.CfnWebACL(this, 'WebACL', {
      defaultAction: { block: {} },
      scope: 'REGIONAL',
      visibilityConfig: {
        cloudWatchMetricsEnabled: true,
        metricName: cdk.Stack.of(this).stackName + 'WebACL',
        sampledRequestsEnabled: true,
      },
      rules: [
        {
          priority: 1,
          name: cdk.Stack.of(this).stackName + 'IpRuleSet',
          action: { allow: {} },
          visibilityConfig: {
            sampledRequestsEnabled: true,
            cloudWatchMetricsEnabled: true,
            metricName: cdk.Stack.of(this).stackName + 'IpRuleSet',
          },
          statement: ipMatchStatement,
        },
      ],
    });

    this.webAclArn = webAcl.attrArn;
  }

  /** API Gateway ステージに WebACL を関連付ける */
  associate(id: string, restApi: { restApiId: string; deploymentStage: { stageName: string } }): void {
    const arn = `arn:aws:apigateway:${cdk.Aws.REGION}::/restapis/${restApi.restApiId}/stages/${restApi.deploymentStage.stageName}`;
    new waf.CfnWebACLAssociation(this, id, {
      resourceArn: arn,
      webAclArn: this.webAclArn,
    });
  }
}
