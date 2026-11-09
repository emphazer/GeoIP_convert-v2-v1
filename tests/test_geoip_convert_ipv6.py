"""Tests for IPv6 Country MMDB to Legacy Country Edition 12 conversion."""

import ipaddress
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


RELEASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RELEASE_DIR))
import geoip_convert_ipv6 as converter


COUNTRY_BEGIN = 16776960
MAX_IPV6 = (1 << 128) - 1


def read_country_id(contents, address):
    """Independent 128-bit lookup over little-endian 24-bit DAT pointers."""
    address = int(ipaddress.IPv6Address(address))
    node = 0
    for bit in range(127, -1, -1):
        offset = node * 6 + 3 * ((address >> bit) & 1)
        pointer = contents[offset:offset + 3]
        if len(pointer) != 3:
            raise AssertionError("DAT pointer is outside the file")
        pointer = int.from_bytes(pointer, "little")
        if pointer >= COUNTRY_BEGIN:
            return pointer - COUNTRY_BEGIN
        node = pointer
    raise AssertionError("No country leaf after 128 address bits")


def encode_mmdb_value(value):
    """Encode only the MMDB types needed by small synthetic fixtures."""
    if isinstance(value, str):
        raw = value.encode("utf-8")
        if len(raw) >= 29:
            raise ValueError("Fixture string is too long")
        return bytes([0x40 + len(raw)]) + raw
    if isinstance(value, int):
        width = (value.bit_length() + 7) // 8
        kind = 5 if width <= 2 else 6
        return bytes([(kind << 5) + width]) + value.to_bytes(width, "big")
    if isinstance(value, list):
        return bytes([len(value), 4]) + b"".join(map(encode_mmdb_value, value))
    if isinstance(value, dict):
        return bytes([0xe0 + len(value)]) + b"".join(
            encode_mmdb_value(key) + encode_mmdb_value(item)
            for key, item in value.items()
        )
    raise TypeError(type(value))


def pack_mmdb(nodes, records, record_size=24, ip_version=6):
    node_count = len(nodes)
    data = bytearray()
    offsets = []
    for record in records:
        offsets.append(len(data))
        data.extend(encode_mmdb_value(record))

    def resolve(value):
        if value is None:
            return node_count
        if isinstance(value, tuple):
            return node_count + 16 + offsets[value[1]]
        return value

    tree = bytearray()
    for left, right in nodes:
        left, right = resolve(left), resolve(right)
        if record_size == 24:
            tree.extend(left.to_bytes(3, "big") + right.to_bytes(3, "big"))
        elif record_size == 28:
            tree.extend((left & 0xffffff).to_bytes(3, "big"))
            tree.append(((left >> 24) << 4) | (right >> 24))
            tree.extend((right & 0xffffff).to_bytes(3, "big"))
        elif record_size == 32:
            tree.extend(left.to_bytes(4, "big") + right.to_bytes(4, "big"))
        else:
            raise ValueError(record_size)
    metadata = {
        "binary_format_major_version": 2,
        "binary_format_minor_version": 0,
        "build_epoch": 1704067200,
        "database_type": "GeoLite2-Country",
        "description": {"en": "IPv6 test fixture"},
        "ip_version": ip_version,
        "languages": ["en"],
        "node_count": node_count,
        "record_size": record_size,
    }
    return bytes(tree) + bytes(16) + bytes(data) + b"\xab\xcd\xefMaxMind.com" + encode_mmdb_value(metadata)


def make_mmdb(record_size=24, ip_version=6):
    records = [
        {"registered_country": {"iso_code": "US"}, "country": {"iso_code": "DE"}},
        {"country": {"iso_code": "FR"}},
    ]
    return pack_mmdb([[("record", 0), ("record", 1)]], records, record_size, ip_version)


