# 動作確認

デプロイ完了後のシステム運用手順です。デプロイ手順は [MANUAL_DEPLOYMENT.md](./MANUAL_DEPLOYMENT.md) を参照してください。

## 1. CSV データの準備

分析対象の CSV ファイルを用意します。

### サンプルデータで試す場合

`scripts/gen_testdata.py` で EC サイトのテストデータを生成できます。

```bash
python3 scripts/gen_testdata.py --output-dir ./testdata
```

デフォルトでは以下の 4 テーブル分の CSV が生成されます:

| ファイル | 件数 | 内容 |
|---------|------|------|
| `customers.csv` | 1,000 | 顧客マスター（ID, メール, 氏名, 性別, 年齢, 都道府県, 登録日） |
| `products.csv` | 500 | 商品マスター（ID, 商品名, カテゴリ, 価格, 原価, 在庫数, 作成日） |
| `orders.csv` | 3,000 | 注文ヘッダー（ID, 顧客ID, 注文日時, ステータス, 支払方法, 配送先, 合計金額） |
| `order_items.csv` | 8,000 | 注文明細（ID, 注文ID, 商品ID, 数量, 単価, 小計） |

`--scale large` を指定すると、上記に加えて 20 テーブル（商品カテゴリ、サプライヤー、倉庫、在庫移動、配送、返品、クーポン、レビュー、ページビュー、キャンペーン、顧客セグメント、従業員、サポートチケット、決済、日次売上サマリー等）が追加され、合計 24 テーブルになります。

```bash
python3 scripts/gen_testdata.py --output-dir ./testdata --scale large
```



### 自前のデータを使う場合

- CSV ファイルはヘッダー行付きで用意してください
- エンコーディングは UTF-8 必須です

### 大量 CSV を 1 テーブルにまとめる: manifest の利用（任意）

同一スキーマの CSV が非常に多い（数百本〜）テーブルは、Redshift COPY MANIFEST を使うと 1 回の COPY で一括ロードできます。Data API の同時実行上限に起因するロード滞留を避けたい場合に有効です。

**使い方**

1. 対象の CSV と同じ prefix 配下に `<任意名>.manifest` という JSON ファイルを 1 つ置く
2. `entries` にまとめたい CSV の絶対 `s3://` URL を列挙する

```json
{
  "entries": [
    { "url": "s3://<CsvBucketName>/sales/2024/01.csv", "mandatory": true },
    { "url": "s3://<CsvBucketName>/sales/2024/02.csv", "mandatory": true }
  ]
}
```

**挙動**

- analyze 時、`.manifest` ファイルが検出されると 1 テーブルとして扱われる（manifest のファイル名 basename がテーブル名のヒントになる）
- manifest に列挙された CSV は、通常の CSV 列挙ルートから除外される（二重ロード防止）
- apply 時、manifest 由来テーブルは `COPY ... MANIFEST` 1 回で全 entry を一括ロードする
- manifest がない prefix では従来通りファイル毎に COPY される（後方互換）

**制約**

- `entries[].url` は CSV バケット内の絶対 `s3://` URL（別バケット参照は analyze で除外集合に反映されないため非推奨）
- 同 prefix 配下に、manifest に列挙されていない同ヘッダの CSV が残っている場合、その CSV は別テーブル扱いになる（同 prefix に置いた CSV は全て manifest に含めることを推奨）
- manifest は Admin UI のローカルアップロード機能では扱えません。アップロード時にディレクトリ構造と URL が維持されないためです。AWS CLI / S3 Console / DataSync 等で S3 に直接配置し、Admin UI の「既存 S3 パス指定」から prefix を指定してください

**動作確認用のテストデータ生成**

manifest を含む包括的な動作確認用データを生成できます（A ヘッダ 403 CSV のうち 400 は manifest、残り 3 は個別 / B ヘッダ 2 CSV は全て manifest / C ヘッダ 2 CSV は manifest なし、計 4 テーブル想定、直下と `sub/` に混在配置）。

