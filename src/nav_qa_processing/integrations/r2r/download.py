"""Downloads a released R2R GNSS fileset with R2R's QA report and nav products, for validation.

`download_fileset(cruise_id, fileset_id, destination)` writes into destination:

    bagit.txt, manifest-md5.txt, data/...   the raw fileset (a BagIt bag)
    r2r_qa.2.0.xml                          R2R's QA report (from api/qa_info's documents)
    products/                               the r2rnav product bag (*.geoCSV), from api/product

R2R's download URLs redirect to a gzipped tar of the bag (Globus) or to the bag's directory
listing (NCEI); both are handled, as in sbe-qa-processing.
"""

import http.cookiejar
import io
import logging
import re
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from nav_qa_processing.integrations.r2r.catalog import (
    R2RError,
    fetch_r2r_records,
    http_error,
    qa_report_url,
)
from nav_qa_processing.network import reaching

logger = logging.getLogger(__name__)
DOWNLOAD_TIMEOUT = 120  # [s]
QA_REPORT = "r2r_qa.2.0.xml"
PRODUCTS = "products"


@dataclass
class Download:
    files: int
    bag_url: str
    qa_report: Path | None  # None when R2R has no QA 2.0 report for the fileset
    product_files: int = 0
    productset_id: str = ""  # "" when R2R has no r2rnav product for the cruise


def build_opener() -> urllib.request.OpenerDirector:
    # NCEI redirects in a loop unless its session cookie is kept
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )


def fetch(opener, url: str, what: str = "R2R's fileset download") -> tuple[str, bytes]:
    """The final URL after redirects, and the body. An HTTP error is an R2RError naming `what`"""
    logger.debug("GET %s", url)
    try:
        with reaching(what, url), opener.open(url, timeout=DOWNLOAD_TIMEOUT) as response:
            return response.geturl(), response.read()
    except urllib.error.HTTPError as error:
        raise http_error(error, what, url) from error


def _is_child(href: str) -> bool:
    """Whether a listing's link is a file or subdirectory directly under it: not a parent link
    (../), another site (http://...) or a name that would escape the destination
    """
    if ":" in href:  # a scheme: another site, mailto:, ...
        return False
    name = urllib.parse.unquote(href).removesuffix("/")
    return bool(name) and name not in (".", "..") and not any(c in name for c in "/\\")


def download_tree(opener, url: str, destination: Path) -> int:
    """Mirrors an Apache-style directory listing into destination"""
    destination.mkdir(parents=True, exist_ok=True)
    _, body = fetch(opener, url)
    links = re.findall(r'href="([^"?/][^"]*)"', body.decode("utf-8", "replace"))
    count = 0
    for name in filter(_is_child, links):
        target = destination / urllib.parse.unquote(name.removesuffix("/"))
        if name.endswith("/"):
            count += download_tree(opener, urllib.parse.urljoin(url, name), target)
        else:
            target.write_bytes(fetch(opener, urllib.parse.urljoin(url, name))[1])
            count += 1
    return count


def extract_bag(archive: bytes, destination: Path) -> int:
    """Extracts the bag in a .tar.gz into destination, so destination/bagit.txt exists. The bag
    can be nested at any depth in the archive (e.g. CRUISE/FILESET/bagit.txt)
    """
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
                # the "data" filter rejects absolute paths, links and anything outside destination
                tar.extract(member, destination, filter="data")
                count += 1
    return count


def download_bag(
    opener, url: str, destination: Path, what: str = "R2R's fileset download"
) -> tuple[str, int]:
    """The bag at url into destination: its final URL and the number of files"""
    final_url, body = fetch(opener, url, what)
    if body.lstrip().startswith(b"ERROR"):
        # R2R lists a download URL for some filesets that aren't actually online
        raise R2RError(f"{final_url}: {body.decode(errors='replace').strip()}")
    if body[:2] == b"\x1f\x8b":
        return final_url, extract_bag(body, destination)
    final_url = final_url if final_url.endswith("/") else final_url + "/"
    return final_url, download_tree(opener, final_url, destination)


def download_qa_report(opener, fileset_id: int | str, destination: Path) -> Path | None:
    url = qa_report_url(fileset_id)
    if url is None:
        return None
    _, body = fetch(opener, url, "R2R's QA report")
    # R2R answers missing files with 200 and an "ERROR: ..." body
    if not body.lstrip().startswith(b"<?xml"):
        return None
    path = destination / QA_REPORT
    path.write_bytes(body)
    return path


def download_fileset(cruise_id: str, fileset_id: int | str, destination: Path) -> Download:
    """The GNSS fileset's bag, R2R's QA report and R2R's nav products, into destination"""
    filesets = [
        f
        for f in fetch_r2r_records("fileset", cruise_id=cruise_id)
        if str(f.get("fileset_id")) == str(fileset_id)
    ]
    if not filesets or not filesets[0].get("download_url"):
        raise R2RError(f"{cruise_id} fileset {fileset_id} isn't released for download")
    opener = build_opener()
    bag_url, count = download_bag(opener, filesets[0]["download_url"], destination)
    try:
        download = Download(count, bag_url, download_qa_report(opener, fileset_id, destination))
        products = [
            p
            for p in fetch_r2r_records("product", cruise_id=cruise_id)
            if p.get("product_type") == "r2rnav"
        ]
        if products:
            _, download.product_files = download_bag(
                opener, products[0]["url"], destination / PRODUCTS, "R2R's product bag"
            )
            download.productset_id = str(products[0].get("productset_id", ""))
    except (R2RError, OSError) as error:  # OSError: NetworkError
        # The raw files are in; say what's missing and how to fetch it (#101)
        raise R2RError(
            f"{count} raw files are in {destination}, but R2R's QA report or products aren't: "
            f"{error}. Run again with --force to fetch them",
            getattr(error, "status", None),
        ) from error
    return download
