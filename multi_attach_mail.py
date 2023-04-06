"""send attachments"""
import asyncio
import os
import ssl
import sys
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart

import yaml
from aiosmtplib import SMTP
from valuefragments import memoize

# from email.mime.text import MIMEText
# https://realpython.com/python-send-email/#adding-attachments-using-the-email-package

infourls = ["https://hilfe.web.de/pop-imap/imap/imap-serverdaten.html"]


@memoize
def read_cfg() -> dict[str, str]:
    """Read configuration from smtpcred.yaml"""
    try:
        with open("smtpcred.yaml", "r") as cfgfile:
            return yaml.safe_load(cfgfile)
    except FileNotFoundError:
        print(
            "ERROR: smtpcred.yaml was not found, I created a template, please fill.",
            file=sys.stderr,
        )
        with open("smtpcred.yaml", "x") as cfgfile:
            print("smtp_server : YOURSMTPSERVER", file=cfgfile)
            print("smtp_port : YOURSMTPSSLSERVERPORT465", file=cfgfile)
            print("smtp_user : YOURSMTPUSERNAME", file=cfgfile)
            print("smtp_password : YOURSMTPPASSWORD ", file=cfgfile)
        raise


async def mailmessagewithfile(
    mailreceipient: str, mailsubject: str, attachmentfilename: str
) -> None:
    """Create an Email with the attachment and send."""
    print("Start")
    smtp_creds = read_cfg()
    mailmessage = MIMEMultipart()
    mailmessage["From"] = smtp_creds["smtp_user"]
    mailmessage["Subject"] = mailsubject
    mailmessage["To"] = mailreceipient
    # mailmessage["Bcc"] = receiver_email
    print(attachmentfilename)
    with open(attachmentfilename, "rb") as _theattachment:
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
        await send_server.sendmail(
            sender=smtp_creds["smtp_user"],
            recipients=mailreceipient,
            message=mailmessage.as_string(),
        )
    print("END")


async def mainmethod():
    """async method for the main task"""
    RECEIPIENT = "bastian.ebeling@gmail.com"
    SUBJECT = "Email with attachment"
    attachmentstosend = [
        f"attachments{os.sep}{attachmenttosend}"
        for attachmenttosend in os.listdir("attachments")
        if attachmenttosend != ".PUT_YOUR_ATTACHMENTS_HERE"
    ]
    coroutines = [
        mailmessagewithfile(RECEIPIENT, SUBJECT, theattachment)
        for theattachment in attachmentstosend
    ]
    await asyncio.gather(*coroutines)


if __name__ == "__main__":
    asyncio.run(mainmethod())
