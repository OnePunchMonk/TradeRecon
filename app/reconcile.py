import threading
import time
from datetime import datetime
from sqlalchemy import create_engine, Column, String, Float, DateTime, Boolean, JSON, Integer
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.sqlite import JSON as SQLiteJSON
import json
import pandas as pd # Import pandas here for pd.notna
from utils import parse_timestamp, is_within_tolerance, is_timestamp_within_drift, calculate_pnl_consistency

# Define the database model
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
    def __init__(self, db_url='sqlite:///./reports/reconciliation.db'):
        self.trade_store = {} # {trade_id: {'execution': data, 'confirmation': data, 'pnl': data, 'status': 'pending'}}
        self.trade_store_lock = threading.Lock() # To protect shared trade_store
        self.engine = create_engine(db_url)
        Base.metadata.create_all(self.engine) # Create tables if they don't exist
        self.Session = sessionmaker(bind=self.engine)
        print(f"ReconciliationEngine initialized with DB: {db_url}")

        # Metrics collectors (will be set from main.py)
        self.total_trades_counter = None
        self.matched_trades_counter = None
        self.mismatched_trades_counter = None
        self.in_memory_store_size_gauge = None
        self.reconciliation_latency_histogram = None

    def set_metrics_collectors(self, total_trades_counter, matched_trades_counter,
                               mismatched_trades_counter, in_memory_store_size_gauge,
                               reconciliation_latency_histogram):
        """Sets the Prometheus metric collector objects."""
        self.total_trades_counter = total_trades_counter
        self.matched_trades_counter = matched_trades_counter
        self.mismatched_trades_counter = mismatched_trades_counter
        self.in_memory_store_size_gauge = in_memory_store_size_gauge
        self.reconciliation_latency_histogram = reconciliation_latency_histogram

    def _get_session(self):
        """Helper to get a new DB session."""
        return self.Session()

    def process_message(self, topic: str, message: dict):
        """
        Processes an incoming message from a Kafka topic.
        Updates the in-memory trade store and triggers reconciliation if complete.
        """
        trade_id = message.get('trade_id')
        if not trade_id:
            print(f"Warning: Message from topic {topic} missing 'trade_id': {message}")
            return

        with self.trade_store_lock:
            if trade_id not in self.trade_store:
                self.trade_store[trade_id] = {
                    'execution': None,
                    'confirmation': None,
                    'pnl': None,
                    'status': 'PENDING',
                    'start_time': time.time() # Record start time for latency
                }
                if self.in_memory_store_size_gauge:
                    self.in_memory_store_size_gauge.inc() # Increment gauge when new trade added

            # Update the specific data part for the trade_id
            if topic == 'executions':
                self.trade_store[trade_id]['execution'] = message
            elif topic == 'confirmations':
                self.trade_store[trade_id]['confirmation'] = message
            elif topic == 'pnl_snapshot':
                self.trade_store[trade_id]['pnl'] = message
            else:
                print(f"Unknown topic: {topic} for trade_id {trade_id}")
                return

            # Attempt reconciliation whenever any new piece of data arrives
            # This allows for iterative updates to the reconciliation status
            self._attempt_reconciliation(trade_id)

    def _attempt_reconciliation(self, trade_id: str):
        """
        Attempts to reconcile a trade if all necessary data (execution, confirmation)
        is available. PnL is optional but will be included if present.
        This function will update the DB record for the trade.
        """
        trade_data = self.trade_store[trade_id]
        execution = trade_data.get('execution')
        confirmation = trade_data.get('confirmation')
        pnl = trade_data.get('pnl')

        # Only reconcile if both execution and confirmation are present
        # The PnL data will be included if available at this point.
        # If PnL arrives later, it will trigger an update through this same path.
        if execution and confirmation:
            self._perform_reconciliation_and_save(trade_id, execution, confirmation, pnl)
            # We don't delete from trade_store here to allow PnL updates
            # or for inspection in a longer-running system.
            # A cleanup mechanism would be needed in a production environment.
        else:
            print(f"Trade {trade_id} not ready for reconciliation. Missing execution or confirmation data.")


    def _perform_reconciliation_and_save(self, trade_id: str, execution: dict, confirmation: dict, pnl: dict = None):
        """
        Compares execution and confirmation data and logs mismatches.
        Persists the result to the database, updating if a record already exists.
        """
        mismatches = []
        is_matched = True

        if self.total_trades_counter:
            self.total_trades_counter.inc() # Increment total trades processed

        # Quantity check
        if not is_within_tolerance(execution['quantity'], confirmation['quantity'], tolerance=0.0): # Exact match for quantity
            mismatches.append({
                'field': 'quantity',
                'execution': execution['quantity'],
                'confirmation': confirmation['quantity'],
                'reason': 'Quantity mismatch'
            })
            is_matched = False

        # Price check (within 2 decimal places tolerance)
        if not is_within_tolerance(execution['price'], confirmation['price'], tolerance=0.005): # 0.005 for 2dp tolerance
            mismatches.append({
                'field': 'price',
                'execution': execution['price'],
                'confirmation': confirmation['price'],
                'reason': 'Price mismatch'
            })
            is_matched = False

        # Timestamp check (within 100ms drift)
        exec_ts = parse_timestamp(execution['timestamp'])
        conf_ts = parse_timestamp(confirmation['timestamp'])
        if not is_timestamp_within_drift(exec_ts, conf_ts, drift_ms=100):
            mismatches.append({
                'field': 'timestamp',
                'execution': execution['timestamp'],
                'confirmation': confirmation['timestamp'],
                'reason': 'Timestamp drift beyond 100ms'
            })
            is_matched = False

        # PnL consistency check (if PnL data is available)
        if pnl and pd.notna(pnl.get('pnl_impact')) and pd.notna(pnl.get('commission')):
            try:
                exec_price_num = float(execution['price'])
                exec_qty_num = int(execution['quantity'])
                pnl_impact_num = float(pnl['pnl_impact'])
                commission_num = float(pnl['commission'])

                if not calculate_pnl_consistency(exec_price_num, exec_qty_num, commission_num, pnl_impact_num, threshold=1.0):
                    mismatches.append({
                        'field': 'pnl_consistency',
                        'calculated_pnl': round((exec_price_num * exec_qty_num) - commission_num, 2),
                        'reported_pnl_impact': pnl_impact_num,
                        'reason': 'PnL consistency check failed'
                    })
                    is_matched = False
            except (ValueError, TypeError) as e:
                mismatches.append({
                    'field': 'pnl_calculation_error',
                    'reason': f'Error converting PnL related values to numbers: {e}'
                })
                is_matched = False


        status = 'MISMATCHED' if not is_matched else 'MATCHED'
        print(f"Reconciliation for Trade {trade_id}: Status = {status}")
        if mismatches:
            print(f"  Mismatches found: {mismatches}")
            if self.mismatched_trades_counter:
                self.mismatched_trades_counter.inc()
        else:
            if self.matched_trades_counter:
                self.matched_trades_counter.inc()

        # Record latency
        if 'start_time' in self.trade_store.get(trade_id, {}):
            latency = time.time() - self.trade_store[trade_id]['start_time']
            if self.reconciliation_latency_histogram:
                self.reconciliation_latency_histogram.observe(latency)
            # Optionally remove trade from in-memory store after full reconciliation and metric collection
            # For simplicity in this project, we let it persist to support later PnL updates.
            # In a prod system, you'd manage this cache explicitly.


        session = self._get_session()
        try:
            # Check if a record for this trade_id already exists
            existing_result = session.query(ReconciliationResult).filter_by(trade_id=trade_id).first()

            if existing_result:
                # Update existing record
                existing_result.ticker = execution.get('ticker', 'N/A')
                existing_result.status = status
                existing_result.execution_data = execution
                existing_result.confirmation_data = confirmation
                existing_result.pnl_data = pnl
                existing_result.mismatch_details = mismatches
                existing_result.reconciliation_timestamp = datetime.utcnow() # Update timestamp
                print(f"Reconciliation result for {trade_id} updated in DB.")
            else:
                # Create new record
                new_result = ReconciliationResult(
                    trade_id=trade_id,
                    ticker=execution.get('ticker', 'N/A'),
                    status=status,
                    execution_data=execution,
                    confirmation_data=confirmation,
                    pnl_data=pnl,
                    mismatch_details=mismatches
                )
                session.add(new_result)
                print(f"Reconciliation result for {trade_id} inserted into DB.")

            session.commit()
        except Exception as e:
            session.rollback()
            print(f"Error saving/updating reconciliation result for {trade_id}: {e}")
        finally:
            session.close()
