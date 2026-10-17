from django.urls import path
from . import views

app_name = "emailassistant"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),

    path("account/", views.account, name="account"),

    path(
        "verify-account-password/",
        views.verify_account_password,
        name="verify_account_password",
    ),

    path(
        "select-gmail-account/",
        views.select_gmail_account,
        name="select_gmail_account",
    ),

    path("compose/", views.compose_email, name="compose_email"),

    path(
        "verify-recipient/",
        views.verify_recipient,
        name="verify_recipient",
    ),

    path("read-emails/", views.read_emails, name="read_emails"),

    path(
        "translate-telugu/",
        views.translate_telugu,
        name="translate_telugu",
    ),

    path("telugu-audio/", views.telugu_audio, name="telugu_audio"),

    path(
        "correct-grammar/",
        views.correct_grammar,
        name="correct_grammar",
    ),
    path(
    "verify-inbox-password/",
    views.verify_inbox_password,
    name="verify_inbox_password",
),

    # Separate route to avoid conflicting with config/urls.py logout
    path("email-logout/", views.email_logout, name="email_logout"),
]