import argparse
import logging

from notification_service.config import get_settings
from notification_service.worker import process_due_notifications, run_worker_loop


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(prog="notification-service")
    subparsers = parser.add_subparsers(dest="command", required=True)

    worker_parser = subparsers.add_parser("worker", help="Run the delivery worker.")
    worker_parser.add_argument("--limit", type=int, default=None)
    worker_parser.add_argument("--poll-interval", type=float, default=None)
    worker_parser.add_argument("--once", action="store_true")

    args = parser.parse_args()
    if args.command == "worker":
        settings = get_settings()
        limit = args.limit or settings.worker_batch_size
        if args.once:
            processed_count = process_due_notifications(limit=limit)
            print(f"processed={processed_count}")
            return
        run_worker_loop(limit=limit, poll_interval_seconds=args.poll_interval)


if __name__ == "__main__":
    main()
