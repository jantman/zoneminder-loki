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

import pymysql
import pymysql.cursors

FORMAT = "[%(asctime)s %(levelname)s] %(message)s"
logging.basicConfig(level=logging.WARNING, format=FORMAT)
logger = logging.getLogger()


class ZmLokiShipper:

    def __init__(self):
        db_host: str = self._env_or_err('ZM_DB_HOST')
        db_user: str = self._env_or_err('ZM_DB_USER')
        db_pass: str = self._env_or_err('ZM_DB_PASS')
        db_name: str = self._env_or_err('ZM_DB_NAME')
        self._loki_url: str = self._env_or_err('LOKI_URL')
        self._poll_interval: int = int(os.environ.get('POLL_SECONDS', '10'))
        self._backfill_minutes: int = int(
            os.environ.get('BACKFILL_MINUTES', '60')
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

    def _env_or_err(self, name: str) -> str:
        s: str = os.environ.get(name)
        if not s:
            raise RuntimeError(
                f'ERROR: You must set the "{name}" environment variable.'
            )
        return s

    def _read_pointer(self) -> int:
        logger.debug('Reading pointer from: %s', self._pointer_path)
        with open(self._pointer_path, 'r') as fh:
            pointer = fh.read().strip()
        logger.debug('Pointer value: %s', pointer)
        return int(pointer)

    def _write_pointer(self):
        logger.debug(
            'Writing pointer of %s to %s',
            self._pointer, self._pointer_path
        )
        with open(self._pointer_path, 'w') as fh:
            fh.write(str(self._pointer))

    def run(self):
        with self.conn:
            if os.path.exists(self._pointer_path):
                self._pointer = self._read_pointer()
                # make sure we can write the file; if we can't, we want to error
                # now, not after we have an update to write...
                self._write_pointer()
                logger.info('Polling for Logs with Id > %d', self._pointer)
            else:
                self._backfill()
            with self.conn.cursor() as cursor:
                logger.info('Entering polling loop...')
                count: int
                while True:
                    count = 0
                    sql = (f'SELECT * FROM Logs WHERE Id > {self._pointer} '
                           f'ORDER BY Id ASC;')
                    logger.debug('Execute: %s', sql)
                    cursor.execute(sql)
                    while (row := cursor.fetchone()) is not None:
                        self._handle_row(row)
                        count += 1
                    if count > 0:
                        logger.info('Shipped %d log messages', count)
                    logger.debug('Sleeping %d seconds', self._poll_interval)
                    sleep(self._poll_interval)

    def _handle_row(self, row: dict):
        """
        Handle one log message from the DB.

        Example:

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
        logger.error('ROW: %s', row)
        raise NotImplementedError()

    def _backfill(self):
        threshold = int(time() - (self._backfill_minutes * 60))
        logger.info(
            'Backfilling logs since %d (last %d minutes)',
            threshold, self._backfill_minutes
        )
        count: int = 0
        with self.conn.cursor() as cursor:
            # first set the pointer; if we backfill zero rows, we want the
            # pointer to still be accurate
            sql = 'SELECT Id FROM Logs ORDER BY Id ASC LIMIT 1;'
            cursor.execute(sql)
            row = cursor.fetchone()
            logger.info(
                'Set initial fallback pointer to: %d', row['Id']
            )
            self._pointer = row['Id']
            sql = (f'SELECT * FROM Logs WHERE TimeKey >= {threshold} '
                   f'ORDER BY Id ASC;')
            logger.debug('Execute: %s', sql)
            cursor.execute(sql)
            while (row := cursor.fetchone()) is not None:
                self._handle_row(row)
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
