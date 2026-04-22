#!/usr/bin/env python3
"""EC サイトのテスト用 CSV データを生成するスクリプト。

生成テーブル (default: 4テーブル):
  - customers.csv   : 顧客マスター (1,000件)
  - products.csv    : 商品マスター (500件)
  - orders.csv      : 注文ヘッダー (3,000件)
  - order_items.csv : 注文明細 (8,000件)

--scale large 指定時: 上記 4 テーブル + 追加 20 テーブル = 合計 24 テーブル

Usage:
  python gen_testdata.py [--output-dir ./testdata] [--scale default|large] [--multi-csv]
"""
import argparse
import csv
import os
import random
import uuid
from datetime import datetime, timedelta

# --- 設定 ---
NUM_CUSTOMERS = 1000
NUM_PRODUCTS = 500
NUM_ORDERS = 3000
NUM_ORDER_ITEMS = 8000

# --- マスターデータ素材 ---
FIRST_NAMES = [
    "Taro", "Hanako", "Ichiro", "Naoko", "Kenta", "Mika", "Shota", "Keiko",
    "Daisuke", "Yuko", "Akira", "Yuki", "Takeshi", "Emi", "Hiroshi", "Ayumi",
    "Kazuki", "Sakura", "Ryo", "Natsumi", "Satoshi", "Yui", "Takuya", "Aya",
    "Makoto", "Haruka", "Daiki", "Misaki", "Tatsuya", "Nanami", "Sho", "Aoi",
    "John", "Mary", "James", "Patricia", "Robert", "Jennifer", "Michael", "Linda",
    "William", "Elizabeth", "David", "Barbara", "Richard", "Susan", "Joseph", "Sarah",
]

LAST_NAMES = [
    "Sato", "Suzuki", "Takahashi", "Tanaka", "Ito", "Watanabe", "Yamamoto",
    "Nakamura", "Kobayashi", "Kato", "Yoshida", "Yamada", "Sasaki", "Yamaguchi",
    "Matsumoto", "Inoue", "Kimura", "Hayashi", "Shimizu", "Saito",
    "Smith", "Johnson", "Williams", "Jones", "Brown", "Davis", "Miller", "Wilson",
    "Moore", "Taylor", "Anderson", "Thomas", "Jackson", "White", "Harris", "Martin",
]

PREFECTURES = [
    "東京都", "大阪府", "神奈川県", "愛知県", "埼玉県", "千葉県", "兵庫県",
    "北海道", "福岡県", "静岡県", "茨城県", "広島県", "京都府", "宮城県",
]

CATEGORIES = ["家電", "ファッション", "食品", "書籍", "スポーツ", "美容", "インテリア", "おもちゃ"]

PRODUCT_ADJECTIVES = ["プレミアム", "スタンダード", "エコ", "プロ", "ライト", "デラックス"]
PRODUCT_NOUNS = {
    "家電": ["掃除機", "炊飯器", "電子レンジ", "ドライヤー", "加湿器", "空気清浄機"],
    "ファッション": ["Tシャツ", "ジャケット", "スニーカー", "バッグ", "帽子", "マフラー"],
    "食品": ["コーヒー豆", "チョコレート", "オリーブオイル", "はちみつ", "ナッツ", "紅茶"],
    "書籍": ["ビジネス書", "小説", "技術書", "料理本", "旅行ガイド", "写真集"],
    "スポーツ": ["ランニングシューズ", "ヨガマット", "ダンベル", "水筒", "リュック", "タオル"],
    "美容": ["化粧水", "乳液", "日焼け止め", "シャンプー", "ハンドクリーム", "美容液"],
    "インテリア": ["クッション", "ラグ", "照明", "時計", "花瓶", "キャンドル"],
    "おもちゃ": ["ブロック", "パズル", "ぬいぐるみ", "ボードゲーム", "ミニカー", "カードゲーム"],
}

