"""Send attachments as separate emails (refactored)."""

import asyncio
import os
from email.message import EmailMessage
from logging import DEBUG, INFO, Logger, basicConfig, getLogger
from sys import argv as sys_argv
from typing import Final

from aiopath import AsyncPath  # type: ignore[import-untyped]
from aiosmtplib import SMTP, SMTPException
from pydantic import BaseModel, Field
from valuefragments import memoize, thread_native_id_filter
from yaml import safe_load

logger: Logger = getLogger(__name__)

_ATTACHMENT_FOLDER: Final[AsyncPath] = AsyncPath("attachments")

MAX_SMTP_WORKERS: Final[int] = (
    os.process_cpu_count()
    if hasattr(os, "process_cpu_count")
    else os.cpu_count()
) or 1

_MAX_CONNECT_RETRIES: Final[int] = 3
_BASE_BACKOFF_SECONDS: Final[float] = 0.5


class SMTPConfig(BaseModel):
    """Configuration for the SMTP server."""

    smtp_server: str
    smtp_port: int = Field(ge=1, le=65535)
    smtp_user: str
    smtp_password: str


QueueItem = tuple[AsyncPath, str]
QueueItemOrSentinel = QueueItem | None


@memoize
def read_cfg() -> SMTPConfig:
    """Read and validate the SMTP configuration from smtpcred.yaml."""
    try:
        with open("smtpcred.yaml", encoding="utf-8") as cfgfile:
            config: object = safe_load(cfgfile)
    except FileNotFoundError:
        logger.error("smtpcred.yaml not found. Creating a template file.")
        with open("smtpcred.yaml", "x", encoding="utf-8") as cfgfile:
            cfgfile.write(
                "smtp_server: YOURSMTPSERVER\n"
                "smtp_port: 465\n"
                "smtp_user: YOURSMTPUSERNAME\n"
                "smtp_password: YOURSMTPPASSWORD\n"
            )
        raise

    return SMTPConfig.model_validate(config)


async def create_message(
    attachment_file: AsyncPath,
    mail_subject: str,
    sender: str,
    recipient: str,
) -> EmailMessage | None:
    """Create an email message containing one attachment."""
    logger.debug("Preparing attachment %s", attachment_file)

    try:
        size = (await attachment_file.stat()).st_size
    except FileNotFoundError:
        logger.error("Attachment %s no longer exists.", attachment_file)
        return None
    except Exception:
        logger.exception("Could not stat attachment %s.", attachment_file)
        return None

    try:
        payload = await attachment_file.read_bytes()
    except FileNotFoundError:
        logger.error("Attachment %s no longer exists.", attachment_file)
        return None
    except Exception:
        logger.exception("Could not read attachment %s.", attachment_file)
        return None

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = mail_subject[:255]
    message.set_content(
        f"Attached file: {attachment_file.name} ({size} bytes)"
    )
    message.add_attachment(
        payload,
        maintype="application",
        subtype="octet-stream",
        filename=attachment_file.name,
    )

    return message


async def _connect_smtp_with_retries(
    smtp_config: SMTPConfig,
) -> SMTP:
    """Create and connect an SMTP client with retries."""
    last_exc: Exception | None = None

    for attempt in range(1, _MAX_CONNECT_RETRIES + 1):
        try:
            use_tls = smtp_config.smtp_port == 465
            start_tls = smtp_config.smtp_port == 587

            smtp = SMTP(
                hostname=smtp_config.smtp_server,
                port=smtp_config.smtp_port,
                use_tls=use_tls,
                start_tls=start_tls,
                username=smtp_config.smtp_user,
                password=smtp_config.smtp_password,
            )

            await smtp.connect()

            if start_tls and not smtp.is_connected:
                raise SMTPException("SMTP connection was not established.")

            return smtp
        except Exception as exc:
            last_exc = exc
            if attempt < _MAX_CONNECT_RETRIES:
                delay = _BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "SMTP connection attempt %d/%d failed: %s. "
                    "Retrying in %.1f seconds.",
                    attempt,
                    _MAX_CONNECT_RETRIES,
                    last_exc,
                    delay,
                )
                await asyncio.sleep(delay)

    logger.exception("All SMTP connect attempts failed.")
    if last_exc is not None:
        raise last_exc

    raise RuntimeError("SMTP connection failed without an exception.")


