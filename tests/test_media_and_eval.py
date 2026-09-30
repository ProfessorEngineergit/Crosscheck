import io
import tarfile

from crosscheck.evaluate import _parse, evaluate
from crosscheck.hostd.jobmedia import (
    DATA_OFFSET,
    build_job_iso,
    create_results_disk,
    read_job_iso,
    read_results_disk,
    slot_size,
    write_results_slot,
)


def tar_bytes(files, extra=None):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        for info in extra or []:
            tf.addfile(info)
    return buf.getvalue()


def test_iso_roundtrip(tmp_path):
    files = {"job.json": b'{"kind":"smoke"}', "source.tar.gz": b"x" * 5000, "crosscheck-guest.py": b"print(1)"}
    build_job_iso(tmp_path / "job.iso", files)
    assert read_job_iso(tmp_path / "job.iso") == files


def test_results_disk_double_buffer(tmp_path):
    disk = tmp_path / "r.img"
    create_results_disk(disk, 4)
    size = disk.stat().st_size
    assert read_results_disk(disk, 1 << 20) is None
    with open(disk, "r+b") as fh:
        write_results_slot(fh, size, 1, b"first")
        write_results_slot(fh, size, 2, b"second")
    assert read_results_disk(disk, 1 << 20) == b"second"
    # a torn write into slot 0 (seq 2) leaves the checksum invalid: the older snapshot wins
    with open(disk, "r+b") as fh:
        fh.seek(DATA_OFFSET + 0 * slot_size(size))
        fh.write(b"XXXXXX")
    assert read_results_disk(disk, 1 << 20) == b"first"


def test_evaluate_whitelist_and_limits():
    link = tarfile.TarInfo("status.json")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/shadow"
    archive = tar_bytes(
        {"build.log": b"ok", "evil.sh": b"rm -rf /", "crash/core.1": b"\x7fELF", "findings.json": b'[{"title": "x"}]'},
        extra=[link],
    )
    out = _parse(archive)
    assert out["files"]["build.log"] == "ok"
    assert "evil.sh" not in out["files"] and "status.json" not in out["files"]
    assert out["crash"][0]["name"] == "core.1"
    assert out["json"]["findings.json"][0]["title"] == "x"
    assert out["ignored"] == 2


def test_evaluate_subprocess_handles_garbage():
    assert evaluate(b"not a tar at all")["errors"]
    assert evaluate(b"")["errors"] == ["no results written"]
    ok = evaluate(tar_bytes({"status.json": b'{"build": {"ok": true}}'}))
    assert ok["json"]["status.json"]["build"]["ok"] is True