ORDER_STATUSES = ["completed", "shipped", "processing", "cancelled", "returned"]
PAYMENT_METHODS = ["credit_card", "bank_transfer", "convenience_store", "cod", "e_money"]


# --- データ生成関数 ---

def gen_customers(n: int) -> list[dict]:
    rows = []
    used_emails: set[str] = set()
    for _ in range(n):
        first = random.choice(FIRST_NAMES)
        last = random.choice(LAST_NAMES)
        base_email = f"{first.lower()}.{last.lower()}@example.com"
        email = base_email
        counter = 1
        while email in used_emails:
            counter += 1
            email = f"{first.lower()}.{last.lower()}{counter}@example.com"
        used_emails.add(email)

        rows.append({
            "customer_id": str(uuid.uuid4()),
            "email": email,
            "first_name": first,
            "last_name": last,
            "gender": random.choice(["male", "female", "other"]),
            "age": random.randint(18, 75),
            "prefecture": random.choice(PREFECTURES),
            "registered_at": (datetime(2024, 1, 1) + timedelta(days=random.randint(0, 730))).strftime("%Y-%m-%d"),
        })
    return rows


def gen_products(n: int) -> list[dict]:
    rows = []
    for i in range(1, n + 1):
        cat = random.choice(CATEGORIES)
        noun = random.choice(PRODUCT_NOUNS[cat])
        adj = random.choice(PRODUCT_ADJECTIVES)
        rows.append({
            "product_id": i,
            "product_name": f"{adj}{noun} {random.randint(100, 999)}",
            "category": cat,
            "price": random.randint(500, 80000),
            "cost": random.randint(200, 40000),
            "stock_quantity": random.randint(0, 500),
            "created_at": (datetime(2023, 6, 1) + timedelta(days=random.randint(0, 900))).strftime("%Y-%m-%d"),
        })
    # cost が price を超えないよう補正
    for r in rows:
        if r["cost"] > r["price"]:
            r["cost"] = int(r["price"] * random.uniform(0.3, 0.7))
    return rows


def gen_orders(n: int, customers: list[dict]) -> list[dict]:
    rows = []
    start = datetime(2024, 1, 1)
    end = datetime(2026, 3, 31)
    span = int((end - start).total_seconds())
    for i in range(1, n + 1):
        cust = random.choice(customers)
        order_dt = start + timedelta(seconds=random.randint(0, span))
        status = random.choices(ORDER_STATUSES, weights=[60, 15, 10, 10, 5])[0]
        rows.append({
            "order_id": i,
            "customer_id": cust["customer_id"],
            "order_date": order_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "status": status,
            "payment_method": random.choice(PAYMENT_METHODS),
            "shipping_prefecture": cust["prefecture"],
            "total_amount": 0,  # 後で明細から集計
        })
    return rows


def gen_order_items(n: int, orders: list[dict], products: list[dict]) -> list[dict]:
    rows = []
    order_totals: dict[int, int] = {}
    for i in range(1, n + 1):
        order = random.choice(orders)
        product = random.choice(products)
        qty = random.randint(1, 5)
        unit_price = product["price"]
        subtotal = unit_price * qty
        oid = order["order_id"]
        order_totals[oid] = order_totals.get(oid, 0) + subtotal
        rows.append({
            "order_item_id": i,
            "order_id": oid,
            "product_id": product["product_id"],
            "quantity": qty,
            "unit_price": unit_price,
            "subtotal": subtotal,
        })
    # orders の total_amount を集計値で更新
    for order in orders:
        order["total_amount"] = order_totals.get(order["order_id"], 0)
    return rows


# --- 追加テーブル用素材データ ---

REVIEW_COMMENTS = [
    "とても良い商品です", "期待通りでした", "コスパ最高", "少し期待外れ",
    "リピート確定", "品質が良い", "デザインが気に入った", "サイズがぴったり",
    "配送が早かった", "梱包が丁寧", "色が写真と違った", "使いやすい",
    "プレゼントに最適", "家族にも好評", "もう少し安ければ", "大満足",
]

