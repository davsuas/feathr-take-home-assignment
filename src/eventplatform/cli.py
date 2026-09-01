"""Operator CLI - credential issuance for the review environment."""

from __future__ import annotations

import argparse
import asyncio

from eventplatform.infrastructure.config.settings import get_settings
from eventplatform.infrastructure.persistence.mongo.client import create_client, get_database
from eventplatform.infrastructure.persistence.mongo.indexes import ensure_indexes
from eventplatform.infrastructure.persistence.mongo.tenant_directory import MongoTenantDirectory


async def _issue_key(tenant: str, label: str) -> str:
    settings = get_settings()
    client = create_client(settings.mongo_uri)
    database = get_database(client, settings.mongo_database)
    await ensure_indexes(database)
    return await MongoTenantDirectory(database).issue(tenant, label=label)


def main() -> None:
    parser = argparse.ArgumentParser(prog="eventplatform")
    sub = parser.add_subparsers(dest="command", required=True)
    issue = sub.add_parser("issue-key", help="Mint an API credential for a tenant")
    issue.add_argument("--tenant", required=True)
    issue.add_argument("--label", default="default")
    args = parser.parse_args()

    if args.command == "issue-key":
        # Printed once and never stored in plaintext - the directory keeps only a hash.
        print(asyncio.run(_issue_key(args.tenant, args.label)))


if __name__ == "__main__":
    main()
