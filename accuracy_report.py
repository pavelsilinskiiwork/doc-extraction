"""Print the accuracy tables (Markdown) computed from the reviewed extractions in the database.

    py accuracy_report.py                # uses DATABASE_URL from the environment or .env

Paste the output into the README once enough documents have been reviewed.
"""

import sys

from dotenv import load_dotenv

load_dotenv()

import db  # noqa: E402
from stats import accuracy_report, markdown_table  # noqa: E402


def main() -> int:
    if not db.configure_from_env():
        print("DATABASE_URL is not set.")
        return 1
    with db.session_scope() as session:
        report = accuracy_report(session)
    if not report["groups"]:
        print("No extractions saved yet.")
        return 0
    print(markdown_table(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