async def smtp_worker(
    queue: asyncio.Queue[QueueItemOrSentinel],
    smtp_config: SMTPConfig,
    recipient: str,
) -> None:
    """Process queued attachments using one persistent SMTP connection."""
    smtp = await _connect_smtp_with_retries(smtp_config)

    try:
        while True:
            item = await queue.get()

            try:
                if item is None:
                    return

                attachment_file, mail_subject = item
                message = await create_message(
                    attachment_file=attachment_file,
                    mail_subject=mail_subject,
                    sender=smtp_config.smtp_user,
                    recipient=recipient,
                )

                if message is None:
                    continue

                for attempt in range(1, 4):
                    try:
                        await smtp.send_message(message)
                        logger.info(
                            "Sent %s successfully.",
                            attachment_file,
                        )
                        break
                    except Exception as exc:
                        logger.warning(
                            "Sending %s failed (attempt %d/3): %s",
                            attachment_file,
                            attempt,
                            exc,
                        )

                        if attempt == 3:
                            logger.exception(
                                "Giving up on %s after 3 attempts.",
                                attachment_file,
                            )
                            break

                        await asyncio.sleep(_BASE_BACKOFF_SECONDS * attempt)

                        if not smtp.is_connected:
                            smtp = await _connect_smtp_with_retries(
                                smtp_config
                            )
            finally:
                queue.task_done()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("SMTP worker terminated unexpectedly.")
        raise
    finally:
        try:
            await smtp.quit()
        except Exception:
            logger.debug("SMTP connection could not be closed cleanly.")


async def get_attachments() -> list[AsyncPath]:
    """Return all attachments in deterministic order."""
    attachments: list[AsyncPath] = []

    if not await _ATTACHMENT_FOLDER.exists():
        logger.error(
            "Attachment folder %s does not exist.", _ATTACHMENT_FOLDER
        )
        return attachments

    async for path in _ATTACHMENT_FOLDER.iterdir():
        if await path.is_file() and path.name != ".PUT_YOUR_ATTACHMENTS_HERE":
            attachments.append(path)

    attachments.sort(key=lambda path: path.name)
    return attachments


async def send_attachments(
    attachments: list[AsyncPath],
    recipient: str,
    mail_subject: str,
    smtp_config: SMTPConfig,
) -> None:
    """Send all attachments using a bounded set of SMTP workers."""
    queue: asyncio.Queue[QueueItemOrSentinel] = asyncio.Queue()

    attachment_count = len(attachments)
    worker_count = min(MAX_SMTP_WORKERS, max(1, attachment_count))

    workers = [
        asyncio.create_task(
            smtp_worker(
                queue=queue,
                smtp_config=smtp_config,
                recipient=recipient,
            )
        )
        for _ in range(worker_count)
    ]

    try:
        for attachment_number, attachment_file in enumerate(
            attachments,
            start=1,
        ):
            subject = f"{mail_subject} {attachment_number}/{attachment_count}"
            await queue.put((attachment_file, subject))

        await queue.join()

        for _ in workers:
            await queue.put(None)

        await queue.join()
        await asyncio.gather(*workers)
    except asyncio.CancelledError:
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        raise
    except Exception:
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        raise


def get_command_line_arguments() -> tuple[str, str]:
    """Return recipient and subject from command-line arguments."""
    recipient = sys_argv[1] if len(sys_argv) > 1 else "bastian.ebeling@web.de"
    mail_subject = sys_argv[2] if len(sys_argv) > 2 else "Betreff"
    return recipient, mail_subject


async def main() -> None:
    """Run the mail attachment sender."""
    setuplogger()

    logger.debug("Command line arguments: %s", sys_argv)

    attachments = await get_attachments()
    if not attachments:
        logger.info("No attachments found.")
        return

    smtp_config = read_cfg()
    recipient, mail_subject = get_command_line_arguments()

    await send_attachments(
        attachments=attachments,
        recipient=recipient,
        mail_subject=mail_subject,
        smtp_config=smtp_config,
    )


def setuplogger() -> None:
    """Configure application logging."""
    logger.addFilter(thread_native_id_filter)
    basicConfig(
        level=DEBUG if __debug__ else INFO,
        format=(
            "%(asctime)s\t"
            "%(levelname)s\t"
            "PID %(process)d\t"
            "ThID %(thread_native)d\t"
            "%(message)s"
        ),
    )


if __name__ == "__main__":
    asyncio.run(main())
