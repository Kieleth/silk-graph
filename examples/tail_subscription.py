"""Cursor-based tail subscription: producer + consumer with persistent cursor.

Shows the Kafka/etcd-style pull pattern:
  - Producer writes entries to the store.
  - Consumer subscribes from a persisted cursor, gets the delta since last run.
  - On crash/restart, consumer resumes from the saved cursor — no missed events.

Run:
    python examples/tail_subscription.py
"""

import json
import os
import threading
import time

from silk import GraphStore


CURSOR_FILE = "/tmp/silk-tail-cursor.json"


ONTOLOGY = json.dumps({
    "node_types": {
        "adoption": {"properties": {"pet_name": {"value_type": "string"}}},
    },
    "edge_types": {},
})


def load_cursor() -> list[str]:
    """Load the persisted cursor. Empty cursor = replay from beginning."""
    if not os.path.exists(CURSOR_FILE):
        return []
    with open(CURSOR_FILE) as f:
        return json.load(f)


def save_cursor(cursor: list[str]) -> None:
    """Persist the cursor so we can resume on restart."""
    with open(CURSOR_FILE, "w") as f:
        json.dump(cursor, f)


def consumer(store, stop_flag):
    """Tail the oplog from the persisted cursor. Print each adoption."""
    cursor = load_cursor()
    if cursor:
        print(f"[consumer] resuming from cursor: {cursor[0][:12]}...")
    else:
        print("[consumer] starting fresh (empty cursor)")

    sub = store.subscribe_from(cursor)

    while not stop_flag.is_set():
        entries = sub.next_batch(timeout_ms=500, max_count=10)
        for e in entries:
            if e["op"] == "add_node" and e.get("node_type") == "adoption":
                print(f"[consumer] adoption: {e['node_id']}")

        # Persist cursor after each batch so we can resume on crash
        save_cursor(sub.current_cursor())

    sub.close()
    print(f"[consumer] final cursor saved: {sub.current_cursor()[0][:12]}...")


def producer(store, stop_flag):
    """Write a few adoptions, then stop."""
    names = ["Max", "Luna", "Charlie", "Bella", "Milo"]
    for i, name in enumerate(names):
        if stop_flag.is_set():
            break
        store.add_node(
            f"adoption-{i}-{int(time.time())}",
            "adoption",
            name,
            {"pet_name": name},
        )
        print(f"[producer] committed adoption #{i}: {name}")
        time.sleep(0.3)


def main():
    store = GraphStore("shelter", ONTOLOGY)
    stop = threading.Event()

    consumer_t = threading.Thread(target=consumer, args=(store, stop))
    producer_t = threading.Thread(target=producer, args=(store, stop))

    consumer_t.start()
    time.sleep(0.1)  # let consumer enter its wait loop
    producer_t.start()

    producer_t.join()
    time.sleep(1.0)  # let consumer drain final entries
    stop.set()
    consumer_t.join(timeout=2)

    print()
    print(f"To replay, delete {CURSOR_FILE} and run again.")
    print(f"To resume, run again with {CURSOR_FILE} intact (will see no new adoptions).")


if __name__ == "__main__":
    main()
