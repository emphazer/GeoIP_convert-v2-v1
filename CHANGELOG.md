# Changelog

## 1.0.2.1

- Keep the date-based default build directory.
- Place DAT files in the build directory and intermediate files and the build
  report in its `tmp/` subdirectory.
- Apply the same layout to explicit output directories and `--dat-only` builds.

## 1.0.2

- Add an explanation of country-selection priorities after successful builds.
- Use English throughout source comments, CLI messages, tests, and documentation.
- Set the default build name to `GeoIP-Customname` and use generic examples.
- Add a repository ignore file for generated output and local configuration.

## 1.0.1

- Support license-key-only downloads through the legacy endpoint.
- Make account-based HTTP Basic Authentication optional.
- Accept the license key and optional build name as positional arguments.
- Add download and command-line regression tests.

## 1.0.0

- Implement IPv4 Country MMDB to Legacy DAT conversion using Python 3.
- Support registered-country-first and geographic-country-first outputs.
- Add offline MMDB/archive input, text/CSV exports, and build metadata.
