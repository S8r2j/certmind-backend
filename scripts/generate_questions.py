#!/usr/bin/env python3
"""
Offline question generation script.
Usage: python scripts/generate_questions.py --exam aws-ai-practitioner --count 200 --output questions.json

Cost: ~200 questions × 600 tokens ≈ $0.24/exam with claude-sonnet-4-6
"""
import argparse
import json
import time
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.anthropic import anthropic_client, EXAM_METADATA  # noqa: E402
from app.core.config import settings  # noqa: F401, E402

EXAMS = list(EXAM_METADATA.keys())


def generate_question(exam_slug: str, domain: str, difficulty: str = "medium") -> dict:
    meta = EXAM_METADATA[exam_slug]
    domain_list = ", ".join(d["name"] for d in meta["domains"])
    prompt = f"""You are an expert AWS certification exam question writer for {meta['title']} ({meta['code']}).
Domains: {domain_list}
Generate ONE MCQ for domain: "{domain}", difficulty: {difficulty}.
Requirements:
- Scenario-based stem (not just definition recall)
- 4 plausible answer options labeled A, B, C, D
- Only one correct answer
- Explanation that references AWS concepts

Respond ONLY in valid JSON (no extra text):
{{"stem": "...", "options": [{{"key": "A", "text": "..."}}, {{"key": "B", "text": "..."}}, {{"key": "C", "text": "..."}}, {{"key": "D", "text": "..."}}], "correct_answer": "A", "explanation": "..."}}"""

    message = anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return json.loads(message.content[0].text.strip())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exam", required=True, choices=EXAMS)
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--output", default="questions.json")
    args = parser.parse_args()

    meta = EXAM_METADATA[args.exam]
    domains = meta["domains"]
    questions = []
    errors = 0

    total_weight = sum(d["weight"] for d in domains)
    per_domain = {
        d["name"]: max(1, round(args.count * d["weight"] / total_weight))
        for d in domains
    }

    print(f"Generating {args.count} questions for {meta['title']}...")
    for domain, count in per_domain.items():
        print(f"  {domain}: {count} questions")
        for i in range(count):
            difficulty = ["easy", "medium", "medium", "hard"][i % 4]
            try:
                q = generate_question(args.exam, domain, difficulty)
                q["exam_slug"] = args.exam
                q["domain"] = domain
                q["difficulty"] = difficulty
                q["is_active"] = True
                questions.append(q)
                print(f"    [{len(questions)}] ✓")
            except Exception as e:
                errors += 1
                print(f"    [{i+1}] ERROR: {e}")
            time.sleep(0.5)

    with open(args.output, "w") as f:
        json.dump(questions, f, indent=2)

    print(f"\nDone. {len(questions)} questions saved to {args.output}. {errors} errors.")


if __name__ == "__main__":
    main()
