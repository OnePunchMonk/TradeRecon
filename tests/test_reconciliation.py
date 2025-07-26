import pytest
import pandas as pd
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os
import json

from app.utils import parse_timestamp, is_within_tolerance, is_timestamp_within_drift, calculate_pnl_consistency
from app.reconcile import ReconciliationEngine, ReconciliationResult, Base

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
EXECUTIONS_CSV = os.path.join(DATA_DIR, 'executions.csv')
CONFIRMATIONS_CSV = os.path.join(DATA_DIR, 'broker_confirmations.csv')
PNL_SNAPSHOT_CSV = os.path.join(DATA_DIR, 'pnl_snapshot.csv')

@pytest.fixture(scope="function")
def in_memory_db():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)

@pytest.fixture(scope="function")
def reconciliation_engine(in_memory_db):
    engine = in_memory_db.bind
    return ReconciliationEngine(db_url=engine.url)

def load_and_prepare_trade_data():
    try:
        executions_df = pd.read_csv(EXECUTIONS_CSV)
        confirmations_df = pd.read_csv(CONFIRMATIONS_CSV)
        pnl_df = pd.read_csv(PNL_SNAPSHOT_CSV)
    except FileNotFoundError as e:
        pytest.fail(f"Required CSV file not found: {e}")

    merged_df = pd.merge(executions_df, confirmations_df, on='trade_id', suffixes=('_exec', '_conf'), how='outer')
    merged_df = pd.merge(merged_df, pnl_df, on='trade_id', how='left')

    test_cases = []
    for _, row in merged_df.iterrows():
        trade_id = row['trade_id']
        ticker = row.get('ticker_exec') or row.get('ticker_conf')

        exec_data = {
            'trade_id': trade_id,
            'ticker': ticker,
            'quantity': row.get('quantity_exec'),
            'price': row.get('price_exec'),
            'timestamp': row.get('timestamp_exec')
        } if pd.notna(row.get('quantity_exec')) else None

        conf_data = {
            'trade_id': trade_id,
            'ticker': ticker,
            'quantity': row.get('quantity_conf'),
            'price': row.get('price_conf'),
            'timestamp': row.get('timestamp_conf')
        } if pd.notna(row.get('quantity_conf')) else None

        pnl_data = {
            'trade_id': trade_id,
            'pnl_impact': row.get('pnl_impact'),
            'commission': row.get('commission')
        } if pd.notna(row.get('pnl_impact')) else None

        expected_status = 'PENDING'
        expected_mismatches = []

        if exec_data and conf_data:
            is_matched = True

            if not is_within_tolerance(exec_data['quantity'], conf_data['quantity'], tolerance=0.0):
                expected_mismatches.append({
                    'field': 'quantity',
                    'execution': exec_data['quantity'],
                    'confirmation': conf_data['quantity'],
                    'reason': 'Quantity mismatch'
                })
                is_matched = False

            if not is_within_tolerance(exec_data['price'], conf_data['price'], tolerance=0.005):
                expected_mismatches.append({
                    'field': 'price',
                    'execution': exec_data['price'],
                    'confirmation': conf_data['price'],
                    'reason': 'Price mismatch'
                })
                is_matched = False

            exec_ts = parse_timestamp(exec_data['timestamp'])
            conf_ts = parse_timestamp(conf_data['timestamp'])
            if not is_timestamp_within_drift(exec_ts, conf_ts, drift_ms=100):
                expected_mismatches.append({
                    'field': 'timestamp',
                    'execution': exec_data['timestamp'],
                    'confirmation': conf_data['timestamp'],
                    'reason': 'Timestamp drift beyond 100ms'
                })
                is_matched = False

            if pnl_data and pd.notna(pnl_data['pnl_impact']) and pd.notna(pnl_data['commission']):
                try:
                    exec_price_num = float(exec_data['price'])
                    exec_qty_num = int(exec_data['quantity'])
                    pnl_impact_num = float(pnl_data['pnl_impact'])
                    commission_num = float(pnl_data['commission'])

                    if not calculate_pnl_consistency(exec_price_num, exec_qty_num, commission_num, pnl_impact_num, threshold=1.0):
                        expected_mismatches.append({
                            'field': 'pnl_consistency',
                            'calculated_pnl': round((exec_price_num * exec_qty_num) - commission_num, 2),
                            'reported_pnl_impact': pnl_impact_num,
                            'reason': 'PnL consistency check failed'
                        })
                        is_matched = False
                except (ValueError, TypeError):
                    pass

            expected_status = 'MISMATCHED' if not is_matched else 'MATCHED'
        elif exec_data or conf_data:
            expected_status = 'PENDING_INCOMPLETE'

        test_cases.append((
            trade_id,
            exec_data,
            conf_data,
            pnl_data,
            expected_status,
            expected_mismatches
        ))
    return test_cases

@pytest.mark.parametrize(
    "trade_id, exec_data, conf_data, pnl_data, expected_status, expected_mismatches",
    load_and_prepare_trade_data()
)
def test_full_reconciliation_flow(
    reconciliation_engine, in_memory_db,
    trade_id, exec_data, conf_data, pnl_data, expected_status, expected_mismatches
):
    if exec_data:
        reconciliation_engine.process_message('executions', exec_data)
    if conf_data:
        reconciliation_engine.process_message('confirmations', conf_data)
    if pnl_data:
        reconciliation_engine.process_message('pnl_snapshot', pnl_data)

    result = in_memory_db.query(ReconciliationResult).filter_by(trade_id=trade_id).first()

    if expected_status == 'PENDING_INCOMPLETE':
        assert result is None
    else:
        assert result is not None
        assert result.status == expected_status

        actual_mismatches_json = sorted([json.dumps(d, sort_keys=True) for d in result.mismatch_details])
        expected_mismatches_json = sorted([json.dumps(d, sort_keys=True) for d in expected_mismatches])

        assert actual_mismatches_json == expected_mismatches_json
