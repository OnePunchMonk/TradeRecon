import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from jinja2 import Environment, FileSystemLoader
from datetime import datetime
import os
from reconcile import ReconciliationResult, Base

class ReportGenerator:
    def __init__(self, db_url='sqlite:///./reports/reconciliation.db', template_dir='./reports/templates'):
        self.engine = create_engine(db_url)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.env = Environment(loader=FileSystemLoader(template_dir))
        self.report_output_dir = './reports'
        os.makedirs(self.report_output_dir, exist_ok=True)
        print(f"ReportGenerator initialized. DB: {db_url}, Templates: {template_dir}")

    def _get_session(self):
        return self.Session()

    def fetch_all_reconciliation_results(self) -> pd.DataFrame:
        session = self._get_session()
        try:
            results = session.query(ReconciliationResult).all()
            data = []
            for r in results:
                mismatch_details = r.mismatch_details if r.mismatch_details is not None else []
                row = {
                    'id': r.id,
                    'trade_id': r.trade_id,
                    'ticker': r.ticker,
                    'status': r.status,
                    'reconciliation_timestamp': r.reconciliation_timestamp.isoformat(),
                    'execution_qty': r.execution_data.get('quantity') if r.execution_data else None,
                    'execution_price': r.execution_data.get('price') if r.execution_data else None,
                    'execution_timestamp': r.execution_data.get('timestamp') if r.execution_data else None,
                    'confirmation_qty': r.confirmation_data.get('quantity') if r.confirmation_data else None,
                    'confirmation_price': r.confirmation_data.get('price') if r.confirmation_data else None,
                    'confirmation_timestamp': r.confirmation_data.get('timestamp') if r.confirmation_data else None,
                    'pnl_impact': r.pnl_data.get('pnl_impact') if r.pnl_data else None,
                    'commission': r.pnl_data.get('commission') if r.pnl_data else None,
                    'mismatch_details': mismatch_details
                }
                data.append(row)
            df = pd.DataFrame(data)
            return df
        except Exception as e:
            print(f"Error fetching reconciliation results: {e}")
            return pd.DataFrame()
        finally:
            session.close()

    def generate_html_report(self, filename: str = None) -> str:
        df = self.fetch_all_reconciliation_results()
        template = self.env.get_template('report.html')

        if not df.empty:
            df['mismatch_summary'] = df['mismatch_details'].apply(
                lambda x: ', '.join([f"{d['field']}: {d['reason']}" for d in x]) if x else 'N/A'
            )
        else:
            df['mismatch_summary'] = pd.Series(dtype='object')

        report_data = df.to_dict(orient='records')
        matched_trades = [t for t in report_data if t['status'] == 'MATCHED']
        mismatched_trades = [t for t in report_data if t['status'] == 'MISMATCHED']

        html_content = template.render(
            report_timestamp=datetime.now().isoformat(timespec='seconds'),
            total_trades=len(report_data),
            matched_count=len(matched_trades),
            mismatched_count=len(mismatched_trades),
            matched_trades=matched_trades,
            mismatched_trades=mismatched_trades
        )

        if filename:
            filepath = os.path.join(self.report_output_dir, filename)
            with open(filepath, 'w') as f:
                f.write(html_content)
            print(f"HTML report saved to {filepath}")
        return html_content

    def generate_csv_report(self, filename: str = 'reconciliation_report.csv'):
        df = self.fetch_all_reconciliation_results()
        if not df.empty:
            df['mismatch_summary'] = df['mismatch_details'].apply(
                lambda x: ', '.join([f"{d['field']}: {d['reason']}" for d in x]) if x else 'N/A'
            )
        else:
            df['mismatch_summary'] = pd.Series(dtype='object')

        filepath = os.path.join(self.report_output_dir, filename)
        df.to_csv(filepath, index=False)
        print(f"CSV report saved to {filepath}")
