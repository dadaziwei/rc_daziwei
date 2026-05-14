import argparse

from notification_service.worker import process_due_notifications


def main() -> None:
    parser = argparse.ArgumentParser(prog="notification-service")
    subparsers = parser.add_subparsers(dest="command", required=True)

    worker_parser = subparsers.add_parser("worker", help="Process due notifications once.")
    worker_parser.add_argument("--limit", type=int, default=10)

    args = parser.parse_args()
    if args.command == "worker":
        processed_count = process_due_notifications(limit=args.limit)
        print(f"processed={processed_count}")


if __name__ == "__main__":
    main()
