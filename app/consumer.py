import threading
import json
from kafka import KafkaConsumer
from .reconcile import ReconciliationEngine

class TradeDataConsumer(threading.Thread):
    def __init__(self, topic: str, bootstrap_servers: str, group_id: str, reconcile_engine: ReconciliationEngine):
        super().__init__()
        self.topic = topic
        self.bootstrap_servers = bootstrap_servers
        self.group_id = group_id
        self.reconcile_engine = reconcile_engine
        self.running = True
        print(f"Initializing consumer for topic: {self.topic}, group_id: {self.group_id}")

    def run(self):
        try:
            consumer = KafkaConsumer(
                self.topic,
                bootstrap_servers=self.bootstrap_servers,
                group_id=self.group_id,
                value_deserializer=lambda m: json.loads(m.decode('utf-8')),
                auto_offset_reset='earliest',
                enable_auto_commit=True,
            )
            print(f"Consumer for topic '{self.topic}' started...")
            for message in consumer:
                if not self.running:
                    break
                print(f"Received message from {message.topic} (offset {message.offset}): {message.value}")
                self.reconcile_engine.process_message(message.topic, message.value)
        except Exception as e:
            print(f"Error in consumer for topic {self.topic}: {e}")
        finally:
            if 'consumer' in locals():
                consumer.close()
                print(f"Consumer for topic '{self.topic}' closed.")

    def stop(self):
        self.running = False
