import * as path from 'node:path';
import { RemovalPolicy } from 'aws-cdk-lib';
import { Distribution, GeoRestriction, ViewerProtocolPolicy } from 'aws-cdk-lib/aws-cloudfront';
import { S3BucketOrigin } from 'aws-cdk-lib/aws-cloudfront-origins';
import { Bucket, BlockPublicAccess } from 'aws-cdk-lib/aws-s3';
import { NodejsBuild } from 'deploy-time-build';
import { Construct } from 'constructs';

export interface FrontendProps {
  /** ビルド時に注入する環境変数 (VITE_APP_*) */
  buildEnvironments: Record<string, string>;
  /** frontend/ 配下のアプリ名 (agent | admin) */
  appName: 'agent' | 'admin';
  /** CloudFront 用 WAF WebACL ARN (us-east-1) */
  webAclArn: string;
}

export class Frontend extends Construct {
  readonly distribution: Distribution;

  constructor(scope: Construct, id: string, props: FrontendProps) {
    super(scope, id);

    const webBucket = new Bucket(this, 'WebBucket', {
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      versioned: false,
      enforceSSL: true,
      blockPublicAccess: BlockPublicAccess.BLOCK_ALL,
    });

    this.distribution = new Distribution(this, 'Distribution', {
      defaultRootObject: 'index.html',
      defaultBehavior: {
        viewerProtocolPolicy: ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        origin: S3BucketOrigin.withOriginAccessControl(webBucket),
      },
      errorResponses: [
        { httpStatus: 403, responseHttpStatus: 200, responsePagePath: '/index.html' },
        { httpStatus: 404, responseHttpStatus: 200, responsePagePath: '/index.html' },
      ],
      geoRestriction: GeoRestriction.allowlist('JP'),
      webAclId: props.webAclArn,
    });

    new NodejsBuild(this, 'Build', {
      assets: [
        { path: path.join(__dirname, `../frontend/${props.appName}`) },
      ],
      destinationBucket: webBucket,
      distribution: this.distribution,
      outputSourceDirectory: 'dist',
      buildCommands: ['npm ci', 'npm run build'],
      buildEnvironment: props.buildEnvironments,
    });
  }
}
