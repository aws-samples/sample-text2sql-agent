#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { DwhAgentStack } from '../lib/dwh-agent-stack';
import { WafStack } from '../lib/waf-stack';

const app = new cdk.App();

const allowOrigin = app.node.tryGetContext('allowOrigin');
const allowedCidrs = app.node.tryGetContext('allowedCidrs');
const bedrockModelId = app.node.tryGetContext('bedrockModelId');
const csvInputBucketName = app.node.tryGetContext('csvInputBucketName');
const sqlResultThreshold = app.node.tryGetContext('sqlResultThreshold') ?? 200;
const enablePromptCache = app.node.tryGetContext('enablePromptCache') ?? false;
const stackPrefix = app.node.tryGetContext('stackPrefix') ?? '';
const testAgentUser = app.node.tryGetContext('testAgentUser') ?? '';
const testAdminUser = app.node.tryGetContext('testAdminUser') ?? '';

// CloudFront 用 WAF は us-east-1 必須
const wafStack = new WafStack(app, stackPrefix + 'DwhAgentWafStack', {
  env: {
    region: 'us-east-1',
    account: process.env.CDK_DEFAULT_ACCOUNT,
  },
  allowedCidrs,
  crossRegionReferences: true,
});

new DwhAgentStack(app, stackPrefix + 'DwhAgentStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION,
  },
  allowOrigin,
  allowedCidrs,
  webAclArn: wafStack.webAclArn,
  bedrockModelId,
  csvInputBucketName,
  sqlResultThreshold,
  enablePromptCache,
  testAgentUser,
  testAdminUser,
  crossRegionReferences: true,
});
