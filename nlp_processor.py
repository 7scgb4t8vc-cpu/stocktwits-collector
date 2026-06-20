"""
NLP Sentiment Processor (FinBERT — free, local, no API key)
=============================================================
Reads stocktwits.csv, cleans message text, then classifies sentiment
locally using FinBERT (ProsusAI/finbert), a free finance-tuned model
that runs on CPU via Hugging Face transformers. No API key, no cost.

Outputs:
  - data/nlp_output.csv       : per-message label, score, clean text (read by the Flask dashboard)
  - data/sentiment_scores.csv : per-stock aggregate sentiment score (signed avg of nlp_score)
  - "NLP Sentiment" sheet in stocktwits_data.xlsx

Each message gets:
  - nlp_label  : bullish | bearish | neutral   (FinBERT has no "mixed" class, so we treat
                 strongly mixed/uncertain probabilities as neutral)
  - nlp_score  : float 0.0–1.0 (model confidence in the predicted label)
  - clean_text : cleaned version of the original message
"""

import csv
import re
import os
import html
from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

# ── Config ────────────────────────────────────────────────────────────────────

DATA_CSV      = Path("data/stocktwits.csv")
NLP_CSV       = Path("data/nlp_output.csv")
SENTIMENT_CSV = Path("data/sentiment_scores.csv")
EXCEL_FILE    = Path("data/stocktwits_data.xlsx")

MODEL_NAME = "ProsusAI/finbert"   # free, finance-tuned, runs locally — no API key needed
BATCH_SIZE = 32                    # messages per inference batch (CPU-friendly)

NLP_FIELDS = ["timestamp", "symbol", "clean_text", "original_sentiment", "nlp_label", "nlp_score"]
SENTIMENT_FIELDS = ["symbol", "sentiment_score", "bullish_count", "bearish_count", "neutral_count", "mixed_count", "total_messages"]

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
    text = re.sub(r"http\S+|www\.\S+", "", text)
    text = re.sub(r"\$[A-Z]{1,5}", "", text)
    text = re.sub(r"@\w+", "", text)
    text = html.unescape(text)  # decode &#39; &quot; &amp; etc. before stripping non-ASCII
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9\s\.\,\!\?\'\-]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.lower()


# ── FinBERT local classifier ──────────────────────────────────────────────────

