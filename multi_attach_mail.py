"""send attachments"""

import asyncio
import os
import sys
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from typing import TypedDict

import yaml
from aiosmtplib import SMTP
from valuefragments import memoize

# from email.mime.text import MIMEText
# https://realpython.com/python-send-email/#adding-attachments-using-the-email-package

infourls = ["https://hilfe.web.de/pop-imap/imap/imap-serverdaten.html"]

_ATTACHMENTFOLDER = "attachments"


class SMTPCFG(TypedDict):
    """Just a Type-Class for the configuration."""

    smtp_server: str
    smtp_port: int
    smtp_user: str
    smtp_password: str


@memoize
def read_cfg() -> SMTPCFG:
    """Read configuration from smtpcred.yaml"""
    try:
        with open("smtpcred.yaml", "r", encoding="ascii") as cfgfile:
            return yaml.safe_load(cfgfile)
    except FileNotFoundError:
        print(
            "ERROR: smtpcred.yaml was not found,",
            "I created a template, please fill.",
            file=sys.stderr,
        )
        with open("smtpcred.yaml", "x", encoding="ascii") as cfgfile:
            print("smtp_server : YOURSMTPSERVER", file=cfgfile)
            print("smtp_port : YOURSMTPSSLSERVERPORT465", file=cfgfile)
            print("smtp_user : YOURSMTPUSERNAME", file=cfgfile)
            print("smtp_password : YOURSMTPPASSWORD ", file=cfgfile)
        raise


async def mailmessagewithfile(
    mailreceipient: str, mailsubject: str, attachmentfilename: str
) -> None:
    """Create an Email with the attachment and send."""
    #    print("Start")
    print(f"Sending {attachmentfilename} to {mailreceipient}")
    smtp_creds: SMTPCFG = read_cfg()
    mailmessage = MIMEMultipart()
    mailmessage["From"] = smtp_creds["smtp_user"]
    mailmessage["Subject"] = mailsubject
    mailmessage["To"] = mailreceipient
    # mailmessage["Bcc"] = receiver_email
    #    print(attachmentfilename)
    with open(
        _ATTACHMENTFOLDER + os.sep + attachmentfilename, "rb"
    ) as _theattachment:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(_theattachment.read())
    encoders.encode_base64(part)
    part.add_header(
        "Content-Disposition", f"attachment; filename= {attachmentfilename}"
    )
    mailmessage.attach(part)
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


async def mainmethod() -> None:
    """async method for the main task"""
    print(sys.argv)
    therecipient: str = (
        sys.argv[1] if len(sys.argv) > 1 else "bastian.ebeling@web.de"
    )
    thesubject: str = sys.argv[2] if len(sys.argv) > 2 else "Betreff"
    attachmentstosend: list[str] = [
        attachmenttosend
        for attachmenttosend in os.listdir(_ATTACHMENTFOLDER)
        if attachmenttosend != ".PUT_YOUR_ATTACHMENTS_HERE"
    ]
    attachmentcount: int = len(attachmentstosend)
    coroutines = [
        mailmessagewithfile(
            therecipient,
            f"{thesubject} {idx}/{attachmentcount}",
            theattachment,
        )
        for idx, theattachment in enumerate(attachmentstosend, start=1)
    ]
    await asyncio.gather(*coroutines)


if __name__ == "__main__":
    asyncio.run(mainmethod())
