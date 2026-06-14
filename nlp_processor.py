"""
NLP Sentiment Processor
========================
Reads stocktwits.csv, cleans message text, then sends ALL messages
in a single batched Claude API call to classify sentiment.

Outputs a new "NLP Sentiment" sheet in stocktwits_data.xlsx.

Each message gets:
  - nlp_label  : bullish | bearish | neutral | mixed
  - nlp_score  : float 0.0–1.0 (confidence/strength)
  - clean_text : cleaned version of the original message
"""

import csv
import json
import re
import os
import anthropic
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

DATA_CSV   = Path("data/stocktwits.csv")
EXCEL_FILE = Path("data/stocktwits_data.xlsx")
MODEL      = "claude-haiku-4-5"   # fast + cheap; ideal for bulk classification
MAX_TOKENS = 8000
BATCH_SIZE = 500                   # max messages per API call (context window safety)

NLP_FIELDS = ["timestamp", "symbol", "clean_text", "original_sentiment", "nlp_label", "nlp_score"]

# ── Text cleaning ─────────────────────────────────────────────────────────────

def clean_text(text: str) -> str:
    """
    Clean a raw StockTwits message:
    - Remove URLs
    - Remove ticker cashtags (e.g. $NVDA)
    - Remove emojis and non-ASCII symbols
    - Remove excess whitespace
    - Lowercase
    """
    # Remove URLs
    text = re.sub(r"http\S+|www\.\S+", "", text)
    # Remove cashtags
    text = re.sub(r"\$[A-Z]{1,5}", "", text)
    # Remove mentions
    text = re.sub(r"@\w+", "", text)
    # Remove emojis and non-ASCII
    text = text.encode("ascii", "ignore").decode("ascii")
    # Remove special characters except basic punctuation
    text = re.sub(r"[^a-zA-Z0-9\s\.\,\!\?\'\-]", "", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text.lower()


# ── Batch sentiment classification ───────────────────────────────────────────

def classify_batch(messages: list[dict], client: anthropic.Anthropic) -> dict:
    """
    Send a batch of messages to Claude in one API call.
    Each message dict has: {id, symbol, text}
    Returns a dict mapping id -> {label, score}
    """
    prompt = f"""You are a financial sentiment classifier for stock market social media posts.

Classify the sentiment of each message below. For each one, return:
- id: the exact id provided
- label: one of "bullish", "bearish", "neutral", or "mixed"
- score: a float from 0.0 to 1.0 representing confidence/strength of sentiment
  (1.0 = very strong clear sentiment, 0.5 = moderate, 0.1 = very weak/ambiguous)

Rules:
- "bullish" = positive outlook, expecting price to rise
- "bearish" = negative outlook, expecting price to fall
- "mixed" = contains both bullish and bearish signals
- "neutral" = no clear directional sentiment (news, questions, general commentary)
- Base your score on how strongly and clearly the sentiment is expressed
- Ignore messages that are too short or meaningless — classify as "neutral" with score 0.1

Messages to classify:
{json.dumps(messages, indent=2)}

Return ONLY a valid JSON array. No explanation, no markdown, no extra text.
Format: [{{"id": 1, "label": "bullish", "score": 0.85}}, ...]"""

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()

    # Strip markdown fences if present
    raw = re.sub(r"^```json\s*|^```\s*|```$", "", raw, flags=re.MULTILINE).strip()

    results = json.loads(raw)
    return {r["id"]: {"label": r["label"], "score": r["score"]} for r in results}


# ── Excel export ──────────────────────────────────────────────────────────────

def write_nlp_sheet(nlp_rows: list[dict]):
    """Add or replace the 'NLP Sentiment' sheet in the existing Excel workbook."""
    if not EXCEL_FILE.exists():
        print(f"  ✗ {EXCEL_FILE} not found — run the collector first.")
        return

    wb = openpyxl.load_workbook(EXCEL_FILE)

    # Remove existing NLP sheet if present
    if "NLP Sentiment" in wb.sheetnames:
        del wb["NLP Sentiment"]

    ws = wb.create_sheet("NLP Sentiment")

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F4E79")

    label_colors = {
        "bullish": "C6EFCE",   # green
        "bearish": "FFC7CE",   # red
        "mixed":   "FFEB9C",   # yellow
        "neutral": "FFFFFF",   # white
    }

    # Header row
    headers = ["TIMESTAMP", "SYMBOL", "CLEAN TEXT", "ORIGINAL SENTIMENT", "NLP LABEL", "NLP SCORE"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font      = header_font
        cell.fill      = header_fill
        cell.alignment = Alignment(horizontal="center")

    # Data rows
    for r, row in enumerate(nlp_rows, 2):
        label = row.get("nlp_label", "neutral")
        fill  = PatternFill("solid", fgColor=label_colors.get(label, "FFFFFF"))
        for col, key in enumerate(NLP_FIELDS, 1):
            cell       = ws.cell(row=r, column=col, value=row.get(key, ""))
            cell.fill  = fill

    # Column widths
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 10
    ws.column_dimensions["C"].width = 70
    ws.column_dimensions["D"].width = 20
    ws.column_dimensions["E"].width = 14
    ws.column_dimensions["F"].width = 12

    wb.save(EXCEL_FILE)
    print(f"  Saved 'NLP Sentiment' sheet → {EXCEL_FILE}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*55}")
    print("NLP Sentiment Processor")
    print(f"{'='*55}")

    # Load API key from environment
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("✗ ANTHROPIC_API_KEY environment variable not set.")
        return

    client = anthropic.Anthropic(api_key=api_key)

    # Load messages from CSV
    if not DATA_CSV.exists():
        print(f"✗ {DATA_CSV} not found — run the collector first.")
        return

    with open(DATA_CSV, "r", encoding="utf-8") as f:
        raw_rows = list(csv.DictReader(f))

    print(f"\n  Loaded {len(raw_rows)} messages from {DATA_CSV}")

    # Clean text
    print("  Cleaning message text...")
    cleaned = []
    for i, row in enumerate(raw_rows):
        cleaned.append({
            "id":                i,
            "timestamp":         row.get("timestamp", ""),
            "symbol":            row.get("symbol", ""),
            "original_sentiment": row.get("sentiment", "None"),
            "clean_text":        clean_text(row.get("message", "")),
        })

    # Filter out empty messages after cleaning
    to_classify = [c for c in cleaned if len(c["clean_text"]) > 5]
    skipped     = len(cleaned) - len(to_classify)
    print(f"  {len(to_classify)} messages to classify, {skipped} skipped (too short after cleaning).")

    # Batch into chunks of BATCH_SIZE
    results = {}
    chunks  = [to_classify[i:i+BATCH_SIZE] for i in range(0, len(to_classify), BATCH_SIZE)]
    print(f"\n  Sending {len(chunks)} batch(es) to Claude ({MODEL})...")

    for batch_num, chunk in enumerate(chunks, 1):
        print(f"  Batch {batch_num}/{len(chunks)} — {len(chunk)} messages...", end=" ")
        batch_input = [{"id": c["id"], "symbol": c["symbol"], "text": c["clean_text"]} for c in chunk]
        try:
            batch_results = classify_batch(batch_input, client)
            results.update(batch_results)
            print("✓")
        except Exception as e:
            print(f"✗ Error: {e}")

    # Assemble final rows
    nlp_rows = []
    for c in cleaned:
        result = results.get(c["id"], {"label": "neutral", "score": 0.0})
        nlp_rows.append({
            "timestamp":          c["timestamp"],
            "symbol":             c["symbol"],
            "clean_text":         c["clean_text"],
            "original_sentiment": c["original_sentiment"],
            "nlp_label":          result["label"],
            "nlp_score":          result["score"],
        })

    # Summary stats
    label_counts = {}
    for row in nlp_rows:
        label_counts[row["nlp_label"]] = label_counts.get(row["nlp_label"], 0) + 1
    print(f"\n  Sentiment breakdown:")
    for label, count in sorted(label_counts.items()):
        print(f"    {label:10s}: {count}")

    # Write to Excel
    print("\n  Writing NLP Sentiment sheet to Excel...")
    write_nlp_sheet(nlp_rows)

    print("\n✓ NLP processing complete!")


if __name__ == "__main__":
    main()
