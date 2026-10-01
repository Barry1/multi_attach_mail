"""Send attachments as separate emails."""

import asyncio
import sys
from email.message import EmailMessage
from logging import DEBUG, INFO, Logger, basicConfig, getLogger
from typing import Final, TypedDict

import yaml
from aiopath import AsyncPath  # type: ignore[import-untyped]
from aiosmtplib import SMTP
from valuefragments import memoize, thread_native_id_filter

logger: Logger = getLogger(name=__name__)

_ATTACHMENT_FOLDER: Final = AsyncPath("attachments")
MAX_SMTP_WORKERS: Final[int] = 4


class SMTPCFG(TypedDict):
    """Configuration for the SMTP server."""

    smtp_server: str
    smtp_port: int
    smtp_user: str
    smtp_password: str


@memoize
def read_cfg() -> SMTPCFG:
    """Read configuration from smtpcred.yaml."""
    try:
        with open("smtpcred.yaml", encoding="utf-8") as cfgfile:
            config = yaml.safe_load(cfgfile)
    except FileNotFoundError:
        logger.error(
            "smtpcred.yaml was not found. "
            "A configuration template was created."
        )
        with open("smtpcred.yaml", "x", encoding="utf-8") as cfgfile:
            cfgfile.write("smtp_server: YOURSMTPSERVER\n")
            cfgfile.write("smtp_port: 465\n")
            cfgfile.write("smtp_user: YOURSMTPUSERNAME\n")
            cfgfile.write("smtp_password: YOURSMTPPASSWORD\n")
        raise

    if not isinstance(config, dict):
        raise TypeError("smtpcred.yaml must contain a YAML mapping.")

    required_keys = {
        "smtp_server",
        "smtp_port",
        "smtp_user",
        "smtp_password",
    }

    missing_keys = required_keys - config.keys()
    if missing_keys:
        raise ValueError(
            "Missing configuration entries in smtpcred.yaml: "
            + ", ".join(sorted(missing_keys))
        )

    if not isinstance(config["smtp_server"], str):
        raise TypeError("smtp_server must be a string.")

    if not isinstance(config["smtp_port"], int):
        raise TypeError("smtp_port must be an integer.")

    if not isinstance(config["smtp_user"], str):
        raise TypeError("smtp_user must be a string.")

    if not isinstance(config["smtp_password"], str):
        raise TypeError("smtp_password must be a string.")

    return config  # type: ignore[return-value]


async def create_message(
    mail_recipient: str,
    mail_subject: str,
    attachment_file: AsyncPath,
    sender: str,
) -> EmailMessage | None:
    """Create an email message with one attachment."""
    logger.info(
        "Preparing %s for %s",
        attachment_file.name,
        mail_recipient,
    )

    try:
        async with attachment_file.open("rb") as attachment:
            payload = await attachment.read()
    except FileNotFoundError:
        logger.error(
            "File %s not found in %s.",
            attachment_file,
            _ATTACHMENT_FOLDER,
        )
        return None

    message = EmailMessage()
    message["From"] = sender
    message["To"] = mail_recipient
    message["Subject"] = mail_subject

    message.add_attachment(
        payload,
        maintype="application",
        subtype="octet-stream",
        filename=attachment_file.name,
    )

    return message


