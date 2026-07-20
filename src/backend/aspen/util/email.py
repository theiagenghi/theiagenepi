"""Outbound email.

Auth0 sent invitation emails on our behalf (`send_invitation_email: True`), so
the backend never had any email infrastructure. Owning invitations locally means
owning delivery too.

The console backend is the default so that tests and local dev stay hermetic --
no network, no AWS credentials, and the invitation URL is visible in the logs.
"""

import logging
import os
from typing import Optional

from aspen.aws import session


class EmailSender:
    def send(self, to_address: str, subject: str, body: str) -> None:
        raise NotImplementedError


class ConsoleEmailSender(EmailSender):
    """Logs the message instead of sending it."""

    def send(self, to_address: str, subject: str, body: str) -> None:
        logging.info(
            "Email not sent (console backend).\n"
            f"To: {to_address}\nSubject: {subject}\n{body}"
        )


class SESEmailSender(EmailSender):
    def __init__(self, from_address: str, client=None) -> None:
        self.from_address = from_address
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = session().client(
                "ses", endpoint_url=os.environ.get("BOTO_ENDPOINT_URL")
            )
        return self._client

    def send(self, to_address: str, subject: str, body: str) -> None:
        self.client.send_email(
            Source=self.from_address,
            Destination={"ToAddresses": [to_address]},
            Message={
                "Subject": {"Data": subject},
                "Body": {"Text": {"Data": body}},
            },
        )


def get_email_sender(backend: str, from_address: Optional[str] = None) -> EmailSender:
    # Unknown backends raise rather than defaulting to console: a typo in
    # EMAIL_BACKEND would otherwise send every production invitation to the logs
    # and nowhere else, and nothing would look wrong until someone reported that
    # invitations never arrive.
    if backend == "ses":
        if not from_address:
            raise ValueError("EMAIL_FROM_ADDRESS is required for the ses backend")
        return SESEmailSender(from_address)
    if backend == "console":
        return ConsoleEmailSender()
    raise ValueError(f"Unknown EMAIL_BACKEND {backend!r}; expected 'ses' or 'console'")
