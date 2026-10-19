# Changelog

## 2.0.4 - 2026-10-07

### Added
- Circuit comparison of two complete seven-day windows in Reports
- Minute-capture eligibility gates suppress changes for sparse or mismatched history
- Tests for missing capture, zero baselines, device isolation, and partial-day exclusion

## 2.0.3 - 2026-10-07

### Added
- Configurable monthly fixed charge, kept separate from circuit usage costs
- Reports projected bill breakdown before assistance credits, with explicit tax handling
- Billing configuration validation and preservation tests

## 2.0.2 - 2026-10-07

### Added
- Opt-in Mac collector-client mode via ENERGY_COLLECTOR_URL, with secure-origin validation
- Persistent Collector Connection menu setting for Finder/login launches
- Verified online SQLite backup command for collector-history migration
- Measured top-five consumption reviews with explicitly hypothetical savings scenarios
- Collector migration and offline synchronization requirements in COLLECTOR_CLIENT.md

### Fixed
- Reports no longer labels instantaneous low loads as verified always-on consumption
- Collector startup requires a successful version response instead of accepting HTTP errors

## 2.0.1 - 2026-10-07

### Added
- Aqara Cloud account configuration and authorization controls in Settings
- Aqara v3 sensor discovery with regional API endpoints, token refresh, and device-model resource lookup

### Fixed
- Aqara API signing, request paths, device pagination, and resource-value request structure
- Aqara sensor errors now surface in the UI instead of being silently converted into an empty list

### Verification
- venv/bin/python3 -m unittest discover -s tests -v

## 2.0.0 - 2026-03-13

### Added
- Server-Sent Events for live nav status and dashboard payload updates
- Latest-channel snapshot table for faster dashboard reads
- Service capability persistence from CSV headers, including `No CT` handling
- Split-phase native/inferred service-feed rendering model
- 7-day forecast strip integrated into the live dashboard banner
- Reports and Trends jump navigation and reorganized section hierarchy
- Dashboard/trends/reports regression tests in `/Users/seandolbec/Projects/Emporia_energy_monitoring/tests/test_energy.py`

### Changed
- Dashboard now focuses on realtime banner, service panel, and live circuit context
- Trends now owns historical charts, month comparison, operational review, and load review
- Reports now owns recommendations, billing review, and budget/monthly analysis
- Settings now owns operational tool links for Circuits, Import, Aqara, and Log
- Explicit panel slot counts supported for non-20/40 panel inventories
- Poll/build flow hardened against stale repo copies and invalid local environments

### Fixed
- Repeated CSV correction migration corruption
- Stored XSS risk from string-template rendering without autoescape
- CSV import scalability and duplicate handling
- Latest-reading selection after historical imports
- Multi-device data conflation
- Incorrect UTC/local analytics mixing
- Numeric circuit names being dropped from the UI
- Bus bar and panel layout drift across Dashboard and Circuits views
- Missing circuit rendering when saved panel layouts were partial
- Build failures caused by unresolved bundle metadata

### Security
- Flask binds to `127.0.0.1` by default
- Credentials and token files are hardened to owner-only permissions

### Verification
- `venv/bin/python3 -m unittest discover -s tests -v`
- `./build.sh --no-pull --no-open`
