#!/usr/bin/env python3
"""GeoLite2 Country MMDB -> GeoIP Legacy Country DAT (IPv4), Python 3.9+."""

import argparse
from array import array
from contextlib import ExitStack
import csv
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import ipaddress
import json
import logging
import os
from pathlib import Path
import shutil
import struct
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import base64


# ---------------------------------------------------------------------------
# Configuration: edit names and output variants here.
# prefer: registered_country or country; the other field is the fallback.
# ---------------------------------------------------------------------------
VERSION = "1.0.2.1"
DEFAULT_NAME = "GeoIP-Customname"
DEFAULT_OUTPUT_DIR = "{date}"
INTERMEDIATE_DIR = "tmp"
OUTPUTS = [
    {"dat": "GeoIP.dat", "text": "geoip", "csv": "GeoIPC.csv",
     "prefer": "registered_country"},
    {"dat": "GeoIP_country.dat", "text": "geoip_country",
     "csv": "GeoIPC_country.csv", "prefer": "country"},
]
DOWNLOAD_URL = (
    "https://download.maxmind.com/geoip/databases/GeoLite2-Country/"
    "download?suffix=tar.gz"
)
LEGACY_DOWNLOAD_URL = "https://download.maxmind.com/app/geoip_download"
MAX_SOURCE_BYTES = 128 * 1024 * 1024
HTTP_TIMEOUT = 60
COUNTRY_BEGIN = 16776960
METADATA_MARKER = b"\xab\xcd\xefMaxMind.com"

# Position-dependent Legacy IDs; this is not an alphabetical ISO country list.
# AN and FX are historical labels at the IDs now used for CW and SX.
COUNTRY_CODES = ("",) + tuple("""
AP EU AD AE AF AG AI AL AM AN AO AQ AR AS AT AU AW AZ BA BB BD BE BF
BG BH BI BJ BM BN BO BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM
CN CO CR CU CV CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK
FM FO FR FX GA GB GD GE GF GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM
HN HR HT HU ID IE IL IN IO IQ IR IS IT JM JO JP KE KG KH KI KM KN KP KR
KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD MG MH MK ML MM MN MO
MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM
PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RU RW SA SB SC SD SE
SG SH SI SJ SK SL SM SN SO SR ST SV SY SZ TC TD TF TG TH TJ TK TM TN TO
TL TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT RS
ZA ZM ME ZW A1 A2 O1 AX GG IM JE BL MF BQ SS
""".split())
COUNTRY_INDEX = {code: i for i, code in enumerate(COUNTRY_CODES)}
COUNTRY_INDEX.update({"--": 0, "CW": COUNTRY_INDEX["AN"],
                      "UK": COUNTRY_INDEX["GB"], "SX": COUNTRY_INDEX["FX"],
                      "XK": COUNTRY_INDEX["RS"]})


class ConversionError(ValueError):
    """Invalid input data or an unsupported database format."""


