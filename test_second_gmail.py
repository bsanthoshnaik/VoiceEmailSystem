import json
from pathlib import Path
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

BASE_DIR = Path(__file__).resolve().parent
TOKEN_PATH = BASE_DIR / "token2.json"

with open(TOKEN_PATH, "r", encoding="utf-8") as file:
    token_data = json.load(file)

creds = Credentials.from_authorized_user_info(
    token_data,
    scopes=[
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
    ],
)

service = build("gmail", "v1", credentials=creds, cache_discovery=False)
profile = service.users().getProfile(userId="me").execute()

print("Second Gmail connected successfully!")
print("Email:", profile["emailAddress"])
print("Total messages:", profile["messagesTotal"])