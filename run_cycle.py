"""The single entry point (now run by a GitHub Actions schedule, twice
daily at 7:30 AM / 7:30 PM IST): process inbound PMO: emails, sync any
completed meeting's next_steps into real tasks, THEN send the digest — in
that order, so the digest always reflects whatever just came in, rather
than the steps racing independently on separate schedules.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from db import TaskStore
import process_email_updates
import send_digest


def main() -> None:
    process_email_updates.log("=== Cycle start: ingest ===")
    process_email_updates.main()

    process_email_updates.log("=== Cycle: syncing meeting next_steps ===")
    # A separate local process (a meeting watcher) writes meeting records
    # directly to Turso, not through any API here — this is what turns a
    # completed meeting's next_steps into real tasks on a schedule, so they
    # show up even if nobody happens to be looking at the dashboard.
    store = TaskStore(send_digest.DB_PATH)
    created = store.sync_meeting_tasks()
    if created:
        process_email_updates.log(f"  Created {len(created)} task(s) from meeting next_steps.")

    process_email_updates.log("=== Cycle: sending digest ===")
    send_digest.main()
    process_email_updates.log("=== Cycle complete ===")


if __name__ == "__main__":
    main()
