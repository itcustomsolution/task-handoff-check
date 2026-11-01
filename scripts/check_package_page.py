#!/usr/bin/env python3
"""Validate the actual wheel description before uploading it (Python 3.11+)."""

import argparse
import email.policy
import re
import sys
import tomllib
import urllib.parse
import urllib.request
import warnings
import zipfile
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path

from readme_renderer.markdown import render


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = set()
        self.ids = set()
        self.code = []
        self.code_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        for key in ("href", "src"):
            if key in attrs:
                self.links.add(attrs[key])
        if tag in ("pre", "code"):
            self.code_depth += 1

    def handle_endtag(self, tag):
        if tag in ("pre", "code"):
            self.code_depth = max(0, self.code_depth - 1)
            if not self.code_depth:
                self.code.append("\n")

    def handle_data(self, data):
        if self.code_depth:
            self.code.append(data)


def check_description(description, name, version, root):
    """Return rendered HTML and absolute links, or reject an unusable page."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rendered = render(description)
    if not rendered or caught:
        raise ValueError("Markdown rendering failed; install requirements/docs.txt")
    page = Page()
    page.feed(rendered)
    remote = set()
    for link in sorted(page.links):
        url = urllib.parse.urlsplit(link)
        if link.startswith("#"):
            if urllib.parse.unquote(url.fragment) not in page.ids:
                raise ValueError(f"broken description anchor: {link}")
            continue
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError(f"link must be absolute HTTPS on both PyPI and GitHub: {link}")
        prefix = f"/itcustomsolution/{name}/blob/main/"
        if url.hostname == "github.com" and url.path.startswith(prefix):
            target = (root / urllib.parse.unquote(url.path[len(prefix):])).resolve()
            if not target.is_relative_to(root.resolve()) or not target.is_file():
                raise ValueError(f"missing repository documentation: {link}")
        remote.add(link)
    pins = re.findall(rf"\b{re.escape(name)}(?:\[[^\]]+\])?==([^\s'\";]+)", "".join(page.code))
    if any(pin != version for pin in pins):
        raise ValueError(f"stale installation pin: {pins}; wheel version is {version}")
    return rendered, remote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--html", type=Path, help="save the rendered package description")
    parser.add_argument("--online", action="store_true", help="also GET every external link; requires network")
    args = parser.parse_args()
    project = tomllib.loads((args.root / "pyproject.toml").read_text())["project"]
    with zipfile.ZipFile(args.wheel) as archive:
        metadata_files = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
        if len(metadata_files) != 1:
            raise ValueError("expected exactly one wheel METADATA file")
        metadata = BytesParser(policy=email.policy.default).parsebytes(archive.read(metadata_files[0]))
    if metadata["Name"] != project["name"] or metadata["Version"] != project["version"]:
        raise ValueError("wheel name/version differ from pyproject.toml")
    if metadata["Description-Content-Type"] != "text/markdown":
        raise ValueError("wheel description must declare text/markdown")
    description = metadata.get_payload(decode=True).decode("utf-8")
    if description.strip() != (args.root / project["readme"]).read_text().strip():
        raise ValueError("wheel description differs from the reviewed README")
    rendered, links = check_description(description, project["name"], project["version"], args.root)
    for header in metadata.get_all("Project-URL", []):
        url = header.split(",", 1)[1].strip()
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError(f"invalid project URL: {url}")
        links.add(url)
    if args.html:
        args.html.write_text(rendered)
    if args.online:
        for url in sorted(links):
            request = urllib.request.Request(urllib.parse.urldefrag(url)[0], headers={"User-Agent": "ITC-package-page-check/1.0"})
            with urllib.request.urlopen(request, timeout=20) as response:
                if response.status != 200:
                    raise ValueError(f"link returned {response.status}: {url}")
            print(f"OK {url}")
    print(f"PASS {project['name']} {project['version']}: rendered wheel description; {len(links)} HTTPS links" + (" checked online" if args.online else " (online availability not checked)"))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        sys.exit(f"FAIL: {exc}")
