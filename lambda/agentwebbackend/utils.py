"""共通ユーティリティ"""

from decimal import Decimal


def convert_decimals(obj):
    """DynamoDB から取得したデータ内の Decimal を float に再帰的に変換する"""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: convert_decimals(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [convert_decimals(v) for v in obj]
    return obj


def convert_floats(obj):
    """DynamoDB に書き込む前に float を Decimal に再帰的に変換する"""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: convert_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [convert_floats(v) for v in obj]
    return obj
