"""The GNSS files of a fileset: a BagIt bag (as R2R distributes it) or a plain directory (as
OpenVDM collects it), with the checksum manifest when there is one.

- A bag's files are under `data/`, its checksums in `manifest-md5.txt`, and R2R's file ids and
  sizes in `file-info.txt`. A plain directory may have an external manifest of "<md5> <path>"
  lines (e.g. OpenVDM's MD5 summary).
- The nav files are every file in the data directory, or those matching a glob (`[nav].files`,
  relative to the fileset root). Compressed `.gz` and `.bz2` files are read transparently by
  `rawnav`.
- `verify_manifest` lists missing files and checksum mismatches as text; nothing here raises
  for a bad file.
"""

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from nav_qa_processing.readable import Readable, block, plural


@dataclass
class ManifestEntry:
    path: str  # relative to the fileset root, e.g. data/SP2613_mru_trimble_bx992-2026-07-09.txt
    md5: str
    file_id: str = ""  # R2R's
    size: int | None = None  # [bytes]


@dataclass
class Fileset(Readable):
    root: Path
    data_dir: Path
    files: list[Path]  # the nav files, sorted by name
    manifest: list[ManifestEntry] = field(default_factory=list)
    is_bag: bool = False
    # when an external manifest was written; files modified later aren't in it yet
    manifest_mtime: float | None = None

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def __str__(self) -> str:
        kind = "BagIt bag" if self.is_bag else "Directory"
        parts = [plural(len(self.files), "nav file")]
        if self.manifest:
            parts.append(f"manifest of {plural(len(self.manifest), 'file')}")
        return block(
            f"{kind} {self.root}: {', '.join(parts)}", [self.relative(f) for f in self.files]
        )


def md5sum(path: Path) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_bag_manifest(root: Path) -> list[ManifestEntry]:
    """manifest-md5.txt (BagIt), with R2R's file ids and sizes from file-info.txt"""
    manifest_path = root / "manifest-md5.txt"
    if not manifest_path.exists():
        return []
    entries = {}
    for line in manifest_path.read_text().splitlines():
        if line.strip():
            md5, path = line.split(maxsplit=1)
            entries[path.strip()] = ManifestEntry(path=path.strip(), md5=md5.lower())
    info_path = root / "file-info.txt"
    if info_path.exists():
        for line in info_path.read_text().splitlines():
            parts = line.split("\t")
            if len(parts) >= 4 and parts[2] in entries:
                entries[parts[2]].file_id = parts[0]
                entries[parts[2]].size = int(parts[3]) if parts[3].isdigit() else None
    return sorted(entries.values(), key=lambda e: e.path.lower())


_MD5 = re.compile(r"^[0-9a-fA-F]{32}$")


def _read_external_manifest(manifest_path: Path, root: Path) -> list[ManifestEntry]:
    """ "<md5> <path>" lines (paths relative to the manifest's directory) for the files under
    root, as paths relative to root. Entries without a real hash (OpenVDM writes asterisks for
    files over its size limit) are skipped
    """
    base = manifest_path.parent.resolve()
    root = root.resolve()
    entries = []
    for line in manifest_path.read_text(errors="replace").splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2 or not _MD5.match(parts[0]):
            continue
        full = (base / parts[1]).resolve()
        if full.is_relative_to(root):
            entries.append(
                ManifestEntry(path=full.relative_to(root).as_posix(), md5=parts[0].lower())
            )
    return sorted(entries, key=lambda e: e.path.lower())


def load_fileset(
    path: Path | str, files: str = "", manifest_path: Path | str | None = None
) -> Fileset:
    """The nav files in a bag or directory: every file in the data directory, or those matching
    the glob `files` (relative to `path`). `manifest_path` supplies checksums for a plain
    directory
    """
    path = Path(path)
    is_bag = (path / "bagit.txt").exists()
    data_dir = path / "data" if is_bag else path
    if not data_dir.is_dir():
        raise FileNotFoundError(f"no data directory at {data_dir}")
    if files:
        found = [f for f in path.glob(files) if f.is_file()]
    else:
        found = [
            f
            for f in data_dir.rglob("*")
            if f.is_file() and not any(part.startswith(".") for part in f.relative_to(path).parts)
        ]
    if manifest_path is not None:  # an external manifest inside the directory isn't nav data
        found = [f for f in found if f.resolve() != Path(manifest_path).resolve()]
    manifest, manifest_mtime = [], None
    if is_bag:
        # A bag is static, so its manifest is never stale (and copying a bag resets file times)
        manifest = _read_bag_manifest(path)
    elif manifest_path is not None and Path(manifest_path).exists():
        manifest = _read_external_manifest(Path(manifest_path), path)
        manifest_mtime = Path(manifest_path).stat().st_mtime
    return Fileset(
        root=path,
        data_dir=data_dir,
        files=sorted(found, key=lambda f: f.name.lower()),
        manifest=manifest,
        is_bag=is_bag,
        manifest_mtime=manifest_mtime,
    )


def verify_manifest(fileset: Fileset) -> list[str]:
    """Problems with the nav files against the manifest: missing files, checksum mismatches, nav
    files the manifest doesn't list. Empty without a manifest. An external manifest older than
    a file doesn't list it yet, so such a file isn't a problem
    """
    if not fileset.manifest:
        return []
    problems = []
    listed = {entry.path: entry for entry in fileset.manifest}
    nav = {fileset.relative(f): f for f in fileset.files}
    for relative, path in nav.items():
        entry = listed.get(relative)
        if entry is None:
            newer = (
                fileset.manifest_mtime is not None
                and path.stat().st_mtime > fileset.manifest_mtime
            )
            if not newer:
                problems.append(f"{relative}: not in the manifest")
        elif md5sum(path) != entry.md5:
            problems.append(f"{relative}: checksum doesn't match the manifest")
    for relative in listed:
        if not (fileset.root / relative).exists():
            problems.append(f"{relative}: in the manifest but missing")
    return problems
