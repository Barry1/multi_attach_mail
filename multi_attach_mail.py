"""Send attachments as separate emails (refactored)."""

import asyncio
import os
import sys
import time
from email.message import EmailMessage
from logging import DEBUG, INFO, Logger, basicConfig, getLogger
from typing import Final, TypedDict, Optional, Tuple, List

import yaml
from aiopath import AsyncPath  # type: ignore[import-untyped]
from aiosmtplib import SMTP, SMTPException
from valuefragments import memoize, thread_native_id_filter

logger: Logger = getLogger(__name__)
_ATTACHMENT_FOLDER: Final[AsyncPath] = AsyncPath("attachments")
MAX_SMTP_WORKERS: Final[int] = (
    os.process_cpu_count()
    if hasattr(os, "process_cpu_count")
    else os.cpu_count()
) or 1

# Retry configuration
_MAX_CONNECT_RETRIES: Final[int] = 3
_BASE_BACKOFF_SECONDS: Final[float] = 0.5


class SMTPCFG(TypedDict):
    """Configuration for the SMTP server."""

    smtp_server: str
    smtp_port: int
    smtp_user: str
    smtp_password: str


QueueItem = Tuple[AsyncPath, str]
QueueItemOrSentinel = Optional[QueueItem]


@memoize
def read_cfg() -> SMTPCFG:
    """Read and validate the SMTP configuration from smtpcred.yaml."""
    try:
        with open("smtpcred.yaml", encoding="utf-8") as cfgfile:
            config: object = yaml.safe_load(cfgfile)
    except FileNotFoundError:
        logger.error("smtpcred.yaml was not found. A configuration template was created.")
        # create a template file for the user
        with open("smtpcred.yaml", "x", encoding="utf-8") as cfgfile:
            cfgfile.write("smtp_server: YOURSMTPSERVER\n")
            cfgfile.write("smtp_port: 465\n")
            cfgfile.write("smtp_user: YOURSMTPUSERNAME\n")
            cfgfile.write("smtp_password: YOURSMTPPASSWORD\n")
        raise

    if not isinstance(config, dict):
        raise TypeError("smtpcred.yaml must contain a YAML mapping.")

    required_keys = {"smtp_server", "smtp_port", "smtp_user", "smtp_password"}
    missing_keys = required_keys - set(config.keys())

    if missing_keys:
        raise TypeError("Missing configuration entries in smtpcred.yaml: " + ", ".join(sorted(missing_keys)))

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
) -> Optional[EmailMessage]:
    """Create an email message with one attachment. Returns None on missing file."""
    logger.info("Preparing %s for %s", attachment_file.name, mail_recipient)

    try:
        size = (await attachment_file.stat()).st_size
    except FileNotFoundError:
        logger.error("File %s not found in %s.", attachment_file, _ATTACHMENT_FOLDER)
        return None
    except Exception:
        logger.exception("Could not stat file %s.", attachment_file)
        return None

    try:
        async with attachment_file.open("rb") as fh:
            payload: bytes = await fh.read()
    except Exception:
        logger.exception("Failed to read attachment %s.", attachment_file)
        return None

    message = EmailMessage()
    message["From"] = sender
    message["To"] = mail_recipient
    # keep subject short and safe
    message["Subject"] = str(mail_subject)[:255]
    message.set_content(f"Attached file: {attachment_file.name} ({size} bytes)")

    message.add_attachment(
        payload,
        maintype="application",
        subtype="octet-stream",
        filename=attachment_file.name,
    )

    return message


async def _connect_smtp_with_retries(smtp_config: SMTPCFG) -> SMTP:
    """Create and return a connected SMTP client with simple retries."""
    last_exc: Optional[BaseException] = None
    for attempt in range(1, _MAX_CONNECT_RETRIES + 1):
        try:
            # choose TLS mode based on port (common conventions)
            port = smtp_config["smtp_port"]
            use_tls = port == 465
            start_tls = port == 587

            smtp = SMTP(
                hostname=smtp_config["smtp_server"],
                port=port,
                username=smtp_config["smtp_user"],
                password=smtp_config["smtp_password"],
                start_tls=start_tls,
                use_tls=use_tls,
            )
            await smtp.connect()
            # if STARTTLS is requested, ensure it's negotiated
            if start_tls and not smtp.is_connected:
                raise SMTPException("STARTTLS negotiation failed")
            return smtp
        except Exception as exc:
            last_exc = exc
            backoff = _BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
            logger.warning("SMTP connect attempt %d failed: %s. Retrying in %.1fs", attempt, exc, backoff)
            await asyncio.sleep(backoff)
    # if we get here, all attempts failed
    logger.exception("All SMTP connect attempts failed.")
    raise last_exc  # type: ignore[raise-value]


