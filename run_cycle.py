"""The single entry point Windows Task Scheduler calls, twice daily
(7:30 AM / 7:30 PM IST): process inbound PMO: emails first, THEN send the
digest — in that order, so the digest always reflects whatever just came in,
rather than the two steps racing independently on separate schedules.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import process_email_updates
import send_digest


def main() -> None:
    process_email_updates.log("=== Cycle start: ingest ===")
    process_email_updates.main()
    process_email_updates.log("=== Cycle: sending digest ===")
    send_digest.main()
    process_email_updates.log("=== Cycle complete ===")


if __name__ == "__main__":
    main()
