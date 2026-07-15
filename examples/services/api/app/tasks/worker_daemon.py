from __future__ import annotations

import os

from app.tasks.manager import task_manager


def main() -> None:
    poll_seconds = float(os.getenv("PITGUARD_WORKER_POLL_SECONDS", "1.0"))
    task_manager.run_worker_forever(poll_seconds=poll_seconds)


if __name__ == "__main__":
    main()
