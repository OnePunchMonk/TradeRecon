from datetime import datetime
import math

def parse_timestamp(ts_str: str) -> datetime:
    if ts_str.endswith('Z'):
        ts_str = ts_str[:-1] + '+00:00'
    return datetime.fromisoformat(ts_str)

def is_within_tolerance(val1: float, val2: float, tolerance: float = 0.01) -> bool:
    return abs(val1 - val2) <= tolerance

def is_timestamp_within_drift(ts1: datetime, ts2: datetime, drift_ms: int = 100) -> bool:
    diff_ms = abs((ts1 - ts2).total_seconds() * 1000)
    return diff_ms <= drift_ms

def calculate_pnl_consistency(price: float, quantity: int, commission: float, pnl_impact: float, threshold: float = 1.0) -> bool:
    calculated_pnl = (price * quantity) - commission
    return abs(calculated_pnl - pnl_impact) < threshold
