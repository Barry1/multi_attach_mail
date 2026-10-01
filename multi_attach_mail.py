"""Send attachments as separate emails."""

import asyncio
import os
import sys
from email.message import EmailMessage
from logging import DEBUG, INFO, Logger, basicConfig, getLogger
from typing import Final, TypedDict

import yaml
from aiopath import AsyncPath  # type: ignore[import-untyped]
from aiosmtplib import SMTP
from valuefragments import memoize, thread_native_id_filter

logger: Logger = getLogger(__name__)
_ATTACHMENT_FOLDER: Final = AsyncPath("attachments")
MAX_SMTP_WORKERS: Final[int] = (
    os.process_cpu_count()
    if hasattr(os, "process_cpu_count")
    else os.cpu_count()
) or 1


class SMTPCFG(TypedDict):
    """Configuration for the SMTP server."""

    smtp_server: str
    smtp_port: int
    smtp_user: str
    smtp_password: str


QueueItem = tuple[AsyncPath, str]
QueueItemOrSentinel = QueueItem | None


@memoize
def read_cfg() -> SMTPCFG:
    """Read and validate the SMTP configuration."""
    try:
        with open("smtpcred.yaml", encoding="utf-8") as cfgfile:
            config: object = yaml.safe_load(cfgfile)
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

    required_keys: Final[frozenset[str]] = frozenset(
        {
            "smtp_server",
            "smtp_port",
            "smtp_user",
            "smtp_password",
        }
    )

    missing_keys = required_keys - config.keys()

    if missing_keys:
        raise TypeError(
            "Missing configuration entries in smtpcred.yaml: "
            + ", ".join(sorted(missing_keys))
        )

    smtp_server = config["smtp_server"]
    smtp_port = config["smtp_port"]
    smtp_user = config["smtp_user"]
    smtp_password = config["smtp_password"]

    if not isinstance(smtp_server, str):
        raise TypeError("smtp_server must be a string.")

    if not isinstance(smtp_port, int):
        raise TypeError("smtp_port must be an integer.")

    if not isinstance(smtp_user, str):
        raise TypeError("smtp_user must be a string.")

    if not isinstance(smtp_password, str):
        raise TypeError("smtp_password must be a string.")

    return SMTPCFG(
        smtp_server=smtp_server,
        smtp_port=smtp_port,
        smtp_user=smtp_user,
        smtp_password=smtp_password,
    )


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
            payload: bytes = await attachment.read()
    except FileNotFoundError:
        logger.error(
            "File %s not found in %s.",
            attachment_file,
            _ATTACHMENT_FOLDER,
        )
        return None

    message: EmailMessage = EmailMessage()

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
    queue: asyncio.Queue[QueueItemOrSentinel],
    smtp_config: SMTPCFG,
    mail_recipient: str,
    sender: str,
    worker_number: int,
) -> None:
    """Process attachments using one persistent SMTP connection."""
    logger.debug(
        "Starting SMTP worker %d.",
        worker_number,
    )

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
            queue_item: QueueItemOrSentinel = await queue.get()

            try:
                if queue_item is None:
                    logger.debug(
                        "SMTP worker %d received shutdown signal.",
                        worker_number,
                    )
                    return

                attachment_file, mail_subject = queue_item

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
                        "Sending %s failed.",
                        attachment_file.name,
                    )
                else:
                    logger.info(
                        "Successfully sent %s with subject %r.",
                        attachment_file.name,
                        mail_subject,
                    )
            finally:
                queue.task_done()


async def get_attachments() -> list[AsyncPath]:
    """Return all files from the attachment folder."""
    attachments: list[AsyncPath] = []

    async for attachment_file in _ATTACHMENT_FOLDER.iterdir():
        if not await attachment_file.is_file():
            continue

        if attachment_file.name == ".PUT_YOUR_ATTACHMENTS_HERE":
            continue

        attachments.append(attachment_file)

    return attachments


async def send_attachments(
    attachments: list[AsyncPath],
    mail_recipient: str,
    mail_subject: str,
    smtp_config: SMTPCFG,
) -> None:
    """Send all attachments using a pool of SMTP workers."""
    queue: asyncio.Queue[QueueItemOrSentinel] = asyncio.Queue()
    sender: str = smtp_config["smtp_user"]
    attachment_count: int = len(attachments)
    worker_count: int = min(
        MAX_SMTP_WORKERS,
        attachment_count,
    )

    logger.info(
        "Using %d SMTP workers for %d attachments.",
        worker_count,
        attachment_count,
    )

    workers: list[asyncio.Task[None]] = [
        asyncio.create_task(
            smtp_worker(
                queue=queue,
                smtp_config=smtp_config,
                mail_recipient=mail_recipient,
                sender=sender,
                worker_number=worker_number,
            )
        )
        for worker_number in range(1, worker_count + 1)
    ]

    try:
        for attachment_number, attachment in enumerate(
            attachments,
            start=1,
        ):
            subject: str = (
                f"{mail_subject} {attachment_number}/{attachment_count}"
            )

            await queue.put(
                (
                    attachment,
                    subject,
                )
            )

        await queue.join()

        for _ in workers:
            await queue.put(None)

        await queue.join()

        await asyncio.gather(*workers)

    except Exception:
        for worker in workers:
            worker.cancel()

        await asyncio.gather(
            *workers,
            return_exceptions=True,
        )

        raise


def get_command_line_arguments() -> tuple[str, str]:
    """Return recipient and subject from command-line arguments."""
    mail_recipient: str = (
        sys.argv[1] if len(sys.argv) > 1 else "bastian.ebeling@web.de"
    )

    mail_subject: str = sys.argv[2] if len(sys.argv) > 2 else "Betreff"

    return mail_recipient, mail_subject


async def main() -> None:
    """Run the main task."""
    setuplogger()

    logger.debug(
        "Invocation with %s",
        sys.argv,
    )

    attachments: list[AsyncPath] = await get_attachments()

    if not attachments:
        logger.warning("No attachments found in the folder.")
        return

    smtp_config: SMTPCFG = read_cfg()

    mail_recipient, mail_subject = get_command_line_arguments()

    logger.info(
        "Sending %d attachments using up to %d SMTP workers.",
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
    the_format: str = (
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
    asyncio.run(main=main())
