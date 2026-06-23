import argparse
import asyncio
import traceback

from cryptography.fernet import Fernet

import db

BATCH_SIZE = 50


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Re-encrypt every stored Todoist token from an old Fernet key to a new one."
    )
    parser.add_argument("--old-key", required=True)
    parser.add_argument("--new-key", required=True)
    return parser.parse_args(argv)


def _rotate_token(encrypted_old: str, old_fernet: Fernet, new_fernet: Fernet) -> str:
    plain = old_fernet.decrypt(encrypted_old.encode()).decode()
    return new_fernet.encrypt(plain.encode()).decode()


async def _process_batch(
    batch: list[dict], old_fernet: Fernet, new_fernet: Fernet
) -> tuple[int, int]:
    succeeded = 0
    failed = 0
    for user in batch:
        user_id = user["telegram_user_id"]
        try:
            new_encrypted = _rotate_token(user["todoist_token"], old_fernet, new_fernet)
            await db.update_token(user_id, new_encrypted)
            succeeded += 1
        except Exception:
            failed += 1
            print(f"FAILED user_id={user_id}")
            traceback.print_exc()
    return succeeded, failed


async def rotate(old_key: str, new_key: str) -> tuple[int, int]:
    old_fernet = Fernet(old_key.encode())
    new_fernet = Fernet(new_key.encode())

    await db.init_pool()
    total_succeeded = 0
    total_failed = 0
    try:
        users = await db.get_all_users()
        batches = [users[i : i + BATCH_SIZE] for i in range(0, len(users), BATCH_SIZE)]
        total_batches = len(batches)
        for batch_index, batch in enumerate(batches, start=1):
            succeeded, failed = await _process_batch(batch, old_fernet, new_fernet)
            total_succeeded += succeeded
            total_failed += failed
            print(
                f"Batch {batch_index}/{total_batches}: "
                f"{succeeded} succeeded, {failed} failed"
            )
    finally:
        await db.close_pool()

    print(f"Done. Succeeded: {total_succeeded}, Failed: {total_failed}")
    return total_succeeded, total_failed


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    asyncio.run(rotate(args.old_key, args.new_key))


if __name__ == "__main__":
    main()
