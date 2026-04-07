#!/usr/bin/env python3
"""
Seed the database with exam definitions and questions.
Usage:
  python scripts/seed_db.py --exams-only
  python scripts/seed_db.py --questions-file questions.json
"""
import argparse
import json
import uuid
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.config import settings  # noqa: F401 — loads .env
from app.services.database import init_pool, execute, fetchone
from app.services.anthropic import EXAM_METADATA

EXAM_DEFINITIONS = [
    {
        "slug": "aws-cloud-practitioner",
        "title": "AWS Cloud Practitioner",
        "description": "Entry-level certification covering AWS Cloud fundamentals, services, and billing.",
        "domains": EXAM_METADATA["aws-cloud-practitioner"]["domains"],
    },
    {
        "slug": "aws-ai-practitioner",
        "title": "AWS AI Practitioner",
        "description": "Validates understanding of AI/ML concepts and AWS AI services.",
        "domains": EXAM_METADATA["aws-ai-practitioner"]["domains"],
    },
    {
        "slug": "aws-solutions-architect",
        "title": "AWS Solutions Architect Associate",
        "description": "Design and deploy scalable systems on AWS.",
        "domains": EXAM_METADATA["aws-solutions-architect"]["domains"],
    },
]


def seed_exams():
    print("Seeding exams...")
    for exam in EXAM_DEFINITIONS:
        existing = fetchone("SELECT id FROM exams WHERE slug = %s", (exam["slug"],))
        if existing:
            execute(
                "UPDATE exams SET title = %s, description = %s, domains = %s WHERE slug = %s",
                (exam["title"], exam["description"], json.dumps(exam["domains"]), exam["slug"]),
            )
        else:
            execute(
                "INSERT INTO exams (id, slug, title, description, domains) VALUES (%s, %s, %s, %s, %s)",
                (str(uuid.uuid4()), exam["slug"], exam["title"], exam["description"], json.dumps(exam["domains"])),
            )
        print(f"  ✓ {exam['slug']}")
    print("Exams seeded.")


def seed_questions(filepath: str):
    with open(filepath) as f:
        questions = json.load(f)

    print(f"Seeding {len(questions)} questions from {filepath}...")
    batch_size = 50
    for i in range(0, len(questions), batch_size):
        batch = questions[i:i + batch_size]
        for q in batch:
            execute(
                "INSERT INTO questions (id, exam_slug, domain, stem, options, correct_answer, explanation, difficulty) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    str(uuid.uuid4()),
                    q["exam_slug"], q["domain"], q["stem"],
                    json.dumps(q["options"]), q["correct_answer"],
                    q.get("explanation", ""), q.get("difficulty", "medium"),
                ),
            )
        print(f"  Inserted {min(i + batch_size, len(questions))}/{len(questions)}")
    print("Questions seeded.")


def main():
    init_pool()
    parser = argparse.ArgumentParser()
    parser.add_argument("--exams-only", action="store_true")
    parser.add_argument("--questions-file", default=None)
    args = parser.parse_args()
    seed_exams()
    if not args.exams_only and args.questions_file:
        seed_questions(args.questions_file)


if __name__ == "__main__":
    main()
