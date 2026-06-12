"""
es_setup.py — Run this once to prepare Elasticsearch for Grafana.

What it does:
  1. Creates an index template so log-anomalies-* indices have correct mappings
  2. Verifies the index exists and has documents
  3. Prints a sample document so you can confirm the structure

Run from your host machine (not inside Docker):
  python es_setup.py
"""

import json
import sys
import time
from elasticsearch import Elasticsearch

ES_HOST  = "http://localhost:9200"
ES_INDEX = "log-anomalies"


def wait_for_es(es: Elasticsearch, retries=10):
    for i in range(retries):
        try:
            if es.ping():
                print("✅ Connected to Elasticsearch")
                return True
        except Exception:
            pass
        print(f"   Waiting for ES... ({i+1}/{retries})")
        time.sleep(3)
    print("❌ Could not connect to Elasticsearch")
    sys.exit(1)


def create_index_template(es: Elasticsearch):
    """
    Index templates apply mappings automatically to any new index
    matching the pattern. This means even if the index is re-created,
    field types stay correct.
    """
    template = {
        "index_patterns": ["log-anomalies*"],
        "settings": {
            "number_of_shards":   1,
            "number_of_replicas": 0,           # 0 replicas = fine for single-node local
            "refresh_interval":   "5s",        # how often ES makes new docs searchable
        },
        "mappings": {
            "properties": {
                # Time fields
                "timestamp":      {"type": "date"},
                "processed_at":   {"type": "date"},

                # String fields stored as-is for filtering/grouping
                "service":        {"type": "keyword"},
                "level":          {"type": "keyword"},
                "method":         {"type": "keyword"},
                "endpoint":       {"type": "keyword"},

                # Numeric fields
                "status_code":    {"type": "integer"},
                "latency_ms":     {"type": "integer"},

                # Full-text (searchable log message)
                "message":        {"type": "text", "fields": {
                    "keyword": {"type": "keyword", "ignore_above": 256}
                }},

                # ML output fields
                "is_anomaly":     {"type": "boolean"},
                "anomaly_score":  {"type": "float"},
                "raw_if_score":   {"type": "float"},
            }
        }
    }

    es.indices.put_template(name="log-anomalies-template", body=template)
    print("✅ Index template created: log-anomalies-template")


def verify_index(es: Elasticsearch):
    """Check the index exists and count documents."""
    if not es.indices.exists(index=ES_INDEX):
        print(f"⚠️  Index '{ES_INDEX}' does not exist yet.")
        print("   Make sure ml-service is running and has processed some logs.")
        print("   Wait ~30 seconds and run this script again.")
        return False

    count = es.count(index=ES_INDEX)["count"]
    print(f"✅ Index '{ES_INDEX}' exists with {count} documents")

    if count == 0:
        print("⚠️  No documents yet. Wait for ml-service to process some logs.")
        return False

    return True


def print_sample(es: Elasticsearch):
    """Print the most recent document to verify the structure."""
    result = es.search(
        index=ES_INDEX,
        body={
            "size": 1,
            "sort": [{"processed_at": {"order": "desc"}}],
        }
    )

    hits = result["hits"]["hits"]
    if hits:
        print("\n📄 Most recent document:")
        print(json.dumps(hits[0]["_source"], indent=2))


def print_anomaly_summary(es: Elasticsearch):
    """Quick stats on anomaly distribution."""
    result = es.search(
        index=ES_INDEX,
        body={
            "size": 0,
            "aggs": {
                "by_service": {
                    "terms": {"field": "service", "size": 10},
                    "aggs": {
                        "anomaly_count": {
                            "filter": {"term": {"is_anomaly": True}}
                        },
                        "avg_latency": {
                            "avg": {"field": "latency_ms"}
                        }
                    }
                },
                "anomaly_total": {
                    "filter": {"term": {"is_anomaly": True}}
                }
            }
        }
    )

    aggs = result["aggregations"]
    total_count    = result["hits"]["total"]["value"]
    anomaly_count  = aggs["anomaly_total"]["doc_count"]

    print(f"\n📊 Summary:")
    print(f"   Total logs indexed : {total_count}")
    print(f"   Anomalies detected : {anomaly_count}")
    print(f"   Anomaly rate       : {anomaly_count/total_count*100:.1f}%" if total_count else "   N/A")

    print("\n   By service:")
    for bucket in aggs["by_service"]["buckets"]:
        print(
            f"   {bucket['key']:<28}  "
            f"docs={bucket['doc_count']:<6}  "
            f"anomalies={bucket['anomaly_count']['doc_count']:<5}  "
            f"avg_latency={bucket['avg_latency']['value']:.0f}ms"
        )


if __name__ == "__main__":
    es = Elasticsearch(ES_HOST)
    wait_for_es(es)
    create_index_template(es)

    if verify_index(es):
        print_sample(es)
        print_anomaly_summary(es)

    print("\n✅ Elasticsearch is ready for Grafana.")
    print("   Next: import the Grafana dashboard JSON at http://localhost:3000")