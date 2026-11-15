"""Delete the old Ren-Gas hankkeet from the live demo database.

Dry run by default; `--apply` performs the deletes. Runs on the App Service worker
where /home/data/keha_lv.db lives, or locally with KEHA_LV_DB_PATH pointing at a copy.

    python3 delete_ren_gas_cases.py            # list
    python3 delete_ren_gas_cases.py --apply    # delete
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(os.environ.get("KEHA_LV_DB_PATH", "/home/data/keha_lv.db"))
UPLOADS = Path(os.environ.get("KEHA_LV_UPLOADS_DIR", "/home/data/uploads"))
CUSTOMER_ID = os.environ.get("KEHA_LV_CUSTOMER_ID", "ren-gas")
APPLY = "--apply" in sys.argv


def main() -> None:
    connection = sqlite3.connect(DB_PATH, timeout=20)
    connection.row_factory = sqlite3.Row
    all_cases = connection.execute(
        "select id, title, status, customer_id from cases where customer_id = ? order by rowid",
        (CUSTOMER_ID,),
    ).fetchall()
    # Only the SUBMITTED Ren-Gas hankkeet are in scope. Other ren-gas cases on the
    # server are colleagues' drafts and must not be deleted by this script.
    cases = [row for row in all_cases if connection.execute(
        "select 1 from artifacts where id like ? limit 1", (f"{row['id']}:receipt:%",)
    ).fetchone()]
    print(f"database: {DB_PATH}")
    print(f"ren-gas cases total: {len(all_cases)} · submitted (in scope): {len(cases)}")
    for row in all_cases:
        if row not in cases:
            print(f"  kept (not submitted): {row['id']} {row['title']}")
    for row in cases:
        receipts = [
            item["id"]
            for item in connection.execute(
                "select id from artifacts where id like ?", (f"{row['id']}:receipt:%",)
            )
        ]
        artifacts = connection.execute(
            "select count(*) from artifacts where id like ?", (f"{row['id']}:%",)
        ).fetchone()[0]
        print(f"  {row['id']}  status={row['status']!s:10s} artifacts={artifacts:3d} receipts={receipts} title={row['title']}")
    if not cases:
        connection.close()
        return
    if not APPLY:
        print("\ndry run: nothing deleted (pass --apply to delete)")
        connection.close()
        return
    deleted = 0
    for row in cases:
        case_id = row["id"]
        connection.execute("delete from artifacts where id like ?", (f"{case_id}:%",))
        connection.execute("delete from files where case_id = ?", (case_id,))
        connection.execute("delete from cases where id = ?", (case_id,))
        connection.commit()
        case_uploads = UPLOADS / "lupademo" / case_id
        if case_uploads.exists():
            shutil.rmtree(case_uploads, ignore_errors=True)
        deleted += 1
        print(f"  deleted {case_id}")
    remaining = connection.execute("select count(*) from cases").fetchone()[0]
    print(f"\ndeleted {deleted} case(s); cases remaining in database: {remaining}")
    connection.close()


if __name__ == "__main__":
    main()