class Decoder:
    """Bounded decoder for the documented MMDB v2 data types."""

    def __init__(self, data, base, end):
        self.data, self.base, self.end = data, base, end

    def read(self, pos, size):
        if size < 0 or pos < self.base or pos + size > self.end:
            raise ConversionError("MMDB: data pointer is outside the data section")
        return self.data[pos:pos + size]

    def decode(self, pos, depth=0, budget=None):
        if budget is None:
            budget = [65536, 4 * 1024 * 1024]
        budget[0] -= 1
        if depth > 64 or budget[0] < 0:
            raise ConversionError("MMDB: data structure exceeds the depth or value limit")
        control = self.read(pos, 1)[0]
        pos += 1
        kind, size = control >> 5, control & 31
        if kind == 0:
            kind = self.read(pos, 1)[0] + 7
            pos += 1
        if kind == 1:
            width = (size >> 3) + 1
            ptr = int.from_bytes(self.read(pos, width), "big")
            if width < 4:
                ptr += (size & 7) << (width * 8)
                ptr += (0, 0, 2048, 526336)[width]
            value, _ = self.decode(self.base + ptr, depth + 1, budget)
            return value, pos + width
        if size >= 29:
            width = size - 28
            size = (0, 29, 285, 65821)[width] + int.from_bytes(
                self.read(pos, width), "big")
            pos += width
        if kind == 7:
            if size * 2 > budget[0]:
                raise ConversionError("MMDB: map exceeds the value limit")
            result = {}
            for _ in range(size):
                key, pos = self.decode(pos, depth + 1, budget)
                if not isinstance(key, str) or key in result:
                    raise ConversionError("MMDB: invalid or duplicate map key")
                result[key], pos = self.decode(pos, depth + 1, budget)
            return result, pos
        if kind == 11:
            if size > budget[0]:
                raise ConversionError("MMDB: array exceeds the value limit")
            result = []
            for _ in range(size):
                item, pos = self.decode(pos, depth + 1, budget)
                result.append(item)
            return result, pos
        if kind == 14:
            if size not in (0, 1):
                raise ConversionError("MMDB: invalid boolean value")
            return bool(size), pos
        budget[1] -= size
        if budget[1] < 0:
            raise ConversionError("MMDB: decoded payload exceeds the size limit")
        raw = self.read(pos, size)
        if kind == 2:
            value = raw.decode("utf-8")
        elif kind == 4:
            value = raw
        elif kind in (5, 6, 8, 9, 10):
            max_size = {5: 2, 6: 4, 8: 4, 9: 8, 10: 16}[kind]
            if size > max_size:
                raise ConversionError("MMDB: invalid integer length")
            value = int.from_bytes(raw, "big", signed=(kind == 8 and size == 4))
        elif kind in (3, 15) and size == (8 if kind == 3 else 4):
            value = struct.unpack(">d" if kind == 3 else ">f", raw)[0]
        else:
            raise ConversionError("MMDB: unsupported data type %s" % kind)
        return value, pos + size


def select_code(record, prefer):
    other = "country" if prefer == "registered_country" else "registered_country"
    for key, field in ((prefer, "iso_code"), (other, "iso_code"),
                       ("continent", "code")):
        value = record.get(key, {})
        if not isinstance(value, dict):
            raise ConversionError("MMDB: invalid country record")
        if field in value:
            code = value[field]
            if not isinstance(code, str):
                raise ConversionError("MMDB: country code is not a string")
            return code.upper() or None
    return None


