"""send attachments"""
import asyncio
import os

# import smtplib
import ssl
import sys
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart

import aiosmtplib
import yaml

# from email.mime.text import MIMEText
# https://realpython.com/python-send-email/#adding-attachments-using-the-email-package

infourls = ["https://hilfe.web.de/pop-imap/imap/imap-serverdaten.html"]


def read_cfg() -> dict[str, str]:
    """read configuration from smtpcred.yaml"""
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


def mailmessagewithfile(
    mailreceipient: str, mailsubject: str, attachmentfilename: str
) -> None:
    """Create an Email with the attachment and send."""
    smtp_creds = read_cfg()
    mailmessage = MIMEMultipart()
    mailmessage["From"] = smtp_creds["smtp_user"]
    mailmessage["Subject"] = mailsubject
    mailmessage["To"] = mailreceipient
    # mailmessage["Bcc"] = receiver_email
    with open(attachmentfilename, "rb") as _theattachment:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(_theattachment.read())
    encoders.encode_base64(part)
    part.add_header(
        "Content-Disposition", f"attachment; filename= {attachmentfilename}"
    )
    mailmessage.attach(part)
    thesslcontext = ssl.create_default_context()
    with smtplib.SMTP_SSL(
        smtp_creds["smtp_server"], smtp_creds["smtp_port"], context=thesslcontext
    ) as send_server:
        send_server.login(smtp_creds["smtp_user"], smtp_creds["smtp_password"])
        send_server.sendmail(
            from_addr=smtp_creds["smtp_user"],
            to_addrs=mailreceipient,
            msg=mailmessage.as_string(),
        )


if __name__ == "__main__":
    RECEIPIENT = "bastian.ebeling@gmail.com"
    SUBJECT = "Email with attachment"
    attachmentstosend = [
        attachmenttosend
        for attachmenttosend in os.listdir("attachments")
        if attachmenttosend != ".PUT_YOUR_ATTACHMENTS_HERE"
    ]
    for theattachment in attachmentstosend:
        mailmessagewithfile(RECEIPIENT, SUBJECT, theattachment)
        print(theattachment)
