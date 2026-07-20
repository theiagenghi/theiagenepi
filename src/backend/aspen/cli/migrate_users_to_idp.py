#!/usr/bin/env python3
"""Plan the move of user accounts off Auth0 and onto a self-hosted IdP.

Reads the `users` table and emits the account records the new identity provider
needs. Authorization is untouched: `user_roles` and `group_roles` already live
in our database and are the source of truth, so nothing here writes to them.

The mapping that matters is `users.auth0_user_id`. Every session, magic link and
foreign key in the system keys off that column, so the new IdP must be seeded
with subjects that match it exactly rather than being allowed to mint its own.
Accounts are created without credentials and flagged password-reset-required --
we cannot migrate password hashes out of Auth0, so every user sets a new one.

This is a planning tool. It defaults to a dry run, and `--apply` only writes the
plan to a file for the IdP import; it never calls Auth0 or the new provider.
"""
import csv
import json
import logging
import sys
from typing import Dict, List

import click
import sqlalchemy as sa
from sqlalchemy.orm import joinedload

from aspen.config.config import Config
from aspen.database.connection import (
    get_db_uri,
    init_db,
    session_scope,
    SqlAlchemyInterface,
)
from aspen.database.models import User, UserRole

logger = logging.getLogger(__name__)


def build_account_records(db) -> List[Dict]:
    users = (
        db.execute(
            sa.select(User).options(  # type: ignore
                joinedload(User.user_roles).options(  # type: ignore
                    joinedload(UserRole.group),  # type: ignore
                    joinedload(UserRole.role),
                )
            )
        )
        .unique()
        .scalars()
        .all()
    )
    records = []
    for user in users:
        records.append(
            {
                # The new IdP must reuse this as the subject claim.
                "subject": user.auth0_user_id,
                "email": user.email,
                "name": user.name,
                "email_verified": True,
                "password_reset_required": True,
                # Informational only -- authorization stays in our database.
                "groups": sorted(
                    {
                        user_role.group.name
                        for user_role in user.user_roles
                        if user_role.group
                    }
                ),
            }
        )
    return records


def report_conflicts(records: List[Dict]) -> int:
    """Anything that would collide in the new IdP, or cannot be migrated."""
    conflicts = 0
    seen_subjects: Dict[str, str] = {}
    seen_emails: Dict[str, str] = {}
    for record in records:
        subject = record["subject"]
        email = record["email"]
        if not subject:
            logger.error(f"User {email} has no auth0_user_id and cannot be mapped")
            conflicts += 1
        elif subject in seen_subjects:
            logger.error(
                f"Duplicate subject {subject}: {seen_subjects[subject]}, {email}"
            )
            conflicts += 1
        else:
            seen_subjects[subject] = email
        if email in seen_emails:
            logger.error(
                f"Duplicate email {email} (subjects {seen_emails[email]}, {subject})"
            )
            conflicts += 1
        else:
            seen_emails[email] = subject
    return conflicts


@click.command("migrate-users-to-idp")
@click.option(
    "--apply",
    "apply_plan",
    is_flag=True,
    default=False,
    help="Write the import file. Without this, nothing is written.",
)
@click.option(
    "--output",
    default="idp_users.json",
    help="Where to write the import file when --apply is given.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["json", "csv"]),
    default="json",
)
def cli(apply_plan: bool, output: str, output_format: str):
    config = Config()
    interface: SqlAlchemyInterface = init_db(get_db_uri(config))
    with session_scope(interface) as db:
        records = build_account_records(db)

    conflicts = report_conflicts(records)
    logger.info(f"{len(records)} accounts to migrate, {conflicts} conflicts")
    if conflicts:
        logger.error("Refusing to emit a plan with unresolved conflicts")
        sys.exit(1)

    if not apply_plan:
        logger.info("Dry run -- nothing written. Re-run with --apply to write.")
        for record in records[:10]:
            logger.info(f"  {record['subject']} <{record['email']}>")
        if len(records) > 10:
            logger.info(f"  ... and {len(records) - 10} more")
        return

    with open(output, "w") as fh:
        if output_format == "json":
            json.dump(records, fh, indent=2)
        else:
            writer = csv.DictWriter(fh, fieldnames=list(records[0].keys()))
            writer.writeheader()
            for record in records:
                writer.writerow({**record, "groups": ";".join(record["groups"])})
    logger.info(f"Wrote {len(records)} accounts to {output}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cli()