class MaxMindDB:
    """Read a Country MMDB, including IPv4 records in an IPv6 ::/96 subtree."""

    def __init__(self, path):
        path = Path(path)
        if path.stat().st_size > MAX_SOURCE_BYTES:
            raise ConversionError("MMDB exceeds the configured size limit")
        self.data = path.read_bytes()
        marker = self.data.rfind(METADATA_MARKER, max(0, len(self.data) - 131072))
        if marker < 0:
            raise ConversionError("MMDB metadata is missing")
        start = marker + len(METADATA_MARKER)
        self.metadata, _ = Decoder(self.data, start, len(self.data)).decode(start)
        if not isinstance(self.metadata, dict):
            raise ConversionError("MMDB metadata is not a map")
        for key in ("node_count", "record_size", "ip_version",
                    "binary_format_major_version", "build_epoch"):
            if type(self.metadata.get(key)) is not int:
                raise ConversionError("MMDB: invalid metadata value for " + key)
        self.node_count = self.metadata["node_count"]
        self.record_size = self.metadata["record_size"]
        self.ip_version = self.metadata["ip_version"]
        if (self.metadata["binary_format_major_version"] != 2
                or self.record_size not in (24, 28, 32)
                or self.ip_version not in (4, 6) or self.node_count < 1):
            raise ConversionError("MMDB version, record size or IP version is not supported")
        if self.metadata.get("database_type") not in ("GeoLite2-Country", "GeoIP2-Country"):
            raise ConversionError("A GeoLite2-Country or GeoIP2-Country MMDB is required")
        self.node_bytes = self.record_size // 4
        self.tree_size = self.node_count * self.node_bytes
        if (self.tree_size + 16 > marker
                or self.data[self.tree_size:self.tree_size + 16] != bytes(16)):
            raise ConversionError("MMDB: invalid separator after the search tree")
        self.decoder = Decoder(self.data, self.tree_size + 16, marker)
        self.record = lru_cache(maxsize=8192)(self._record)

    def close(self):
        self.record.cache_clear()
        self.decoder.data = b""
        self.data = b""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def children(self, node):
        if not 0 <= node < self.node_count:
            raise ConversionError("MMDB: invalid search tree node")
        pos = node * self.node_bytes
        raw = self.data[pos:pos + self.node_bytes]
        if self.record_size == 28:
            return (int.from_bytes(raw[:3], "big") | ((raw[3] >> 4) << 24),
                    int.from_bytes(raw[4:], "big") | ((raw[3] & 15) << 24))
        width = self.record_size // 8
        return int.from_bytes(raw[:width], "big"), int.from_bytes(raw[width:], "big")

    def _record(self, pointer):
        value, _ = self.decoder.decode(self.tree_size + pointer - self.node_count)
        if not isinstance(value, dict):
            raise ConversionError("MMDB: search tree leaf is not a map")
        return value

    def ipv4_ranges(self, prefer="registered_country"):
        if prefer not in ("registered_country", "country"):
            raise ValueError("Invalid country selection priority")
        root = 0
        if self.ip_version == 6:
            for _ in range(96):
                if root >= self.node_count:
                    break
                root = self.children(root)[0]
        stack = [(root, 0, 0)]
        while stack:
            node, lo, depth = stack.pop()
            if node == self.node_count:
                continue
            if node > self.node_count:
                code = select_code(self.record(node), prefer)
                if code:
                    yield lo, lo + (1 << (32 - depth)) - 1, code
                continue
            if depth >= 32:
                raise ConversionError("MMDB: IPv4 search tree exceeds 32 bits")
            left, right = self.children(node)
            stack.append((right, lo + (1 << (31 - depth)), depth + 1))
            stack.append((left, lo, depth + 1))


def merge_ranges(ranges):
    previous = None
    for lo, hi, code in ranges:
        if previous and previous[1] + 1 == lo and previous[2] == code:
            previous = (previous[0], hi, code)
        else:
            if previous:
                yield previous
            previous = (lo, hi, code)
    if previous:
        yield previous


class LegacyCountryTree:
    """Six-byte nodes, little-endian 24-bit pointers, Country Edition 1."""

    def __init__(self, comment):
        self.comment = comment.encode("ascii")
        if len(self.comment) > 90 or any(b < 32 or b > 126 for b in self.comment):
            raise ValueError("Build comment must contain at most 90 printable ASCII characters")
        self.nodes = array("I", [COUNTRY_BEGIN, COUNTRY_BEGIN])
        self.last_end = -1
        self.netcount = 0
        self.unknown_codes = set()

    def add_range(self, lo, hi, code):
        if not 0 <= lo <= hi <= 0xffffffff or lo <= self.last_end:
            raise ValueError("IPv4 ranges must be valid, sorted and non-overlapping")
        self.last_end = hi
        code = code.upper()
        if code not in COUNTRY_INDEX:
            self.unknown_codes.add(code)
        leaf = COUNTRY_BEGIN + COUNTRY_INDEX.get(code, 0)
        for net in ipaddress.summarize_address_range(
                ipaddress.IPv4Address(lo), ipaddress.IPv4Address(hi)):
            self.netcount += 1
            inet, prefix = int(net.network_address), net.prefixlen
            if prefix == 0:
                self.nodes[0:2] = array("I", [leaf, leaf])
                continue
            node = 0
            for depth in range(prefix):
                slot = node * 2 + ((inet >> (31 - depth)) & 1)
                if depth == prefix - 1:
                    self.nodes[slot] = leaf
                else:
                    child = self.nodes[slot]
                    if child >= COUNTRY_BEGIN:
                        child = len(self.nodes) // 2
                        if child >= COUNTRY_BEGIN:
                            raise ConversionError("Too many nodes for the Legacy Country format")
                        self.nodes.extend((COUNTRY_BEGIN, COUNTRY_BEGIN))
                        self.nodes[slot] = child
                    node = child

    def write(self, path):
        count = len(self.nodes) // 2
        with Path(path).open("wb") as output:
            block = bytearray()
            for pointer in self.nodes:
                block.extend(pointer.to_bytes(3, "little"))
                if len(block) >= 65532:
                    output.write(block)
                    block.clear()
            output.write(block)
            output.write(bytes(3) + self.comment + b"\xff\xff\xff\x01"
                         + count.to_bytes(3, "little"))


