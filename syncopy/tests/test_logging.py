# -*- coding: utf-8 -*-
#
# Test logging.
#

import os
import platform

# Local imports
import syncopy as spy
from syncopy.shared.log import get_logger
from syncopy.shared.errors import SPYLog


class TestLogging:

    def test_seq_logfile_exists(self):
        logfile = os.path.join(spy.__logdir__, "syncopy.log")
        assert os.path.isfile(logfile)

    def test_par_logfile_exists(self):
        par_logfile = os.path.join(spy.__logdir__, f"syncopy_{platform.node()}.log")
        assert os.path.isfile(par_logfile)

    def test_default_log_level_is_important(self):

        # Ensure the log level is at default (that user did not change SPYLOGLEVEL on test system)
        assert os.getenv("SPYLOGLEVEL", "IMPORTANT") == "IMPORTANT"

        logfile = os.path.join(spy.__logdir__, "syncopy.log")
        assert os.path.isfile(logfile)
        num_lines_initial = sum(1 for line in open(logfile)) # The log file gets appended, so it will most likely *not* be empty.

        # Log something with log level info and DEBUG, which should not affect the logfile.
        logger = get_logger()
        logger.info("I am adding an INFO level log entry.")
        SPYLog("I am adding a DEBUG level log entry.", loglevel="DEBUG")

        num_lines_after_info_debug = sum(1 for line in open(logfile))

        assert num_lines_initial == num_lines_after_info_debug

        # Now log something with log level WARNING
        SPYLog("I am adding a WARNING level log entry.", loglevel="WARNING")

        num_lines_after_warning = sum(1 for line in open(logfile))
        assert num_lines_after_warning > num_lines_after_info_debug





