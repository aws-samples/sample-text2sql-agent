import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as wafv2 from 'aws-cdk-lib/aws-wafv2';

export interface WafStackProps extends cdk.StackProps {
  allowedCidrs: string[];
}

export class WafStack extends cdk.Stack {
  public readonly webAclArn: string;

  constructor(scope: Construct, id: string, props: WafStackProps) {
    super(scope, id, props);

    const wafIPSet = new wafv2.CfnIPSet(this, 'IPSet', {
      name: cdk.Stack.of(this).stackName + 'CFIPSet',
      ipAddressVersion: 'IPV4',
      scope: 'CLOUDFRONT',
      addresses: props.allowedCidrs,
    });

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
          statement: {
            ipSetReferenceStatement: {
              arn: wafIPSet.attrArn,
            },
          },
        },
      ],
    });

    this.webAclArn = webAcl.attrArn;
  }
}
