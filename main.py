#!/usr/bin/env python
"""
https://github.com/jantman/zoneminder-loki

MIT License

Copyright (c) 2024 Jason Antman

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import sys
import os
import argparse
import logging
from time import sleep, time
from typing import List, Dict, Optional
from collections import defaultdict

import pymysql
import pymysql.cursors
import requests

FORMAT = "[%(asctime)s %(levelname)s] %(message)s"
logging.basicConfig(level=logging.WARNING, format=FORMAT)
logger = logging.getLogger()


# suppress annoying logging
for lname in ['urllib3']:
    lgr = logging.getLogger(lname)
    lgr.setLevel(logging.WARNING)
    lgr.propagate = True


def zm_level_name(level: int) -> str:
    """
    ZoneMinder logging levels:
    https://github.com/ZoneMinder/zoneminder/blob/master/scripts/ZoneMinder/lib/ZoneMinder/Logger.pm#L109-L126
    use constant {
      DEBUG9 => 9,
      DEBUG8 => 8,
      DEBUG7 => 7,
      DEBUG6 => 6,
      DEBUG5 => 5,
      DEBUG4 => 4,
      DEBUG3 => 3,
      DEBUG2 => 2,
      DEBUG1 => 1,
      DEBUG => 1,
      INFO => 0,
      WARNING => -1,
      ERROR => -2,
      FATAL => -3,
      PANIC => -4,
      NOLOG => -5
    };
    """
    name: str
    match level:
        case -5:
            name = 'nolog'
        case -4:
            name = 'panic'
        case -3:
            name = 'fatal'
        case -2:
            name = 'error'
        case -1:
            name = 'warning'
        case 0:
            name = 'info'
        case _:
            name = 'unknown'
    if level >= 1:
        name = 'debug'
    return name


class ZmLokiShipper:

    def __init__(self):
        db_host: str = self._env_or_err('ZM_DB_HOST')
        db_user: str = self._env_or_err('ZM_DB_USER')
        db_pass: str = self._env_or_err('ZM_DB_PASS')
        db_name: str = self._env_or_err('ZM_DB_NAME')
        log_host: str = self._env_or_err('LOG_HOST')
        self._loki_url: str = self._env_or_err('LOKI_URL')
        self._poll_interval: int = int(os.environ.get('POLL_SECONDS', '10'))
        self._backfill_minutes: int = int(
            os.environ.get('BACKFILL_MINUTES', '120')
        )
        self._pointer_path: str = os.environ.get(
            'POINTER_PATH', '/pointer.txt'
        )
        self._pointer: int = -1
        logger.info(
            'Connecting to MySQL on %s as user %s and database name %s; '
            'polling every %d seconds', db_host, db_user, db_name,
            self._poll_interval
        )
        self.conn: pymysql.Connection = pymysql.connect(
            host=db_host, user=db_user, password=db_pass, database=db_name,
            charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor
        )
        logger.debug('Connected to MySQL')
        self._batch_size: int = 1  # just in case we want to tune in future
        self._session: requests.Session = requests.Session()
        self._labels: Dict[str, str] = {
            'host': log_host, 'job': 'zoneminder-loki'
        }
        logger.debug('Common labels: %s', self._labels)

    def _env_or_err(self, name: str) -> str:
        s: str = os.environ.get(name)
        if not s:
            raise RuntimeError(
                f'ERROR: You must set the "{name}" environment variable.'
            )
        return s

    def _read_pointer(self) -> Optional[int]:
        logger.debug('Reading pointer from: %s', self._pointer_path)
        try:
            with open(self._pointer_path, 'r') as fh:
                pointer = fh.read().strip()
            logger.debug('Pointer value: %s', pointer)
            return int(pointer)
        except Exception as ex:
            logger.error(
                'Unable to read pointer from %s: %s', self._pointer_path, ex,
                exc_info=True
            )
            return None

    def _write_pointer(self):
        logger.debug(
            'Writing pointer of %s to %s',
            self._pointer, self._pointer_path
        )
        with open(self._pointer_path, 'w') as fh:
            fh.write(str(self._pointer))

    def _loki_post(self, data: dict):
        logger.debug('POST to Loki with data: %s', data)
        r: requests.Response = self._session.post(
            url=self._loki_url, json=data
        )
        logger.debug(
            'Loki responded HTTP %d: %s', r.status_code, r.content
        )
        assert r.status_code == 204

    def run(self):
        with self.conn:
            if pointer := self._read_pointer():
                self._pointer = pointer
                # make sure we can write the file; if we can't, we want to error
                # now, not after we have an update to write...
                self._write_pointer()
                logger.info('Polling for Logs with Id > %d', self._pointer)
            else:
                self._backfill()
            cursor: pymysql.cursors.DictCursor
            with self.conn.cursor() as cursor:
                logger.info(
                    'Entering polling loop; reading from DB in batches of '
                    '%d rows', self._batch_size
                )
                count: int
                rows: List[dict]
                while True:
                    count = 0
                    sql = (f'SELECT * FROM Logs WHERE Id > {self._pointer} '
                           f'ORDER BY Id ASC;')
                    logger.debug('Execute: %s', sql)
                    rowcount = cursor.execute(sql)
                    logger.debug('Query matched %d rows', rowcount)
                    while rows := cursor.fetchmany(self._batch_size):
                        self._handle_rows(rows)
                        count += 1
                    if count > 0:
                        logger.info('Shipped %d log messages', count)
                    logger.debug('Sleeping %d seconds', self._poll_interval)
                    sleep(self._poll_interval)

    def _handle_rows(self, rows: List[dict]):
        """
        Handle one log message from the DB.

        Example DB entry:
        {
            'Id': 221456,
            'TimeKey': Decimal('1703574639.334704'),
            'Component': 'zmc_m2',
            'ServerId': 0,
            'Pid': 79,
            'Level': 0,
            'Code': 'INF',
            'Message': 'Office: 377000 - Capturing at 9.97 fps, capturing bandwidth 165274bytes/sec Analysing at 0.00 fps',
            'File': 'zm_monitor.cpp',
            'Line': 1680
        }
        """
        streams: Dict[tuple, list] = defaultdict(list)
        for row in rows:
            labels: tuple = (
                ('component', str(row['Component'])),
                ('server_id', str(row['ServerId'])),
                ('PID', str(row['Pid'])),
                ('level', zm_level_name(row['Level'])),
                ('file', str(row['File'])),
                ('line', str(row['Line'])),
            )
            streams[labels].append([
                # Loki needs a nanoseconds timestamp
                str(int(row['TimeKey']) * 1000000000),
                row['Message']
            ])
        data: dict = {'streams': []}
        for keys, vals in streams.items():
            data['streams'].append({
                'stream': dict(keys) | self._labels,
                'values': vals
            })
        self._loki_post(data)
        self._pointer = max([x['Id'] for x in rows])
        self._write_pointer()

    def _backfill(self):
        threshold = int(time() - (self._backfill_minutes * 60))
        logger.info(
            'Backfilling logs since %d (last %d minutes)',
            threshold, self._backfill_minutes
        )
        count: int = 0
        cursor: pymysql.cursors.DictCursor
        with self.conn.cursor() as cursor:
            # first set the pointer; if we backfill zero rows, we want the
            # pointer to still be accurate
            sql = 'SELECT Id FROM Logs ORDER BY Id DESC LIMIT 1;'
            cursor.execute(sql)
            row = cursor.fetchone()
            logger.info(
                'Set initial fallback pointer to: %d', row['Id']
            )
            self._pointer = row['Id']
            sql = (f'SELECT * FROM Logs WHERE TimeKey >= {threshold} '
                   f'ORDER BY Id ASC;')
            logger.debug('Execute: %s', sql)
            rowcount = cursor.execute(sql)
            logger.debug('Query matched %d rows', rowcount)
            rows: List[dict]
            while rows := cursor.fetchmany(self._batch_size):
                self._handle_rows(rows)
                count += 1
        logger.info('Done backfilling %d older log messages', count)


def parse_args(argv):
    p = argparse.ArgumentParser(description='ZoneMinder Loki log shipper')
    p.add_argument(
        '-v', '--verbose', dest='verbose', action='store_true',
        default=False, help='debug-level log output'
    )
    args = p.parse_args(argv)
    return args


def set_log_info():
    set_log_level_format(
        logging.INFO, '%(asctime)s %(levelname)s:%(name)s:%(message)s'
    )


def set_log_debug():
    set_log_level_format(
        logging.DEBUG,
        "%(asctime)s [%(levelname)s %(filename)s:%(lineno)s - "
        "%(name)s.%(funcName)s() ] %(message)s"
    )


def set_log_level_format(level: int, fmt: str):
    """
    Set logger level and format.

    :param level: logging level; see the :py:mod:`logging` constants.
    :type level: int
    :param format: logging formatter format string
    :type format: str
    """
    formatter = logging.Formatter(fmt=fmt)
    logger.handlers[0].setFormatter(formatter)
    logger.setLevel(level)


if __name__ == "__main__":
    args = parse_args(sys.argv[1:])
    if args.verbose:
        set_log_debug()
    else:
        set_log_info()
    ZmLokiShipper().run()
