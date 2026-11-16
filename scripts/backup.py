
"""WarrantyVault local backup and restore commands."""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from app import create_app
from app.services.backup import create_backup, restore_backup


def main():
    parser = argparse.ArgumentParser(
        description="WarrantyVault backup and restore utility"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("create", help="Create a full backup")

    restore = commands.add_parser("restore", help="Restore a full backup")
    restore.add_argument("archive", help="Path to the backup ZIP")

    args = parser.parse_args()
    app = create_app()

    with app.app_context():
        if args.command == "create":
            result = create_backup(app)
            print(f"Backup created: {result}")

        elif args.command == "restore":
            print("WARNING: This replaces the current database and uploads.")
            confirmation = input("Type RESTORE to continue: ")

            if confirmation != "RESTORE":
                print("Restore cancelled.")
                return

            safety = restore_backup(app, Path(args.archive))
            print("Restore completed successfully.")
            print(f"Previous installation preserved at: {safety}")


if __name__ == "__main__":
    main()
