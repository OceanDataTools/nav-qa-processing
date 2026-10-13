#!/usr/bin/env python3
"""Downloads a released R2R GNSS fileset with R2R's QA report and nav products, for validation.

    uv run scripts/fetch_r2r_fileset.py SP2613 169851

writes data/<CRUISE>_<FILESET>_gnss/:
    bagit.txt, manifest-md5.txt, data/...   the raw fileset (a BagIt bag)
    r2r_qa.2.0.xml                          R2R's QA report (from api/qa_info "documents")
    products/                               the r2rnav product bag (*.geoCSV), from api/product

R2R's download URLs redirect to a gzipped tar of the bag (Globus) or to the bag's directory
listing (NCEI); both are handled, as in sbe-qa-processing's fetcher.
"""

import argparse
import http.cookiejar
import io
import json
import re
import sys
import tarfile
import urllib.parse
import urllib.request
from pathlib import Path

R2R = "https://service.rvdata.us"
TIMEOUT = 120  # [s]
# NCEI redirects in a loop unless its session cookie is kept
OPENER = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
)


def fetch(url: str) -> tuple[str, bytes]:
    """The final URL after redirects, and the body"""
    with OPENER.open(url, timeout=TIMEOUT) as response:
        return response.geturl(), response.read()


def api(endpoint: str, **query) -> list[dict]:
    _, body = fetch(f"{R2R}/api/{endpoint}/?{urllib.parse.urlencode(query)}")
    return json.loads(body).get("data") or []


def _is_child(href: str) -> bool:
    if ":" in href:  # another site, mailto:, ...
        return False
    name = urllib.parse.unquote(href).removesuffix("/")
    return bool(name) and name not in (".", "..") and not any(c in name for c in "/\\")


def download_tree(url: str, destination: Path) -> int:
    """Mirrors an Apache-style directory listing into destination"""
    destination.mkdir(parents=True, exist_ok=True)
    _, body = fetch(url)
    links = re.findall(r'href="([^"?/][^"]*)"', body.decode("utf-8", "replace"))
    count = 0
    for name in filter(_is_child, links):
        target = destination / urllib.parse.unquote(name.removesuffix("/"))
        if name.endswith("/"):
            count += download_tree(urllib.parse.urljoin(url, name), target)
        else:
            target.write_bytes(fetch(urllib.parse.urljoin(url, name))[1])
            count += 1
    return count


def extract_bag(archive: bytes, destination: Path) -> int:
    """Extracts the bag in a .tar.gz, at whatever depth it's nested, into destination"""
    destination.mkdir(parents=True, exist_ok=True)
    count = 0
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        members = tar.getmembers()
        roots = [Path(m.name).parent for m in members if Path(m.name).name == "bagit.txt"]
        if not roots:
            raise ValueError("archive contains no bagit.txt")
        root = min(roots, key=lambda r: len(r.parts))
        for member in members:
            path = Path(member.name)
            if member.isfile() and path.is_relative_to(root):
                member.name = str(path.relative_to(root))
                # the "data" filter rejects absolute paths, links and escapes
                tar.extract(member, destination, filter="data")
                count += 1
    return count


def download_bag(url: str, destination: Path) -> int:
    final_url, body = fetch(url)
    if body.lstrip().startswith(b"ERROR"):
        sys.exit(f"{final_url}: {body.decode(errors='replace').strip()}")
    if body[:2] == b"\x1f\x8b":
        return extract_bag(body, destination)
    return download_tree(final_url.rstrip("/") + "/", destination)


def download_qa_report(fileset_id: int, destination: Path) -> Path | None:
    for record in api("qa_info", fileset_id=fileset_id):
        for document in json.loads(record.get("documents") or "[]"):
            if re.search(r"_qa\.2\.0\..*\.xml$", document.get("file_name", "")):
                body = fetch(document["uri"])[1]
                if body.lstrip().startswith(b"<?xml"):
                    path = destination / "r2r_qa.2.0.xml"
                    path.write_bytes(body)
                    return path
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cruise_id")
    parser.add_argument("fileset_id", type=int, help="the raw GNSS fileset")
    parser.add_argument("-o", "--output", type=Path, default=Path("data"))
    parser.add_argument("--force", action="store_true", help="download into an existing directory")
    args = parser.parse_args()

    destination = args.output / f"{args.cruise_id}_{args.fileset_id}_gnss"
    if destination.exists() and not args.force:
        sys.exit(f"{destination} exists (use --force)")

    filesets = [
        f
        for f in api("fileset", cruise_id=args.cruise_id)
        if f.get("fileset_id") == args.fileset_id
    ]
    if not filesets or not filesets[0].get("download_url"):
        sys.exit(f"{args.cruise_id} fileset {args.fileset_id} isn't released for download")
    count = download_bag(filesets[0]["download_url"], destination)
    print(f"{count} raw files -> {destination}")

    report = download_qa_report(args.fileset_id, destination)
    print(f"QA report -> {report}" if report else "R2R has no QA 2.0 report for this fileset")

    products = [
        p for p in api("product", cruise_id=args.cruise_id) if p.get("product_type") == "r2rnav"
    ]
    if products:
        count = download_bag(products[0]["url"], destination / "products")
        print(f"{count} product files (fileset {products[0]['productset_id']}) -> products/")
    else:
        print("R2R has no r2rnav product for this cruise")
    return 0


if __name__ == "__main__":
    sys.exit(main())
