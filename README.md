# GeoIP Country Converter

Convert GeoLite2 Country or GeoIP2 Country MMDB data into IPv4 GeoIP Legacy
Country DAT files using **Python 3.9+ and the standard library**.

The converter supports an existing MMDB, a GeoLite2 Country download archive,
or a direct MaxMind download. No third-party Python packages are required.

By default, each build uses a `YYYYMMDD/` directory in the current working
directory. The two DAT files are placed directly in that directory;
intermediate files and the build report are kept in its `tmp/` subdirectory.

## Quick start

Convert a local MMDB without network access:

```bash
python3 geoip_convert.py --mmdb /path/to/GeoLite2-Country.mmdb --name 'GeoIP-Customname'
```

Or read a local MaxMind download archive:

```bash
python3 geoip_convert.py --archive /path/to/GeoLite2-Country.tar.gz --name 'GeoIP-Customname'
```

`--archive` expects an archive containing exactly one regular file named
`GeoLite2-Country.mmdb`. Only that file is copied into the temporary build
directory; archive paths and other files are not extracted.

## Which DAT file should I use?

Both DAT files use the same Legacy Country format. Their country-selection
priorities differ:

| File | Preferred MMDB field | Fallback order |
|---|---|---|
| `GeoIP.dat` | `registered_country.iso_code` | `country.iso_code`, then `continent.code` |
| `GeoIP_country.dat` | `country.iso_code` | `registered_country.iso_code`, then `continent.code` |

- **Registered country** is the country where the ISP registered the network.
  Use `GeoIP.dat` when this administrative assignment is the intended result.
- **Geographic country** is MaxMind's estimated country associated with the IP
  address. Use `GeoIP_country.dat` when geographic assignment is the intended
  result.

For example, a record with `registered_country.iso_code = US` and
`country.iso_code = DE` is assigned US in `GeoIP.dat` and DE in
`GeoIP_country.dat`. Missing fields can cause either output to use its fallback.
Neither file guarantees the physical location of an IP address or its user.

After a successful build, the program prints an explanation of the output
files and their selection priorities. This also applies to `--dat-only` builds.

### Legacy country IDs

The writer preserves the positional Legacy Country ID table and these aliases:

| Input code | Legacy table entry |
|---|---|
| `CW` | `AN` |
| `UK` | `GB` |
| `SX` | `FX` |
| `XK` | `RS` |

The consuming application's own country table determines the labels it displays.
Unsupported codes are written as country ID 0 and reported in a warning.

The `continent.code` fallback is retained for compatibility. Some continent codes
also name countries in the Legacy table: for example, `NA` maps to Namibia and
`SA` to Saudi Arabia. This fallback does not provide a separate continent field.

## Download from MaxMind

Without `--mmdb` or `--archive`, the converter downloads GeoLite2 Country.
A license key is required. An account ID is optional in the program.

### License key only

```bash
python3 geoip_convert.py 'YOUR_LICENSE_KEY' 'GeoIP-Customname'
```

Without an account ID, the converter uses the legacy HTTPS download endpoint
with the license key in the query string. Availability depends on MaxMind
accepting that endpoint and credential combination. Rejected requests produce
an HTTP error; the program does not automatically switch authentication methods.

The key can also be read from an environment variable:

```bash
read -r -s -p 'MaxMind license key: ' MAXMIND_LICENSE_KEY
printf '\n'
export MAXMIND_LICENSE_KEY
python3 geoip_convert.py --name 'GeoIP-Customname'
unset MAXMIND_LICENSE_KEY
```

Or from a file containing only the key:

```bash
python3 geoip_convert.py --license-key-file /path/to/maxmind-license.key --name 'GeoIP-Customname'
```

A positional key or key file takes precedence over `MAXMIND_LICENSE_KEY`.
Supplying both a positional key and `--license-key-file` is rejected.

### Optional account ID

Supplying `--account-id` or `MAXMIND_ACCOUNT_ID` selects MaxMind's documented
HTTP Basic Authentication download endpoint, using the account ID as the
username and the license key as the password:

```bash
python3 geoip_convert.py --account-id YOUR_ACCOUNT_ID --license-key-file /path/to/maxmind-license.key --name 'GeoIP-Customname'
```