async def smtp_worker(
    queue: asyncio.Queue[AsyncPath | None],
    smtp_config: SMTPCFG,
    mail_recipient: str,
    mail_subject: str,
    sender: str,
    worker_number: int,
) -> None:
    """Process attachments using one persistent SMTP connection."""
    logger.debug("Starting SMTP worker %d.", worker_number)

    async with SMTP(
        hostname=smtp_config["smtp_server"],
        port=smtp_config["smtp_port"],
        username=smtp_config["smtp_user"],
        password=smtp_config["smtp_password"],
        start_tls=False,
        use_tls=True,
    ) as smtp:
        logger.debug(
            "SMTP worker %d connected to %s:%d.",
            worker_number,
            smtp_config["smtp_server"],
            smtp_config["smtp_port"],
        )

        while True:
            attachment_file = await queue.get()

            try:
                if attachment_file is None:
                    logger.debug(
                        "SMTP worker %d received shutdown signal.",
                        worker_number,
                    )
                    return

                message = await create_message(
                    mail_recipient=mail_recipient,
                    mail_subject=mail_subject,
                    attachment_file=attachment_file,
                    sender=sender,
                )

                if message is None:
                    continue

                try:
                    await smtp.send_message(
                        message,
                        sender=sender,
                        recipients=mail_recipient,
                    )
                except Exception:
                    logger.exception(
                        "Sending %s to %s failed.",
                        attachment_file.name,
                        mail_recipient,
                    )
                else:
                    logger.info(
                        "Successfully sent %s to %s.",
                        attachment_file.name,
                        mail_recipient,
                    )
            finally:
                queue.task_done()


async def get_attachments() -> list[AsyncPath]:
    """Return all files from the attachment folder."""
    return [
        attachment_file
        async for attachment_file in _ATTACHMENT_FOLDER.iterdir()
        if await attachment_file.is_file()
        and attachment_file.name != ".PUT_YOUR_ATTACHMENTS_HERE"
    ]


async def send_attachments(
    attachments: list[AsyncPath],
    mail_recipient: str,
    mail_subject: str,
    smtp_config: SMTPCFG,
) -> None:
    """Send all attachments using a pool of SMTP workers."""
    queue: asyncio.Queue[AsyncPath | None] = asyncio.Queue()

    sender = smtp_config["smtp_user"]

    workers = [
        asyncio.create_task(
            smtp_worker(
                queue=queue,
                smtp_config=smtp_config,
                mail_recipient=mail_recipient,
                mail_subject=mail_subject,
                sender=sender,
                worker_number=worker_number,
            )
        )
        for worker_number in range(1, MAX_SMTP_WORKERS + 1)
    ]

    try:
        for attachment in attachments:
            await queue.put(attachment)

        await queue.join()

        for _ in workers:
            await queue.put(None)

        await queue.join()

        await asyncio.gather(*workers)
    except Exception:
        for worker in workers:
            worker.cancel()

        await asyncio.gather(*workers, return_exceptions=True)
        raise


def get_command_line_arguments(
    attachment_count: int,
) -> tuple[str, str]:
    """Return recipient and subject from command-line arguments."""
    mail_recipient = (
        sys.argv[1] if len(sys.argv) > 1 else "bastian.ebeling@web.de"
    )

    mail_subject = (
        sys.argv[2] if len(sys.argv) > 2 else f"Betreff 1/{attachment_count}"
    )

    return mail_recipient, mail_subject


async def main() -> None:
    """Run the main task."""
    setuplogger()

    logger.debug("Invocation with %s", sys.argv)

    attachments = await get_attachments()

    if not attachments:
        logger.warning("No attachments found in the folder.")
        return

    smtp_config = read_cfg()

    mail_recipient, mail_subject = get_command_line_arguments(len(attachments))

    logger.info(
        "Sending %d attachments using %d SMTP workers.",
        len(attachments),
        MAX_SMTP_WORKERS,
    )

    await send_attachments(
        attachments=attachments,
        mail_recipient=mail_recipient,
        mail_subject=mail_subject,
        smtp_config=smtp_config,
    )


def setuplogger() -> None:
    """Configure logging."""
    the_format = (
        "%(asctime)s\t"
        "%(levelname)s\t"
        "PID %(process)d\t"
        "ThID %(thread_native)d\t"
        "%(message)s"
    )

    logger.addFilter(filter=thread_native_id_filter)

    basicConfig(
        level=DEBUG if __debug__ else INFO,
        format=the_format,
    )


if __name__ == "__main__":
    asyncio.run(main())
