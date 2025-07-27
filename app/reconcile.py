import threading
import time
from datetime import datetime
from sqlalchemy import create_engine, Column, String, Float, DateTime, Boolean, JSON, Integer
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.sqlite import JSON as SQLiteJSON
import json
import pandas as pd # Import pandas here for pd.notna
from .utils import parse_timestamp, is_within_tolerance, is_timestamp_within_drift, calculate_pnl_consistency

Base = declarative_base()

class ReconciliationResult(Base):
    """
    SQLAlchemy model for storing reconciliation results.
    Using JSON type for mismatch_details to store flexible mismatch info.
    """
    __tablename__ = 'reconciliation_results'

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(String, unique=True, nullable=False)
    ticker = Column(String)
    status = Column(String, nullable=False) # 'MATCHED', 'MISMATCHED', 'PENDING'
    execution_data = Column(SQLiteJSON) # Store as JSON
    confirmation_data = Column(SQLiteJSON) # Store as JSON
    pnl_data = Column(SQLiteJSON) # Store as JSON
    mismatch_details = Column(SQLiteJSON) # Store details of mismatches as JSON
    reconciliation_timestamp = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<ReconciliationResult(trade_id='{self.trade_id}', status='{self.status}')>"
class ReconciliationEngine:
    """
    Core engine for real-time trade reconciliation.
    Manages incoming trade data and performs reconciliation checks.
    Uses an in-memory store for pending trades and SQLAlchemy for persistence.
    """
    def __init__(self, db_url='sqlite:///./reports/reconciliation.db', db_session=None):
        self.trade_store = {}
        self.trade_store_lock = threading.Lock()
        self.engine = create_engine(db_url)
        Base.metadata.create_all(self.engine)

        # This is the key change:
        if db_session:
            # If a session is provided (from your test), use it.
            self.session = db_session
        else:
            # Otherwise (in your main app), create a new one.
            Session = sessionmaker(bind=self.engine)
            self.session = Session()

        # Keep a public reference for tests to use
        self.db_session = self.session

        print(f"ReconciliationEngine initialized with DB: {db_url}")
        
        # Metrics (set externally)
        self.total_trades_counter = None
        self.matched_trades_counter = None
        self.mismatched_trades_counter = None
        self.in_memory_store_size_gauge = None
        self.reconciliation_latency_histogram = None

    def set_metrics_collectors(self, total_trades_counter, matched_trades_counter,
                               mismatched_trades_counter, in_memory_store_size_gauge,
                               reconciliation_latency_histogram):
        self.total_trades_counter = total_trades_counter
        self.matched_trades_counter = matched_trades_counter
        self.mismatched_trades_counter = mismatched_trades_counter
        self.in_memory_store_size_gauge = in_memory_store_size_gauge
        self.reconciliation_latency_histogram = reconciliation_latency_histogram

    def process_message(self, topic: str, message: dict):
        trade_id = message.get('trade_id')
        if not trade_id:
            print(f"Warning: Message from topic {topic} missing 'trade_id': {message}")
            return
        trade_id = str(trade_id)

        with self.trade_store_lock:
            if trade_id not in self.trade_store:
                self.trade_store[trade_id] = {
                    'execution': None, 'confirmation': None, 'pnl': None,
                    'status': 'PENDING', 'start_time': time.time()
                }
                if self.in_memory_store_size_gauge:
                    self.in_memory_store_size_gauge.inc()

            if topic == 'executions':
                self.trade_store[trade_id]['execution'] = message
            elif topic == 'confirmations':
                self.trade_store[trade_id]['confirmation'] = message
            elif topic == 'pnl_snapshot':
                self.trade_store[trade_id]['pnl'] = message
            else:
                print(f"Unknown topic: {topic} for trade_id {trade_id}")
                return

            self._attempt_reconciliation(trade_id)

    def _attempt_reconciliation(self, trade_id: str):
        trade_data = self.trade_store.get(trade_id)
        if not trade_data:
            print(f"Trade {trade_id} not found in store for reconciliation")
            return

        execution = trade_data.get('execution')
        confirmation = trade_data.get('confirmation')
        pnl = trade_data.get('pnl')

        if execution and confirmation:
            self._perform_reconciliation_and_save(trade_id, execution, confirmation, pnl)
        else:
            print(f"Trade {trade_id} not ready. Missing execution or confirmation.")

    def _perform_reconciliation_and_save(self, trade_id: str, execution: dict, confirmation: dict, pnl: dict = None):
        mismatches = []
        is_matched = True

        if self.total_trades_counter:
            self.total_trades_counter.inc()

        # --- Reconciliation Checks ---
        if not is_within_tolerance(execution['quantity'], confirmation['quantity'], tolerance=0.0):
            mismatches.append({'field': 'quantity', 'execution': execution['quantity'], 'confirmation': confirmation['quantity'], 'reason': 'Quantity mismatch'})
            is_matched = False
        if not is_within_tolerance(execution['price'], confirmation['price'], tolerance=0.005):
            mismatches.append({'field': 'price', 'execution': execution['price'], 'confirmation': confirmation['price'], 'reason': 'Price mismatch'})
            is_matched = False
        exec_ts = parse_timestamp(execution['timestamp'])
        conf_ts = parse_timestamp(confirmation['timestamp'])
        if not is_timestamp_within_drift(exec_ts, conf_ts, drift_ms=100):
            mismatches.append({'field': 'timestamp', 'execution': execution['timestamp'], 'confirmation': confirmation['timestamp'], 'reason': 'Timestamp drift beyond 100ms'})
            is_matched = False
        if pnl and pd.notna(pnl.get('pnl_impact')) and pd.notna(pnl.get('commission')):
            try:
                exec_price_num = float(execution['price'])
                exec_qty_num = int(execution['quantity'])
                pnl_impact_num = float(pnl['pnl_impact'])
                commission_num = float(pnl['commission'])
                if not calculate_pnl_consistency(exec_price_num, exec_qty_num, commission_num, pnl_impact_num, threshold=1.0):
                    mismatches.append({'field': 'pnl_consistency', 'calculated_pnl': round((exec_price_num * exec_qty_num) - commission_num, 2), 'reported_pnl_impact': pnl_impact_num, 'reason': 'PnL consistency check failed'})
                    is_matched = False
            except (ValueError, TypeError) as e:
                mismatches.append({'field': 'pnl_calculation_error', 'reason': f'Error converting PnL related values: {e}'})
                is_matched = False
        
        status = 'MISMATCHED' if not is_matched else 'MATCHED'
        print(f"Reconciliation for Trade {trade_id}: Status = {status}")
        if mismatches:
            print(f"  Mismatches: {mismatches}")
            if self.mismatched_trades_counter: self.mismatched_trades_counter.inc()
        else:
            if self.matched_trades_counter: self.matched_trades_counter.inc()
        
        if 'start_time' in self.trade_store[trade_id] and self.reconciliation_latency_histogram:
            latency = time.time() - self.trade_store[trade_id]['start_time']
            self.reconciliation_latency_histogram.observe(latency)

        session = self.session
        try:
            existing = session.query(ReconciliationResult).filter_by(trade_id=trade_id).first()
            if existing:
                existing.ticker = execution.get('ticker', 'N/A')
                existing.status = status
                existing.execution_data = execution
                existing.confirmation_data = confirmation
                existing.pnl_data = pnl
                existing.mismatch_details = mismatches
                existing.reconciliation_timestamp = datetime.utcnow()
                print(f"Updated trade {trade_id} in DB.")
            else:
                new_result = ReconciliationResult(
                    trade_id=trade_id, ticker=execution.get('ticker', 'N/A'), status=status,
                    execution_data=execution, confirmation_data=confirmation,
                    pnl_data=pnl, mismatch_details=mismatches
                )
                session.add(new_result)
                print(f"Inserted trade {trade_id} into DB.")
            session.commit()
        except Exception as e:
            session.rollback()
            print(f"DB error for trade {trade_id}: {e}")
        # NOTE: We do NOT close the session here, as it's managed by the fixture or the main app.