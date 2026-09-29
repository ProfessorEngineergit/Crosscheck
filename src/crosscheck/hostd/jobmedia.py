"""Job CD (read-only, into the VM) and results disk (raw, out of the VM).

The results disk has no filesystem. The guest writes snapshots of one tar archive into two
alternating slots, so a VM that is stopped mid-write still leaves the previous snapshot intact:

    offset 0 and 512: slot headers  b"CCRESULT2\n" <seq:16 hex>\n <len:16 hex>\n <sha256:64 hex>\n
    slot 0 data at DATA_OFFSET, slot 1 data at DATA_OFFSET + SLOT_SIZE

hostd never mounts the disk and never parses the archive. It returns the newest slot whose
checksum matches, and the controller parses it in a resource-limited worker (crosscheck.evaluate).
"""

import hashlib
import io
import json
from pathlib import Path

import pycdlib

RESULT_MAGIC = b"CCRESULT2\n"
HEADER_LEN = len(RESULT_MAGIC) + 17 + 17 + 65
HEADER_OFFSETS = (0, 512)
DATA_OFFSET = 4096
RESULTS_DISK_SERIAL = "ccresults"
JOB_VOLUME_LABEL = "CCJOB"


def _iso_name(name: str) -> str:
    base = name.upper().replace("-", "_").replace(".", "_")[:8]
    return f"/{base}.;1"


def build_job_iso(path: Path, files: dict[str, bytes]) -> None:
    """Write an ISO 9660 image with Joliet and Rock Ridge names."""
    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, joliet=3, rock_ridge="1.09", vol_ident=JOB_VOLUME_LABEL)
    used = set()
    for name, data in files.items():
        if "/" in name or name.startswith("."):
            raise ValueError(f"invalid job file name {name!r}")
        iso_name = _iso_name(name)
        n = 0
        while iso_name in used:
            n += 1
            iso_name = f"/F{n:07d}.;1"
        used.add(iso_name)
        iso.add_fp(io.BytesIO(data), len(data), iso_name, rr_name=name, joliet_path=f"/{name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    iso.write(str(path))
    iso.close()


def read_job_iso(path: Path) -> dict[str, bytes]:
    """Used by tests and the fake backend."""
    iso = pycdlib.PyCdlib()
    iso.open(str(path))
    out = {}
    for child in iso.list_children(joliet_path="/"):
        if child.is_dir():
            continue
        name = child.file_identifier().decode("utf-16-be").split(";")[0]
        buf = io.BytesIO()
        iso.get_file_from_iso_fp(buf, joliet_path=f"/{name}")
        out[name] = buf.getvalue()
    iso.close()
    return out


def create_results_disk(path: Path, size_mb: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.truncate(size_mb * 1024 * 1024)


def slot_size(disk_bytes: int) -> int:
    return (disk_bytes - DATA_OFFSET) // 2


def _parse_header(raw: bytes) -> tuple[int, int, str] | None:
    if not raw.startswith(RESULT_MAGIC):
        return None
    try:
        body = raw[len(RESULT_MAGIC) : HEADER_LEN].decode("ascii").split("\n")
        return int(body[0], 16), int(body[1], 16), body[2]
    except (UnicodeDecodeError, ValueError, IndexError):
        return None


def read_results_disk(path: Path, max_bytes: int) -> bytes | None:
    """Return the newest valid archive snapshot written by the guest, or None."""
    size = path.stat().st_size if path.is_file() else _block_size(path)
    ssize = slot_size(size)
    best: tuple[int, bytes] | None = None
    with open(path, "rb") as fh:
        for slot, hoff in enumerate(HEADER_OFFSETS):
            fh.seek(hoff)
            hdr = _parse_header(fh.read(HEADER_LEN))
            if not hdr:
                continue
            seq, length, digest = hdr
            if length <= 0 or length > min(max_bytes, ssize):
                continue
            fh.seek(DATA_OFFSET + slot * ssize)
            data = fh.read(length)
            if len(data) != length or hashlib.sha256(data).hexdigest() != digest:
                continue
            if best is None or seq > best[0]:
                best = (seq, data)
    return best[1] if best else None


def _block_size(path: Path) -> int:
    with open(path, "rb") as fh:
        return fh.seek(0, 2)


def write_results_slot(fh, disk_bytes: int, seq: int, archive: bytes) -> None:
    """Reference writer, mirrored by the guest runner. Data first, header last."""
    ssize = slot_size(disk_bytes)
    if len(archive) > ssize:
        raise ValueError("archive larger than slot")
    slot = seq % 2
    fh.seek(DATA_OFFSET + slot * ssize)
    fh.write(archive)
    fh.flush()
    header = (
        RESULT_MAGIC + b"%016x\n%016x\n" % (seq, len(archive)) + hashlib.sha256(archive).hexdigest().encode() + b"\n"
    )
    fh.seek(HEADER_OFFSETS[slot])
    fh.write(header)
    fh.flush()


def job_manifest(**fields) -> bytes:
    return json.dumps(fields, indent=2, sort_keys=True).encode()
