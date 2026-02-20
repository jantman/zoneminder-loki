# zoneminder-loki

Docker image to ship ZoneMinder logs to Loki

[![Project Status: WIP – Initial development is in progress, but there has not yet been a stable, usable release suitable for the public.](https://www.repostatus.org/badges/latest/wip.svg)](https://www.repostatus.org/#wip)

**IMPORTANT:** This is a personal project only. PRs are accepted, but this is not supported and "issues" will likely not be fixed or responded to. This is only for people who understand the details of everything invovled.

**NOTE:** This project is intended for use with Loki 3.0+ with [structured metadata](https://grafana.com/docs/loki/latest/get-started/labels/structured-metadata/) enabled (`allow_structured_metadata: true` in `limits_config`). If you are running an older version of Loki or do not have structured metadata enabled, set the `STRUCTURED_METADATA=false` environment variable.

## Description

This is a very simple daemon that polls [ZoneMinder](https://zoneminder.com/)'s MySQL database for new log messages (in the ZoneMinder `Logs` table, the same one that drives the "Log" section of the UI) every N seconds (default 10) and ships them to [Loki](https://grafana.com/oss/loki/). This is intended as a way to get ZM's logs out of their walled-garden database table and into (1) the same place _all_ of your other logs are, and (2) somewhere that can alert on problems in a meaningful way.

## Loki Metadata

By default, `PID`, `file`, and `line` are sent as Loki [structured metadata](https://grafana.com/docs/loki/latest/get-started/labels/structured-metadata/) (requires Loki 3.0+ with `allow_structured_metadata: true` in `limits_config`).

Set `STRUCTURED_METADATA=false` to send `PID`, `file`, and `line` as regular Loki labels instead, for compatibility with older Loki versions.

## Usage

This is really only intended to be run in Docker; if you need to run it locally, make your environment like the Docker container.

```
docker run \
    -e LOG_HOST="$(hostname)" \
    -e LOKI_URL=http://myloki:3100/loki/api/v1/push \
    -e ZM_DB_HOST ZM_DB_USER ZM_DB_PASS ZM_DB_NAME \
    -v /opt/zm-loki-pointer.txt:/pointer.txt \
    jantman/zoneminder-loki:latest
```

### Environment Variables

* `LOKI_URL` (**required**) - Loki URL to ship logs to; e.g. `http://my-loki-instance/loki/api/v1/push`
* `LOG_HOST` (**required**) - Value to specify for the `host` label on log messages
* `ZM_DB_HOST` (**required**) - ZoneMinder MySQL database hostname (or IP address)
* `ZM_DB_USER` (**required**) - ZoneMinder MySQL database username
* `ZM_DB_PASS` (**required**) - ZoneMinder MySQL database password
* `ZM_DB_NAME` (**required**) - ZoneMinder MySQL database name
* `POLL_SECONDS` (_optional_) - Integer number of seconds for how often to poll MySQL for log messages; default 10
* `BACKFILL_MINUTES` (_optional_) - If the pointer file does not exist, how many minutes worth of logs to backfill into Loki; default 120
* `POINTER_PATH` (_optional_) - Path to the pointer position file; default `/pointer.txt`
* `STRUCTURED_METADATA` (_optional_) - Send PID/file/line as Loki structured metadata (default `true`); set to `false` to send them as labels instead, for Loki versions older than 3.0

## Debugging

For debugging, append `-v` to your `docker run` command, to run the entrypoint with debug-level logging.

## Development

Clone the repo, then in your clone:

```
python3 -mvenv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Release Process

Tag the repo. [GitHub Actions](https://github.com/jantman/zoneminder-loki/actions) will run a Docker build, push to Docker Hub and GHCR (GitHub Container Registry), and create a release on the repo.