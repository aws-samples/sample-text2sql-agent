#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { DwhAgentStack } from '../lib/dwh-agent-stack';
import { WafStack } from '../lib/waf-stack';

const app = new cdk.App();

const allowOrigin = app.node.tryGetContext('allowOrigin');
const allowedCidrs = app.node.tryGetContext('allowedCidrs');
const allowedIpv6Cidrs: string[] = app.node.tryGetContext('allowedIpv6Cidrs') ?? [];
const bedrockModelId = app.node.tryGetContext('bedrockModelId');
const csvInputBucketName = app.node.tryGetContext('csvInputBucketName');
const sqlResultThreshold = app.node.tryGetContext('sqlResultThreshold') ?? 200;
const enablePromptCache = app.node.tryGetContext('enablePromptCache') ?? false;
const stackPrefix = app.node.tryGetContext('stackPrefix') ?? '';
const testAgentUser = app.node.tryGetContext('testAgentUser') ?? '';
const testAdminUser = app.node.tryGetContext('testAdminUser') ?? '';

// 既存 Redshift Serverless 参照モード (workgroupName / database / secretArn の 3 点セット)
const existingRedshiftContext = app.node.tryGetContext('existingRedshift') ?? {};
const existingRedshiftKeys = ['workgroupName', 'database', 'secretArn'] as const;
const providedKeys = existingRedshiftKeys.filter((k) => existingRedshiftContext[k]);
if (providedKeys.length > 0 && providedKeys.length < existingRedshiftKeys.length) {
  throw new Error(
    `existingRedshift context には ${existingRedshiftKeys.join(', ')} をすべて指定してください。` +
    `指定済み: ${providedKeys.join(', ')}`,
  );
}
const existingRedshift = providedKeys.length === existingRedshiftKeys.length
  ? {
      workgroupName: existingRedshiftContext.workgroupName as string,
      database: existingRedshiftContext.database as string,
      secretArn: existingRedshiftContext.secretArn as string,
    }
  : undefined;

// CloudFront 用 WAF は us-east-1 必須
const wafStack = new WafStack(app, stackPrefix + 'DwhAgentWafStack', {
  env: {
    region: 'us-east-1',
    account: process.env.CDK_DEFAULT_ACCOUNT,
  },
  allowedCidrs,
  allowedIpv6Cidrs,
  crossRegionReferences: true,
});

new DwhAgentStack(app, stackPrefix + 'DwhAgentStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION,
  },
  allowOrigin,
  allowedCidrs,
  allowedIpv6Cidrs,
  webAclArn: wafStack.webAclArn,
  bedrockModelId,
  csvInputBucketName,
  sqlResultThreshold,
  enablePromptCache,
  testAgentUser,
  testAdminUser,
  existingRedshift,
  crossRegionReferences: true,
});