def make_alias_mmdb():
    """Three prefixes point to the same two-leaf subtree at different depths."""
    nodes = [[None, None], [("record", 0), ("record", 1)]]
    for prefix in ("::/96", "::ffff:0:0/96", "2002::/16"):
        network = ipaddress.IPv6Network(prefix)
        address = int(network.network_address)
        node = 0
        for depth in range(network.prefixlen):
            bit = (address >> (127 - depth)) & 1
            if depth == network.prefixlen - 1:
                nodes[node][bit] = 1
            else:
                if nodes[node][bit] is None:
                    nodes[node][bit] = len(nodes)
                    nodes.append([None, None])
                node = nodes[node][bit]
    return pack_mmdb(nodes, [{"country": {"iso_code": "US"}}, {"country": {"iso_code": "DE"}}])


class LegacyCountryTreeTests(unittest.TestCase):
    def serialize(self, ranges):
        with tempfile.TemporaryDirectory() as work:
            output = Path(work) / "GeoIPv6.dat"
            tree = converter.LegacyCountryTree("Regression 20240101 Build")
            for lo, hi, code in ranges:
                tree.add_range(lo, hi, code)
            tree.write(output)
            return output.read_bytes()

    def test_zero_prefix_covers_all_128_bits(self):
        data = self.serialize([(0, MAX_IPV6, "DE")])
        for address in (0, 1, 1 << 32, 1 << 64, 1 << 127, MAX_IPV6):
            with self.subTest(address=address):
                self.assertEqual(read_country_id(data, address), 56)

    def test_one_bit_prefixes_and_ipv6_edition_trailer(self):
        data = self.serialize([(0, (1 << 127) - 1, "DE"), (1 << 127, MAX_IPV6, "US")])
        self.assertEqual(read_country_id(data, (1 << 127) - 1), 56)
        self.assertEqual(read_country_id(data, 1 << 127), 225)
        self.assertEqual(read_country_id(data, MAX_IPV6), 225)
        self.assertEqual(data[-7:-3], b"\xff\xff\xff\x0c")
        count = int.from_bytes(data[-3:], "little")
        self.assertEqual(count, 1)
        self.assertEqual(data[count * 6:count * 6 + 3], bytes(3))

    def test_single_addresses_and_high_bits_are_distinct(self):
        values = [0, 1 << 32, 1 << 64, (1 << 96) + 1, MAX_IPV6]
        codes = ["DE", "US", "FR", "GB", "JP"]
        data = self.serialize([(value, value, code) for value, code in zip(values, codes)])
        for value, code in zip(values, codes):
            with self.subTest(value=value):
                self.assertEqual(read_country_id(data, value), converter.COUNTRY_INDEX[code])
        for value in (1, (1 << 32) + 1, (1 << 64) + 1, MAX_IPV6 - 1):
            self.assertEqual(read_country_id(data, value), 0)

    def test_non_cidr_range_keeps_exact_boundaries(self):
        data = self.serialize([(1, MAX_IPV6 - 1, "FR")])
        self.assertEqual(read_country_id(data, 0), 0)
        self.assertEqual(read_country_id(data, 1), converter.COUNTRY_INDEX["FR"])
        self.assertEqual(read_country_id(data, 1 << 112), converter.COUNTRY_INDEX["FR"])
        self.assertEqual(read_country_id(data, MAX_IPV6 - 1), converter.COUNTRY_INDEX["FR"])
        self.assertEqual(read_country_id(data, MAX_IPV6), 0)

    def test_invalid_unsorted_and_overlapping_ranges_are_rejected(self):
        for lo, hi in ((-1, 0), (0, MAX_IPV6 + 1), (2, 1)):
            with self.subTest(lo=lo, hi=hi):
                with self.assertRaises(ValueError):
                    converter.LegacyCountryTree("Test").add_range(lo, hi, "DE")
        for lo, hi in ((0, 5), (10, 20), (15, 30), (20, 20)):
            with self.subTest(lo=lo, hi=hi):
                tree = converter.LegacyCountryTree("Test")
                tree.add_range(10, 20, "DE")
                with self.assertRaises(ValueError):
                    tree.add_range(lo, hi, "US")

    def test_country_aliases_and_unknown_codes(self):
        for alias, original in (("CW", "AN"), ("UK", "GB"), ("SX", "FX"), ("XK", "RS")):
            with self.subTest(alias=alias):
                data = self.serialize([(MAX_IPV6, MAX_IPV6, alias)])
                self.assertEqual(read_country_id(data, MAX_IPV6), converter.COUNTRY_INDEX[original])
        self.assertEqual(read_country_id(self.serialize([(0, 0, "ZZ")]), 0), 0)


