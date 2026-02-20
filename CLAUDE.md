# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Docker daemon that polls ZoneMinder's MySQL `Logs` table and ships log messages to Grafana Loki. The entire application is a single Python file (`main.py`, ~330 lines). Requires Loki [structured metadata](https://grafana.com/docs/loki/latest/get-started/labels/structured-metadata/) support enabled.

## Development Setup

```bash
python3 -mvenv venv
source venv/bin/activate
pip install -r requirements.txt
```

Dependencies: `PyMySQL`, `requests` (unpinned in requirements.txt).

## Running

Intended to run in Docker. Requires these environment variables: `LOKI_URL`, `LOG_HOST`, `ZM_DB_HOST`, `ZM_DB_USER`, `ZM_DB_PASS`, `ZM_DB_NAME`. Optional: `POLL_SECONDS` (default 10), `BACKFILL_MINUTES` (default 120), `POINTER_PATH` (default `/pointer.txt`). Use `-v` flag for debug logging.

## Architecture

**Single-file app** (`main.py`) with one class and a few helper functions:

- `ZmLokiShipper` - Main class. Connects to MySQL, polls for new log rows by tracking the last-seen `Id` in a pointer file, groups rows by Loki labels, POSTs to Loki's push API.
- `zm_level_name()` - Maps ZoneMinder integer log levels (from Logger.pm) to string names.

**Data flow:** MySQL poll → group rows by (component, server_id, level) → format with nanosecond timestamps → POST JSON to Loki → update pointer file.

**Loki labels** (indexed): `host`, `job`, `component`, `server_id`, `level`. **Structured metadata** (not indexed): `PID`, `file`, `line`.

**State persistence:** A pointer file stores the last processed `Logs.Id`. On first run with no pointer, backfills logs from the last N minutes.

## Build & Release

- Push to `main` → GitHub Actions builds Docker image, pushes to GHCR with SHA tag.
- Push a git tag → GitHub Actions builds, pushes to Docker Hub (`jantman/zoneminder-loki`) and GHCR with version + `latest` tags, creates GitHub release.

## Notes

- No test suite exists.
- Batch size is hardcoded to 1 in `_batch_size`.
- SQL queries use string formatting (not parameterized), acceptable since values are internally sourced integers.
- Handles Loki HTTP 400 "timestamp too old" errors gracefully by skipping the log entry.
