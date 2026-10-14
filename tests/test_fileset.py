import hashlib
import os
import time

import pytest

from nav_qa_processing.fileset import load_fileset, verify_manifest


def md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def bag(tmp_path, files: dict[str, str], manifest: dict[str, str] | None = None):
    (tmp_path / "bagit.txt").write_text("BagIt-Version: 0.97\n")
    for name, text in files.items():
        (tmp_path / "data" / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "data" / name).write_text(text)
    listed = manifest if manifest is not None else {n: md5(t) for n, t in files.items()}
    (tmp_path / "manifest-md5.txt").write_text(
        "".join(f"{digest}  data/{name}\n" for name, digest in listed.items())
    )
    (tmp_path / "file-info.txt").write_text(
        "".join(f"{100 + i}\t{d}\tdata/{n}\t{len(n)}\n" for i, (n, d) in enumerate(listed.items()))
    )
    return tmp_path


def test_a_bag_with_r2rs_file_ids(tmp_path):
    fileset = load_fileset(bag(tmp_path, {"b.txt": "2\n", "a.txt": "1\n"}))
    assert fileset.is_bag and [f.name for f in fileset.files] == ["a.txt", "b.txt"]
    assert [e.file_id for e in fileset.manifest] == ["101", "100"]
    assert verify_manifest(fileset) == []
    assert "BagIt bag" in str(fileset) and "2 nav files" in str(fileset)


def test_a_glob_selects_the_nav_files(tmp_path):
    root = bag(tmp_path, {"gps.raw": "x\n", "notes.log": "y\n"})
    assert [f.name for f in load_fileset(root, files="data/*.raw").files] == ["gps.raw"]


def test_bad_checksum_missing_and_unlisted_files(tmp_path):
    root = bag(tmp_path, {"a.txt": "1\n", "b.txt": "2\n"},
               manifest={"a.txt": md5("changed\n"), "c.txt": md5("3\n")})  # fmt: skip
    problems = verify_manifest(load_fileset(root))
    assert problems == [
        "data/a.txt: checksum doesn't match the manifest",
        "data/b.txt: not in the manifest",
        "data/c.txt: in the manifest but missing",
    ]


def test_a_plain_directory_with_an_external_manifest(tmp_path):
    (tmp_path / "gps").mkdir()
    (tmp_path / "gps" / "a.txt").write_text("1\n")
    (tmp_path / "md5.txt").write_text(f"{md5('1' + chr(10))} gps/a.txt\n**** gps/huge.txt\n")
    fileset = load_fileset(tmp_path / "gps", manifest_path=tmp_path / "md5.txt")
    assert not fileset.is_bag and [e.path for e in fileset.manifest] == ["a.txt"]
    assert verify_manifest(fileset) == []


def test_files_newer_than_an_external_manifest_arent_problems(tmp_path):
    (tmp_path / "md5.txt").write_text("")
    os.utime(tmp_path / "md5.txt", (time.time() - 60, time.time() - 60))
    (tmp_path / "a.txt").write_text("1\n")
    (tmp_path / "md5.txt").write_text(f"{md5('0' + chr(10))} other.txt\n")
    os.utime(tmp_path / "md5.txt", (time.time() - 60, time.time() - 60))
    problems = verify_manifest(load_fileset(tmp_path, manifest_path=tmp_path / "md5.txt"))
    assert problems == ["other.txt: in the manifest but missing"]


def test_no_data_directory(tmp_path):
    (tmp_path / "bagit.txt").write_text("")
    with pytest.raises(FileNotFoundError):
        load_fileset(tmp_path)