class MaxMindReaderTests(unittest.TestCase):
    def test_native_ipv6_all_node_sizes_and_both_priorities(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "fixture.mmdb"
            for width in (24, 28, 32):
                with self.subTest(width=width):
                    path.write_bytes(make_mmdb(width))
                    with converter.MaxMindDB(path) as reader:
                        self.assertEqual(list(reader.ipv6_ranges("registered_country")),
                                         [(0, (1 << 127) - 1, "US"), (1 << 127, MAX_IPV6, "FR")])
                        self.assertEqual(list(reader.ipv6_ranges("country")),
                                         [(0, (1 << 127) - 1, "DE"), (1 << 127, MAX_IPV6, "FR")])

    def test_all_ipv4_alias_paths_are_preserved(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "alias.mmdb"
            path.write_bytes(make_alias_mmdb())
            with converter.MaxMindDB(path) as reader:
                ranges = list(reader.ipv6_ranges("country"))
            expected = []
            for prefix in ("::/96", "::ffff:0:0/96", "2002::/16"):
                network = ipaddress.IPv6Network(prefix)
                lo, size = int(network.network_address), network.num_addresses
                expected.extend([(lo, lo + size // 2 - 1, "US"),
                                 (lo + size // 2, lo + size - 1, "DE")])
            self.assertEqual(ranges, expected)

    def test_ipv4_only_database_is_rejected(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "ipv4.mmdb"
            path.write_bytes(make_mmdb(ip_version=4))
            with self.assertRaises(converter.ConversionError):
                with converter.MaxMindDB(path) as reader:
                    list(reader.ipv6_ranges())


class CommandLineTests(unittest.TestCase):
    def run_cli(self, *arguments, cwd=None):
        environment = {key: value for key, value in os.environ.items() if not key.startswith("MAXMIND_")}
        return subprocess.run([sys.executable, str(RELEASE_DIR / "geoip_convert_ipv6.py"), *arguments],
                              cwd=cwd or RELEASE_DIR, env=environment, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=15)

    def test_default_dated_layout_in_full_and_dat_only_modes(self):
        with tempfile.TemporaryDirectory() as work:
            source = Path(work) / "fixture.mmdb"
            source.write_bytes(make_mmdb(28))
            for dat_only in (False, True):
                with self.subTest(dat_only=dat_only):
                    cwd = Path(work) / ("dat-only" if dat_only else "full")
                    cwd.mkdir()
                    args = ["--mmdb", str(source), "--build-date", "20240101"]
                    if dat_only:
                        args.append("--dat-only")
                    result = self.run_cli(*args, cwd=cwd)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    output = cwd / "20240101"
                    self.assertEqual({p.name for p in output.iterdir()}, {"GeoIPv6.dat", "GeoIPv6_country.dat", "tmp"})
                    expected = {"build_ipv6.json"}
                    if not dat_only:
                        expected.update({"geoip6", "geoip6_country", "GeoIPv6C.csv", "GeoIPv6C_country.csv"})
                    self.assertEqual({p.name for p in (output / "tmp").iterdir()}, expected)
                    for text in ("GeoIPv6.dat", "GeoIPv6_country.dat", "registered_country.iso_code", "country.iso_code", "continent.code"):
                        self.assertIn(text, result.stdout)
                    self.assertIn(b"GeoIP-Customname 20240101 Build", (output / "GeoIPv6.dat").read_bytes())
                    self.assertEqual(read_country_id((output / "GeoIPv6.dat").read_bytes(), "2001:db8::1"), 225)
                    self.assertEqual(read_country_id((output / "GeoIPv6_country.dat").read_bytes(), "2001:db8::1"), 56)

    def test_explicit_output_and_overwrite_protection(self):
        with tempfile.TemporaryDirectory() as work:
            source, output = Path(work) / "fixture.mmdb", Path(work) / "result"
            source.write_bytes(make_mmdb())
            args = ["--mmdb", str(source), "--output-dir", str(output)]
            first = self.run_cli(*args)
            self.assertEqual(first.returncode, 0, first.stderr)
            before = (output / "GeoIPv6.dat").read_bytes()
            rejected = self.run_cli(*args)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertEqual(rejected.stdout, "")
            self.assertEqual((output / "GeoIPv6.dat").read_bytes(), before)
            forced = self.run_cli(*args, "--force")
            self.assertEqual(forced.returncode, 0, forced.stderr)
            self.assertEqual((output / "GeoIPv6.dat").read_bytes(), before)

    def test_ipv4_output_files_can_coexist_without_modification(self):
        with tempfile.TemporaryDirectory() as work:
            source, output = Path(work) / "fixture.mmdb", Path(work) / "result"
            source.write_bytes(make_mmdb())
            (output / "tmp").mkdir(parents=True)
            existing = {"GeoIP.dat": b"IPv4 registered", "GeoIP_country.dat": b"IPv4 country", "tmp/build.json": b"IPv4 report"}
            for name, content in existing.items():
                (output / name).write_bytes(content)
            result = self.run_cli("--mmdb", str(source), "--output-dir", str(output))
            self.assertEqual(result.returncode, 0, result.stderr)
            for name, content in existing.items():
                self.assertEqual((output / name).read_bytes(), content)

    def test_archive_input_and_build_report(self):
        with tempfile.TemporaryDirectory() as work:
            archive, output = Path(work) / "source.tar.gz", Path(work) / "result"
            fixture = make_mmdb()
            with tarfile.open(archive, "w:gz") as target:
                member = tarfile.TarInfo("GeoLite2-Country/GeoLite2-Country.mmdb")
                member.size = len(fixture)
                target.addfile(member, io.BytesIO(fixture))
            result = self.run_cli("--archive", str(archive), "--output-dir", str(output))
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((output / "tmp/build_ipv6.json").read_text())
            self.assertEqual(report["build_name"], "GeoIP-Customname")
            self.assertEqual(len(report["outputs"]), 2)

    def test_invalid_or_ipv4_input_does_not_publish_databases(self):
        with tempfile.TemporaryDirectory() as work:
            source = Path(work) / "fixture.mmdb"
            for content in (b"invalid database", make_mmdb(ip_version=4)):
                source.write_bytes(content)
                output = Path(work) / "result"
                result = self.run_cli("--mmdb", str(source), "--output-dir", str(output))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse((output / "GeoIPv6.dat").exists())

    def test_help_is_english_and_available_without_credentials(self):
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for text in ("--mmdb", "--archive", "--output-dir", "license", "IPv6"):
            self.assertIn(text, result.stdout)


class DownloaderTests(unittest.TestCase):
    def test_license_only_and_optional_basic_auth_requests(self):
        for account in ("", "test-account"):
            with self.subTest(account=account):
                opener = mock.Mock()
                opener.open.return_value = io.BytesIO(b"mock archive")
                with tempfile.TemporaryDirectory() as work:
                    path = Path(work) / "download.tar.gz"
                    with mock.patch.object(converter.urllib.request, "build_opener", return_value=opener):
                        converter.download_database(path, account, "test+key/&=")
                    self.assertEqual(path.read_bytes(), b"mock archive")
                request = opener.open.call_args.args[0]
                if account:
                    self.assertTrue(request.get_header("Authorization").startswith("Basic "))
                    redirected = converter.SafeRedirect().redirect_request(request, None, 302, "Found", {}, "https://storage.example/object")
                    self.assertIsNone(redirected.get_header("Authorization"))
                else:
                    parsed = converter.urllib.parse.urlsplit(request.full_url)
                    self.assertEqual(parsed.path, "/app/geoip_download")
                    self.assertEqual(converter.urllib.parse.parse_qs(parsed.query)["license_key"], ["test+key/&="])
                    self.assertIsNone(request.get_header("Authorization"))


if __name__ == "__main__":
    unittest.main()
