from django.urls import path
from . import views

urlpatterns = [
    # Homepage and dashboard
    path("", views.dashboard, name="dashboard"),

    # Account password verification
    path(
        "verify-account-password/",
        views.verify_account_password,
        name="verify_account_password",
    ),

    # Compose and send emails
    path(
        "compose/",
        views.compose_email,
        name="compose_email",
    ),

    # Verify recipient email
    path(
        "verify-recipient/",
        views.verify_recipient,
        name="verify_recipient",
    ),

    # Read Gmail inbox
    path(
        "read-emails/",
        views.read_emails,
        name="read_emails",
    ),

    # English and Telugu translation
    path(
        "translate-telugu/",
        views.translate_telugu,
        name="translate_telugu",
    ),

    # Telugu text-to-speech
    path(
        "telugu-audio/",
        views.telugu_audio,
        name="telugu_audio",
    ),

    # English grammar correction
    path(
        "correct-grammar/",
        views.correct_grammar,
        name="correct_grammar",
    ),

    # Logout
    path(
        "logout/",
        views.email_logout,
        name="email_logout",
    ),
]