CARRIER_NAMES = [
    "ヤマト運輸", "佐川急便", "日本郵便", "西濃運輸", "福山通運",
    "名鉄運輸", "トナミ運輸", "第一貨物", "久留米運送", "SBS即配",
]

WAREHOUSE_REGIONS = ["関東", "関西", "中部", "北海道", "九州", "東北", "中国", "四国"]

CAMPAIGN_TYPES = ["セール", "ポイント還元", "送料無料", "クーポン配布", "タイムセール", "福袋"]
CAMPAIGN_CHANNELS = ["email", "web", "sns", "app_push", "line", "display_ad"]

SEGMENT_NAMES = [
    "VIP顧客", "新規顧客", "休眠顧客", "リピーター", "高額購入者",
    "若年層", "シニア層", "都市部", "地方", "キャンペーン反応層",
]

DEPARTMENTS = ["営業", "マーケティング", "カスタマーサポート", "物流", "IT", "経理", "人事", "商品企画"]
POSITIONS = ["スタッフ", "リーダー", "マネージャー", "シニアマネージャー", "部長"]

TICKET_CATEGORIES = ["商品不良", "配送遅延", "返品希望", "注文変更", "アカウント", "支払い", "その他"]
TICKET_PRIORITIES = ["low", "medium", "high", "urgent"]
TICKET_STATUSES = ["open", "in_progress", "waiting", "resolved", "closed"]

RETURN_REASONS = ["商品不良", "サイズ違い", "イメージ違い", "誤配送", "破損", "その他"]

PAGE_TYPES = ["top", "category", "product_detail", "cart", "checkout", "search", "mypage", "faq"]
DEVICE_TYPES = ["desktop", "mobile", "tablet"]
BROWSERS = ["Chrome", "Safari", "Firefox", "Edge", "Samsung Internet", "Opera"]

SUBCATEGORIES = {
    "家電": ["生活家電", "キッチン家電", "季節家電"],
    "ファッション": ["メンズ", "レディース", "キッズ"],
    "食品": ["飲料", "菓子", "調味料"],
    "書籍": ["和書", "洋書", "電子書籍"],
    "スポーツ": ["フィットネス", "アウトドア", "ウォーター"],
    "美容": ["スキンケア", "ヘアケア", "ボディケア"],
    "インテリア": ["リビング", "ベッドルーム", "ダイニング"],
    "おもちゃ": ["知育", "アクション", "パーティー"],
}

PAYMENT_STATUSES = ["authorized", "captured", "refunded", "failed", "pending"]
PAYMENT_GATEWAYS = ["stripe", "paypay", "rakuten_pay", "amazon_pay", "gmo"]


# --- 追加テーブル生成関数 ---

