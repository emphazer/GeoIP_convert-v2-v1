# Validation

Version: 1.0.2.1. Test environment: Python 3.12.14 on Linux.

## Automated tests

All **32 tests** passed. The suite uses synthetic MMDB fixtures and an
independent Legacy DAT lookup implementation. It checks:

- IPv4 records in both IPv4 and IPv6 MMDBs with 24-, 28-, and 32-bit nodes.
- Registered-country and geographic-country selection, including fallback.
- Legacy country IDs, aliases, unknown codes, boundaries, gaps, and /0, /1,
  and /32 networks.
- Offline archive conversion and rejection of symlink MMDB members.
- Both download methods, HTTPS redirects, credential handling, size limits,
  and sanitized errors using simulated responses.
- CLI output, overwrite protection, explicit replacement, and English help
  and errors.
- The default date directory, with DAT files at its root and intermediate
  files and the build report under `tmp/`.
- The generic default build name and the explanation printed only after all
  final output files exist, including `--dat-only` builds.

Run from the project directory:

```bash
python3 -m unittest discover -s tests -v
```

## Full-data regression

The full-data regression was performed with version 1.0.2 using a GeoLite2
Country database released on 2026-11-06. Version 1.0.2.1 changes output placement
without changing the DAT conversion logic; this full-data run was not repeated
for version 1.0.2.1.
Its MMDB contains 544859 search nodes with 24-bit records and an IPv6 tree
including the IPv4 subtree.

The build used `--name GeoIP-Customname --build-date 20240101`. The fixed build
date is a reproducibility parameter, not the source database release date.

| Output | Merged IPv4 ranges | CIDR networks | DAT nodes | File size |
|---|---:|---:|---:|---:|
| `GeoIP.dat` | 143893 | 186017 | 198717 | 1192343 bytes |
| `GeoIP_country.dat` | 361124 | 562176 | 574876 | 3449297 bytes |

The search-tree bytes of both files matched the reference Legacy DAT trees
exactly. This covers all IPv4 mappings and unassigned ranges, rather than
selected sample lookups. The file trailers were checked separately against
the generic build comment and Country Edition 1 metadata.

SHA-256 of the complete generated files for this fixed source and build:

| Output | SHA-256 |
|---|---|
| `GeoIP.dat` | `81862c6e41667b559ad127c483652ec7b0a76969021c169d41480111fa8b9e16` |
| `GeoIP_country.dat` | `d0bfb7cc54de234af41db332dc52530ba2a89a8f2170c5fa01dec9839ee6c9a4` |

## Limits

The full-data comparison confirms conversion compatibility for the tested
source database. It does not establish the geographic accuracy of MaxMind's
country assignments. Changing the source, build name, or build date changes
the complete-file hashes.

Authenticated live downloads were not performed. Download tests use simulated
responses and cannot establish current server availability or acceptance of
a particular license key. Python 3.9 is the minimum based on the standard
library APIs used; runtime testing was performed on Python 3.12.14 only.