async def smtp_worker(
    queue: asyncio.Queue[QueueItemOrSentinel],
    smtp_config: SMTPCFG,
    mail_recipient: str,
    sender: str,
    worker_number: int,
) -> None:
    """Process attachments using one persistent SMTP connection with reconnect logic."""
    logger.debug("Starting SMTP worker %d.", worker_number)

    smtp: Optional[SMTP] = None
    try:
        smtp = await _connect_smtp_with_retries(smtp_config)
        logger.debug("SMTP worker %d connected to %s:%d.", worker_number, smtp_config["smtp_server"], smtp_config["smtp_port"])

        while True:
            queue_item = await queue.get()
            try:
                if queue_item is None:
                    logger.debug("SMTP worker %d received shutdown signal.", worker_number)
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

                # simple send retry per message
                for attempt in range(1, 4):
                    try:
                        await smtp.send_message(message, sender=sender, recipients=[mail_recipient])
                    except Exception as exc:
                        logger.warning("Worker %d: send attempt %d for %s failed: %s", worker_number, attempt, attachment_file.name, exc)
                        await asyncio.sleep(0.5 * attempt)
                        # try to reconnect if connection seems broken
                        if not smtp.is_connected:
                            try:
                                await smtp.connect()
                            except Exception:
                                logger.debug("Worker %d: reconnect failed.", worker_number)
                        if attempt == 3:
                            logger.exception("Sending %s failed after retries.", attachment_file.name)
                    else:
                        logger.info("Successfully sent %s with subject %r.", attachment_file.name, mail_subject)
                        break
            finally:
                queue.task_done()
    except asyncio.CancelledError:
        logger.debug("SMTP worker %d cancelled.", worker_number)
        raise
    except Exception:
        logger.exception("SMTP worker %d encountered an unrecoverable error.", worker_number)
    finally:
        if smtp is not None and smtp.is_connected:
            try:
                await smtp.quit()
            except Exception:
                # best effort
                logger.debug("Error while quitting SMTP connection for worker %d.", worker_number)


async def get_attachments() -> List[AsyncPath]:
    """Return all files from the attachment folder."""
    attachments: List[AsyncPath] = []

    if not await _ATTACHMENT_FOLDER.exists():
        logger.warning("Attachment folder %s does not exist.", _ATTACHMENT_FOLDER)
        return attachments

    async for attachment_file in _ATTACHMENT_FOLDER.iterdir():
        try:
            if not await attachment_file.is_file():
                continue
        except Exception:
            logger.debug("Skipping unreadable entry %s", attachment_file)
            continue

        if attachment_file.name == ".PUT_YOUR_ATTACHMENTS_HERE":
            continue

        attachments.append(attachment_file)

    # sort for deterministic order
    attachments.sort(key=lambda p: p.name)
    return attachments


async def send_attachments(
    attachments: List[AsyncPath],
    mail_recipient: str,
    mail_subject: str,
    smtp_config: SMTPCFG,
) -> None:
    """Send all attachments using a pool of SMTP workers."""
    queue: asyncio.Queue[QueueItemOrSentinel] = asyncio.Queue()
    sender: str = smtp_config["smtp_user"]
    attachment_count: int = len(attachments)
    worker_count: int = min(MAX_SMTP_WORKERS, max(1, attachment_count))

    logger.info("Using %d SMTP workers for %d attachments.", worker_count, attachment_count)

    workers = [
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
        for attachment_number, attachment in enumerate(attachments, start=1):
            subject = f"{mail_subject} {attachment_number}/{attachment_count}"
            await queue.put((attachment, subject))

        # wait until all items processed
        await queue.join()

        # send shutdown sentinel for each worker
        for _ in workers:
            await queue.put(None)

        # wait until workers have acknowledged sentinels
        await queue.join()

        # wait for worker tasks to finish
        await asyncio.gather(*workers)
    except Exception:
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        raise


def get_command_line_arguments() -> Tuple[str, str]:
    """Return recipient and subject from command-line arguments."""
    mail_recipient = sys.argv[1] if len(sys.argv) > 1 else "bastian.ebeling@web.de"
    mail_subject = sys.argv[2] if len(sys.argv) > 2 else "Betreff"
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
    mail_recipient, mail_subject = get_command_line_arguments()

    logger.info("Sending %d attachments using up to %d SMTP workers.", len(attachments), MAX_SMTP_WORKERS)

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

    logger.addFilter(thread_native_id_filter)
    basicConfig(level=DEBUG if __debug__ else INFO, format=the_format)


if __name__ == "__main__":
    asyncio.run(main())
