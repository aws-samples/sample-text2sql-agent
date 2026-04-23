import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as wafv2 from 'aws-cdk-lib/aws-wafv2';

export interface WafStackProps extends cdk.StackProps {
  allowedCidrs: string[];
  allowedIpv6Cidrs?: string[];
}

export class WafStack extends cdk.Stack {
  public readonly webAclArn: string;

  constructor(scope: Construct, id: string, props: WafStackProps) {
    super(scope, id, props);

    const ipv4Set = new wafv2.CfnIPSet(this, 'IPSet', {
      name: cdk.Stack.of(this).stackName + 'CFIPSet',
      ipAddressVersion: 'IPV4',
      scope: 'CLOUDFRONT',
      addresses: props.allowedCidrs,
    });

    // IPv6 IPSet（CIDRが指定されている場合のみ有効なルールを追加）
    const ipv6Cidrs = props.allowedIpv6Cidrs ?? [];
    const ipv6Set = new wafv2.CfnIPSet(this, 'IPSetV6', {
      name: cdk.Stack.of(this).stackName + 'CFIPSetV6',
      ipAddressVersion: 'IPV6',
      scope: 'CLOUDFRONT',
      addresses: ipv6Cidrs,
    });

    // IPv4 と IPv6 を OR 条件で許可するルール
    const ipMatchStatement: wafv2.CfnWebACL.StatementProperty = ipv6Cidrs.length > 0
      ? {
          orStatement: {
            statements: [
              { ipSetReferenceStatement: { arn: ipv4Set.attrArn } },
              { ipSetReferenceStatement: { arn: ipv6Set.attrArn } },
            ],
          },
        }
      : { ipSetReferenceStatement: { arn: ipv4Set.attrArn } };

    const webAcl = new wafv2.CfnWebACL(this, 'WebAcl', {
      scope: 'CLOUDFRONT',
      defaultAction: { block: {} },
      visibilityConfig: {
        cloudWatchMetricsEnabled: true,
        metricName: 'webACL',
        sampledRequestsEnabled: true,
      },
      rules: [
        {
          priority: 1,
          name: cdk.Stack.of(this).stackName + 'CFWebAclRuleSet',
          action: { allow: {} },
          visibilityConfig: {
            sampledRequestsEnabled: true,
            cloudWatchMetricsEnabled: true,
            metricName: cdk.Stack.of(this).stackName + 'CFWebAclRuleSet',
          },
          statement: ipMatchStatement,
        },
      ],
    });

    this.webAclArn = webAcl.attrArn;
  }
}
