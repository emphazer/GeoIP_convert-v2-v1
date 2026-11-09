# GeoIP Country Converter

Convert GeoLite2 Country or GeoIP2 Country MMDB data into IPv4 and IPv6
GeoIP Legacy Country DAT files using **Python 3.9+ and the standard library**.
No third-party Python packages are required.

The project provides two independent entry points:

| Script | Address family | Legacy database edition |
|---|---|---|
| `geoip_convert.py` | IPv4 | Country Edition 1 |
| `geoip_convert_ipv6.py` | IPv6 | Country IPv6 Edition 12 |

Both support local MMDB files, MaxMind download archives, and direct downloads.
The IPv6 converter runs independently of the IPv4 converter. With the default
filenames, both can write to the same dated directory without replacing each
other's DAT files, intermediate exports, or build reports.

## Quick start

Convert both address families from an existing MMDB without network access:

```bash
python3 geoip_convert.py --mmdb /path/to/GeoLite2-Country.mmdb --name 'GeoIP-Customname'
python3 geoip_convert_ipv6.py --mmdb /path/to/GeoLite2-Country.mmdb --name 'GeoIP-Customname'
```

Either script can also read a local MaxMind download archive:

```bash
python3 geoip_convert.py --archive /path/to/GeoLite2-Country.tar.gz --name 'GeoIP-Customname'
python3 geoip_convert_ipv6.py --archive /path/to/GeoLite2-Country.tar.gz --name 'GeoIP-Customname'
```

The IPv4 converter reads IPv4 MMDB trees and IPv4 records embedded in IPv6
MMDB trees. The IPv6 converter requires an IPv6 MMDB tree and rejects
IPv4-only trees.

`--archive` expects exactly one regular file named `GeoLite2-Country.mmdb`.
Only that file is copied into a temporary directory; archive paths and other
members are not extracted.

By default, each build uses a `YYYYMMDD/` directory in the current working
directory. DAT files are placed directly in that directory, while intermediate
exports and build reports are kept in its `tmp/` subdirectory.

## Which DAT file should I use?

Each converter produces two country-selection variants:

| File | Family | Preferred MMDB field | Fallback order |
|---|---|---|---|
| `GeoIP.dat` | IPv4 | `registered_country.iso_code` | `country.iso_code`, then `continent.code` |
| `GeoIP_country.dat` | IPv4 | `country.iso_code` | `registered_country.iso_code`, then `continent.code` |
| `GeoIPv6.dat` | IPv6 | `registered_country.iso_code` | `country.iso_code`, then `continent.code` |
| `GeoIPv6_country.dat` | IPv6 | `country.iso_code` | `registered_country.iso_code`, then `continent.code` |

**Registered country** is the country where the ISP registered the network.
Choose the registered-country-first variant when this administrative assignment
is the intended result. **Geographic country** is MaxMind's estimated country
associated with the IP address; choose the geographic-country-first variant
when that assignment is intended.

For example, a record with registered country US and geographic country DE
selects US in `GeoIP.dat` or `GeoIPv6.dat`, and DE in the corresponding
`_country.dat` file. Missing fields can cause either variant to use its fallback.
Neither variant guarantees the physical location of a device or user.

After successful generation, each program explains its output variants and
selection priorities. This also applies to `--dat-only` builds.

### Reader compatibility

The consuming application must support the matching Legacy database edition
and address family. IPv6 DAT files use edition 12 and require an IPv6-capable
lookup API, such as `GeoIP_country_code_by_addr_v6` in the Legacy C API.
Renaming an IPv6 file does not make it readable by an IPv4-only application.

Both converters write Country databases. City, ASN, ISP, and Organization
editions are outside their scope.

### IPv6 address coverage

The IPv6 converter traverses the complete 128-bit source MMDB tree. Stored
paths for IPv4-compatible `::/96`, IPv4-mapped `::ffff:0:0/96`, and 6to4
`2002::/16` addresses are retained when present.

The converter does not perform Teredo extraction or other transformations
outside the source tree. A consuming API may apply its own transformations.
The Legacy C API enables Teredo interpretation by default; use
`GeoIP_enable_teredo(handle, 0)` when raw-tree lookup semantics are required.

### Legacy country IDs

Both writers preserve the positional Legacy Country ID table and these aliases:

| Input code | Legacy table entry |
|---|---|
| `CW` | `AN` |
| `UK` | `GB` |
| `SX` | `FX` |
| `XK` | `RS` |

The consuming application's own country table determines the displayed labels.
Unsupported codes are written as country ID 0 and reported in a warning.
Unassigned addresses also use country ID 0.

The `continent.code` fallback is retained for compatibility. Some continent
codes also name countries in the Legacy table: `NA` maps to Namibia and `SA`
to Saudi Arabia. This fallback does not add a separate continent field.

## Download from MaxMind

Without `--mmdb` or `--archive`, either program downloads GeoLite2 Country.
A license key is required. An account ID is optional in the programs.

### License key only

```bash
python3 geoip_convert.py 'YOUR_LICENSE_KEY' 'GeoIP-Customname'
python3 geoip_convert_ipv6.py 'YOUR_LICENSE_KEY' 'GeoIP-Customname'
```

Without an account ID, the legacy HTTPS endpoint is used with the license key
in the query string. Availability depends on MaxMind accepting that endpoint
and credential combination. Rejected requests produce an HTTP error; the
programs do not switch authentication methods automatically.

A key can instead be read from the environment:

