"""Send attachments."""

import asyncio
import logging
import sys
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import TypedDict

import yaml
from aiopath import AsyncPath
from aiosmtplib import SMTP
from valuefragments import memoize

# from email.mime.text import MIMEText
# https://realpython.com/python-send-email/#adding-attachments-using-the-email-package

infourls: list[str] = [
    "https://hilfe.web.de/pop-imap/imap/imap-serverdaten.html",
    "https://hilfe.gmx.net/pop-imap/pop3/serverdaten.html",
]

_ATTACHMENTFOLDER = AsyncPath("attachments")

logging.basicConfig(
    level=logging.DEBUG if __debug__ else logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)


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
    mailreceipient: str, mailsubject: str, attachmentfile: Path
) -> None:
    """Create an Email with the attachment and send."""
    #    print("Start")
    logging.info("Sending %s to %s", attachmentfile.name, mailreceipient)
    smtp_creds: SMTPCFG = read_cfg()
    mailmessage = MIMEMultipart()
    mailmessage["From"] = smtp_creds["smtp_user"]
    mailmessage["Subject"] = mailsubject
    mailmessage["To"] = mailreceipient
    # mailmessage["Bcc"] = receiver_email
    #    print(attachmentfilename)
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
        print(
            f"ERROR: Sending {attachmentfile.name} to {mailreceipient} failed: {e}",
            file=sys.stderr,
        )
        return


async def mainmethod() -> None:
    """Async method for the main task."""
    logging.debug("Aufruf mit %s", sys.argv)
    therecipient: str = (
        sys.argv[1] if len(sys.argv) > 1 else "bastian.ebeling@web.de"
    )
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


if __name__ == "__main__":
    asyncio.run(mainmethod())