def load_finbert():
    """Load the FinBERT tokenizer + model once (downloads on first run, then cached)."""
    from transformers import AutoTokenizer, AutoModelForSequenceClassification
    print(f"  Loading {MODEL_NAME} (downloads on first run, ~440MB)...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME)
    model.eval()
    print("  Model loaded.")
    return tokenizer, model


def classify_batch(texts: list[str], tokenizer, model) -> list[dict]:
    """
    Classify a batch of texts with FinBERT.
    FinBERT label order (id2label): 0=positive, 1=negative, 2=neutral
    Returns a list of {label, score} dicts, one per input text, in order.
    Maps FinBERT's positive/negative/neutral -> bullish/bearish/neutral
    to match the rest of the dashboard's vocabulary.
    """
    import torch

    inputs = tokenizer(texts, padding=True, truncation=True, max_length=128, return_tensors="pt")
    with torch.no_grad():
        outputs = model(**inputs)
        probs = torch.nn.functional.softmax(outputs.logits, dim=-1)

    id2label = model.config.id2label  # e.g. {0: 'positive', 1: 'negative', 2: 'neutral'}
    label_map = {"positive": "bullish", "negative": "bearish", "neutral": "neutral"}

    results = []
    for row in probs:
        row = row.tolist()
        best_idx = max(range(len(row)), key=lambda i: row[i])
        raw_label = id2label[best_idx].lower()
        label = label_map.get(raw_label, "neutral")
        score = row[best_idx]  # model's confidence in the chosen label

        # If the model is barely more confident in its top class than the
        # runner-up, treat it as a "mixed" signal rather than a confident call.
        sorted_probs = sorted(row, reverse=True)
        if len(sorted_probs) > 1 and (sorted_probs[0] - sorted_probs[1]) < 0.10 and label != "neutral":
            label = "mixed"

        results.append({"label": label, "score": round(score, 4)})

    return results


# ── Per-stock sentiment score ─────────────────────────────────────────────────

def compute_sentiment_scores(nlp_rows: list[dict]) -> list[dict]:
    """
    Aggregate per-message nlp_label + nlp_score into a single signed
    sentiment score per symbol, in the range -1.0 (very bearish) to
    +1.0 (very bullish).

    Signing rule per message:
      bullish ->  +score
      bearish ->  -score
      mixed   ->   0   (net neutral contribution; strength still counted via 'mixed_count')
      neutral ->   0
    Per-stock score = average of signed values across all its messages.
    """
    by_symbol = {}
    for row in nlp_rows:
        sym = row.get("symbol", "")
        if not sym:
            continue
        by_symbol.setdefault(sym, []).append(row)

    output = []
    for sym, rows in by_symbol.items():
        signed_total = 0.0
        counts = {"bullish": 0, "bearish": 0, "neutral": 0, "mixed": 0}

        for row in rows:
            label = row.get("nlp_label", "neutral")
            score = float(row.get("nlp_score", 0.0) or 0.0)
            counts[label] = counts.get(label, 0) + 1

            if label == "bullish":
                signed_total += score
            elif label == "bearish":
                signed_total -= score
            # mixed and neutral contribute 0

        total = len(rows)
        avg_signed = signed_total / total if total else 0.0
        avg_signed = max(-1.0, min(1.0, avg_signed))

        output.append({
            "symbol":          sym,
            "sentiment_score": round(avg_signed, 4),
            "bullish_count":   counts["bullish"],
            "bearish_count":   counts["bearish"],
            "neutral_count":   counts["neutral"],
            "mixed_count":     counts["mixed"],
            "total_messages":  total,
        })

    return sorted(output, key=lambda r: r["sentiment_score"], reverse=True)


# ── CSV export ─────────────────────────────────────────────────────────────────

def write_nlp_csv(nlp_rows: list[dict]):
    NLP_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(NLP_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=NLP_FIELDS)
        writer.writeheader()
        writer.writerows(nlp_rows)
    print(f"  Saved {NLP_CSV} — {len(nlp_rows)} rows.")


def write_sentiment_csv(sentiment_rows: list[dict]):
    SENTIMENT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(SENTIMENT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SENTIMENT_FIELDS)
        writer.writeheader()
        writer.writerows(sentiment_rows)
    print(f"  Saved {SENTIMENT_CSV} — {len(sentiment_rows)} symbols.")


# ── Excel export ──────────────────────────────────────────────────────────────

def write_nlp_sheet(nlp_rows: list[dict]):
    if not EXCEL_FILE.exists():
        print(f"  - {EXCEL_FILE} not found - run the collector first.")
        return

    wb = openpyxl.load_workbook(EXCEL_FILE)
    if "NLP Sentiment" in wb.sheetnames:
        del wb["NLP Sentiment"]
    ws = wb.create_sheet("NLP Sentiment")

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F4E79")
    label_colors = {"bullish": "C6EFCE", "bearish": "FFC7CE", "mixed": "FFEB9C", "neutral": "FFFFFF"}

    headers = ["TIMESTAMP", "SYMBOL", "CLEAN TEXT", "ORIGINAL SENTIMENT", "NLP LABEL", "NLP SCORE"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    for r, row in enumerate(nlp_rows, 2):
        label = row.get("nlp_label", "neutral")
        fill = PatternFill("solid", fgColor=label_colors.get(label, "FFFFFF"))
        for col, key in enumerate(NLP_FIELDS, 1):
            cell = ws.cell(row=r, column=col, value=row.get(key, ""))
            cell.fill = fill

    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 10
    ws.column_dimensions["C"].width = 70
    ws.column_dimensions["D"].width = 20
    ws.column_dimensions["E"].width = 14
    ws.column_dimensions["F"].width = 12

    wb.save(EXCEL_FILE)
    print(f"  Saved 'NLP Sentiment' sheet -> {EXCEL_FILE}")


def write_sentiment_sheet(sentiment_rows: list[dict]):
    if not EXCEL_FILE.exists():
        return

    wb = openpyxl.load_workbook(EXCEL_FILE)
    if "Sentiment Scores" in wb.sheetnames:
        del wb["Sentiment Scores"]
    ws = wb.create_sheet("Sentiment Scores")

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F4E79")

    headers = ["SYMBOL", "SENTIMENT SCORE", "BULLISH", "BEARISH", "NEUTRAL", "MIXED", "TOTAL MESSAGES"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    for r, row in enumerate(sentiment_rows, 2):
        score = row["sentiment_score"]
        if score > 0.15:
            fill_color = "C6EFCE"
        elif score < -0.15:
            fill_color = "FFC7CE"
        else:
            fill_color = "FFFFFF"
        fill = PatternFill("solid", fgColor=fill_color)
        for col, key in enumerate(SENTIMENT_FIELDS, 1):
            cell = ws.cell(row=r, column=col, value=row.get(key, ""))
            cell.fill = fill

    ws.column_dimensions["A"].width = 12
    ws.column_dimensions["B"].width = 16
    ws.column_dimensions["C"].width = 10
    ws.column_dimensions["D"].width = 10
    ws.column_dimensions["E"].width = 10
    ws.column_dimensions["F"].width = 10
    ws.column_dimensions["G"].width = 16

    wb.save(EXCEL_FILE)
    print(f"  Saved 'Sentiment Scores' sheet -> {EXCEL_FILE}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*55}")
    print("NLP Sentiment Processor (FinBERT, local, free)")
    print(f"{'='*55}")

    if not DATA_CSV.exists():
        print(f"- {DATA_CSV} not found - run the collector first.")
        return

    with open(DATA_CSV, "r", encoding="utf-8") as f:
        raw_rows = list(csv.DictReader(f))

    print(f"\n  Loaded {len(raw_rows)} messages from {DATA_CSV}")

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

    print("  Removing duplicate posts...")
    seen_texts = set()
    deduped = []
    for c in cleaned:
        if c["clean_text"] not in seen_texts:
            seen_texts.add(c["clean_text"])
            deduped.append(c)
    duplicates_removed = len(cleaned) - len(deduped)
    print(f"  {duplicates_removed} duplicate(s) removed, {len(deduped)} unique messages remaining.")

    to_classify = [c for c in deduped if len(c["clean_text"]) > 5]
    skipped = len(deduped) - len(to_classify)
    print(f"  {len(to_classify)} messages to classify, {skipped} skipped (too short after cleaning).")

    results = {}
    if to_classify:
        tokenizer, model = load_finbert()

        chunks = [to_classify[i:i+BATCH_SIZE] for i in range(0, len(to_classify), BATCH_SIZE)]
        print(f"\n  Classifying {len(to_classify)} messages in {len(chunks)} batch(es) of up to {BATCH_SIZE}...")

        for batch_num, chunk in enumerate(chunks, 1):
            texts = [c["clean_text"] for c in chunk]
            try:
                batch_results = classify_batch(texts, tokenizer, model)
                for c, r in zip(chunk, batch_results):
                    results[c["id"]] = r
                print(f"  Batch {batch_num}/{len(chunks)} - {len(chunk)} messages... done")
            except Exception as e:
                print(f"  Batch {batch_num}/{len(chunks)} - error: {e}")

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

    label_counts = {}
    for row in nlp_rows:
        label_counts[row["nlp_label"]] = label_counts.get(row["nlp_label"], 0) + 1
    print(f"\n  Sentiment breakdown:")
    for label, count in sorted(label_counts.items()):
        print(f"    {label:10s}: {count}")

    print("\n  Computing per-stock sentiment scores...")
    sentiment_rows = compute_sentiment_scores(nlp_rows)
    for row in sentiment_rows:
        print(f"    {row['symbol']:6s} score={row['sentiment_score']:+.3f}  "
              f"(bull={row['bullish_count']} bear={row['bearish_count']} "
              f"neu={row['neutral_count']} mix={row['mixed_count']})")

    print("\n  Writing CSV outputs...")
    write_nlp_csv(nlp_rows)
    write_sentiment_csv(sentiment_rows)

    print("\n  Writing Excel sheets...")
    write_nlp_sheet(nlp_rows)
    write_sentiment_sheet(sentiment_rows)

    print("\nNLP processing complete!")


if __name__ == "__main__":
    main()