def extract_mmdb(archive, destination):
    """Copy the single regular MMDB member without extracting archive paths."""
    with tarfile.open(archive, "r:gz") as source:
        members = [m for m in source.getmembers()
                   if m.name.endswith("/GeoLite2-Country.mmdb")
                   or m.name == "GeoLite2-Country.mmdb"]
        if len(members) != 1 or not members[0].isfile():
            raise ConversionError("Archive must contain exactly one regular GeoLite2-Country.mmdb file")
        if members[0].size > MAX_SOURCE_BYTES:
            raise ConversionError("Archived MMDB exceeds the size limit")
        with source.extractfile(members[0]) as src, Path(destination).open("wb") as dst:
            shutil.copyfileobj(src, dst)


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise ConversionError("Download: redirect to a non-HTTPS URL was rejected")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_database(destination, account_id, license_key):
    if not license_key:
        raise ConversionError("Download requires a license key: positional argument, "
                              "MAXMIND_LICENSE_KEY or --license-key-file")
    if account_id:
        url = DOWNLOAD_URL
    else:
        # License-only download endpoint.
        url = LEGACY_DOWNLOAD_URL + "?" + urllib.parse.urlencode({
            "edition_id": "GeoLite2-Country", "license_key": license_key,
            "suffix": "tar.gz"})
    req = urllib.request.Request(url, headers={"User-Agent": "geoip-convert/" + VERSION})
    if account_id:
        auth = base64.b64encode((account_id + ":" + license_key).encode()).decode("ascii")
        # urllib does not forward this header when following redirects.
        req.add_unredirected_header("Authorization", "Basic " + auth)
    opener = urllib.request.build_opener(SafeRedirect())
    try:
        with opener.open(req, timeout=HTTP_TIMEOUT) as src, Path(destination).open("wb") as dst:
            size = 0
            while True:
                block = src.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if size > MAX_SOURCE_BYTES:
                    raise ConversionError("Download exceeds the size limit")
                dst.write(block)
    except urllib.error.HTTPError as exc:
        raise ConversionError("MaxMind download failed: HTTP %s" % exc.code) from None
    except (urllib.error.URLError, TimeoutError):
        raise ConversionError("MaxMind download failed: check the network, TLS settings and timeout") from None


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_outputs():
    if (Path(INTERMEDIATE_DIR).name != INTERMEDIATE_DIR
            or INTERMEDIATE_DIR in (".", "..", "")):
        raise ConversionError("INTERMEDIATE_DIR must be a plain directory name")
    names = ["build.json"]
    for entry in OUTPUTS:
        if entry["prefer"] not in ("country", "registered_country"):
            raise ConversionError("OUTPUTS: invalid country selection priority")
        if entry["dat"] == INTERMEDIATE_DIR:
            raise ConversionError("OUTPUTS: a DAT file cannot use the intermediate directory name")
        names.extend(entry[key] for key in ("dat", "text", "csv"))
    if not OUTPUTS or len(names) != len(set(names)):
        raise ConversionError("OUTPUTS: no output variants or duplicate file names")
    for name in names:
        if Path(name).name != name or name in (".", "..", ""):
            raise ConversionError("OUTPUTS: only plain file names are allowed")
    return names


