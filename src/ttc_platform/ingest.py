"""Ingestion: discover resources through the CKAN API and download them.

Design notes
------------
* Discovery goes through ``package_show`` instead of hard-coding download
  URLs, so a re-published resource is picked up automatically.
* Downloads are idempotent: a file whose SHA-256 already matches the
  manifest from the previous run is not re-downloaded.
* Every download is recorded in ``data/raw/manifest.json`` with its URL,
  byte size and SHA-256, so any bronze row can be traced to an exact
  source file.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import requests

from .config import CKAN_BASE, PACKAGE_ID, RAW_DIR, SOURCE_FILES, SourceFile


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def discover_resources(session: requests.Session | None = None) -> dict[str, str]:
    """Return {resource name: download URL} for the CKAN package."""
    session = session or requests.Session()
    resp = session.get(
        f"{CKAN_BASE}/api/3/action/package_show",
        params={"id": PACKAGE_ID},
        timeout=60,
    )
    resp.raise_for_status()
    resources = resp.json()["result"]["resources"]
    return {r["name"]: r["url"] for r in resources if r.get("url")}


def resolve_source(source: SourceFile, resources: dict[str, str]) -> str:
    for name, url in resources.items():
        if source.resource_name_substring.lower() in name.lower():
            return url
    raise LookupError(
        f"No CKAN resource matching {source.resource_name_substring!r}; "
        f"available: {sorted(resources)}"
    )


def download_all(raw_dir: Path = RAW_DIR) -> dict:
    """Download every configured source file. Returns the manifest dict."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = raw_dir / "manifest.json"
    previous: dict = {}
    if manifest_path.exists():
        previous = {e["logical_name"]: e for e in json.loads(manifest_path.read_text())["files"]}

    resources = discover_resources()
    session = requests.Session()
    entries = []
    for source in SOURCE_FILES:
        url = resolve_source(source, resources)
        dest = raw_dir / source.filename
        if dest.exists() and previous.get(source.logical_name, {}).get("sha256") == _sha256(dest):
            status = "unchanged_skipped"
        else:
            with session.get(url, stream=True, timeout=300) as resp:
                resp.raise_for_status()
                with dest.open("wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        fh.write(chunk)
            status = "downloaded"
        entries.append(
            {
                "logical_name": source.logical_name,
                "filename": source.filename,
                "source_url": url,
                "bytes": dest.stat().st_size,
                "sha256": _sha256(dest),
                "status": status,
            }
        )
    manifest = {"package_id": PACKAGE_ID, "files": entries}
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest


if __name__ == "__main__":
    print(json.dumps(download_all(), indent=2))
