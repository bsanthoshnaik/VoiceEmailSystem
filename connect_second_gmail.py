from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

BASE_DIR = Path(__file__).resolve().parent

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

flow = InstalledAppFlow.from_client_secrets_file(
    str(BASE_DIR / "credentials.json"),
    SCOPES,
)

creds = flow.run_local_server(
    host="localhost",
    port=0,
    open_browser=True,
    prompt="select_account",
)

# Save the second Gmail account separately
token_path = BASE_DIR / "token2.json"
token_path.write_text(creds.to_json(), encoding="utf-8")

service = build("gmail", "v1", credentials=creds, cache_discovery=False)

profile = service.users().getProfile(userId="me").execute()

print("\nSecond Gmail connected successfully!")
print("Email:", profile["emailAddress"])
print("Token saved as token2.json")