```bash
read -r -s -p 'MaxMind license key: ' MAXMIND_LICENSE_KEY
printf '\n'
export MAXMIND_LICENSE_KEY
python3 geoip_convert.py --name 'GeoIP-Customname'
python3 geoip_convert_ipv6.py --name 'GeoIP-Customname'
unset MAXMIND_LICENSE_KEY
```

Or use a file containing only the key:

```bash
python3 geoip_convert.py --license-key-file /path/to/maxmind-license.key --name 'GeoIP-Customname'
python3 geoip_convert_ipv6.py --license-key-file /path/to/maxmind-license.key --name 'GeoIP-Customname'
```

A positional key or key file takes precedence over `MAXMIND_LICENSE_KEY`.
Supplying both a positional key and `--license-key-file` is rejected. To avoid
separate downloads when generating both families, download once and use the
same local MMDB or archive with both scripts.

### Optional account ID

Supplying `--account-id` or `MAXMIND_ACCOUNT_ID` selects MaxMind's documented
HTTP Basic Authentication endpoint, with the account ID as the username and
the license key as the password. For example:

```bash
python3 geoip_convert_ipv6.py --account-id YOUR_ACCOUNT_ID --license-key-file /path/to/maxmind-license.key --name 'GeoIP-Customname'
```

The same option is supported by `geoip_convert.py`. License keys are excluded
from program log messages and build reports. Basic Auth headers are not
forwarded on redirects. Temporary downloads and extracted MMDB files are
removed after the build; local input files are retained.

## Outputs

Paths below are relative to the selected build directory:

| File | Content |
|---|---|
| `GeoIP.dat`, `GeoIP_country.dat` | IPv4 Legacy Country databases |
| `GeoIPv6.dat`, `GeoIPv6_country.dat` | IPv6 Legacy Country databases |
| `tmp/geoip`, `tmp/geoip_country` | Decimal IPv4 bounds and selected country codes |
| `tmp/GeoIPC.csv`, `tmp/GeoIPC_country.csv` | IPv4 ranges in six-column CSV form |
| `tmp/build.json` | IPv4 build report |
| `tmp/geoip6`, `tmp/geoip6_country` | Compressed IPv6 bounds and selected country codes |
| `tmp/GeoIPv6C.csv`, `tmp/GeoIPv6C_country.csv` | IPv6 ranges in six-column CSV form |
| `tmp/build_ipv6.json` | IPv6 build report |

For example, `--build-date 20240101` places the four DAT files in `20240101/`
and their intermediate files in `20240101/tmp/`. Directories are created
automatically. Running only one converter creates only its own outputs.

CSV columns are start address, end address, start address as a decimal integer,
end address as a decimal integer, selected country code, and an empty field.
These exports use the selected address family; they are not MaxMind's source
CSV schema. Build reports include converter version, build name/date, source
metadata, counts, and SHA-256 hashes.

`--dat-only` omits text and CSV files. Each converter still writes its two DAT
files and its own report under `tmp/`. Source databases, credentials, and
local generated outputs are not required in the repository.

## Configuration and command-line options

Defaults and filenames are defined near the top of each script:
`DEFAULT_NAME`, `DEFAULT_OUTPUT_DIR`, `INTERMEDIATE_DIR`, `OUTPUTS`, download
URLs, size limit, and timeout. The IPv6 script also defines `REPORT_FILENAME`.
Each output variant uses either `country` or `registered_country` as its
preference. Keep filenames unique, including across both scripts if they
share an output directory.

These options are supported by both entry points:

| Option | Effect |
|---|---|
| `--name NAME` | Set the DAT build name; defaults to `GeoIP-Customname` |
| `--build-date YYYYMMDD` | Set the build date; defaults to the current local date |
| `--output-dir PATH` | Replace the default `YYYYMMDD` output directory |
| `--dat-only` | Omit text and CSV exports |
| `--force` | Replace the selected converter's existing output files |
| `--help` | Display all arguments |
| `--version` | Display the selected converter's version |

An explicit `--output-dir /path/to/results` writes DAT files directly to
`/path/to/results/` and intermediate files to `/path/to/results/tmp/`, without
adding a date directory. `--name` takes precedence over the optional second
positional argument. The DAT comment is `NAME YYYYMMDD Build` and must contain
at most 90 printable ASCII characters, including the date and suffix.

Existing outputs are protected unless `--force` is supplied. Conversion is
completed in a temporary directory before publication. Each final file is
published atomically; the complete output group is not a transaction. An I/O
error during publication may leave some files already published. Avoid
concurrent `--force` builds targeting the same directory.

Exit codes: `0` success, `1` conversion/file/download error, `2` invalid CLI
arguments, and `130` keyboard interrupt.

## Tests and validation

Run both suites from the repository root:

```bash
python3 -m unittest discover -s tests -v
```

The suites contain **32 IPv4 tests and 16 IPv6 tests**. They use synthetic
MMDB fixtures and independent Legacy DAT lookup implementations. Coverage
includes country priorities, boundary ranges, MMDB node sizes, output layout,
overwrite protection, downloads, and coexistence of IPv4 and IPv6 outputs.
Download tests use simulated responses; authenticated live downloads were
not performed during validation.

- [IPv4 validation results](VALIDATION.md)
- [IPv6 validation results](VALIDATION_IPV6.md)

## References

- [MaxMind DB file format](https://maxmind.github.io/MaxMind-DB/)
- [MaxMind Legacy C API and country table](https://github.com/maxmind/geoip-api-c)
- [MaxMind database download documentation](https://dev.maxmind.com/geoip/updating-databases/)
- [MaxMind country field definitions](https://dev.maxmind.com/geoip/docs/databases/city-and-country/)