License keys are excluded from the program's log messages and build report.
The Basic Auth header is not forwarded on redirects. Temporary downloads and
extracted MMDB files are removed after the build; local input files are retained.

## Outputs

| File | Content |
|---|---|
| `GeoIP.dat` | Registered-country-first IPv4 Legacy Country database |
| `GeoIP_country.dat` | Geographic-country-first IPv4 Legacy Country database |
| `tmp/geoip`, `tmp/geoip_country` | Decimal IPv4 ranges and selected country codes |
| `tmp/GeoIPC.csv`, `tmp/GeoIPC_country.csv` | Legacy-style CSV ranges with textual and numeric IPv4 addresses |
| `tmp/build.json` | Converter version, build name/date, source metadata, counts, and SHA-256 hashes |

Paths in this table are relative to the build directory. For example,
`--build-date 20240101` uses these default paths:

| Output | Default path |
|---|---|
| Registered-country DAT | `20240101/GeoIP.dat` |
| Geographic-country DAT | `20240101/GeoIP_country.dat` |
| Registered-country text ranges | `20240101/tmp/geoip` |
| Geographic-country text ranges | `20240101/tmp/geoip_country` |
| Registered-country CSV | `20240101/tmp/GeoIPC.csv` |
| Geographic-country CSV | `20240101/tmp/GeoIPC_country.csv` |
| Build report | `20240101/tmp/build.json` |

Both directories are created automatically. `--dat-only` writes the two DAT
files directly in the build directory and keeps only `build.json` in `tmp/`.
The project package contains source, tests, and documentation; databases,
download archives, private configuration, credentials, and logs are excluded.

## Configuration and command-line options

Defaults are defined near the top of `geoip_convert.py`: `DEFAULT_NAME`,
`DEFAULT_OUTPUT_DIR`, `INTERMEDIATE_DIR`, `OUTPUTS`, download URLs, size limit,
and HTTP timeout.
Change `OUTPUTS` to rename files or add output variants. Each variant must use
unique filenames and either `country` or `registered_country` as its preference.

| Option | Effect |
|---|---|
| `--name NAME` | Set the DAT build name; defaults to `GeoIP-Customname` |
| `--build-date YYYYMMDD` | Set the build date; defaults to the current local date |
| `--output-dir PATH` | Set the output directory; defaults to the build date as `YYYYMMDD` |
| `--dat-only` | Omit text and CSV files |
| `--force` | Replace existing output files |
| `--help` | Display all arguments |
| `--version` | Display the converter version |

An explicit `--output-dir` replaces the date directory while retaining the
same file split. For example, `--output-dir /path/to/results` writes DAT files
to `/path/to/results/` and other generated files to `/path/to/results/tmp/`;
no date directory is added inside the chosen destination.

`--name` takes precedence over the optional second positional argument.
The DAT comment is `NAME YYYYMMDD Build` and must contain at most 90 printable
ASCII characters, including the date and suffix.

Existing files are left in place unless `--force` is supplied. Conversion is
completed in a temporary directory before publication. Each final file is
published atomically, but the output group is not a transaction: an I/O error
during publication may leave some files already published. Avoid concurrent
`--force` builds targeting the same directory.

Exit codes: `0` success, `1` conversion/file/download error, `2` invalid CLI
arguments, and `130` keyboard interrupt.

## Tests and validation

```bash
python3 -m unittest discover -s tests -v
```

Tests use synthetic MMDB fixtures and an independent Legacy DAT reader. Coverage
includes IPv4 and IPv4 embedded in IPv6 databases, 24/28/32-bit records, country
priorities, boundary ranges, downloads, and command-line behavior. Downloads
are simulated in the test suite. See [VALIDATION.md](VALIDATION.md) for validation
results and the limits of testing.

The converter writes IPv4 Legacy Country databases. It does not write IPv6 DAT
files or City, ASN, ISP, and Organization editions.

## References

- [MaxMind DB file format](https://maxmind.github.io/MaxMind-DB/)
- [MaxMind Legacy C API and country table](https://github.com/maxmind/geoip-api-c)
- [MaxMind database download documentation](https://dev.maxmind.com/geoip/updating-databases/)
- [MaxMind country field definitions](https://dev.maxmind.com/geoip/docs/databases/city-and-country/)
