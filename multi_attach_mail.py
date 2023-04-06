"""send attachments"""
import smtplib
import ssl
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import yaml

# https://realpython.com/python-send-email/#adding-attachments-using-the-email-package

infourls = ["https://hilfe.web.de/pop-imap/imap/imap-serverdaten.html"]


def read_cfg() -> dict[str, str]:
    """read configuration from smtpcred.yaml"""
    with open("smtpcred.yaml", "r") as cfgfile:
        return yaml.safe_load(cfgfile)


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
    with open(attachmentfilename, "rb") as theattachment:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(theattachment.read())
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
    mailmessagewithfile(
        "bastian.ebeling@web.de", "Test-Email mit Anhang", "multi_attach_mail.py"
    )
