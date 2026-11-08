"""Regression tests for the IPv4 GeoLite2 to Legacy Country conversion.

Run from the release directory with:
    python3 -m unittest discover -s tests -v

The DAT lookup below reads the binary format directly and does not call the
converter's lookup or serialization helpers.
"""

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
import geoip_convert as converter  # noqa: E402


COUNTRY_BEGIN = 16776960
MAX_IPV4 = (1 << 32) - 1


def read_country_id(contents, address):
    """Read a Country Edition DAT using its 3-byte little-endian pointers."""
    address = int(ipaddress.IPv4Address(address))
    node_offset = 0
    for bit in range(31, -1, -1):
        side = (address >> bit) & 1
        offset = node_offset + side * 3
        raw = contents[offset : offset + 3]
        if len(raw) != 3:
            raise AssertionError("DAT pointer lies beyond the end of the file")
        pointer = int.from_bytes(raw, "little")
        if pointer >= COUNTRY_BEGIN:
            return pointer - COUNTRY_BEGIN
        node_offset = pointer * 6
    raise AssertionError("DAT has no country leaf within 32 IPv4 bits")


def encode_mmdb_value(value):
    """Encode the small subset needed for independent, valid MMDB fixtures."""
    if isinstance(value, str):
        value = value.encode("utf-8")
        if len(value) >= 29:
            raise ValueError("Fixture strings must be shorter than 29 bytes")
        return bytes([0x40 + len(value)]) + value
    if isinstance(value, int):
        size = (value.bit_length() + 7) // 8
        kind = 5 if size <= 2 else 6
        return bytes([(kind << 5) + size]) + value.to_bytes(size, "big")
    if isinstance(value, dict):
        return bytes([0xE0 + len(value)]) + b"".join(
            encode_mmdb_value(key) + encode_mmdb_value(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return bytes([len(value), 4]) + b"".join(map(encode_mmdb_value, value))
    raise TypeError(type(value))


def make_mmdb(record_size=24, ip_version=4):
    """Two IPv4 half-space records; IPv6 embeds them below 96 zero bits."""
    left = encode_mmdb_value(
        {"country": {"iso_code": "DE"}, "registered_country": {"iso_code": "US"}}
    )
    right = encode_mmdb_value({"country": {"iso_code": "FR"}})
    node_count = 97 if ip_version == 6 else 1
    data_left = node_count + 16
    data_right = data_left + len(left)
    nodes = [(index + 1, node_count) for index in range(node_count - 1)]
    nodes.append((data_left, data_right))
    tree = bytearray()
    for a, b in nodes:
        if record_size == 24:
            tree.extend(a.to_bytes(3, "big") + b.to_bytes(3, "big"))
        elif record_size == 28:
            tree.extend((a & 0xFFFFFF).to_bytes(3, "big"))
            tree.append(((a >> 24) << 4) | (b >> 24))
            tree.extend((b & 0xFFFFFF).to_bytes(3, "big"))
        elif record_size == 32:
            tree.extend(a.to_bytes(4, "big") + b.to_bytes(4, "big"))
        else:
            raise ValueError(record_size)
    metadata = {
        "binary_format_major_version": 2,
        "binary_format_minor_version": 0,
        "build_epoch": 1704067200,
        "database_type": "GeoLite2-Country",
        "description": {"en": "Test fixture"},
        "ip_version": ip_version,
        "languages": ["en"],
        "node_count": node_count,
        "record_size": record_size,
    }
    return bytes(tree) + b"\x00" * 16 + left + right + b"\xab\xcd\xefMaxMind.com" + encode_mmdb_value(metadata)


class MaxMindReaderTests(unittest.TestCase):
    def test_ipv4_and_embedded_ipv4_with_all_node_record_sizes(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "fixture.mmdb"
            for ip_version in (4, 6):
                for record_size in (24, 28, 32):
                    with self.subTest(ip_version=ip_version, record_size=record_size):
                        path.write_bytes(make_mmdb(record_size, ip_version))
                        with converter.MaxMindDB(path) as reader:
                            self.assertEqual(reader.metadata["record_size"], record_size)
                            self.assertEqual(reader.metadata["ip_version"], ip_version)
                            self.assertEqual(
                                list(reader.ipv4_ranges(prefer="registered_country")),
                                [(0, (1 << 31) - 1, "US"), (1 << 31, MAX_IPV4, "FR")],
                            )
                            self.assertEqual(
                                list(reader.ipv4_ranges(prefer="country")),
                                [(0, (1 << 31) - 1, "DE"), (1 << 31, MAX_IPV4, "FR")],
                            )


class LegacyCountryTreeTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.output = Path(self.work.name) / "GeoIP.dat"

    def serialize(self, ranges):
        tree = converter.LegacyCountryTree("Regression 20240101 Build")
        for lo, hi, code in ranges:
            tree.add_range(lo, hi, code)
        tree.write(self.output)
        return self.output.read_bytes()

    def assert_country(self, contents, address, code):
        expected = converter.COUNTRY_INDEX[code]
        self.assertEqual(read_country_id(contents, address), expected, address)

    def test_zero_prefix_covers_the_entire_ipv4_space(self):
        contents = self.serialize([(0, MAX_IPV4, "DE")])
        for address in ("0.0.0.0", "1.2.3.4", "127.255.255.255", "128.0.0.0", "255.255.255.255"):
            with self.subTest(address=address):
                self.assert_country(contents, address, "DE")

    def test_two_one_bit_prefixes_keep_their_countries(self):
        contents = self.serialize([(0, (1 << 31) - 1, "DE"), (1 << 31, MAX_IPV4, "US")])
        self.assert_country(contents, "0.0.0.0", "DE")
        self.assert_country(contents, "127.255.255.255", "DE")
        self.assert_country(contents, "128.0.0.0", "US")
        self.assert_country(contents, "255.255.255.255", "US")

    def test_single_addresses_and_unassigned_gaps(self):
        contents = self.serialize([(0, 0, "DE"), (0x08080808, 0x08080808, "US"), (MAX_IPV4, MAX_IPV4, "GB")])
        self.assert_country(contents, "0.0.0.0", "DE")
        self.assert_country(contents, "8.8.8.8", "US")
        self.assert_country(contents, "255.255.255.255", "GB")
        for address in ("0.0.0.1", "8.8.8.7", "8.8.8.9", "255.255.255.254"):
            with self.subTest(address=address):
                self.assertEqual(read_country_id(contents, address), 0)

    def test_non_cidr_range_preserves_both_boundaries(self):
        contents = self.serialize([(1, MAX_IPV4 - 1, "FR")])
        self.assertEqual(read_country_id(contents, "0.0.0.0"), 0)
        self.assert_country(contents, "0.0.0.1", "FR")
        self.assert_country(contents, "128.0.0.0", "FR")
        self.assert_country(contents, "255.255.255.254", "FR")
        self.assertEqual(read_country_id(contents, "255.255.255.255"), 0)

    def test_aliases_keep_legacy_country_ids(self):
        for alias, original in (("CW", "AN"), ("UK", "GB"), ("SX", "FX"), ("XK", "RS")):
            with self.subTest(alias=alias):
                self.assertEqual(converter.COUNTRY_INDEX[alias], converter.COUNTRY_INDEX[original])
                contents = self.serialize([(0, 0, alias)])
                self.assert_country(contents, "0.0.0.0", original)

    def test_unknown_code_becomes_unassigned(self):
        contents = self.serialize([(0, 0, "ZZ")])
        self.assertEqual(read_country_id(contents, "0.0.0.0"), 0)

    def test_country_ids_have_not_been_reordered(self):
        self.assertEqual(converter.COUNTRY_CODES[0], "")
        self.assertEqual(converter.COUNTRY_CODES[10], "AN")
        self.assertEqual(converter.COUNTRY_CODES[75], "FX")
        self.assertEqual(converter.COUNTRY_INDEX["DE"], 56)
        self.assertEqual(converter.COUNTRY_INDEX["US"], 225)

    def test_overlapping_and_out_of_order_ranges_are_rejected(self):
        for lo, hi in ((15, 25), (10, 20), (0, 5), (20, 20)):
            with self.subTest(lo=lo, hi=hi):
                tree = converter.LegacyCountryTree("Regression")
                tree.add_range(10, 20, "DE")
                with self.assertRaises(ValueError):
                    tree.add_range(lo, hi, "US")

    def test_invalid_range_boundaries_are_rejected(self):
        for lo, hi in ((-1, 0), (0, MAX_IPV4 + 1), (2, 1)):
            with self.subTest(lo=lo, hi=hi):
                tree = converter.LegacyCountryTree("Regression")
                with self.assertRaises(ValueError):
                    tree.add_range(lo, hi, "DE")


class CommandLineTests(unittest.TestCase):
    def run_cli(self, *arguments, cwd=None):
        environment = os.environ.copy()
        for key in tuple(environment):
            if key.startswith("MAXMIND_"):
                environment.pop(key)
        return subprocess.run(
            [sys.executable, str(RELEASE_DIR / "geoip_convert.py"), *arguments],
            cwd=cwd or RELEASE_DIR,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            check=False,
        )

    def test_help_is_available_without_dependencies_or_credentials(self):
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--mmdb", result.stdout)
        self.assertIn("--output-dir", result.stdout)

    def test_help_and_converter_error_messages_are_english(self):
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("license", result.stdout.lower())
        self.assertIn("output", result.stdout.lower())
        with tempfile.TemporaryDirectory() as work:
            source = Path(work) / "invalid.mmdb"
            source.write_bytes(b"not a database")
            result = self.run_cli("--mmdb", str(source), "--output-dir", str(Path(work) / "output"))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("metadata", result.stderr.lower())

    def test_default_name_and_success_explanation_in_all_output_modes(self):
        with tempfile.TemporaryDirectory() as work:
            source = Path(work) / "fixture.mmdb"
            source.write_bytes(make_mmdb())
            for dat_only in (False, True):
                with self.subTest(dat_only=dat_only):
                    working_dir = Path(work) / ("dat-only" if dat_only else "all-files")
                    working_dir.mkdir()
                    output = working_dir / "20240101"
                    arguments = ["--mmdb", str(source), "--build-date", "20240101"]
                    if dat_only:
                        arguments.append("--dat-only")
                    result = self.run_cli(*arguments, cwd=working_dir)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(b"GeoIP-Customname 20240101 Build", (output / "GeoIP.dat").read_bytes())
                    for text in ("GeoIP.dat", "GeoIP_country.dat", "registered_country.iso_code",
                                 "country.iso_code", "continent.code"):
                        self.assertIn(text, result.stdout)
                    self.assertIn("registered", result.stdout.lower())
                    self.assertIn("geographic", result.stdout.lower())
                    self.assertIn("fallback", result.stdout.lower())
                    self.assertIn("0", result.stdout)
                    self.assertEqual((output / "tmp" / "GeoIPC.csv").exists(), not dat_only)
                    self.assertEqual({p.name for p in working_dir.iterdir()}, {"20240101"})
                    self.assertEqual({p.name for p in output.iterdir()}, {"GeoIP.dat", "GeoIP_country.dat", "tmp"})
                    expected_tmp = {"build.json"}
                    if not dat_only:
                        expected_tmp.update({"geoip", "geoip_country", "GeoIPC.csv", "GeoIPC_country.csv"})
                    self.assertEqual({p.name for p in (output / "tmp").iterdir()}, expected_tmp)

    def test_failure_does_not_print_a_success_explanation(self):
        with tempfile.TemporaryDirectory() as work:
            source = Path(work) / "invalid.mmdb"
            source.write_bytes(b"invalid")
            output = Path(work) / "output"
            result = self.run_cli("--mmdb", str(source), "--output-dir", str(output))
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")

    def test_existing_outputs_do_not_print_a_new_success_explanation(self):
        with tempfile.TemporaryDirectory() as work:
            source = Path(work) / "fixture.mmdb"
            source.write_bytes(make_mmdb())
            output = Path(work) / "output"
            arguments = ["--mmdb", str(source), "--output-dir", str(output)]
            first = self.run_cli(*arguments)
            self.assertEqual(first.returncode, 0, first.stderr)
            rejected = self.run_cli(*arguments)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertEqual(rejected.stdout, "")

    def test_explanation_follows_publication_of_the_complete_output(self):
        with tempfile.TemporaryDirectory() as work:
            source = Path(work) / "fixture.mmdb"
            source.write_bytes(make_mmdb())
            output = Path(work) / "output"

            def check_published_files(*args, **kwargs):
                for filename in ("GeoIP.dat", "GeoIP_country.dat", "tmp/geoip", "tmp/geoip_country",
                                 "tmp/GeoIPC.csv", "tmp/GeoIPC_country.csv", "tmp/build.json"):
                    self.assertTrue((output / filename).is_file(), filename)

            with mock.patch.object(converter, "print_output_explanation", side_effect=check_published_files) as explain:
                result = converter.main(["--mmdb", str(source), "--output-dir", str(output)])
            self.assertEqual(result, 0)
            explain.assert_called_once()

    def test_missing_local_input_fails_without_a_traceback(self):
        with tempfile.TemporaryDirectory() as work:
            source = Path(work) / "missing.mmdb"
            output = Path(work) / "output"
            result = self.run_cli("--mmdb", str(source), "--output-dir", str(output))
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse((output / "GeoIP.dat").exists())
            self.assertFalse((output / "GeoIP_country.dat").exists())

    def test_invalid_mmdb_fails_without_producing_databases(self):
        with tempfile.TemporaryDirectory() as work:
            source = Path(work) / "invalid.mmdb"
            source.write_bytes(b"This is not a MaxMind database.")
            output = Path(work) / "output"
            result = self.run_cli("--mmdb", str(source), "--output-dir", str(output))
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse((output / "GeoIP.dat").exists())
            self.assertFalse((output / "GeoIP_country.dat").exists())

    def test_complete_conversion_and_explicit_overwrite(self):
        with tempfile.TemporaryDirectory() as work:
            source = Path(work) / "fixture.mmdb"
            source.write_bytes(make_mmdb(28, 6))
            output = Path(work) / "output"
            arguments = (
                "--mmdb", str(source), "--output-dir", str(output),
                "--build-date", "20240101", "--name", "Regression",
            )
            result = self.run_cli(*arguments)
            self.assertEqual(result.returncode, 0, result.stderr)
            registered = (output / "GeoIP.dat").read_bytes()
            actual = (output / "GeoIP_country.dat").read_bytes()
            self.assertEqual(read_country_id(registered, "0.0.0.0"), converter.COUNTRY_INDEX["US"])
            self.assertEqual(read_country_id(actual, "0.0.0.0"), converter.COUNTRY_INDEX["DE"])
            self.assertEqual(read_country_id(registered, "255.255.255.255"), converter.COUNTRY_INDEX["FR"])
            self.assertEqual(read_country_id(actual, "255.255.255.255"), converter.COUNTRY_INDEX["FR"])
            self.assertIn(b"Regression 20240101 Build", registered)
            report = json.loads((output / "tmp" / "build.json").read_text(encoding="utf-8"))
            self.assertEqual(report["build_name"], "Regression")
            for filename in ("geoip", "geoip_country", "GeoIPC.csv", "GeoIPC_country.csv"):
                self.assertTrue((output / "tmp" / filename).is_file(), filename)

            rejected = self.run_cli(*arguments)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertEqual((output / "GeoIP.dat").read_bytes(), registered)
            self.assertEqual((output / "GeoIP_country.dat").read_bytes(), actual)

            accepted = self.run_cli(*arguments, "--force")
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            self.assertEqual((output / "GeoIP.dat").read_bytes(), registered)
            self.assertEqual((output / "GeoIP_country.dat").read_bytes(), actual)

    def test_local_tar_archive_is_converted_without_network_access(self):
        with tempfile.TemporaryDirectory() as work:
            archive = Path(work) / "GeoLite2-Country.tar.gz"
            fixture = make_mmdb(24, 6)
            with tarfile.open(archive, "w:gz") as target:
                member = tarfile.TarInfo("GeoLite2-Country_20240101/GeoLite2-Country.mmdb")
                member.size = len(fixture)
                target.addfile(member, io.BytesIO(fixture))
            output = Path(work) / "output"
            result = self.run_cli("--archive", str(archive), "--output-dir", str(output))
            self.assertEqual(result.returncode, 0, result.stderr)
            registered = (output / "GeoIP.dat").read_bytes()
            actual = (output / "GeoIP_country.dat").read_bytes()
            self.assertEqual(read_country_id(registered, "0.0.0.0"), converter.COUNTRY_INDEX["US"])
            self.assertEqual(read_country_id(actual, "0.0.0.0"), converter.COUNTRY_INDEX["DE"])
            self.assertEqual(read_country_id(actual, "255.255.255.255"), converter.COUNTRY_INDEX["FR"])

    def test_existing_intermediate_file_prevents_publication_without_force(self):
        with tempfile.TemporaryDirectory() as work:
            source = Path(work) / "fixture.mmdb"
            source.write_bytes(make_mmdb())
            output = Path(work) / "output"
            intermediate = output / "tmp" / "build.json"
            intermediate.parent.mkdir(parents=True)
            intermediate.write_text("existing report", encoding="utf-8")
            result = self.run_cli("--mmdb", str(source), "--output-dir", str(output))
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(intermediate.read_text(encoding="utf-8"), "existing report")
            self.assertFalse((output / "GeoIP.dat").exists())
            self.assertFalse((output / "GeoIP_country.dat").exists())
            self.assertEqual(result.stdout, "")

    def test_nested_output_cannot_overwrite_input_even_with_force(self):
        with tempfile.TemporaryDirectory() as work:
            output = Path(work) / "output"
            source = output / "tmp" / "build.json"
            source.parent.mkdir(parents=True)
            original = make_mmdb()
            source.write_bytes(original)
            result = self.run_cli("--mmdb", str(source), "--output-dir", str(output), "--force")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("overwrite the input", result.stderr)
            self.assertEqual(source.read_bytes(), original)
            self.assertFalse((output / "GeoIP.dat").exists())
            self.assertFalse((output / "GeoIP_country.dat").exists())
            self.assertEqual(result.stdout, "")

    def test_intermediate_directory_rejects_files_and_symlinks(self):
        for kind in ("file", "symlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as work:
                source = Path(work) / "fixture.mmdb"
                source.write_bytes(make_mmdb())
                output = Path(work) / "output"
                output.mkdir()
                intermediate = output / "tmp"
                outside = Path(work) / "outside"
                outside.mkdir()
                if kind == "file":
                    intermediate.write_text("existing file", encoding="utf-8")
                else:
                    intermediate.symlink_to(outside, target_is_directory=True)
                result = self.run_cli("--mmdb", str(source), "--output-dir", str(output), "--force")
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertFalse((output / "GeoIP.dat").exists())
                self.assertFalse((output / "GeoIP_country.dat").exists())
                self.assertEqual(list(outside.iterdir()), [])
                if kind == "file":
                    self.assertEqual(intermediate.read_text(encoding="utf-8"), "existing file")
                else:
                    self.assertTrue(intermediate.is_symlink())

    def test_mmdb_symlink_archive_member_is_rejected(self):
        with tempfile.TemporaryDirectory() as work:
            archive = Path(work) / "GeoLite2-Country.tar.gz"
            linked_source = Path(work) / "outside.mmdb"
            linked_source.write_bytes(make_mmdb())
            with tarfile.open(archive, "w:gz") as target:
                member = tarfile.TarInfo("GeoLite2-Country_20240101/GeoLite2-Country.mmdb")
                member.type = tarfile.SYMTYPE
                member.linkname = str(linked_source)
                target.addfile(member)
            output = Path(work) / "output"
            result = self.run_cli("--archive", str(archive), "--output-dir", str(output))
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse((output / "GeoIP.dat").exists())
            self.assertFalse((output / "GeoIP_country.dat").exists())


class DownloaderTests(unittest.TestCase):
    def test_license_only_download_uses_original_endpoint_and_encodes_key(self):
        license_key = "test+key/&?= value"
        opener = mock.Mock()
        opener.open.return_value = io.BytesIO(b"test payload")
        with tempfile.TemporaryDirectory() as work:
            destination = Path(work) / "download.tar.gz"
            with mock.patch.object(converter.urllib.request, "build_opener", return_value=opener):
                converter.download_database(destination, "", license_key)
            request = opener.open.call_args.args[0]
            parsed = converter.urllib.parse.urlsplit(request.full_url)
            self.assertEqual(parsed.path, "/app/geoip_download")
            self.assertEqual(converter.urllib.parse.parse_qs(parsed.query), {
                "edition_id": ["GeoLite2-Country"], "license_key": [license_key],
                "suffix": ["tar.gz"]})
            self.assertIsNone(request.get_header("Authorization"))
            self.assertEqual(destination.read_bytes(), b"test payload")

    def test_original_positional_invocation_downloads_and_converts_without_account(self):
        payload = io.BytesIO()
        fixture = make_mmdb()
        with tarfile.open(fileobj=payload, mode="w:gz") as archive:
            member = tarfile.TarInfo("GeoLite2-Country/GeoLite2-Country.mmdb")
            member.size = len(fixture)
            archive.addfile(member, io.BytesIO(fixture))
        opener = mock.Mock()
        opener.open.return_value = io.BytesIO(payload.getvalue())
        with tempfile.TemporaryDirectory() as work:
            with mock.patch.dict(os.environ, {"MAXMIND_ACCOUNT_ID": ""}):
                with mock.patch.object(converter.urllib.request, "build_opener", return_value=opener):
                    result = converter.main(["test-key", "CustomBuild", "--output-dir", work])
            self.assertEqual(result, 0)
            self.assertIn(b"CustomBuild", (Path(work) / "GeoIP.dat").read_bytes())
            report = (Path(work) / "tmp" / "build.json").read_text()
            self.assertNotIn("test-key", report)
            self.assertIn("license_key=test-key", opener.open.call_args.args[0].full_url)

    def test_license_only_errors_do_not_expose_query_credentials(self):
        opener = mock.Mock()
        opener.open.side_effect = converter.urllib.error.HTTPError(
            "https://download.maxmind.com/app/geoip_download?license_key=test-secret",
            403, "Denied: test-secret", {}, io.BytesIO(b"test-secret"))
        with tempfile.TemporaryDirectory() as work:
            with mock.patch.object(converter.urllib.request, "build_opener", return_value=opener):
                with self.assertRaises(converter.ConversionError) as caught:
                    converter.download_database(Path(work) / "download.tar.gz", "", "test-secret")
            self.assertNotIn("test-secret", str(caught.exception))
            self.assertIn("403", str(caught.exception))

    def test_license_is_required_but_account_is_not(self):
        with self.assertRaises(converter.ConversionError):
            converter.download_database(Path("unused.tar.gz"), "", "")

    def test_basic_auth_is_present_only_on_the_initial_request(self):
        payload = b"mock compressed database bytes"
        opener = mock.Mock()

        def fake_open(request, timeout):
            self.assertEqual(timeout, converter.HTTP_TIMEOUT)
            self.assertEqual(request.get_header("Authorization"), "Basic dGVzdC1hY2NvdW50OnRlc3Qta2V5")
            handler = converter.SafeRedirect()
            # Test both same-host and cross-host redirects through the real
            # urllib redirect implementation; no request is sent anywhere.
            for location in (
                "https://download.maxmind.com/another-download",
                "https://download-storage.example/temporary-object",
            ):
                redirected = handler.redirect_request(request, None, 302, "Found", {}, location)
                self.assertIsNotNone(redirected)
                self.assertIsNone(redirected.get_header("Authorization"))
                self.assertNotIn("authorization", {key.lower() for key, value in redirected.header_items()})
            return io.BytesIO(payload)

        opener.open.side_effect = fake_open
        with tempfile.TemporaryDirectory() as work:
            destination = Path(work) / "download.tar.gz"
            with mock.patch.object(converter.urllib.request, "build_opener", return_value=opener) as build:
                converter.download_database(destination, "test-account", "test-key")
            self.assertEqual(destination.read_bytes(), payload)
            self.assertIsInstance(build.call_args.args[0], converter.SafeRedirect)
        opener.open.assert_called_once()

    def test_unencrypted_redirect_is_rejected(self):
        request = converter.urllib.request.Request("https://download.maxmind.com/test")
        with self.assertRaises(converter.ConversionError):
            converter.SafeRedirect().redirect_request(request, None, 302, "Found", {}, "http://example.invalid/test")

    def test_oversized_download_is_rejected_without_network_access(self):
        opener = mock.Mock()
        opener.open.return_value = io.BytesIO(b"12345")
        with tempfile.TemporaryDirectory() as work:
            destination = Path(work) / "download.tar.gz"
            with mock.patch.object(converter.urllib.request, "build_opener", return_value=opener):
                with mock.patch.object(converter, "MAX_SOURCE_BYTES", 4):
                    with self.assertRaises(converter.ConversionError):
                        converter.download_database(destination, "test-account", "test-key")

    def test_download_errors_do_not_expose_credentials(self):
        account_id, license_key = "test-account", "test-key"
        errors = (
            converter.urllib.error.HTTPError(
                "https://download.maxmind.com/test?license_key=" + license_key,
                403, "Forbidden for " + account_id + ":" + license_key,
                {}, io.BytesIO(license_key.encode("ascii")),
            ),
            converter.urllib.error.URLError("Mock network error for " + license_key),
        )
        with tempfile.TemporaryDirectory() as work:
            for error in errors:
                with self.subTest(error_type=type(error).__name__):
                    opener = mock.Mock()
                    opener.open.side_effect = error
                    with mock.patch.object(converter.urllib.request, "build_opener", return_value=opener):
                        with self.assertRaises(converter.ConversionError) as caught:
                            converter.download_database(Path(work) / "download.tar.gz", account_id, license_key)
                    message = str(caught.exception)
                    self.assertNotIn(account_id, message)
                    self.assertNotIn(license_key, message)
                    self.assertNotIn("license_key=", message)
                    if isinstance(error, converter.urllib.error.HTTPError):
                        self.assertIn("403", message)

if __name__ == "__main__":
    unittest.main()
