from pathlib import Path
import base64
import mimetypes
from email.message import EmailMessage

import httplib2
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

CREDENTIALS_PATH = BASE_DIR / "credentials.json"

# Two authorized Gmail accounts.
GMAIL_ACCOUNTS = {
    "gmail1": {
        "token_path": BASE_DIR / "token.json",
        "email": "bsanthosh4045@gmail.com",
    },
    "gmail2": {
        "token_path": BASE_DIR / "token2.json",
        "email": "testeproject66@gmail.com",
    },
}

DEFAULT_ACCOUNT = "gmail1"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

REQUEST_TIMEOUT = 20


# ============================================================
# ACCOUNT HELPERS
# ============================================================

def get_account_id(account_id=None):
    """Return a valid Gmail account ID."""

    if account_id is None:
        return DEFAULT_ACCOUNT

    if account_id not in GMAIL_ACCOUNTS:
        raise ValueError("Invalid Gmail account selected.")

    return account_id


def get_available_gmail_accounts():
    """Return configured Gmail accounts for the website."""

    return [
        {
            "id": account_id,
            "email": account["email"],
        }
        for account_id, account in GMAIL_ACCOUNTS.items()
    ]


# ============================================================
# SAVE GOOGLE CREDENTIALS
# ============================================================

def save_credentials(creds, account_id=None):
    """Save credentials to the selected account's token file."""

    account_id = get_account_id(account_id)
    token_path = GMAIL_ACCOUNTS[account_id]["token_path"]

    token_path.write_text(
        creds.to_json(),
        encoding="utf-8",
    )


# ============================================================
# LOAD AND REFRESH GOOGLE CREDENTIALS
# ============================================================

def get_credentials(account_id=None):
    """Load or refresh credentials for the selected Gmail account."""

    account_id = get_account_id(account_id)
    token_path = GMAIL_ACCOUNTS[account_id]["token_path"]

    creds = None

    if token_path.is_file():
        try:
            creds = Credentials.from_authorized_user_file(
                str(token_path),
                SCOPES,
            )
        except (ValueError, OSError):
            creds = None

    if creds and not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                save_credentials(creds, account_id)

            except Exception as exc:
                raise ConnectionError(
                    f"Could not refresh authorization for {account_id}. "
                    "Reconnect that Gmail account."
                ) from exc

        else:
            raise ConnectionError(
                f"No valid authorization for {account_id}. "
                "Authorize this Gmail account first."
            )

    if not creds or not creds.valid:
        raise ConnectionError(
            f"Token file is missing or invalid for {account_id}: "
            f"{token_path.name}. Connect this Gmail account first."
        )

    return creds


# ============================================================
# CREATE GMAIL API SERVICE
# ============================================================

def get_gmail_service(account_id=None):
    """Create a Gmail API service for the selected account."""

    creds = get_credentials(account_id)

    http = httplib2.Http(timeout=REQUEST_TIMEOUT)

    authorized_http = AuthorizedHttp(
        creds,
        http=http,
    )

    return build(
        "gmail",
        "v1",
        http=authorized_http,
        cache_discovery=False,
    )


# ============================================================
# CHECK GMAIL CONNECTION
# ============================================================

def is_gmail_connected(account_id=None):
    """Check whether the selected Gmail account is accessible."""

    try:
        profile = get_gmail_profile(account_id)
        return bool(profile.get("emailAddress"))

    except Exception:
        return False


# ============================================================
# GET GMAIL PROFILE
# ============================================================

def get_gmail_profile(account_id=None):
    """Return profile details for the selected Gmail account."""

    service = get_gmail_service(account_id)

    try:
        profile = service.users().getProfile(
            userId="me",
        ).execute(num_retries=0)

        return {
            "emailAddress": profile.get("emailAddress", ""),
            "messagesTotal": profile.get("messagesTotal", 0),
            "threadsTotal": profile.get("threadsTotal", 0),
        }

    except HttpError as exc:
        raise ConnectionError(
            f"Gmail API request failed: {exc}"
        ) from exc

    except (OSError, TimeoutError, httplib2.HttpLib2Error) as exc:
        raise ConnectionError(
            "Cannot connect to Google Gmail API. "
            "Check your network connection."
        ) from exc


