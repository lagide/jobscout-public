"""Purge rétroactive des jobs déjà en DB qui matchent la blacklist actuelle.

Usage :
    sudo -n python3 purge_blacklisted.py --dry-run        # affiche ce qui serait supprimé
    sudo -n python3 purge_blacklisted.py                  # supprime pour de vrai

Protection : ne touche JAMAIS aux jobs avec application_status != None ou archived=True
(jobs curés manuellement par l'utilisateur).
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

# Surchargeable via JOBSCOUT_ROOT / JOBSCOUT_DB.
ROOT = os.getenv("JOBSCOUT_ROOT", str(Path(__file__).resolve().parent))
sys.path.insert(0, f"{ROOT}/backend")
from constants import is_company_blacklisted, is_title_blacklisted  # noqa: E402

DB = os.getenv(
    "JOBSCOUT_DB",
    "/volume1/@docker/volumes/jobscout_jobscout-data/_data/jobs.db",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="affiche sans supprimer")
    parser.add_argument(
        "--include-curated",
        action="store_true",
        help="purge aussi les jobs avec application_status ou archived (déconseillé)",
    )
    args = parser.parse_args()

    con = sqlite3.connect(DB, timeout=30.0)
    con.row_factory = sqlite3.Row

    where = "1=1"
    if not args.include_curated:
        where = "application_status IS NULL AND archived = 0"

    rows = con.execute(
        f"SELECT id, title, company, relevance_score FROM jobs WHERE {where}"
    ).fetchall()

    to_delete: list[tuple[int, str, str, str]] = []  # (id, title, company, reason)
    for r in rows:
        title = r["title"] or ""
        company = r["company"] or ""
        if is_title_blacklisted(title):
            to_delete.append((r["id"], title, company, "title"))
        elif is_company_blacklisted(company):
            to_delete.append((r["id"], title, company, "company"))

    print(f"Total jobs analysés : {len(rows)}")
    print(f"À purger : {len(to_delete)}")
    by_reason: dict[str, int] = {}
    for _, _, _, reason in to_delete:
        by_reason[reason] = by_reason.get(reason, 0) + 1
    for reason, count in by_reason.items():
        print(f"  - {reason}: {count}")

    if not to_delete:
        print("Rien à faire.")
        return

    print("\n--- Échantillon (10 premières) ---")
    for jid, title, company, reason in to_delete[:10]:
        print(f"  [{reason}] id={jid}  {title[:60]:60} | {company[:30]}")

    if args.dry_run:
        print("\n(dry-run — aucune suppression)")
        return

    ids = [t[0] for t in to_delete]
    # Batch DELETE en chunks de 500 (SQLite parameter limit ~999)
    deleted = 0
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        placeholders = ",".join("?" * len(chunk))
        cur = con.execute(f"DELETE FROM jobs WHERE id IN ({placeholders})", chunk)
        deleted += cur.rowcount or 0
    con.commit()
    print(f"\n✓ Supprimé : {deleted} jobs")


if __name__ == "__main__":
    main()