def convert(mmdb, stage, name, date, intermediates=True):
    stats = []
    with MaxMindDB(mmdb) as source:
        metadata = source.metadata
        for entry in OUTPUTS:
            start = time.monotonic()
            tree = LegacyCountryTree("%s %s Build" % (name, date))
            ranges = 0
            with ExitStack() as stack:
                if intermediates:
                    text = stack.enter_context((stage / entry["text"]).open("w", encoding="utf-8", newline=""))
                    csv_file = stack.enter_context((stage / entry["csv"]).open("w", encoding="utf-8", newline=""))
                    writer = csv.writer(csv_file, quoting=csv.QUOTE_ALL, lineterminator="\n")
                    updated = datetime.fromtimestamp(metadata["build_epoch"], timezone.utc)
                    text.write("# Last updated based on %s Maxmind GeoLite2 Country\n" % updated.strftime("%B %d %Y"))
                for lo, hi, code in merge_ranges(source.ipv4_ranges(entry["prefer"])):
                    tree.add_range(lo, hi, code)
                    ranges += 1
                    if intermediates:
                        text.write("%d,%d,%s\n" % (lo, hi, code))
                        writer.writerow((str(ipaddress.IPv4Address(lo)),
                                         str(ipaddress.IPv4Address(hi)), lo, hi, code, ""))
            if not ranges:
                raise ConversionError("MMDB contains no usable IPv4 country records")
            tree.write(stage / entry["dat"])
            if tree.unknown_codes:
                logging.warning("%s: unknown codes mapped to ID 0: %s", entry["dat"],
                                ", ".join(sorted(tree.unknown_codes)))
            item = {"file": entry["dat"], "prefer": entry["prefer"],
                    "ranges": ranges, "networks": tree.netcount,
                    "nodes": len(tree.nodes) // 2,
                    "sha256": sha256(stage / entry["dat"]),
                    "unknown_codes": sorted(tree.unknown_codes)}
            stats.append(item)
            logging.info("%s: %d ranges, %d networks, %d nodes (%.1f s)",
                         item["file"], ranges, tree.netcount, item["nodes"],
                         time.monotonic() - start)
    report = {"converter_version": VERSION, "build_name": name, "build_date": date,
              "source_database_type": metadata["database_type"],
              "source_build_epoch": metadata["build_epoch"],
              "source_sha256": sha256(mmdb), "outputs": stats}
    (stage / "build.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("license_key", nargs="?", help="MaxMind license key")
    parser.add_argument("custom_name", nargs="?", help="Optional build name")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--mmdb", type=Path, help="Existing Country MMDB; skip downloading")
    source.add_argument("--archive", type=Path, help="Existing GeoLite2-Country.tar.gz; skip downloading")
    parser.add_argument("--name", help="Build name in the DAT comment")
    parser.add_argument("--build-date", default=datetime.now().strftime("%Y%m%d"), metavar="YYYYMMDD")
    parser.add_argument("--output-dir", type=Path, help="Output directory (default: YYYYMMDD)")
    parser.add_argument("--account-id", default=os.environ.get("MAXMIND_ACCOUNT_ID", ""),
                        help="Optional: use Basic Authentication instead of the license-only URL")
    parser.add_argument("--license-key-file", type=Path, help="File containing the MaxMind license key")
    parser.add_argument("--dat-only", action="store_true", help="Skip intermediate text and CSV files")
    parser.add_argument("--force", action="store_true", help="Replace existing output files")
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args(argv)
    if args.license_key and args.license_key_file:
        parser.error("Provide the license key as a positional argument or a file, not both")
    args.name = args.name or args.custom_name or DEFAULT_NAME
    return args


def print_output_explanation():
    """Describe the generated variants after all output files are published."""
    lines = ["", "Generated database variants:"]
    for entry in OUTPUTS:
        prefer = entry["prefer"]
        other = "country" if prefer == "registered_country" else "registered_country"
        if prefer == "registered_country":
            description = "Prefers the country where the ISP registered the network."
        else:
            description = "Prefers MaxMind's estimated geographic country of the IP address."
        lines.extend([
            "", entry["dat"] + ":", "  " + description,
            "  Selection order: %s.iso_code -> %s.iso_code -> continent.code."
            % (prefer, other),
        ])
    lines.extend([
        "", "The variants can differ when registration and geographic country differ.",
        "Neither variant guarantees the actual location of a device or user.",
        "The Legacy continent fallback can collide with country codes (for example, NA = Namibia).",
        "Unassigned addresses and codes absent from the Legacy table use country ID 0.",
    ])
    print("\n".join(lines), flush=True)


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        try:
            if len(args.build_date) != 8:
                raise ValueError
            datetime.strptime(args.build_date, "%Y%m%d")
        except ValueError:
            raise ConversionError("--build-date must be a valid date in YYYYMMDD format") from None
        LegacyCountryTree("%s %s Build" % (args.name, args.build_date))
        names = validate_outputs()
        if args.dat_only:
            names = [e["dat"] for e in OUTPUTS] + ["build.json"]
        dat_files = {entry["dat"] for entry in OUTPUTS}
        destinations = {
            name: Path(name) if name in dat_files else Path(INTERMEDIATE_DIR) / name
            for name in names
        }
        output = (args.output_dir or Path(DEFAULT_OUTPUT_DIR.format(date=args.build_date))).absolute()
        output.mkdir(parents=True, exist_ok=True)
        intermediate = output / INTERMEDIATE_DIR
        if intermediate.is_symlink() or (intermediate.exists() and not intermediate.is_dir()):
            raise ConversionError("Intermediate output path must be a directory, not a file or symlink")
        conflicts = [n for n in names if os.path.lexists(output / destinations[n])]
        if conflicts and not args.force:
            raise ConversionError("Output files already exist; use --force to replace them")
        inputs = [p.resolve() for p in (args.mmdb, args.archive) if p]
        if any((output / destinations[n]).resolve() in inputs for n in names):
            raise ConversionError("An output file would overwrite the input file")
        with tempfile.TemporaryDirectory(prefix=".geoip-build-", dir=output) as temporary:
            stage = Path(temporary)
            mmdb = args.mmdb
            if mmdb is None:
                archive = args.archive
                if archive is None:
                    archive = stage / "download.tar.gz"
                    key = (args.license_key_file.read_text(encoding="utf-8").strip()
                           if args.license_key_file else args.license_key
                           or os.environ.get("MAXMIND_LICENSE_KEY", ""))
                    logging.info("Downloading GeoLite2-Country")
                    download_database(archive, args.account_id, key)
                mmdb = stage / "source.mmdb"
                extract_mmdb(archive, mmdb)
            convert(mmdb, stage, args.name, args.build_date, not args.dat_only)
            intermediate.mkdir(exist_ok=True)
            # Publish only after conversion succeeds. Each file is replaced
            # atomically; the complete file set is not a transaction.
            for filename in names:
                destination = output / destinations[filename]
                if args.force:
                    os.replace(stage / filename, destination)
                else:
                    # Do not overwrite files created by a concurrent invocation.
                    os.link(stage / filename, destination)
            logging.info("Completed: %s", output)
        print_output_explanation()
        return 0
    except (OSError, ValueError, OverflowError, tarfile.TarError) as exc:
        logging.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        logging.error("Interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
