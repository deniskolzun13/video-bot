"""Unit tests для utils/logging: job_context и setup_logging."""
import io
import logging

from utils.logging import job_context, setup_logging


class TestJobContext:
    def test_job_id_in_format(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(job_id)s%(message)s"))
        handler.addFilter(__import__("utils.logging", fromlist=["JobIdFilter"]).JobIdFilter())
        root = logging.getLogger()
        root.handlers = [handler]
        root.setLevel(logging.INFO)

        with job_context("JOB-123"):
            logging.getLogger("t").info("msg")
        assert "JOB-123" in stream.getvalue()

    def test_outside_job_empty_prefix(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(job_id)s%(message)s"))
        handler.addFilter(__import__("utils.logging", fromlist=["JobIdFilter"]).JobIdFilter())
        root = logging.getLogger()
        root.handlers = [handler]
        root.setLevel(logging.INFO)

        logging.getLogger("t").info("plain")
        assert "[job" not in stream.getvalue()

    def test_setup_logging_adds_filter(self):
        setup_logging(logging.INFO)
        root = logging.getLogger()
        assert any(isinstance(h.filters and h.filters[0], object) for h in root.handlers)
        from utils.logging import JobIdFilter

        assert any(
            isinstance(f, JobIdFilter)
            for h in root.handlers
            for f in h.filters
        )