# ============================================================
# DISCONNECT GMAIL
# ============================================================

def disconnect_gmail(account_id=None):
    """Remove the selected account's local token file."""

    account_id = get_account_id(account_id)
    token_path = GMAIL_ACCOUNTS[account_id]["token_path"]

    if token_path.exists():
        token_path.unlink()


# ============================================================
# GET LATEST EMAILS
# ============================================================

def get_latest_emails(max_results=10, account_id=None):
    """Retrieve inbox emails from the selected Gmail account."""

    if (
        not isinstance(max_results, int)
        or isinstance(max_results, bool)
        or max_results < 1
    ):
        raise ValueError("max_results must be a positive integer.")

    max_results = min(max_results, 100)

    account_id = get_account_id(account_id)
    service = get_gmail_service(account_id)

    try:
        results = service.users().messages().list(
            userId="me",
            labelIds=["INBOX"],
            maxResults=max_results,
        ).execute(num_retries=0)

        emails = []

        for item in results.get("messages", []):
            details = service.users().messages().get(
                userId="me",
                id=item["id"],
                format="full",
            ).execute(num_retries=0)

            payload = details.get("payload", {})
            headers = payload.get("headers", [])

            def get_header(name):
                for header in headers:
                    if header.get("name", "").lower() == name.lower():
                        return header.get("value", "")

                return ""

            emails.append({
                "id": item["id"],
                "sender": get_header("From") or "Unknown sender",
                "subject": get_header("Subject") or "(No subject)",
                "date": get_header("Date"),
                "snippet": details.get("snippet", ""),
                "body": get_email_body(payload),
                "account_id": account_id,
            })

        return emails

    except HttpError as exc:
        raise ConnectionError(
            f"Unable to retrieve Gmail messages: {exc}"
        ) from exc


# ============================================================
# EXTRACT EMAIL BODY
# ============================================================

def get_email_body(payload):
    """Extract plain-text content from an email."""

    body = ""

    def find_plain_text(part):
        nonlocal body

        if body:
            return

        mime_type = part.get("mimeType", "")
        data = part.get("body", {}).get("data")

        if mime_type == "text/plain" and data:
            try:
                decoded = base64.urlsafe_b64decode(
                    data + "=" * (-len(data) % 4)
                )

                body = decoded.decode(
                    "utf-8",
                    errors="replace",
                )

            except (ValueError, UnicodeDecodeError):
                body = ""

        for child in part.get("parts", []):
            find_plain_text(child)

    find_plain_text(payload)

    return body


# ============================================================
# SEND EMAIL
# ============================================================

def send_email(
    recipient,
    subject="",
    body="",
    attachments=None,
    account_id=None,
):
    """Send an email from the selected Gmail account."""

    if not recipient or not recipient.strip():
        raise ValueError("Recipient email address is required.")

    account_id = get_account_id(account_id)
    service = get_gmail_service(account_id)

    message = EmailMessage()
    message["To"] = recipient.strip()

    if subject and subject.strip():
        message["Subject"] = subject.strip()

    message.set_content(body or "")

    for attachment in attachments or []:
        if (
            hasattr(attachment, "read")
            and hasattr(attachment, "name")
        ):
            filename = Path(attachment.name).name

            if hasattr(attachment, "seek"):
                attachment.seek(0)

            file_data = attachment.read()

            if hasattr(attachment, "seek"):
                attachment.seek(0)

        else:
            file_path = Path(attachment)

            if not file_path.is_file():
                raise FileNotFoundError(
                    f"Attachment not found: {file_path}"
                )

            filename = file_path.name
            file_data = file_path.read_bytes()

        mime_type, _ = mimetypes.guess_type(filename)

        if mime_type:
            maintype, subtype = mime_type.split("/", 1)
        else:
            maintype, subtype = "application", "octet-stream"

        message.add_attachment(
            file_data,
            maintype=maintype,
            subtype=subtype,
            filename=filename,
        )

    raw_message = base64.urlsafe_b64encode(
        message.as_bytes()
    ).decode("utf-8")

    try:
        return service.users().messages().send(
            userId="me",
            body={"raw": raw_message},
        ).execute(num_retries=0)

    except HttpError as exc:
        raise ConnectionError(
            f"Unable to send email: {exc}"
        ) from exc