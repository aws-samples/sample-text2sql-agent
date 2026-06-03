import * as cdk from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';

export interface CsvDownloadStorageProps {
  /**
   * オブジェクト保持日数。presigned URL の TTL より十分長く取る (デフォルト 7 日)。
   */
  expirationDays?: number;
}

/**
 * CSV ダウンロード機能用 S3 バケット
 *
 * AgentCore Runtime の `_create_csv_file` ツールが UNLOAD で書き込み、
 * presigned URL 経由でフロントエンドからダウンロードする。
 *
 * 注意:
 *  - CSV Input 用バケット (CsvStorage) とは用途・権限が異なるため別 Construct に分離している
 *  - presigned URL の TTL (1 時間) より長いライフサイクルで自動削除する
 */
export class CsvDownloadStorage extends Construct {
  readonly bucket: s3.Bucket;

  constructor(scope: Construct, id: string, props: CsvDownloadStorageProps = {}) {
    super(scope, id);

    const expirationDays = props.expirationDays ?? 7;

    this.bucket = new s3.Bucket(this, 'Bucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      lifecycleRules: [
        {
          // 一定期間後に自動削除（presigned URL は 1 時間で切れるため十分余裕がある）
          expiration: cdk.Duration.days(expirationDays),
        },
      ],
    });
  }
}
