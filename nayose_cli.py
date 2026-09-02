#!/usr/bin/env python3
"""CLI for Nayose entity matching inference."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from nayose_inference import load_engine


def main():
    parser = argparse.ArgumentParser(description="Nayose Entity Matching Inference")
    parser.add_argument("--model-dir", default="models", help="Model directory")
    parser.add_argument("--data-dir", default="data", help="Data directory")
    parser.add_argument("--name", help="Name (kanji)")
    parser.add_argument("--kana", help="Name (kana)")
    parser.add_argument("--phone", help="Phone number")
    parser.add_argument("--email", help="Email address")
    parser.add_argument("--address", help="Address")
    parser.add_argument("--dob", help="Date of birth (YYYY-MM-DD)")
    parser.add_argument("--postal", help="Postal code")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--interactive", "-i", action="store_true", help="Interactive mode")

    args = parser.parse_args()

    print(f"Loading model from {args.model_dir}...")
    engine = load_engine(args.model_dir, args.data_dir)
    print("✓ Model loaded\n")

    if args.interactive:
        interactive_mode(engine)
    elif args.name or args.kana or args.phone or args.email:
        result = engine.recommend({
            "party_type": "person",
            "name_kanji": args.name or "",
            "name_kana": args.kana or "",
            "phone": args.phone or "",
            "email": args.email or "",
            "address": args.address or "",
            "date_birth": args.dob or "",
            "postal_code": args.postal or "",
        })
        if args.json:
            print(json.dumps({
                "action": result.action,
                "top_nayose_id": result.top_nayose_id,
                "top_probability": result.top_probability,
                "margin": result.margin,
                "candidates": len(result.candidates),
            }, indent=2))
        else:
            print(f"Action: {result.action}")
            print(f"Top Entity: {result.top_nayose_id or 'None'}")
            print(f"Probability: {result.top_probability:.3%}")
            print(f"Margin: {result.margin:.3%}")
            if not result.candidates.empty:
                print("\nCandidates:")
                print(result.candidates.to_string(index=False))
    else:
        parser.print_help()


def interactive_mode(engine):
    """Interactive entity matching."""
    print("=== Nayose Interactive Mode ===")
    while True:
        print("\n--- New Query ---")
        q = {
            "party_type": "person",
            "name_kanji": input("Name (kanji): ").strip(),
            "name_kana": input("Name (kana): ").strip(),
            "phone": input("Phone: ").strip(),
            "email": input("Email: ").strip(),
            "address": input("Address: ").strip(),
            "date_birth": input("DOB (YYYY-MM-DD): ").strip(),
            "postal_code": input("Postal code: ").strip(),
        }
        if not any([q["name_kanji"], q["name_kana"], q["phone"], q["email"]]):
            print("Error: At least one identifier required.")
            continue

        result = engine.recommend(q)
        print(f"\nAction: {result.action}")
        print(f"Top Entity: {result.top_nayose_id or 'None'}")
        print(f"Probability: {result.top_probability:.3%}")
        if not result.candidates.empty:
            print(result.candidates.to_string(index=False))

        if input("\nAnother? (y/n): ").strip().lower() != 'y':
            break


if __name__ == "__main__":
    main()
