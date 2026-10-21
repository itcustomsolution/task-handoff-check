"""Proposed offline release-metadata validator. Prepared, not executed.

Reads public source and archives as data. It imports no candidate package and
does not extract, install, publish, repair, or rename an archive.
"""
import base64
import configparser
import csv
from email.parser import BytesParser
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import stat
import sys
import tarfile
import zipfile

MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_EXPANDED_BYTES = 16 * 1024 * 1024
MAX_MEMBERS = 128
RC_PINS = {
    "task_handoff_check-0.1.1rc3-py3-none-any.whl": "a189c3420a8c62e0e77bcacac25aa6cf8f7b7c9b5542279f07ff7a3a9199b076",
    "task_handoff_check-0.1.1rc3.tar.gz": "20dba2b8396b14963f055b327ce554f7b4ed6ca87b6a3ec71d1aa3f9064af258",
    "delivery_evidence_check-0.2.0rc2-py3-none-any.whl": "161e296598a9cff6fcbce07e09798b27503ba57ae21ef101175346177d6144fd",
    "delivery_evidence_check-0.2.0rc2.tar.gz": "bbf043a66056f64f2830bccc0d476625736dc70fed3431baa2b8911f4088791d",
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_name(name):
    path = PurePosixPath(name)
    require(bool(name) and not path.is_absolute() and ".." not in path.parts
            and "\\" not in name, "unsafe archive path: " + name)


def archive(path):
    require(path.is_file() and not path.is_symlink(), "not an ordinary archive")
    require(path.stat().st_size <= MAX_ARCHIVE_BYTES, "archive byte ceiling")
    members = {}
    expanded = 0
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as stream:
            info = stream.infolist()
            require(len(info) <= MAX_MEMBERS, "wheel member ceiling")
            for item in info:
                safe_name(item.filename)
                require(not item.is_dir(), "unexpected wheel directory")
                require(not item.flag_bits & 1, "encrypted wheel member")
                mode = item.external_attr >> 16
                require(not stat.S_ISLNK(mode), "wheel link")
                require(item.filename not in members, "duplicate wheel member")
                expanded += item.file_size
                require(expanded <= MAX_EXPANDED_BYTES, "expanded wheel ceiling")
                members[item.filename] = stream.read(item)
    else:
        with tarfile.open(path, "r:gz") as stream:
            seen = set()
            for count, item in enumerate(stream, 1):
                require(count <= MAX_MEMBERS, "sdist member ceiling")
                safe_name(item.name)
                require(item.name not in seen, "duplicate sdist member")
                seen.add(item.name)
                require(item.isdir() or item.isfile(), "sdist special/link member")
                if item.isdir():
                    continue
                expanded += item.size
                require(expanded <= MAX_EXPANDED_BYTES, "expanded sdist ceiling")
                members[item.name] = stream.extractfile(item).read()
    return members


def metadata(data, name, version):
    parsed = BytesParser().parsebytes(data)
    require(parsed.get_all("Name") == [name], "metadata name")
    require(parsed.get_all("Version") == [version], "metadata version")
    require(parsed.get_all("Requires-Python") == [">=3.10"], "Python requirement")
    require(not parsed.get_all("Requires-Dist"), "unexpected runtime dependency")
    return parsed


def check_record(data, members, record_name):
    rows = list(csv.reader(io.StringIO(data.decode())))
    require(all(len(row) == 3 for row in rows), "malformed RECORD")
    require(len(rows) == len(members), "RECORD count")
    require(len({row[0] for row in rows}) == len(rows), "duplicate RECORD row")
    require({row[0] for row in rows} == set(members), "RECORD membership")
    for name, pin, size in rows:
        if name == record_name:
            require(pin == size == "", "RECORD self fields")
        else:
            raw = members[name]
            encoded = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")
            require(pin == "sha256=" + encoded and size == str(len(raw)), "RECORD binding: " + name)


def check_tool(proposal, source_root, dist_root, rc_root):
    name = proposal["tool"]
    package = proposal["runtime_package"]
    version = proposal["stable_target"]
    old = proposal["from"]
    files = {item["path"]: item for item in proposal["files"]}
    source = source_root / name
    for rel, item in files.items():
        path = source / rel
        require(path.is_file() and not path.is_symlink(), "source member type")
        require(digest(path.read_bytes()) == item["prepared_stable_sha256"], "prepared source drift: " + rel)
    require({str(p.relative_to(source)) for p in source.rglob("*") if p.is_file()} == set(files), "source extras/missing")
    python_files = {rel for rel in files if rel.startswith(package + "/") and rel.endswith(".py")}
    require(len(python_files) == 3, "runtime member count")
    result = {"tool": name, "version": version, "artifacts": []}
    for suffix in ("-py3-none-any.whl", ".tar.gz"):
        artifact_name = package + "-" + version + suffix
        rc_name = package + "-" + old + suffix
        candidate = dist_root / name / artifact_name
        rc = rc_root / rc_name
        require(digest(rc.read_bytes()) == RC_PINS[rc_name], "audited RC hash drift")
        current = archive(candidate)
        previous = archive(rc)
        current_prefix = package + "-" + version
        old_prefix = package + "-" + old
        if suffix.endswith(".whl"):
            prefix = current_prefix + ".dist-info/"
            old_prefix += ".dist-info/"
            generated = {prefix + x for x in ("licenses/LICENSE", "METADATA", "WHEEL", "entry_points.txt", "top_level.txt", "RECORD")}
            require(set(current) == python_files | generated, "unexpected wheel payload")
            check_record(current[prefix + "RECORD"], current, prefix + "RECORD")
            metadata(current[prefix + "METADATA"], name, version)
            metadata(previous[old_prefix + "METADATA"], name, old)
            require(current[prefix + "METADATA"] == previous[old_prefix + "METADATA"].replace(
                ("\nVersion: " + old + "\n").encode(), ("\nVersion: " + version + "\n").encode(), 1), "metadata differs beyond version")
            for rel in ("WHEEL", "entry_points.txt", "top_level.txt", "licenses/LICENSE"):
                require(current[prefix + rel] == previous[old_prefix + rel], "wheel metadata delta: " + rel)
            wheel_metadata = BytesParser().parsebytes(current[prefix + "WHEEL"])
            require(wheel_metadata["Root-Is-Purelib"] == "true" and wheel_metadata.get_all("Tag") == ["py3-none-any"], "wheel platform tag")
            entry = configparser.ConfigParser()
            entry.read_string(current[prefix + "entry_points.txt"].decode())
            require(entry.sections() == ["console_scripts"] and dict(entry["console_scripts"]) == {name: package + ".__main__:main"}, "entry point")
            require(current[prefix + "licenses/LICENSE"] == (source / "LICENSE").read_bytes(), "wheel license")
        else:
            current_prefix += "/"
            old_prefix += "/"
            require(all(x.startswith(current_prefix) for x in current), "sdist root")
            require(all(x.startswith(old_prefix) for x in previous), "RC sdist root")
            current = {x[len(current_prefix):]: data for x, data in current.items()}
            previous = {x[len(old_prefix):]: data for x, data in previous.items()}
            require(set(current) == set(previous), "sdist payload inventory delta")
            for rel, data in current.items():
                if rel in files:
                    require(data == (source / rel).read_bytes(), "sdist source delta: " + rel)
                elif rel in ("PKG-INFO", package + ".egg-info/PKG-INFO"):
                    metadata(data, name, version)
                    require(data == previous[rel].replace(("\nVersion: " + old + "\n").encode(), ("\nVersion: " + version + "\n").encode(), 1), "sdist metadata delta")
                else:
                    require(data == previous[rel], "sdist generated delta: " + rel)
        for rel in python_files:
            require(current[rel] == (source / rel).read_bytes(), "runtime differs from prepared source")
            require(digest(previous[rel]) == files[rel]["audited_rc_sha256"], "RC runtime source binding")
            if rel.endswith("/__init__.py"):
                require(current[rel] == previous[rel].replace(("__version__ = '" + old + "'").encode(), ("__version__ = '" + version + "'").encode(), 1), "init differs beyond version")
            else:
                require(current[rel] == previous[rel], "tested implementation changed")
        result["artifacts"].append({"path": str(candidate), "sha256": digest(candidate.read_bytes()), "bytes": candidate.stat().st_size, "member_count": len(current)})
    return result


def main():
    require(len(sys.argv) == 6, "usage: validator MANIFEST SOURCE_ROOT DIST_ROOT RC_ROOT NEW_RECEIPT")
    manifest, source, dist, rc, receipt = map(Path, sys.argv[1:])
    require(not receipt.exists(), "existing receipt; reconcile instead of replay")
    data = json.loads(manifest.read_text())
    require(len(data["proposals"]) == 2, "proposal count")
    expected = {str(Path(p["tool"]) / f) for p in data["proposals"] for f in p["expected_artifacts"]}
    require({str(p.relative_to(dist)) for p in dist.rglob("*") if p.is_file()} == expected, "dist extras/missing")
    results = [check_tool(p, source, dist, rc) for p in data["proposals"]]
    receipt.parent.mkdir(parents=True, exist_ok=True)
    with receipt.open("x") as stream:
        json.dump({"status": "ARTIFACT_METADATA_AND_PAYLOAD_BOUND", "tools": results,
                   "full_suites_rerun": False, "installed_cli_smokes": "separate exact-command receipts required",
                   "published": False}, stream, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
