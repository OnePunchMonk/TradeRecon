import json
import time
import pandas as pd
from kafka import KafkaProducer
from kafka.errors import NoBrokersAvailable
from datetime import datetime, timedelta
import random
import os

# Kafka configuration
KAFKA_BOOTSTRAP_SERVERS = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'localhost:9092')
EXECUTION_TOPIC = 'executions'
CONFIRMATION_TOPIC = 'confirmations'
PNL_TOPIC = 'pnl_snapshot'

def create_kafka_producer_with_retries(retries=10, delay=5):
    producer = None
    for i in range(retries):
        try:
            print(f"Attempting to connect to Kafka brokers at {KAFKA_BOOTSTRAP_SERVERS} (Attempt {i+1}/{retries})...")
            producer = KafkaProducer(
                bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                acks='all',
                retries=3
            )
            print("Successfully connected to Kafka.")
            return producer
        except NoBrokersAvailable:
            print(f"No Kafka brokers available. Retrying in {delay} seconds...")
            time.sleep(delay)
        except Exception as e:
            print(f"An unexpected error occurred during Kafka connection: {e}. Retrying...")
            time.sleep(delay)
    raise Exception(f"Failed to connect to Kafka after {retries} attempts.")


def generate_trade_data(trade_id_prefix="T", num_trades=30):
    # This function is now commented out in __main__ and primarily for reference
    trades = []
    base_time = datetime.now()

    for i in range(num_trades):
        trade_id = f"{trade_id_prefix}{i+1:03d}"
        ticker = random.choice(['AAPL', 'GOOG', 'MSFT', 'AMZN', 'TSLA', 'NVDA', 'META', 'NFLX', 'AMD', 'INTC'])
        quantity = random.randint(10, 200) * 10
        price = round(random.uniform(50.0, 2000.0), 2)
        timestamp_exec = base_time + timedelta(seconds=i * 2, milliseconds=random.randint(0, 999))
        timestamp_conf = timestamp_exec + timedelta(milliseconds=random.randint(-150, 150))
        timestamp_pnl = timestamp_exec + timedelta(seconds=random.randint(1, 5))

        exec_qty = quantity
        exec_price = price
        conf_qty = quantity
        conf_price = price
        conf_ts = timestamp_conf

        if random.random() < 0.2: # 20% chance of quantity mismatch
            conf_qty += random.choice([-1, 1]) * random.randint(1, 5) * 10
        if random.random() < 0.2: # 20% chance of price mismatch
            conf_price += random.choice([-1, 1]) * round(random.uniform(0.01, 0.1), 2)
        if random.random() < 0.2: # 20% chance of timestamp drift beyond tolerance (100ms)
            conf_ts = timestamp_exec + timedelta(milliseconds=random.choice([-1, 1]) * random.randint(101, 500))

        pnl_impact = round(exec_price * exec_qty * random.uniform(0.0005, 0.001), 2)
        commission = round(random.uniform(0.5, 5.0), 2)
        if random.random() < 0.2: # 20% chance of PnL mismatch
            pnl_impact += random.choice([-1, 1]) * round(random.uniform(10.0, 50.0), 2)


        trades.append({
            "trade_id": trade_id,
            "ticker": ticker,
            "execution": {
                "quantity": exec_qty,
                "price": exec_price,
                "timestamp": timestamp_exec.isoformat(timespec='milliseconds') + 'Z'
            },
            "confirmation": {
                "quantity": conf_qty,
                "price": conf_price,
                "timestamp": conf_ts.isoformat(timespec='milliseconds') + 'Z'
            },
            "pnl_snapshot": {
                "pnl_impact": pnl_impact,
                "commission": commission
            }
        })
    return trades

def send_trade_data(producer, trades_data):
    # This function is now commented out in __main__ and primarily for reference
    for trade in trades_data:
        trade_id = trade["trade_id"]
        print(f"Sending data for Trade ID: {trade_id}")

        exec_msg = {
            "trade_id": trade_id,
            "ticker": trade["ticker"],
            "quantity": trade["execution"]["quantity"],
            "price": trade["execution"]["price"],
            "timestamp": trade["execution"]["timestamp"]
        }
        producer.send(EXECUTION_TOPIC, exec_msg)
        print(f"  Sent execution to {EXECUTION_TOPIC}: {exec_msg['trade_id']}")

        time.sleep(random.uniform(0.05, 0.2)) # Simulate slight delay
        conf_msg = {
            "trade_id": trade_id,
            "ticker": trade["ticker"],
            "quantity": trade["confirmation"]["quantity"],
            "price": trade["confirmation"]["price"],
            "timestamp": trade["confirmation"]["timestamp"]
        }
        producer.send(CONFIRMATION_TOPIC, conf_msg)
        print(f"  Sent confirmation to {CONFIRMATION_TOPIC}: {conf_msg['trade_id']}")

        time.sleep(random.uniform(0.5, 1.5)) # Simulate longer delay for PnL
        pnl_msg = {
            "trade_id": trade_id,
            "pnl_impact": trade["pnl_snapshot"]["pnl_impact"],
            "commission": trade["pnl_snapshot"]["commission"]
        }
        producer.send(PNL_TOPIC, pnl_msg)
        print(f"  Sent PnL snapshot to {PNL_TOPIC}: {pnl_msg['trade_id']}")

        time.sleep(1)

    producer.flush()
    print("Finished sending all simulated trade data.")

def send_csv_data(producer, file_path, topic):
    try:
        df = pd.read_csv(file_path)
        print(f"Sending data from {file_path} to topic {topic}...")
        for index, row in df.iterrows():
            message = row.to_dict()
            producer.send(topic, message)
            # Add print statement to confirm data is being sent for each row
            print(f"    Sent row {index+1} for trade_id {row.get('trade_id', 'N/A')} to {topic}")
            time.sleep(0.01) # Small delay to avoid overwhelming Kafka
        producer.flush()
        print(f"Finished sending data from {file_path} to topic {topic}.")
    except FileNotFoundError:
        print(f"Error: CSV file not found at {file_path}")
    except Exception as e:
        print(f"An error occurred while sending CSV data: {e}")


if __name__ == "__main__":
    producer = create_kafka_producer_with_retries()

    # Option 1: Generate dynamic trade data with mismatches (COMMENTED OUT)
    # print("\n--- Generating and sending dynamic trade data ---")
    # simulated_trades = generate_trade_data(num_trades=30)
    # send_trade_data(producer, simulated_trades)

    # Option 2: Send data from static CSV files (UNCOMMENTED)
    print("\n--- Sending static CSV data (executions) ---")
    send_csv_data(producer, 'data/executions.csv', EXECUTION_TOPIC)
    print("\n--- Sending static CSV data (broker_confirmations) ---")
    send_csv_data(producer, 'data/broker_confirmations.csv', CONFIRMATION_TOPIC)
    print("\n--- Sending static CSV data (pnl_snapshot) ---")
    send_csv_data(producer, 'data/pnl_snapshot.csv', PNL_TOPIC)

    producer.close()
