from emailassistant.gmail_service import get_gmail_service


def main():
    service = get_gmail_service()

    profile = service.users().getProfile(userId="me").execute()

    print("\nGmail connected successfully!")
    print("Email address:", profile.get("emailAddress"))
    print("Total messages:", profile.get("messagesTotal"))


if __name__ == "__main__":
    main()