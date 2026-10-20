"""Upload PR screenshots to R2 and print the Markdown that embeds them.

Images stay out of the repo (CONTRIBUTING.md). A screenshot goes to the media bucket under
``pr-screenshots/<date>/<file>``, and the description embeds the public URL this prints. The
bucket deletes everything under that prefix 30 days after upload (a lifecycle rule set on the
bucket itself, not here), so a PR's images stop showing a month after it opened.

The keys are the app's own ``R2_*`` media keys, which can write to every member upload in the
bucket. So this writes only under ``pr-screenshots/`` and refuses to replace an existing
object: a name clash means pick a new file name, never overwrite.

    .venv/bin/python scripts/upload_screenshot.py mockups/screenshots/412-review-01.png [...]

Keys come from the environment, else from ``.env`` (in a worktree, the main checkout's). Outside contributors without
them drag the image into the PR description instead, which GitHub hosts.
"""

from __future__ import annotations

import argparse
import datetime
import mimetypes
import os
import pathlib
import subprocess
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PREFIX = "pr-screenshots"
PICTURE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
_KEYS = ("R2_ACCOUNT_ID", "R2_BUCKET_NAME", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_PUBLIC_URL")


def read_env_file(text: str) -> dict[str, str]:
    """``KEY=value`` lines from a ``.env`` file, quotes stripped, comments skipped."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.removeprefix("export ").strip()] = value.strip().strip("\"'")
    return values


def _env_files() -> list[pathlib.Path]:
    """This checkout's ``.env``, then the main checkout's, which a worktree does not copy."""
    common = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
    ).stdout.strip()
    candidates = [_REPO_ROOT / ".env"]
    if common:
        candidates.append(pathlib.Path(common).parent / ".env")
    return [path for path in candidates if path.exists()]


def settings() -> dict[str, str]:
    from_file: dict[str, str] = {}
    for env_file in reversed(_env_files()):
        from_file.update(read_env_file(env_file.read_text()))
    found = {key: os.environ.get(key) or from_file.get(key, "") for key in _KEYS}
    missing = [key for key, value in found.items() if not value]
    if missing:
        sys.exit(f"Missing {', '.join(missing)}. Without R2 keys, drag the image into the PR description.")
    return found


def object_key(path: pathlib.Path, day: datetime.date) -> str:
    return f"{PREFIX}/{day.isoformat()}/{path.name}"


def markdown(path: pathlib.Path, url: str) -> str:
    return f"![{path.stem}]({url})"


def _exists(client, bucket: str, key: str) -> bool:
    from botocore.exceptions import ClientError

    try:
        client.head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False
        raise
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+", type=pathlib.Path)
    args = parser.parse_args(argv)

    for path in args.files:
        if path.suffix.lower() not in PICTURE_SUFFIXES or not path.is_file():
            sys.exit(f"{path} is not an image file.")

    import boto3

    config = settings()
    client = boto3.client(
        "s3",
        endpoint_url=f"https://{config['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=config["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=config["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )
    bucket, public = config["R2_BUCKET_NAME"], config["R2_PUBLIC_URL"].rstrip("/")
    today = datetime.datetime.now(datetime.UTC).date()
    for path in args.files:
        key = object_key(path, today)
        if _exists(client, bucket, key):
            sys.exit(f"{key} already exists. Rename the file; this never overwrites.")
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        client.upload_file(str(path), bucket, key, ExtraArgs={"ContentType": content_type})
        print(markdown(path, f"{public}/{key}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
