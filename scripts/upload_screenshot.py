"""Upload PR screenshots and guidance shots to R2 and print the Markdown that embeds them.

Images stay out of the repo (CONTRIBUTING.md). A PR screenshot goes to the media bucket under
``pr-screenshots/<date>/<file>``, and the description embeds the public URL this prints. The
bucket deletes everything under that prefix 30 days after upload (a lifecycle rule set on the
bucket itself, not here), so a PR's images stop showing a month after it opened.

    .venv/bin/python scripts/upload_screenshot.py mockups/screenshots/412-review-01.png [...]

A guidance shot (#1050, ``docs/GUIDANCE.md``) is a step of a What's New walkthrough and lives
for as long as its entry does, so it goes under ``guidance/<fragment-slug>/step-N.png``, outside
the 30-day rule:

    .venv/bin/python scripts/upload_screenshot.py --guidance 1050-guidance-shots step-1.png step-2.png

The names must be ``step-N.png`` consecutive from 1, each a PNG of at most 500 KB, and a step
already on the bucket is refused unless ``--replace`` says this is a remake after the screen
changed. It prints the PR Markdown (one ``### Step N`` and image per shot) and the ``[[shots]]``
skeleton for the fragment, so the description and the fragment carry the same pictures in the
same order.

The keys are the app's own ``R2_*`` media keys, which can write to every member upload in the
bucket. So this writes only under the two prefixes and never replaces an object it was not told
to: a name clash under ``pr-screenshots/`` means pick a new file name.

Keys come from the environment, else from ``.env`` (in a worktree, the main checkout's). Outside contributors without
them drag the image into the PR description instead, which GitHub hosts.
"""

from __future__ import annotations

import argparse
import datetime
import mimetypes
import os
import pathlib
import re
import subprocess
import sys
from typing import Any

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
from plfog.changelog import GUIDANCE_PREFIX, shot_key  # noqa: E402

PREFIX = "pr-screenshots"
PICTURE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
_KEYS = ("R2_ACCOUNT_ID", "R2_BUCKET_NAME", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_PUBLIC_URL")

#: A guidance shot is at most this many bytes: the modal pays for every step a member walks through.
SHOT_MAX_BYTES = 500 * 1024
_STEP_NAME = re.compile(r"^step-(\d+)\.([A-Za-z0-9]+)$")
#: A fragment's filename stem, the grammar every file in changelog.d/ follows.
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
#: What the first bytes of a file say it is, so a JPEG renamed .png is still refused as a JPEG.
_SIGNATURES = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"GIF8", "image/gif"))


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


def _exists(client: Any, bucket: str, key: str) -> bool:
    from botocore.exceptions import ClientError

    try:
        client.head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False
        raise
    return True


def _client(config: dict[str, str]) -> Any:
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=f"https://{config['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=config["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=config["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def sniff_type(path: pathlib.Path) -> str:
    """What the file's bytes say it is, else what its name suggests, else ``an unknown type``."""
    with path.open("rb") as handle:
        head = handle.read(12)
    for signature, kind in _SIGNATURES:
        if head.startswith(signature):
            return kind
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return mimetypes.guess_type(path.name)[0] or "an unknown type"


def check_shot(path: pathlib.Path) -> None:
    """Exit naming the problem unless ``path`` is a PNG of at most :data:`SHOT_MAX_BYTES`."""
    if not path.is_file():
        sys.exit(f"{path} is not a file.")
    kind = sniff_type(path)
    if path.suffix.lower() != ".png" or kind != "image/png":
        sys.exit(f"{path.name} is {kind}, not a PNG; guidance shots are PNG only (docs/GUIDANCE.md).")
    size = path.stat().st_size
    if size > SHOT_MAX_BYTES:
        sys.exit(f"{path.name} is {size / 1024:.0f} KB; a guidance shot is at most {SHOT_MAX_BYTES // 1024} KB.")


def check_shots(paths: list[pathlib.Path]) -> list[pathlib.Path]:
    """The shots in step order, or exit naming the first problem with their names, types or sizes.

    The names are ``step-N.png`` running from 1 with none missing or repeated, because the
    fragment's ``[[shots]]`` are matched to the files by position alone.
    """
    numbered: dict[int, pathlib.Path] = {}
    for path in paths:
        match = _STEP_NAME.match(path.name)
        if match is None:
            sys.exit(f"{path.name} is not a step name; guidance shots are step-1.png, step-2.png and so on.")
        if match[2].lower() != "png":
            sys.exit(
                f"{path.name} is {sniff_type(path) if path.is_file() else 'not a PNG'}, not a PNG; guidance shots are PNG only (docs/GUIDANCE.md)."
            )
        numbered[int(match[1])] = path
    expected = list(range(1, len(paths) + 1))
    if sorted(numbered) != expected:
        given = ", ".join(path.name for path in paths)
        sys.exit(f"Steps must run step-1.png, step-2.png and so on from 1, none missing or repeated (got {given}).")
    for number in expected:
        check_shot(numbered[number])
    return [numbered[number] for number in expected]


def guidance_markdown(keys: list[str], public: str) -> str:
    """The PR description's walkthrough: one ``### Step N`` heading and image per shot, in order."""
    return "\n\n".join(
        f"### Step {number}\n\n![step-{number}]({public}/{key})" for number, key in enumerate(keys, start=1)
    )


def shots_toml(count: int) -> str:
    """The fragment's ``[[shots]]`` skeleton, one block per shot with an empty caption to fill in."""
    return 'guidance = "shots"\n' + "".join('\n[[shots]]\ncaption = ""\n' for _ in range(count))


def upload_guidance(slug: str, files: list[pathlib.Path], *, replace: bool) -> int:
    """Upload ``files`` as the shots of the fragment ``slug`` and print the Markdown and the TOML skeleton."""
    if not _SLUG.match(slug):
        sys.exit(f"{slug!r} is not a fragment slug: lowercase letters, digits and hyphens, like 1050-guidance-shots.")
    shots = check_shots(files)
    config = settings()
    client = _client(config)
    bucket, public = config["R2_BUCKET_NAME"], config["R2_PUBLIC_URL"].rstrip("/")
    keys = [shot_key(slug, number) for number in range(1, len(shots) + 1)]
    taken = [key for key in keys if _exists(client, bucket, key)]
    if taken and not replace:
        sys.exit(f"{taken[0]} already exists. A remake passes --replace; a new walkthrough uses its own fragment slug.")
    for path, key in zip(shots, keys, strict=True):
        client.upload_file(str(path), bucket, key, ExtraArgs={"ContentType": "image/png"})
    print(guidance_markdown(keys, public))
    print()
    print(shots_toml(len(keys)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+", type=pathlib.Path)
    parser.add_argument(
        "--guidance",
        metavar="FRAGMENT_SLUG",
        help=f"upload the files as this fragment's guidance shots, under {GUIDANCE_PREFIX}/<slug>/step-N.png",
    )
    parser.add_argument(
        "--replace", action="store_true", help="with --guidance: replace shots already there (a remake)"
    )
    args = parser.parse_args(argv)
    if args.replace and not args.guidance:
        parser.error("--replace only applies with --guidance; a PR screenshot is never replaced, rename it instead.")
    if args.guidance:
        return upload_guidance(args.guidance, args.files, replace=args.replace)

    for path in args.files:
        if path.suffix.lower() not in PICTURE_SUFFIXES or not path.is_file():
            sys.exit(f"{path} is not an image file.")

    config = settings()
    client = _client(config)
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
