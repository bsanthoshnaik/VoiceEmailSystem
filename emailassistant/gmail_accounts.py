from pathlib import Path
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

BASE_DIR = Path(__file__).resolve().parent.parent

GMAIL_ACCOUNTS = {
    "gmail1": BASE_DIR / "token.json",
    "gmail2": BASE_DIR / "token2.json",
}


def get_connected_accounts():
    accounts = []

    for account_id, token_path in GMAIL_ACCOUNTS.items():
        if not token_path.exists():
            continue

        try:
            creds = Credentials.from_authorized_user_file(str(token_path))

            if not creds.valid:
                continue

            service = build(
                "gmail", "v1",
                credentials=creds,
                cache_discovery=False,
            )

            profile = service.users().getProfile(userId="me").execute()

            accounts.append({
                "id": account_id,
                "email": profile["emailAddress"],
            })

        except Exception as error:
            print(f"Could not connect {account_id}: {error}")

    return accounts