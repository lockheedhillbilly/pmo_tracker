"""Tests for the pure parsing helpers in process_email_updates.py (decoding
subjects, extracting a readable body from a MIME message). No IMAP/SMTP
connection involved — those helpers take an already-fetched email.message.Message.
"""

import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import Header
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from process_email_updates import decode_subject, extract_body


def test_decode_subject_plain_ascii():
    assert decode_subject("PMO: Update from Aayushi") == "PMO: Update from Aayushi"


def test_decode_subject_encoded_utf8():
    encoded = Header("PMO: Café update", "utf-8").encode()
    assert decode_subject(encoded) == "PMO: Café update"


def test_decode_subject_empty():
    assert decode_subject("") == ""
    assert decode_subject(None) == ""


def test_extract_body_plain_text_only():
    msg = MIMEText("Simple plain text body", "plain")
    assert extract_body(msg).strip() == "Simple plain text body"


def test_extract_body_prefers_plain_over_html_in_multipart():
    msg = MIMEMultipart("alternative")
    msg.attach(MIMEText("<b>Rich</b> body", "html"))
    msg.attach(MIMEText("Plain body", "plain"))
    assert extract_body(msg).strip() == "Plain body"


def test_extract_body_falls_back_to_stripped_html():
    msg = MIMEMultipart("alternative")
    msg.attach(MIMEText("<p>Only <b>HTML</b> here</p>", "html"))
    result = extract_body(msg)
    assert "Only" in result and "HTML" in result
    assert "<" not in result
