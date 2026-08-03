from db import messages_collection

def main():
    coll = messages_collection()
    result = coll.update_many(
        {"nlp_label": "neutral", "nlp_score": 0.0},
        {"$unset": {"nlp_label": "", "nlp_score": ""}}
    )
    print(f"Reset {result.modified_count} stuck messages back to unscored.")

if __name__ == "__main__":
    main()
