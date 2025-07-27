import os
import threading
import time
from flask import Flask, render_template, send_file, request, jsonify
from .consumer import TradeDataConsumer
from .reconcile import ReconciliationEngine
from .report_generator import ReportGenerator
from prometheus_client import start_http_server, Counter, Gauge, Histogram

app = Flask(__name__, template_folder='../reports/templates', static_folder='../reports')

KAFKA_BOOTSTRAP_SERVERS = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'localhost:9092')
DB_URL = 'sqlite:///./reports/reconciliation.db'

TOTAL_TRADES_PROCESSED = Counter('traderecon_total_trades_processed', 'Total number of trades processed')
MATCHED_TRADES_COUNT = Counter('traderecon_matched_trades_total', 'Total number of trades that matched')
MISMATCHED_TRADES_COUNT = Counter('traderecon_mismatched_trades_total', 'Total number of trades that mismatched')
IN_MEMORY_STORE_SIZE = Gauge('traderecon_in_memory_store_size', 'Current size of the in-memory trade store')
RECONCILIATION_LATENCY_SECONDS = Histogram('traderecon_reconciliation_latency_seconds', 'Latency of trade reconciliation (seconds)')

TEST_HTTP_REQUESTS_TOTAL = Counter('traderecon_http_requests_total', 'Total HTTP requests to Flask app')
reconciliation_engine = ReconciliationEngine(db_url=DB_URL)
report_generator = ReportGenerator(db_url=DB_URL, template_dir='./reports/templates')
reconciliation_engine.set_metrics_collectors(
    total_trades_counter=TOTAL_TRADES_PROCESSED,
    matched_trades_counter=MATCHED_TRADES_COUNT,
    mismatched_trades_counter=MISMATCHED_TRADES_COUNT,
    in_memory_store_size_gauge=IN_MEMORY_STORE_SIZE,
    reconciliation_latency_histogram=RECONCILIATION_LATENCY_SECONDS
)

consumer_threads = []

@app.route('/')
def index():
    TEST_HTTP_REQUESTS_TOTAL.inc()
    html_report_content = report_generator.generate_html_report()
    return html_report_content

@app.route('/api/reconciliation_status')
def get_reconciliation_status():
    TEST_HTTP_REQUESTS_TOTAL.inc()
    df = report_generator.fetch_all_reconciliation_results()
    return jsonify(df.to_dict(orient='records'))

@app.route('/download/csv')
def download_csv():
    TEST_HTTP_REQUESTS_TOTAL.inc() # Also increment for CSV downloads
    csv_filename = 'reconciliation_report.csv'
    report_generator.generate_csv_report(filename=csv_filename)
    csv_filepath = os.path.join(report_generator.report_output_dir, csv_filename)
    return send_file(csv_filepath, as_attachment=True, download_name='TradeRecon_Report.csv', mimetype='text/csv')

def start_consumers():
    print("Starting Kafka consumers...")
    execution_consumer = TradeDataConsumer(
        topic='executions',
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id=f'traderecon_exec_group_{int(time.time())}',
        reconcile_engine=reconciliation_engine
    )
    confirmation_consumer = TradeDataConsumer(
        topic='confirmations',
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id=f'traderecon_conf_group_{int(time.time())}',
        reconcile_engine=reconciliation_engine
    )
    pnl_consumer = TradeDataConsumer(
        topic='pnl_snapshot',
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id=f'traderecon_pnl_group_{int(time.time())}',
        reconcile_engine=reconciliation_engine
    )

    consumer_threads.append(execution_consumer)
    consumer_threads.append(confirmation_consumer)
    consumer_threads.append(pnl_consumer)

    for consumer in consumer_threads:
        consumer.start()
        time.sleep(0.5)

    print("All Kafka consumer threads started.")

def stop_consumers():
    print("Stopping Kafka consumers...")
    for consumer in consumer_threads:
        consumer.stop()
    for consumer in consumer_threads:
        consumer.join()
    print("All Kafka consumer threads stopped.")

if __name__ == '__main__':
    start_http_server(8000, addr='0.0.0.0')

    print("Prometheus metrics server started on port 8000.")

    consumer_thread = threading.Thread(target=start_consumers)
    consumer_thread.start()

    try:
        app.run(debug=False, host='0.0.0.0', port=5000)
    except KeyboardInterrupt:
        print("Flask app shutting down.")
    finally:
        stop_consumers()
        consumer_thread.join()
        print("Application gracefully shut down.")
