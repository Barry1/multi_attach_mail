"""Send attachments."""

import asyncio
import logging
import sys
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from typing import TypedDict

import yaml
from aiopath import AsyncPath
from aiosmtplib import SMTP
from valuefragments import memoize
from valuefragments.helpers import thread_native_id_filter

# from email.mime.text import MIMEText
# https://realpython.com/python-send-email/#adding-attachments-using-the-email-package

infourls: list[str] = [
    "https://hilfe.web.de/pop-imap/imap/imap-serverdaten.html",
    "https://hilfe.gmx.net/pop-imap/pop3/serverdaten.html",
]

_ATTACHMENTFOLDER = AsyncPath("attachments")


class SMTPCFG(TypedDict):
    """Just a Type-Class for the configuration."""

    smtp_server: str
    smtp_port: int
    smtp_user: str
    smtp_password: str


@memoize
def read_cfg() -> SMTPCFG:
    """Read configuration from smtpcred.yaml."""
    try:
        # noinspection PyArgumentEqualDefault
        with open("smtpcred.yaml", "r", encoding="ascii") as cfgfile:
            return yaml.safe_load(cfgfile)
    except FileNotFoundError:
        logging.error(
            "%s %s",
            "ERROR: smtpcred.yaml was not found,",
            "I created a template, please fill.",
        )
        with open("smtpcred.yaml", "x", encoding="ascii") as cfgfile:
            print("smtp_server : YOURSMTPSERVER", file=cfgfile)
            print("smtp_port : YOURSMTPSSLSERVERPORT465", file=cfgfile)
            print("smtp_user : YOURSMTPUSERNAME", file=cfgfile)
            print("smtp_password : YOURSMTPPASSWORD ", file=cfgfile)
        raise


async def mailmessagewithfile(
    mailreceipient: str, mailsubject: str, attachmentfile: AsyncPath
) -> None:
    """Create an Email with the attachment and send."""
    logging.info("Start sending %s to %s", attachmentfile.name, mailreceipient)
    smtp_creds: SMTPCFG = read_cfg()
    mailmessage = MIMEMultipart()
    mailmessage["From"] = smtp_creds["smtp_user"]
    mailmessage["Subject"] = mailsubject
    mailmessage["To"] = mailreceipient
    # mailmessage["Bcc"] = receiver_email
    part = MIMEBase(_maintype="application", _subtype="octet-stream")
    try:
        async with attachmentfile.open("rb") as _theattachment:
            part.set_payload(payload=await _theattachment.read())
    except FileNotFoundError:
        logging.error(
            "ERROR: File %s not found in %s.",
            attachmentfile,
            _ATTACHMENTFOLDER,
        )
        return
    encoders.encode_base64(part)
    part.add_header(
        _name="Content-Disposition",
        _value=f"attachment; filename= {attachmentfile.name}",
    )
    mailmessage.attach(part)
    try:
        async with SMTP(
            hostname=smtp_creds["smtp_server"],
            port=smtp_creds["smtp_port"],
            username=smtp_creds["smtp_user"],
            password=smtp_creds["smtp_password"],
            start_tls=False,
            use_tls=True,
        ) as send_server:
            await send_server.send_message(
                mailmessage,
                sender=smtp_creds["smtp_user"],
                recipients=mailreceipient,
            )
    except Exception as e:
        logging.error(
            "ERROR: Sending %s to %s failed: %s",
            attachmentfile.name,
            mailreceipient,
            e,
        )
        return


async def mainmethod() -> None:
    """Async method for the main task."""
    setuplogger()
    logging.debug("Aufruf mit %s", sys.argv)
    therecipient: str = sys.argv[1] if len(sys.argv) > 1 else "bastian.ebeling@web.de"
    thesubject: str = sys.argv[2] if len(sys.argv) > 2 else "Betreff"
    attachmentstosend: list[AsyncPath] = [
        file
        async for file in _ATTACHMENTFOLDER.iterdir()
        if await file.is_file() and file.name != ".PUT_YOUR_ATTACHMENTS_HERE"
    ]
    logging.debug("%s", attachmentstosend)
    if not attachmentstosend:
        logging.warning("No attachments found in the folder.")
        return

    coroutines = [
        mailmessagewithfile(
            mailreceipient=therecipient,
            mailsubject=f"{thesubject} {idx}/{len(attachmentstosend)}",
            attachmentfile=theattachment,
        )
        for idx, theattachment in enumerate(attachmentstosend, start=1)
    ]
    await asyncio.gather(*coroutines)


def setuplogger():
    """
    Configures the logging system with a specific format and logging level.

    The logging format includes the following fields:
        - Timestamp of the log entry.
        - Log level (e.g., DEBUG, INFO, WARNING, etc.).
        - Process ID (PID).
        - Thread native ID (ThID).
        - Log message.

    The logging level is set to DEBUG if the `__debug__` flag is True,
    otherwise it is set to INFO.

    Additionally, a custom filter (`thread_native_id_filter`)
    is added to the root logger.

    Note:
        Ensure that `thread_native_id_filter` is defined before.
    """
    the_format: str = "\t".join(
        [
            "%(asctime)s",
            "%(levelname)s",
            "PID %(process)d",
            "ThID %(thread_native)d",
            "%(message)s",
        ]
    )
    logging.getLogger().addFilter(thread_native_id_filter)
    logging.basicConfig(
        level=logging.DEBUG if __debug__ else logging.INFO, format=the_format
    )


if __name__ == "__main__":
    asyncio.run(mainmethod())
