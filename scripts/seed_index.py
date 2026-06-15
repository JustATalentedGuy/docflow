#!/usr/bin/env python3
"""
Seed Docflow with a folder of documents.

Usage:
    python scripts/seed_index.py --folder ./my_docs --api http://localhost:80

This script:
  1. Uploads all supported files in --folder to the running API.
  2. Polls each job until completed or failed.
  3. Prints a summary.

Useful for:
  - Bulk testing with real documents before a demo.
  - Warming up the system (PaddleOCR model loads on first OCR task).
  - Verifying end-to-end pipeline health.
"""

import argparse
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests")
    sys.exit(1)

SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff"}


def upload_file(api_base: str, file_path: Path) -> tuple[str, str]:
    """
    Upload a single file. Returns (job_id, filename) on success.
    Raises on HTTP error.
    """
    with open(file_path, "rb") as fh:
        resp = requests.post(
            f"{api_base}/api/v1/upload",
            files={"file": (file_path.name, fh)},
            timeout=60,
        )
    resp.raise_for_status()
    data = resp.json()
    return data["job_id"], file_path.name


def poll_status(api_base: str, job_id: str, timeout: int = 600) -> str:
    """
    Poll /status/{job_id} every 5 seconds until completed, failed, or timeout.
    Returns the final status string.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = requests.get(f"{api_base}/api/v1/status/{job_id}", timeout=10)
        if resp.status_code == 200:
            status = resp.json().get("status", "unknown")
            if status in ("completed",) or status.startswith("failed"):
                return status
        time.sleep(5)
    return "timeout"


def seed_folder(folder: str, api_base: str, timeout: int) -> None:
    folder_path = Path(folder)
    if not folder_path.is_dir():
        print(f"ERROR: '{folder}' is not a directory.")
        sys.exit(1)

    files = sorted(
        f for f in folder_path.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    )

    if not files:
        print(f"No supported files found in '{folder}'.")
        print(f"Supported: {sorted(SUPPORTED_EXTENSIONS)}")
        sys.exit(0)

    print(f"Found {len(files)} file(s). Uploading to {api_base} ...\n")

    jobs: list[tuple[str, str]] = []  # [(job_id, filename), ...]
    failed_uploads = []

    for file_path in files:
        try:
            job_id, name = upload_file(api_base, file_path)
            jobs.append((job_id, name))
            print(f"  ✓ Uploaded  {name:<40}  job_id={job_id}")
        except Exception as exc:
            failed_uploads.append(file_path.name)
            print(f"  ✗ Failed    {file_path.name:<40}  {exc}")

    if not jobs:
        print("\nNo jobs submitted.")
        return

    print(f"\nPolling {len(jobs)} job(s) (timeout={timeout}s each) ...\n")

    results = {"completed": [], "failed": [], "timeout": []}

    for job_id, name in jobs:
        print(f"  Waiting: {name} ... ", end="", flush=True)
        status = poll_status(api_base, job_id, timeout=timeout)
        emoji  = "✓" if status == "completed" else "✗"
        print(f"{emoji} {status}")
        bucket = "completed" if status == "completed" else (
            "timeout" if status == "timeout" else "failed"
        )
        results[bucket].append(name)

    print("\n── Summary ────────────────────────────────────────")
    print(f"  Completed : {len(results['completed'])}")
    print(f"  Failed    : {len(results['failed']) + len(failed_uploads)}")
    print(f"  Timeout   : {len(results['timeout'])}")

    if results["failed"] or failed_uploads:
        print("\n  Failed files:")
        for f in failed_uploads + results["failed"]:
            print(f"    • {f}")

    if results["completed"]:
        print(f"\n✓ {len(results['completed'])} document(s) ready to query.")
        print(f"  Try:  curl -s -X POST {api_base}/api/v1/query \\")
        print( "          -H 'Content-Type: application/json' \\")
        print( "          -d '{\"query\": \"Summarise the main topics\"}'")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Seed Docflow with a folder of documents.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--folder", required=True,
        help="Path to a folder containing PDF, PNG, JPG, or TIFF files",
    )
    parser.add_argument(
        "--api", default="http://localhost:80",
        help="Docflow API base URL (default: http://localhost:80)",
    )
    parser.add_argument(
        "--timeout", type=int, default=600,
        help="Seconds to wait per job before declaring timeout (default: 600)",
    )
    args = parser.parse_args()
    seed_folder(args.folder, args.api, args.timeout)
