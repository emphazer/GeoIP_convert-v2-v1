# Validation

Version: 1.0.0. Test environment: Python 3.12.14 on Linux.

## Automated tests

All **16 tests** passed. The suite uses synthetic MMDB fixtures and an
independent 128-bit Legacy DAT lookup implementation. Coverage includes:

- /0, /1, and /128 ranges, the first and last IPv6 addresses, high address
  bits, non-CIDR ranges, gaps, invalid ranges, and overlapping input.
- Country IDs, aliases, unknown codes, and the IPv6 Edition 12 trailer.
- Native IPv6 MMDB trees with 24-, 28-, and 32-bit node records.
- Shared subtrees under `::/96`, `::ffff:0:0/96`, and `2002::/16`.
- Rejection of IPv4-only MMDB trees.
- Dated output directories, intermediate paths, `--dat-only`, explicit
  destinations, overwrite protection, archive input, and IPv4 coexistence.
- Simulated license-only and optional account-based downloads.

Run from the project directory:

```bash
python3 -m unittest discover -s tests -v
```

## Full-data conversion

A full conversion used a GeoLite2 Country MMDB released on 2026-11-06.
The source has 544859 search nodes with 24-bit records. Both variants were
built with `--name GeoIP-Customname --build-date 20240101`. The fixed build
date is a reproducibility parameter, not the source database release date.

| Output | Merged country-bearing ranges | CIDR networks | DAT nodes | File size |
|---|---:|---:|---:|---:|
| `GeoIPv6.dat` | 646656 | 841437 | 1203204 | 7219265 bytes |
| `GeoIPv6_country.dat` | 1702226 | 2743226 | 3104993 | 18629999 bytes |

SHA-256 for the complete generated files with this source and build comment:

| Output | SHA-256 |
|---|---|
| `GeoIPv6.dat` | `0e310a1e59879cc9a0421fe5ad2c3c26ec679265a3f47958527620b10b67bc3e` |
| `GeoIPv6_country.dat` | `57f1728e54eb5263c38a1a9d805d81042f7fe2f8ec45361dad33c9a15d54e246` |

## Complete address-space comparison

An independent reference traversal used the official MaxMind Python reader
(`maxminddb` 3.2.0),
including its MMDB data decoder, to interpret the source tree. The expected
country IDs were computed separately for the two selection priorities.
The generated DAT trees were independently read and normalized to disjoint
intervals, including unassigned addresses.

Both normalized mappings matched across the complete **2^128 IPv6 address
space**, including the stored IPv4 alias paths. Adjacent intervals with the
same country ID were merged. Hash input was ASCII `start,end,country_id`
followed by a newline for each interval.

| Output | Canonical intervals including ID 0 | Mapping SHA-256 |
|---|---:|---|
| `GeoIPv6.dat` | 748995 | `a16406383795d3ac686568acf47b5b678b376e938f1f745e2ab93b54eb986ff9` |
| `GeoIPv6_country.dat` | 1804545 | `11d31ab432223b52ccd20df76940ed9e356b0de7801e73e9feb9ef4a60719589` |

The reference reader is a validation tool only. It is not required by the
converter or bundled with the project.

## Legacy reader compatibility

The official MaxMind Legacy C implementation was compiled locally from its
unmodified upstream sources for a separate compatibility check. It recognized
both generated files as Country IPv6 Edition 12.

For each file, **185529 boundary, random, and alias-address lookups** matched
the expected country IDs: **371058 comparisons** in total. Teredo
interpretation was disabled with `GeoIP_enable_teredo(handle, 0)` to compare
the raw database mappings. The reference library is not a converter dependency
and is not included in the project package.

## Limits

The complete address-space comparison establishes conversion compatibility
for the tested source. It does not establish the geographic accuracy of its
country assignments. File hashes change with the source, build name, or date.

Authenticated live downloads were not performed. Download tests use simulated
responses and cannot establish server availability or acceptance of a license
key. Python 3.9 is the minimum based on the standard library APIs used;
runtime tests were performed on Python 3.12.14 only.

Applications must support Legacy Country IPv6 Edition 12 and use IPv6 lookup
APIs. Reader-level address transformations, including Teredo interpretation,
can change lookup semantics independently of the stored tree.
