"""Small SMTP helper shared by claim submissions and scheduled reminders."""
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path
import mimetypes
import smtplib
import ssl


def smtp_is_configured(config) -> bool:
    return bool(
        config.get("MAIL_SERVER")
        and config.get("MAIL_PORT")
        and config.get("MAIL_DEFAULT_SENDER")
        and config.get("MAIL_USERNAME")
        and config.get("MAIL_PASSWORD")
    )


def send_email(config, *, recipient: str, subject: str, body: str,
               reply_to: str | None = None, attachments: list[tuple[str, str]] | None = None) -> str:
    """Send one email using environment-configured SMTP; return its Message-ID.

    attachments is a sequence of (absolute_path, display_filename) pairs.
    A successful return means the configured SMTP server accepted the message,
    not that the recipient's mail system or a manufacturer acknowledged it.
    """
    if not smtp_is_configured(config):
        raise RuntimeError(
            "Email is not configured. Set MAIL_SERVER, MAIL_PORT, MAIL_USERNAME, "
            "MAIL_PASSWORD and MAIL_DEFAULT_SENDER in your .env file."
        )

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config["MAIL_DEFAULT_SENDER"]
    message["To"] = recipient
    if reply_to:
        message["Reply-To"] = reply_to
    message["Message-ID"] = make_msgid(domain=None)
    message.set_content(body)

    total_bytes = 0
    for file_path, display_name in attachments or []:
        path = Path(file_path)
        if not path.is_file():
            continue
        payload = path.read_bytes()
        total_bytes += len(payload)
        if total_bytes > 10 * 1024 * 1024:
            raise ValueError("Email attachments exceed the 10 MB combined limit. Remove a file and try again.")
        content_type, _encoding = mimetypes.guess_type(display_name)
        maintype, subtype = (content_type or "application/octet-stream").split("/", 1)
        message.add_attachment(payload, maintype=maintype, subtype=subtype, filename=display_name)

    host = config["MAIL_SERVER"]
    port = int(config["MAIL_PORT"])
    timeout = int(config.get("MAIL_TIMEOUT", 20))
    use_ssl = bool(config.get("MAIL_USE_SSL"))
    use_tls = bool(config.get("MAIL_USE_TLS"))
    if use_ssl and use_tls:
        raise RuntimeError("Choose either MAIL_USE_SSL or MAIL_USE_TLS, not both.")

    context = ssl.create_default_context()
    if use_ssl:
        smtp = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
    else:
        smtp = smtplib.SMTP(host, port, timeout=timeout)
    try:
        smtp.ehlo()
        if use_tls:
            smtp.starttls(context=context)
            smtp.ehlo()
        smtp.login(config["MAIL_USERNAME"], config["MAIL_PASSWORD"])
        smtp.send_message(message)
    finally:
        try:
            smtp.quit()
        except Exception:
            smtp.close()
    return message["Message-ID"]
