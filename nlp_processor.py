"""
NLP Sentiment Processor (FinBERT — free, local, no API key)
=============================================================
Reads unscored messages from MongoDB, cleans message text, classifies
sentiment locally using FinBERT (ProsusAI/finbert), writes results back
to MongoDB, and updates per-symbol aggregate sentiment scores.
"""

import re
import html

from db import get_messages, update_sentiment, finviz_collection, get_db

# ── Config ────────────────────────────────────────────────────────────────────

MODEL_NAME = "ProsusAI/finbert"
BATCH_SIZE = 32


# ── Text cleaning ─────────────────────────────────────────────────────────────

def clean_text(text: str) -> str:
    text = re.sub(r"http\S+|www\.\S+", "", text)
    text = re.sub(r"\$[A-Z]{1,5}", "", text)
    text = re.sub(r"@\w+", "", text)
    text = html.unescape(text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9\s\.\,\!\?\'\-]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.lower()


# ── FinBERT local classifier ──────────────────────────────────────────────────

def load_finbert():
    from transformers import AutoTokenizer, AutoModelForSequenceClassification
    print(f"  Loading {MODEL_NAME} (downloads on first run, ~440MB)...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME)
    model.eval()
    print("  Model loaded.")
    return tokenizer, model


def classify_batch(texts: list, tokenizer, model) -> list:
    import torch

    inputs = tokenizer(texts, padding=True, truncation=True, max_length=128, return_tensors="pt")
    with torch.no_grad():
        outputs = model(**inputs)
        probs = torch.nn.functional.softmax(outputs.logits, dim=-1)

    id2label = model.config.id2label
    label_map = {"positive": "bullish", "negative": "bearish", "neutral": "neutral"}

    results = []
    for row in probs:
        row = row.tolist()
        best_idx = max(range(len(row)), key=lambda i: row[i])
        raw_label = id2label[best_idx].lower()
        label = label_map.get(raw_label, "neutral")
        score = row[best_idx]

        sorted_probs = sorted(row, reverse=True)
        if len(sorted_probs) > 1 and (sorted_probs[0] - sorted_probs[1]) < 0.10 and label != "neutral":
            label = "mixed"

        results.append({"label": label, "score": round(score, 4)})

    return results


# ── Per-stock sentiment score ─────────────────────────────────────────────────

def compute_sentiment_scores(scored_messages: list) -> list:
    by_symbol = {}
    for row in scored_messages:
        sym = row.get("symbol", "")
        if not sym:
            continue
        by_symbol.setdefault(sym, []).append(row)

    output = []
    for sym, rows in by_symbol.items():
        signed_total = 0.0
        counts = {"bullish": 0, "bearish": 0, "neutral": 0, "mixed": 0}

        for row in rows:
            label = row.get("sentiment", "neutral")
            score = float(row.get("sentiment_score", 0.0) or 0.0)
            counts[label] = counts.get(label, 0) + 1

            if label == "bullish":
                signed_total += score
            elif label == "bearish":
                signed_total -= score

        total = len(rows)
        avg_signed = signed_total / total if total else 0.0
        avg_signed = max(-1.0, min(1.0, avg_signed))

        output.append({
            "symbol":          sym,
            "agg_sentiment":   round(avg_signed, 4),
            "bullish_count":   counts["bullish"],
            "bearish_count":   counts["bearish"],
            "neutral_count":   counts["neutral"],
            "mixed_count":     counts["mixed"],
            "total_messages":  total,
        })

    return sorted(output, key=lambda r: r["agg_sentiment"], reverse=True)


def save_sentiment_scores(scores: list):
    coll = get_db()["sentiment_scores"]
    for row in scores:
        coll.update_one(
            {"symbol": row["symbol"]},
            {"$set": row},
            upsert=True
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*55}")
    print("NLP Sentiment Processor (FinBERT, local, free)")
    print(f"{'='*55}")

    unscored = get_messages(unscored_only=True)
    print(f"\n  Loaded {len(unscored)} unscored messages from MongoDB")

    if not unscored:
        print("  Nothing new to classify.")
        return

    print("  Cleaning message text...")
    cleaned = []
    for row in unscored:
        cleaned.append({
            "_id":          row["_id"],
            "symbol":       row.get("symbol", ""),
            "clean_text":   clean_text(row.get("message", "")),
        })

    to_classify = [c for c in cleaned if len(c["clean_text"]) > 5]
    skipped = len(cleaned) - len(to_classify)
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
                    results[c["_id"]] = r
                print(f"  Batch {batch_num}/{len(chunks)} - {len(chunk)} messages... done")
            except Exception as e:
                print(f"  Batch {batch_num}/{len(chunks)} - error: {e}")

    # Write sentiment back to each message in MongoDB
    print("\n  Writing sentiment results back to MongoDB...")
    for c in cleaned:
        result = results.get(c["_id"], {"label": "neutral", "score": 0.0})
        update_sentiment(c["_id"], result["label"], result["score"])

    label_counts = {}
    for c in cleaned:
        result = results.get(c["_id"], {"label": "neutral", "score": 0.0})
        label_counts[result["label"]] = label_counts.get(result["label"], 0) + 1
    print(f"\n  Sentiment breakdown (this run):")
    for label, count in sorted(label_counts.items()):
        print(f"    {label:10s}: {count}")

    # Recompute aggregate scores using ALL scored messages (not just this run)
    print("\n  Computing per-stock sentiment scores...")
    all_scored = get_messages(scored_only=True)
    sentiment_rows = compute_sentiment_scores(all_scored)
    for row in sentiment_rows:
        print(f"    {row['symbol']:6s} score={row['agg_sentiment']:+.3f}  "
              f"(bull={row['bullish_count']} bear={row['bearish_count']} "
              f"neu={row['neutral_count']} mix={row['mixed_count']})")

    save_sentiment_scores(sentiment_rows)
    print("\nNLP processing complete!")


if __name__ == "__main__":
    main()
