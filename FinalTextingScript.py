from http.client import responses
import os
import sqlite3
import csv
import time
import uuid
import subprocess
import logging
from datetime import datetime, timedelta

# ---------------- CONFIG ----------------
DB_PATH = os.path.expanduser("~/Library/Messages/chat.db")
PHONE_NUMBER = "3322227612"   # <-- target phone number
OUTPUT_CSV = "qa_results_cleaned.csv"
LOG_FILE = "sms_processing.log"

WAIT_FIRST = 120   # seconds after first question
WAIT_OTHERS = 80  # seconds after other questions
WAIT_LATE = 180   # seconds after last question

QUESTIONS = [
    "What signs indicate my child might be dehydrated?",
    "How can I help my baby with colic?",
    "When should my toddler start potty training?",
    "What should I do if my child has a rash?",
    "Is it normal for my child to stutter sometimes?",
    "How do I know if my child is getting enough iron?",
]

# --------------- LOGGING ----------------
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
console.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logging.getLogger().addHandler(console)

# --------------- HELPERS ----------------
def send_sms(phone: str, text: str):
    osa_cmd = f'''
    tell application "Messages"
        set targetService to 1st service whose service type = SMS
        set targetBuddy to buddy "{phone}" of targetService
        send "{text}" to targetBuddy
    end tell
    '''
    subprocess.run(["osascript", "-e", osa_cmd])

def fetch_messages(since: datetime):
    # fetching all messages with the given date
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT
            message.date/1000000000 + strftime('%s','2001-01-01') as ts,
            message.is_from_me,
            message.text
        FROM message
        WHERE ts >= ?
        ORDER BY ts ASC
    """, (since.timestamp(),))
    rows = c.fetchall()
    conn.close()
    return [
        {"date": datetime.fromtimestamp(r[0]), "is_from_me": r[1], "text": r[2] or ""}
        for r in rows
    ]
def group_responses(messages, start_time, end_time, gap_seconds=30):
    """Group incoming messages between start_time and end_time into response blocks."""
    blocks = []
    current_block = []
    last_time = None

    for m in messages:
        if start_time <= m["date"] < end_time and not m["is_from_me"]:
            if last_time and (m["date"] - last_time).total_seconds() > gap_seconds:
                # start new block if gap too long
                if current_block:
                    blocks.append(" ".join(current_block))
                    current_block = []
            current_block.append(m["text"].strip())
            last_time = m["date"]

    if current_block:
        blocks.append(" ".join(current_block))
    return blocks

# --------------- MAIN ----------------
def main():
    logging.info("Starting SMS Question-Response Processor")
    results = []
    anchors = []

    start_time = datetime.now()

    # sending questions
    for i, q in enumerate(QUESTIONS, 1):
        logging.info(f"Sending question {i}/{len(QUESTIONS)}: {q[:60]}...")
        send_sms(PHONE_NUMBER, q)
        send_time = datetime.now()
        anchors.append((i, send_time, q))

        logging.info(f"Sent SMS: {q[:60]}...")
        if i == 1:
            logging.info(f"Waiting {WAIT_FIRST} seconds after sending question...")
            time.sleep(WAIT_FIRST)
        else:
            logging.info(f"Waiting {WAIT_OTHERS} seconds after sending question...")
            time.sleep(WAIT_OTHERS)

    # waiting for late responses and database to sync
    logging.info(f"Waiting {WAIT_LATE} seconds for late responses...")
    time.sleep(WAIT_LATE)

    # fetching all messages since before first question
    all_msgs = fetch_messages(start_time - timedelta(seconds=10))
    logging.debug(f"Fetched {len(all_msgs)} messages since {start_time.isoformat()}")

    # working backwards to anchor responses
    for idx in reversed(range(len(anchors))):
        qid = str(uuid.uuid4())
        i, q_time, q_text = anchors[idx]
        next_time = anchors[idx+1][1] if idx+1 < len(anchors) else datetime.now()

        # collecting responses between q_time and next_time
        responses = group_responses(all_msgs, q_time, next_time, gap_seconds=30)
        clean_resp = " || ".join(responses)
        logging.info(f"Q{i}: anchored at {q_time}, collected {len(responses)} responses")

        results.append({
            "question_id": qid,
            "question": q_text,
            "responses": clean_resp,
            "timestamp": q_time.isoformat()
        })

    # CSV in order of questions
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["question_id", "question", "responses", "timestamp"])
        writer.writeheader()
        for r in sorted(results, key=lambda x: x["timestamp"]):
            writer.writerow(r)

    logging.info(f"Saved results to {OUTPUT_CSV}")
    logging.info("\nSummary:\n- Questions sent: %d\n- Results saved to: %s\n- Log saved to: %s",
                 len(QUESTIONS), OUTPUT_CSV, LOG_FILE)

if __name__ == "__main__":
    main()