```bash
python3 scripts/gen_testdata.py --manifest-test \
  --output-dir ./testdata \
  --manifest-s3-prefix s3://<CsvBucketName>/testdata/
```

生成後、`./testdata/` を指定の prefix（`--manifest-s3-prefix` と揃える）にアップロードし、Admin UI の Mode B から同 prefix を指定して analyze → apply を実行してください。

## 2. Upload & Build（テーブル作成）

Admin Frontend にログインし、「Upload & Build」タブで以下の手順を実行します。

### Step 1: CSV 指定

2 つのモードから選択します。

**モード A: ローカルファイルアップロード**

1. 「ローカルファイルアップロード」を選択
2. 「CSV ファイルを選択」で CSV ファイルを複数選択
3. 「アップロード」をクリック → S3 に直接アップロードされます

**モード B: 既存 S3 パス指定**

S3 に既にアップロード済みの CSV がある場合:

1. 「既存 S3 パス指定」を選択
2. S3 prefix を入力（例: `data/sales/`）
3. 「ファイル一覧取得」をクリック → prefix 配下の CSV ファイルが表示されます

サンプルデータを S3 に事前アップロードする場合:

```bash
aws s3 cp ./testdata/ s3://<CsvBucketName>/testdata/ --recursive
```

その後モード B で prefix に `testdata/` を入力します。

### Step 2: AI 分析

1. 「分析」ボタンをクリック
2. AI が CSV のヘッダー・サンプルデータ・統計情報を分析し、以下を自動生成します:
   - System Prompt（Agent 用のシステムプロンプト）
   - DB Schema（テーブル定義、カラム型、CSV オプション）

### Step 3: 確認・編集

AI の提案結果が構造化フォームで表示されます。全フィールド編集可能です:

- System Prompt: 自由に編集可能
- テーブル名・説明
- カラム名・型・説明
- CSV オプション（encoding, delimiter, quote_char, null_as）
- S3 キー（表示のみ）

### Step 4: 確定

1. 「確定（テーブル作成）」ボタンをクリック
2. 以下が自動実行されます:
   - 既存テーブルの DROP（再実行時）
   - CREATE TABLE（DDL 実行）
   - COPY（S3 → Redshift データロード）
   - agent_readonly ユーザー作成（初回のみ、冪等）
   - SELECT 権限の GRANT
   - DynamoDB に system_prompt と db_schema を保存

> agent_readonly ユーザーの作成と権限設定は「確定」実行時に自動で行われます。
> 独立した DB 初期化操作は不要です。
> 詳細は [REDSHIFT_PERMISSION.md](./REDSHIFT_PERMISSION.md) を参照。

## 3. Knowledge 設定

「Prompt & Knowledge」タブで Agent に追加の知識を与えます。Upload & Build とは独立して管理可能です。

各 Knowledge は構造化フォームで以下の 3 フィールドを入力します:

| フィールド | 説明 |
|-----------|------|
| name | Knowledge の識別名（英小文字・数字・ハイフン、1〜64 文字。例: `sql-best-practices`） |
| description | Knowledge の説明（例: 「SQLクエリのベストプラクティスをレビューする」） |
| instructions | Agent への具体的な指示（Markdown 形式で記述） |

- 「Knowledge を追加」ボタンで空のフォームカードを追加できます
- 各カードのゴミ箱アイコンで削除できます

保存すると、各エントリは内部的に Strands Agent Skills 仕様（YAML フロントマター + Markdown 本文）に変換され、DynamoDB config テーブルの `skills` フィールドに文字列配列として保存されます。Agent 初期化時に `Skill.from_content()` でパースされ、`AgentSkills` プラグインとして登録されます。


## 4. Agent で分析

Agent Frontend にログインし、チャットで自然言語の質問を入力します。

```
先月の売上トップ10商品を教えてください
```


## 5. データの再構築

CSV データを差し替えたい場合は、Upload & Build を再実行してください。
「確定」実行時に既存テーブルは全削除され、新しいデータで作り直されます。

> DB 構成は 1 つのみ保持されます。再実行で上書きされます。
> Agent 側の既存セッションとの整合性は保証されません。
