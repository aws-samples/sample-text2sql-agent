import * as cdk from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';

export interface CsvStorageProps {
  /**
   * 既存バケット名。指定時は fromBucketName で参照し、新規作成しない。
   * 未指定時は新規バケットを作成。
   */
  existingBucketName?: string;
  /** presigned URL アップロード用 CORS 許可オリジン */
  allowOrigin: string;
}

/**
 * CSV Input 用 S3 バケット
 */
export class CsvStorage extends Construct {
  readonly bucket: s3.IBucket;
  /** true のとき cdk.json で指定された既存バケットを参照している */
  readonly isExisting: boolean;

  constructor(scope: Construct, id: string, props: CsvStorageProps) {
    super(scope, id);

    if (props.existingBucketName) {
      this.bucket = s3.Bucket.fromBucketName(this, 'ExistingBucket', props.existingBucketName);
      this.isExisting = true;
    } else {
      this.bucket = new s3.Bucket(this, 'CsvInputBucket', {
        blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
        encryption: s3.BucketEncryption.S3_MANAGED,
        enforceSSL: true,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
        autoDeleteObjects: true,
        cors: [
          {
            allowedOrigins: [props.allowOrigin],
            allowedMethods: [s3.HttpMethods.PUT],
            allowedHeaders: ['*'],
          },
        ],
      });
      this.isExisting = false;
    }
  }
}