def gen_extra_tables(
    customers: list[dict],
    products: list[dict],
    orders: list[dict],
    order_items: list[dict],
) -> dict[str, list[dict]]:
    """--scale large 用の追加 20 テーブルを生成して返す。"""

    tables: dict[str, list[dict]] = {}
    dt_start = datetime(2024, 1, 1)
    dt_end = datetime(2026, 3, 31)
    dt_span = int((dt_end - dt_start).total_seconds())

    def rand_dt() -> str:
        return (dt_start + timedelta(seconds=random.randint(0, dt_span))).strftime("%Y-%m-%d %H:%M:%S")

    def rand_date() -> str:
        return (dt_start + timedelta(days=random.randint(0, 820))).strftime("%Y-%m-%d")

    # 1. product_categories (30件)
    rows = []
    cat_id = 0
    for cat in CATEGORIES:
        cat_id += 1
        parent_id = cat_id
        rows.append({
            "category_id": cat_id,
            "category_name": cat,
            "parent_category_id": None,
            "depth": 0,
            "display_order": cat_id,
            "is_active": True,
            "icon_url": f"https://example.com/icons/{cat_id}.png",
            "description": f"{cat}カテゴリ",
            "product_count": 0,
            "created_at": "2023-01-01",
        })
        for sub in SUBCATEGORIES[cat]:
            cat_id += 1
            rows.append({
                "category_id": cat_id,
                "category_name": sub,
                "parent_category_id": parent_id,
                "depth": 1,
                "display_order": cat_id,
                "is_active": True,
                "icon_url": f"https://example.com/icons/{cat_id}.png",
                "description": f"{cat} > {sub}",
                "product_count": 0,
                "created_at": "2023-01-01",
            })
    tables["product_categories"] = rows

    # 2. suppliers (100件)
    rows = []
    for i in range(1, 101):
        rows.append({
            "supplier_id": i,
            "supplier_name": f"サプライヤー{i:03d}",
            "contact_email": f"supplier{i}@example.com",
            "contact_phone": f"03-{random.randint(1000,9999)}-{random.randint(1000,9999)}",
            "prefecture": random.choice(PREFECTURES),
            "address": f"テスト住所{i}",
            "is_active": random.random() > 0.1,
            "rating": round(random.uniform(2.0, 5.0), 1),
            "contract_start": (datetime(2022, 1, 1) + timedelta(days=random.randint(0, 1000))).strftime("%Y-%m-%d"),
            "created_at": "2023-01-01",
        })
    tables["suppliers"] = rows

    # 3. product_suppliers (600件)
    rows = []
    for i in range(1, 601):
        rows.append({
            "product_supplier_id": i,
            "product_id": random.choice(products)["product_id"],
            "supplier_id": random.randint(1, 100),
            "supply_price": random.randint(100, 30000),
            "lead_time_days": random.randint(1, 30),
            "min_order_quantity": random.choice([1, 5, 10, 20, 50]),
            "is_primary": random.random() > 0.7,
            "currency": "JPY",
            "last_order_date": rand_date(),
            "created_at": rand_date(),
        })
    tables["product_suppliers"] = rows

    # 4. warehouses (20件)
    rows = []
    for i in range(1, 21):
        region = WAREHOUSE_REGIONS[i % len(WAREHOUSE_REGIONS)]
        rows.append({
            "warehouse_id": i,
            "warehouse_name": f"{region}倉庫{i}",
            "region": region,
            "prefecture": random.choice(PREFECTURES),
            "capacity_sqm": random.randint(500, 10000),
            "current_usage_pct": round(random.uniform(30.0, 95.0), 1),
            "is_active": True,
            "manager_name": f"管理者{i}",
            "opened_at": (datetime(2020, 1, 1) + timedelta(days=random.randint(0, 1500))).strftime("%Y-%m-%d"),
            "monthly_cost": random.randint(500000, 5000000),
        })
    tables["warehouses"] = rows

    # 5. inventory_movements (10,000件)
    rows = []
    movement_types = ["入庫", "出庫", "移動", "棚卸調整", "返品入庫"]
    for i in range(1, 10001):
        rows.append({
            "movement_id": i,
            "product_id": random.choice(products)["product_id"],
            "warehouse_id": random.randint(1, 20),
            "movement_type": random.choice(movement_types),
            "quantity": random.randint(-50, 200),
            "before_stock": random.randint(0, 500),
            "after_stock": random.randint(0, 500),
            "reference_id": f"REF-{random.randint(10000, 99999)}",
            "operator": f"OP{random.randint(1, 50):03d}",
            "moved_at": rand_dt(),
        })
    tables["inventory_movements"] = rows

    # 6. shipping_carriers (10件)
    rows = []
    for i, name in enumerate(CARRIER_NAMES, 1):
        rows.append({
            "carrier_id": i,
            "carrier_name": name,
            "carrier_code": f"CR{i:03d}",
            "tracking_url_template": f"https://{name.lower()}.example.com/track/{{id}}",
            "avg_delivery_days": round(random.uniform(1.0, 5.0), 1),
            "base_shipping_fee": random.choice([500, 600, 700, 800, 1000]),
            "free_shipping_threshold": random.choice([3000, 5000, 8000, 10000]),
            "is_active": True,
            "support_phone": f"0120-{random.randint(100,999)}-{random.randint(100,999)}",
            "contract_start": (datetime(2022, 1, 1) + timedelta(days=random.randint(0, 500))).strftime("%Y-%m-%d"),
        })
    tables["shipping_carriers"] = rows

    # 7. shipments (3,000件)
    rows = []
    shipment_statuses = ["preparing", "shipped", "in_transit", "delivered", "failed"]
    for i in range(1, 3001):
        order = random.choice(orders)
        ship_dt = datetime.strptime(order["order_date"], "%Y-%m-%d %H:%M:%S") + timedelta(hours=random.randint(1, 72))
        rows.append({
            "shipment_id": i,
            "order_id": order["order_id"],
            "carrier_id": random.randint(1, 10),
            "tracking_number": f"TRK{random.randint(1000000000, 9999999999)}",
            "status": random.choices(shipment_statuses, weights=[5, 10, 15, 65, 5])[0],
            "shipped_at": ship_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "delivered_at": (ship_dt + timedelta(days=random.randint(1, 5))).strftime("%Y-%m-%d %H:%M:%S"),
            "shipping_fee": random.choice([0, 500, 600, 700, 800]),
            "weight_kg": round(random.uniform(0.1, 30.0), 2),
            "created_at": ship_dt.strftime("%Y-%m-%d %H:%M:%S"),
        })
    tables["shipments"] = rows

    # 8. returns (500件)
    completed_orders = [o for o in orders if o["status"] in ("completed", "returned")]
    rows = []
    for i in range(1, 501):
        order = random.choice(completed_orders) if completed_orders else random.choice(orders)
        return_dt = datetime.strptime(order["order_date"], "%Y-%m-%d %H:%M:%S") + timedelta(days=random.randint(3, 30))
        rows.append({
            "return_id": i,
            "order_id": order["order_id"],
            "customer_id": order["customer_id"],
            "reason": random.choice(RETURN_REASONS),
            "status": random.choice(["requested", "approved", "received", "refunded", "rejected"]),
            "refund_amount": random.randint(500, 50000),
            "return_shipping_fee": random.choice([0, 500, 700]),
            "is_restockable": random.random() > 0.3,
            "notes": random.choice(["", "検品済み", "破損あり", "未開封", ""]),
            "requested_at": return_dt.strftime("%Y-%m-%d %H:%M:%S"),
        })
    tables["returns"] = rows

    # 9. coupons (50件)
    rows = []
    coupon_types = ["percentage", "fixed_amount", "free_shipping"]
    for i in range(1, 51):
        ctype = random.choice(coupon_types)
        rows.append({
            "coupon_id": i,
            "coupon_code": f"COUPON{i:04d}",
            "coupon_type": ctype,
            "discount_value": random.randint(5, 50) if ctype == "percentage" else random.randint(100, 5000),
            "min_order_amount": random.choice([0, 1000, 3000, 5000, 10000]),
            "max_uses": random.choice([100, 500, 1000, None]),
            "current_uses": random.randint(0, 500),
            "is_active": random.random() > 0.3,
            "valid_from": rand_date(),
            "valid_until": (dt_start + timedelta(days=random.randint(400, 900))).strftime("%Y-%m-%d"),
        })
    tables["coupons"] = rows

    # 10. coupon_usage (2,000件)
    rows = []
    for i in range(1, 2001):
        order = random.choice(orders)
        rows.append({
            "usage_id": i,
            "coupon_id": random.randint(1, 50),
            "order_id": order["order_id"],
            "customer_id": order["customer_id"],
            "discount_amount": random.randint(100, 5000),
            "original_amount": random.randint(1000, 80000),
            "final_amount": random.randint(500, 75000),
            "applied_at": order["order_date"],
            "coupon_code": f"COUPON{random.randint(1, 50):04d}",
            "is_first_use": random.random() > 0.7,
        })
    tables["coupon_usage"] = rows

    # 11. product_reviews (5,000件)
    rows = []
    for i in range(1, 5001):
        rows.append({
            "review_id": i,
            "product_id": random.choice(products)["product_id"],
            "customer_id": random.choice(customers)["customer_id"],
            "rating": random.randint(1, 5),
            "title": random.choice(REVIEW_COMMENTS)[:20],
            "body": random.choice(REVIEW_COMMENTS),
            "is_verified_purchase": random.random() > 0.2,
            "helpful_count": random.randint(0, 50),
            "reported": random.random() < 0.05,
            "reviewed_at": rand_dt(),
        })
    tables["product_reviews"] = rows

    # 12. page_views (50,000件)
    rows = []
    for i in range(1, 50001):
        rows.append({
            "view_id": i,
            "session_id": f"sess-{random.randint(1, 20000):06d}",
            "customer_id": random.choice(customers)["customer_id"] if random.random() > 0.3 else None,
            "page_type": random.choice(PAGE_TYPES),
            "page_url": f"/page/{random.randint(1, 1000)}",
            "device_type": random.choice(DEVICE_TYPES),
            "browser": random.choice(BROWSERS),
            "duration_sec": random.randint(1, 600),
            "referrer": random.choice(["google", "direct", "sns", "email", "ad", ""]),
            "viewed_at": rand_dt(),
        })
    tables["page_views"] = rows

    # 13. campaigns (30件)
    rows = []
    for i in range(1, 31):
        start_d = dt_start + timedelta(days=random.randint(0, 600))
        rows.append({
            "campaign_id": i,
            "campaign_name": f"{random.choice(CAMPAIGN_TYPES)}キャンペーン{i}",
            "campaign_type": random.choice(CAMPAIGN_TYPES),
            "channel": random.choice(CAMPAIGN_CHANNELS),
            "budget": random.randint(100000, 5000000),
            "spent": random.randint(50000, 4000000),
            "target_segment": random.choice(SEGMENT_NAMES),
            "is_active": random.random() > 0.4,
            "start_date": start_d.strftime("%Y-%m-%d"),
            "end_date": (start_d + timedelta(days=random.randint(7, 90))).strftime("%Y-%m-%d"),
        })
    tables["campaigns"] = rows

    # 14. campaign_results (1,000件)
    rows = []
    for i in range(1, 1001):
        rows.append({
            "result_id": i,
            "campaign_id": random.randint(1, 30),
            "result_date": rand_date(),
            "impressions": random.randint(100, 100000),
            "clicks": random.randint(10, 10000),
            "conversions": random.randint(0, 500),
            "revenue": random.randint(0, 5000000),
            "cost": random.randint(1000, 200000),
            "ctr_pct": round(random.uniform(0.1, 15.0), 2),
            "roas": round(random.uniform(0.5, 20.0), 2),
        })
    tables["campaign_results"] = rows

    # 15. customer_segments (10件)
    rows = []
    for i, name in enumerate(SEGMENT_NAMES, 1):
        rows.append({
            "segment_id": i,
            "segment_name": name,
            "description": f"{name}の顧客グループ",
            "criteria": f"rule_{i}",
            "member_count": 0,
            "is_dynamic": random.random() > 0.5,
            "priority": random.randint(1, 10),
            "created_by": f"admin{random.randint(1, 5)}",
            "created_at": "2024-01-01",
            "updated_at": rand_date(),
        })
    tables["customer_segments"] = rows

    # 16. customer_segment_map (2,000件)
    rows = []
    for i in range(1, 2001):
        rows.append({
            "mapping_id": i,
            "customer_id": random.choice(customers)["customer_id"],
            "segment_id": random.randint(1, 10),
            "score": round(random.uniform(0.0, 1.0), 3),
            "assigned_at": rand_dt(),
            "expires_at": (dt_end + timedelta(days=random.randint(0, 365))).strftime("%Y-%m-%d"),
            "source": random.choice(["auto", "manual", "ml_model"]),
            "confidence": round(random.uniform(0.5, 1.0), 2),
            "is_active": random.random() > 0.1,
            "updated_at": rand_dt(),
        })
    tables["customer_segment_map"] = rows

    # 17. employees (200件)
    rows = []
    for i in range(1, 201):
        rows.append({
            "employee_id": i,
            "employee_name": f"{random.choice(LAST_NAMES)} {random.choice(FIRST_NAMES)}",
            "email": f"emp{i}@example.com",
            "department": random.choice(DEPARTMENTS),
            "position": random.choice(POSITIONS),
            "hire_date": (datetime(2018, 1, 1) + timedelta(days=random.randint(0, 2500))).strftime("%Y-%m-%d"),
            "is_active": random.random() > 0.05,
            "phone": f"090-{random.randint(1000,9999)}-{random.randint(1000,9999)}",
            "salary_grade": random.randint(1, 10),
            "manager_id": random.randint(1, 20) if i > 20 else None,
        })
    tables["employees"] = rows

    # 18. support_tickets (3,000件)
    rows = []
    for i in range(1, 3001):
        cust = random.choice(customers)
        created = rand_dt()
        rows.append({
            "ticket_id": i,
            "customer_id": cust["customer_id"],
            "order_id": random.choice(orders)["order_id"] if random.random() > 0.2 else None,
            "category": random.choice(TICKET_CATEGORIES),
            "priority": random.choice(TICKET_PRIORITIES),
            "status": random.choice(TICKET_STATUSES),
            "assigned_employee_id": random.randint(1, 200) if random.random() > 0.1 else None,
            "resolution_hours": round(random.uniform(0.5, 120.0), 1) if random.random() > 0.3 else None,
            "satisfaction_score": random.randint(1, 5) if random.random() > 0.4 else None,
            "created_at": created,
        })
    tables["support_tickets"] = rows

    # 19. payment_transactions (4,000件)
    rows = []
    for i in range(1, 4001):
        order = random.choice(orders)
        rows.append({
            "transaction_id": f"TXN-{i:08d}",
            "order_id": order["order_id"],
            "payment_method": order["payment_method"],
            "gateway": random.choice(PAYMENT_GATEWAYS),
            "amount": order["total_amount"] if order["total_amount"] > 0 else random.randint(500, 50000),
            "currency": "JPY",
            "status": random.choices(PAYMENT_STATUSES, weights=[10, 70, 5, 5, 10])[0],
            "fee": random.randint(0, 500),
            "authorized_at": order["order_date"],
            "settled_at": (datetime.strptime(order["order_date"], "%Y-%m-%d %H:%M:%S") + timedelta(days=random.randint(1, 7))).strftime("%Y-%m-%d %H:%M:%S"),
        })
    tables["payment_transactions"] = rows

    # 20. daily_sales_summary (800件)
    rows = []
    base = datetime(2024, 1, 1)
    for i in range(800):
        d = (base + timedelta(days=i)).strftime("%Y-%m-%d")
        rows.append({
            "summary_date": d,
            "total_orders": random.randint(5, 80),
            "total_revenue": random.randint(50000, 2000000),
            "total_items_sold": random.randint(10, 300),
            "avg_order_value": random.randint(2000, 30000),
            "new_customers": random.randint(0, 30),
            "returning_customers": random.randint(5, 50),
            "refund_amount": random.randint(0, 100000),
            "discount_amount": random.randint(0, 50000),
            "unique_visitors": random.randint(100, 5000),
        })
    tables["daily_sales_summary"] = rows

    return tables


# --- メイン ---

def write_csv(filepath: str, rows: list[dict]) -> None:
    if not rows:
        return
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def write_multi_csv_orders(output_dir: str, orders: list[dict]) -> None:
    """orders を order_date の年月で分割して orders_YYYYMM.csv として出力"""
    from collections import defaultdict
    by_month: dict[str, list[dict]] = defaultdict(list)
    for row in orders:
        ym = row["order_date"][:7].replace("-", "")  # "2024-01" -> "202401"
        by_month[ym].append(row)
    for ym in sorted(by_month.keys()):
        write_csv(os.path.join(output_dir, f"orders_{ym}.csv"), by_month[ym])
    return sorted(by_month.keys())


def write_multi_csv_order_items(output_dir: str, order_items: list[dict]) -> None:
    """order_items を 3 分割して order_items_part1〜3.csv として出力"""
    n = len(order_items)
    chunk_size = n // 3
    parts = [
        order_items[:chunk_size],
        order_items[chunk_size:chunk_size * 2],
        order_items[chunk_size * 2:],
    ]
    for i, part in enumerate(parts, 1):
        write_csv(os.path.join(output_dir, f"order_items_part{i}.csv"), part)


def main():
    parser = argparse.ArgumentParser(description="EC サイトテスト用 CSV データ生成")
    parser.add_argument("--output-dir", default="testdata", help="出力ディレクトリ (default: testdata)")
    parser.add_argument("--scale", choices=["default", "large"], default="default",
                        help="データ規模: default=4テーブル, large=24テーブル (default: default)")
    parser.add_argument("--multi-csv", action="store_true",
                        help="orders / order_items を複数 CSV に分割して出力（複数CSV→1テーブルのテスト用）")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    random.seed(42)

    customers = gen_customers(NUM_CUSTOMERS)
    products = gen_products(NUM_PRODUCTS)
    orders = gen_orders(NUM_ORDERS, customers)
    order_items = gen_order_items(NUM_ORDER_ITEMS, orders, products)

    if args.multi_csv:
        # customers, products はマスターデータなので分割しない
        write_csv(os.path.join(args.output_dir, "customers.csv"), customers)
        write_csv(os.path.join(args.output_dir, "products.csv"), products)

        # orders を月次分割
        months = write_multi_csv_orders(args.output_dir, orders)
        print(f"生成完了: {args.output_dir}/")
        print(f"  customers.csv : {len(customers)} 件")
        print(f"  products.csv : {len(products)} 件")
        print(f"  orders_YYYYMM.csv : {len(orders)} 件 → {len(months)} ファイルに分割")

        # order_items を 3 分割
        write_multi_csv_order_items(args.output_dir, order_items)
        print(f"  order_items_part{{1..3}}.csv : {len(order_items)} 件 → 3 ファイルに分割")
    else:
        base_tables = {
            "customers": customers,
            "products": products,
            "orders": orders,
            "order_items": order_items,
        }

        for name, rows in base_tables.items():
            write_csv(os.path.join(args.output_dir, f"{name}.csv"), rows)

        print(f"生成完了: {args.output_dir}/")
        for name, rows in base_tables.items():
            print(f"  {name}.csv : {len(rows)} 件")

    if args.scale == "large":
        extra = gen_extra_tables(customers, products, orders, order_items)
        for name, rows in extra.items():
            write_csv(os.path.join(args.output_dir, f"{name}.csv"), rows)
        print(f"\n追加テーブル ({len(extra)} テーブル):")
        for name, rows in extra.items():
            print(f"  {name}.csv : {len(rows)} 件")
        total_base = 4 if not args.multi_csv else 2 + len(months) + 3  # type: ignore[possibly-undefined]
        print(f"\n合計: {total_base + len(extra)} テーブル")


if __name__ == "__main__":
    main()
