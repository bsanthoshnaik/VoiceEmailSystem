import asyncio
import json
import logging
import os
import re
import tempfile

import edge_tts
import requests

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from .gmail_service import (
    get_latest_emails,
    get_gmail_profile,
    send_email,
    is_gmail_connected,
    get_available_gmail_accounts,
)

logger = logging.getLogger(__name__)

# ==================================================
# CONFIGURATION
# ==================================================
MAX_ATTACHMENT_SIZE = 20 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_SIZE = 25 * 1024 * 1024
MAX_ATTACHMENT_COUNT = 10

ALLOWED_ATTACHMENT_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".doc", ".docx", ".txt",
}

# ==================================================
# GMAIL ACCOUNT MAPPING
# ==================================================
DEFAULT_GMAIL_ACCOUNT_ID = "gmail2"
GMAIL_USER_MAPPING = {
    "pandu": "gmail1",
    "testeproject66@gmail.com": "gmail2",
}


def get_configured_gmail_account_ids():
    """Return the IDs of Gmail accounts configured in gmail_service.py."""
    configured_accounts = get_available_gmail_accounts()
    if isinstance(configured_accounts, dict):
        return set(configured_accounts.keys())
    return {
        account.get("id")
        for account in configured_accounts
        if isinstance(account, dict) and account.get("id")
    }


def get_selected_gmail_account(request):
    """Select a configured Gmail account for the current website user."""
    if request.user.is_authenticated:
        username = request.user.get_username().strip().lower()
        mapped_account_id = GMAIL_USER_MAPPING.get(username)
        selected_account_id = request.session.get("selected_gmail_account_id")
        account_id = (
            selected_account_id or mapped_account_id or DEFAULT_GMAIL_ACCOUNT_ID
        )
    else:
        account_id = DEFAULT_GMAIL_ACCOUNT_ID

    try:
        valid_account_ids = get_configured_gmail_account_ids()
        if account_id not in valid_account_ids:
            logger.error(
                "Gmail account ID %s is not configured. Available IDs: %s",
                account_id,
                valid_account_ids,
            )
            request.session.pop("gmail_account_id", None)
            return None
        request.session["gmail_account_id"] = account_id
        return account_id
    except Exception:
        logger.exception("Could not retrieve configured Gmail accounts.")
        return None


def get_gmail_account_context(request):
    """Return selected Gmail account information for a trusted, logged-in user."""
    account_id = get_selected_gmail_account(request)
    selected_gmail_email = ""
    if account_id:
        try:
            if is_gmail_connected(account_id):
                profile = get_gmail_profile(account_id=account_id) or {}
                selected_gmail_email = profile.get("emailAddress", "")
        except Exception:
            logger.exception(
                "Could not retrieve Gmail profile for account %s.", account_id
            )
    return {
        "selected_account": account_id,
        "selected_gmail_email": selected_gmail_email,
    }

# ==================================================
# PUBLIC DASHBOARD
# ==================================================
def dashboard(request):
    """Show the dashboard publicly, without exposing Gmail data to visitors."""
    context = {
        "email_address": "",
        "total_messages": 0,
        "total_threads": 0,
        "gmail_error": None,
        "selected_account": None,
        "selected_gmail_email": "",
    }

    # Gmail profile and inbox statistics are private; load them only after login.
    if not request.user.is_authenticated:
        return render(request, "emailassistant/dashboard.html", context)

    account_id = get_selected_gmail_account(request)
    context["selected_account"] = account_id
    if not account_id:
        context["gmail_error"] = (
            "The Gmail account is not configured. Check DEFAULT_GMAIL_ACCOUNT_ID "
            "and gmail_service.py."
        )
        return render(request, "emailassistant/dashboard.html", context)

    try:
        if not is_gmail_connected(account_id):
            context["gmail_error"] = (
                "The Gmail account is not connected. Check its Google authorization."
            )
            return render(request, "emailassistant/dashboard.html", context)

        profile = get_gmail_profile(account_id=account_id) or {}
        context["email_address"] = profile.get("emailAddress", "")
        context["selected_gmail_email"] = profile.get("emailAddress", "")
        context["total_messages"] = profile.get("messagesTotal", 0)
        context["total_threads"] = profile.get("threadsTotal", 0)
    except Exception:
        logger.exception("Unable to load Gmail profile for account %s.", account_id)
        context["gmail_error"] = (
            "Unable to load Gmail information. Check Gmail authorization and logs."
        )

    return render(request, "emailassistant/dashboard.html", context)

# ==================================================
# LOGOUT
# ==================================================
@login_required
def email_logout(request):
    """Log out of the website and clear the selected Gmail account."""
    request.session.pop("gmail_account_id", None)
    request.session.pop("selected_gmail_account_id", None)
    request.session.pop("gmail_inbox_verified", None)
    request.session.pop("account_password_verified", None)
    logout(request)
    messages.success(request, "You have been logged out successfully.")
    return redirect("emailassistant:dashboard")

# ==================================================
# EMAIL ADDRESS VALIDATION
# ==================================================
def is_valid_recipient_email(email):
    """Validate the format of a recipient email address."""
    if not isinstance(email, str):
        return False
    email = email.strip()
    if not email or len(email) > 254 or any(c.isspace() for c in email):
        return False
    try:
        validate_email(email)
    except ValidationError:
        return False
    if "@" not in email:
        return False
    local_part, domain = email.rsplit("@", 1)
    if not local_part or not domain or "." not in domain:
        return False
    domain_parts = domain.split(".")
    if any(not part for part in domain_parts):
        return False
    if any(part.startswith("-") or part.endswith("-") for part in domain_parts):
        return False
    return True

# ==================================================
# ATTACHMENT VALIDATION
# ==================================================
def validate_attachments(files):
    """Validate attachment count, extensions, and sizes."""
    if len(files) > MAX_ATTACHMENT_COUNT:
        return [], f"You can attach a maximum of {MAX_ATTACHMENT_COUNT} files."

    total_size = 0
    for uploaded_file in files:
        filename = os.path.basename(uploaded_file.name)
        extension = os.path.splitext(filename)[1].lower()
        if extension not in ALLOWED_ATTACHMENT_EXTENSIONS:
            return [], (
                f"Unsupported file format: {filename}. Allowed formats: "
                "PDF, images, DOC, DOCX and TXT."
            )
        if uploaded_file.size <= 0:
            return [], f"The file {filename} is empty."
        if uploaded_file.size > MAX_ATTACHMENT_SIZE:
            return [], f"The file {filename} exceeds the 20 MB limit."
        total_size += uploaded_file.size

    if total_size > MAX_TOTAL_ATTACHMENT_SIZE:
        return [], "The total size of attachments cannot exceed 25 MB."
    return files, None

# ==================================================
# RECIPIENT VERIFICATION
# ==================================================
@require_POST
def verify_recipient(request):
    """Validate email syntax and optionally ask Abstract API about deliverability."""
    recipient = request.POST.get("recipient", "").strip()
    if not is_valid_recipient_email(recipient):
        return JsonResponse({
            "success": False,
            "message": "The email format is incorrect. Please check the complete address.",
            "status": "invalid_format",
        }, status=400)

    api_key = os.environ.get("ABSTRACT_API_KEY", "").strip()
    if not api_key:
        return JsonResponse({
            "success": True,
            "message": "Email format is valid.",
            "status": "format_valid",
        })

    try:
        response = requests.get(
            "https://emailreputation.abstractapi.com/v1/",
            params={"api_key": api_key, "email": recipient},
            timeout=15,
        )
        if response.status_code in (401, 403):
            logger.warning("Abstract API rejected the configured API key.")
            return JsonResponse({
                "success": True,
                "message": "Email format is valid, but external verification is unavailable.",
                "status": "format_valid_api_unavailable",
            })
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Unexpected API response.")
        deliverability = data.get("email_deliverability") or {}
        if not isinstance(deliverability, dict):
            deliverability = {}
        status = str(deliverability.get("status", "")).strip().lower()
        if status == "deliverable":
            return JsonResponse({
                "success": True,
                "message": "The address appears deliverable.",
                "status": "deliverable",
            })
        if status == "undeliverable":
            return JsonResponse({
                "success": False,
                "message": "The verification service reports that this address may be undeliverable. Please check it.",
                "status": "undeliverable",
            }, status=400)
        return JsonResponse({
            "success": True,
            "message": "Email format is valid, but deliverability could not be confirmed.",
            "status": status or "unknown",
        })
    except requests.Timeout:
        logger.warning("Recipient verification timed out.")
        return JsonResponse({
            "success": True,
            "message": "Email format is valid. External verification timed out.",
            "status": "format_valid_timeout",
        })
    except requests.RequestException:
        logger.exception("Recipient verification request failed.")
        return JsonResponse({
            "success": True,
            "message": "Email format is valid. The external verification service is unavailable.",
            "status": "format_valid_api_unavailable",
        })
    except (ValueError, TypeError, AttributeError):
        logger.exception("Invalid recipient verification API response.")
        return JsonResponse({
            "success": True,
            "message": "Email format is valid, but external verification could not be completed.",
            "status": "format_valid_unknown",
        })

# ==================================================
# ENGLISH GRAMMAR CORRECTION
# ==================================================
def improve_common_phrases(text):
    """Improve selected common English phrases."""
    replacements = [
        (r"\bdear\s+sir\b", "Dear Sir"),
        (r"\bdear\s+madam\b", "Dear Madam"),
        (r"\bi completed my b\.?\s*tech\b", "I have completed my B.Tech"),
        (r"\bi completed my tech\b", "I have completed my B.Tech"),
        (r"\bi am searching job in your company\b", "I am looking for a job opportunity at your company"),
        (r"\bi am searching for a job in your company\b", "I am looking for a job opportunity at your company"),
        (r"\bi am searching for job in your company\b", "I am looking for a job opportunity at your company"),
        (r"\bi am looking job in your company\b", "I am looking for a job opportunity at your company"),
        (r"\bplease consider my resume\b", "Kindly consider my resume for a suitable position"),
        (r"\bplease consider my application\b", "Kindly consider my application for a suitable position"),
        (r"\bi am not coming office\b", "I will not be coming to the office"),
        (r"\bi am going office\b", "I am going to the office"),
        (r"\bi am coming office\b", "I am coming to the office"),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def format_english_text(text):
    """Clean spacing, capitalization, and punctuation."""
    text = text.strip()
    if not text:
        return text
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\bi\b", "I", text)
    text = text[0].upper() + text[1:]
    text = re.sub(r"([,.!?;:])(?=[A-Za-z])", r"\1 ", text)
    text = re.sub(r" {2,}", " ", text)
    if text[-1] not in ".!?":
        text += "."
    return text


@require_POST
def correct_grammar(request):
    """Correct English grammar in submitted text."""
    try:
        data = json.loads(request.body or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"success": False, "error": "Invalid request data."}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({"success": False, "error": "Request must contain a JSON object."}, status=400)
    text = data.get("text", "")
    if not isinstance(text, str):
        return JsonResponse({"success": False, "error": "Text must be a string."}, status=400)
    text = text.strip()
    if not text:
        return JsonResponse({"success": False, "error": "Please speak or enter an English message first."}, status=400)
    if len(text) > 10000:
        return JsonResponse({"success": False, "error": "Keep the message under 10,000 characters."}, status=400)

    original_text = text
    corrected_text = text
    try:
        import language_tool_python
        tool = language_tool_python.LanguageTool("en-US")
        try:
            corrected_text = language_tool_python.utils.correct(
                corrected_text, tool.check(corrected_text)
            )
        finally:
            tool.close()
    except ImportError:
        logger.info("LanguageTool is not installed. Using built-in phrase corrections.")
    except Exception:
        logger.exception("LanguageTool failed. Using built-in phrase corrections.")

    corrected_text = format_english_text(improve_common_phrases(corrected_text))
    return JsonResponse({
        "success": True,
        "original": original_text,
        "corrected": corrected_text,
        "changed": corrected_text != original_text,
        "message": "Grammar correction completed. Please review your message before sending.",
    })

# ==================================================
# COMPOSE AND SEND EMAIL
# ==================================================
def compose_email(request):
    """Allow anyone to open compose; require login to send email."""
    # Public GET: visitors can view the compose page before signing in.
    if request.method != "POST":
        account_id = get_selected_gmail_account(request) if request.user.is_authenticated else None
        return render(request, "emailassistant/compose.html", {
            "selected_account": account_id,
            "requires_login_to_send": not request.user.is_authenticated,
        })

    # Server-side protection: JavaScript/modal checks are not sufficient.
    if not request.user.is_authenticated:
        messages.warning(request, "Please log in before sending your email.")
        return redirect_to_login(
            request.get_full_path(),
            login_url="login",
        )

    account_id = get_selected_gmail_account(request)
    if not account_id:
        messages.error(request, "No Gmail account is assigned to your website login. Check GMAIL_USER_MAPPING in views.py.")
        return redirect("emailassistant:dashboard")

    try:
        if not is_gmail_connected(account_id):
            messages.error(request, "The Gmail account assigned to your login is not connected.")
            return redirect("emailassistant:dashboard")
    except Exception:
        logger.exception("Unable to check Gmail connection for account %s.", account_id)
        messages.error(request, "Unable to check Gmail connection. Please try again.")
        return redirect("emailassistant:dashboard")

    recipient = request.POST.get("recipient", "").strip()
    subject = request.POST.get("subject", "").strip()
    body = request.POST.get("body", "").strip()
    if not is_valid_recipient_email(recipient):
        messages.error(request, "Enter a valid recipient email address.")
        return redirect("emailassistant:compose_email")
    if not body:
        messages.error(request, "The email message cannot be empty.")
        return redirect("emailassistant:compose_email")

    attachments = request.FILES.getlist("attachments")
    validated_files, attachment_error = validate_attachments(attachments)
    if attachment_error:
        messages.error(request, attachment_error)
        return redirect("emailassistant:compose_email")

    try:
        result = send_email(
            recipient=recipient,
            subject=subject,
            body=body,
            attachments=validated_files,
            account_id=account_id,
        )
        if not isinstance(result, dict) or not result.get("id"):
            logger.error("Gmail returned no message ID: %r", result)
            messages.error(request, "Sending could not be confirmed. Check your Gmail Sent folder before retrying.")
            return redirect("emailassistant:compose_email")

        logger.info("Gmail accepted message ID %s from account %s", result["id"], account_id)
        if validated_files:
            messages.success(request, f"Email sent successfully with {len(validated_files)} attachment(s).")
        else:
            messages.success(request, "Email sent successfully.")
    except Exception as exc:
        logger.exception("Gmail email sending failed for account %s.", account_id)
        error_text = str(exc).lower()
        if "invalid_grant" in error_text:
            reason = "Gmail authorization expired or was revoked. Reconnect that Gmail account."
        elif "401" in error_text or "unauthorized" in error_text:
            reason = "Gmail authorization failed. Check your token."
        elif "403" in error_text or "forbidden" in error_text:
            reason = "Gmail rejected the request. Check API permissions and OAuth scopes."
        elif "429" in error_text or "rate limit" in error_text:
            reason = "Gmail rate limit reached. Wait and try again."
        elif any(word in error_text for word in ("connection", "network", "timeout")):
            reason = "A network error occurred. Check your connection and Gmail Sent folder."
        else:
            reason = "The Gmail API could not complete the request. Check the server logs for details."
        messages.error(request, f"Email sending failed: {reason}")
    return redirect("emailassistant:compose_email")

# ==================================================
# TRANSLATE ENGLISH AND TELUGU
# ==================================================
@require_POST
def translate_telugu(request):
    """Translate English to Telugu or Telugu to English."""
    try:
        data = json.loads(request.body or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid request data."}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({"error": "Request must contain a JSON object."}, status=400)
    text = data.get("text", "")
    if not isinstance(text, str):
        return JsonResponse({"error": "Text must be a string."}, status=400)
    text = text.strip()
    if not text:
        return JsonResponse({"error": "Enter text to translate."}, status=400)
    if len(text) > 5000:
        return JsonResponse({"error": "Keep the text under 5,000 characters."}, status=400)

    direction = data.get("direction", "en-te")
    language_pairs = {"en-te": ("en", "te"), "te-en": ("te", "en")}
    if direction not in language_pairs:
        return JsonResponse({"error": "Unsupported translation direction."}, status=400)
    source_language, target_language = language_pairs[direction]

    try:
        response = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text, "langpair": f"{source_language}|{target_language}"},
            timeout=20,
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("Unexpected translation response.")
        if result.get("responseStatus") != 200:
            logger.warning("Translation API returned status: %r", result.get("responseStatus"))
            return JsonResponse({"error": "Translation failed. Try again."}, status=502)
        response_data = result.get("responseData") or {}
        if not isinstance(response_data, dict):
            raise ValueError("Unexpected translation response data.")
        translated_text = str(response_data.get("translatedText", "")).strip()
        if not translated_text:
            return JsonResponse({"error": "No translation was returned."}, status=502)
        return JsonResponse({"original": text, "translation": translated_text, "direction": direction})
    except requests.Timeout:
        logger.warning("Translation API request timed out.")
        return JsonResponse({"error": "Translation timed out. Try again."}, status=504)
    except requests.RequestException:
        logger.exception("Translation API request failed.")
        return JsonResponse({"error": "Translation service is unavailable."}, status=502)
    except (ValueError, TypeError, AttributeError):
        logger.exception("Invalid translation API response.")
        return JsonResponse({"error": "The translation service returned invalid data."}, status=502)

# ==================================================
# TELUGU TEXT-TO-SPEECH
# ==================================================
@require_POST
def telugu_audio(request):
    """Generate Telugu speech and return an MP3 response."""
    audio_path = None
    try:
        try:
            data = json.loads(request.body or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({"error": "Invalid request data."}, status=400)
        if not isinstance(data, dict):
            return JsonResponse({"error": "Request must contain a JSON object."}, status=400)
        telugu_text = data.get("text", "")
        if not isinstance(telugu_text, str):
            return JsonResponse({"error": "Text must be a string."}, status=400)
        telugu_text = telugu_text.strip()
        if not telugu_text:
            return JsonResponse({"error": "No Telugu text provided."}, status=400)
        if len(telugu_text) > 5000:
            return JsonResponse({"error": "Keep the text under 5,000 characters."}, status=400)

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as audio_file:
            audio_path = audio_file.name

        async def generate_audio():
            communicate = edge_tts.Communicate(telugu_text, voice="te-IN-ShrutiNeural")
            await communicate.save(audio_path)

        asyncio.run(generate_audio())
        if not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
            raise RuntimeError("The TTS service returned an empty audio file.")
        with open(audio_path, "rb") as audio_file:
            audio_data = audio_file.read()
        response = HttpResponse(audio_data, content_type="audio/mpeg")
        response["Cache-Control"] = "no-store"
        response["Content-Length"] = str(len(audio_data))
        return response
    except Exception:
        logger.exception("Telugu audio generation failed.")
        return JsonResponse({"error": "Unable to generate Telugu audio. Check server logs and internet connection."}, status=502)
    finally:
        if audio_path and os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except OSError:
                logger.warning("Could not remove temporary audio file: %s", audio_path)

# ==================================================
# READ EMAILS FROM GMAIL (PRIVATE: LOGIN REQUIRED)
# ==================================================
@login_required
def read_emails(request):
    """Retrieve emails from the selected Gmail account after login."""
    account_id = get_selected_gmail_account(request)
    context = {
        "emails": [],
        "error": None,
        "selected_gmail_email": "",
        "selected_account": account_id,
    }
    if not account_id:
        context["error"] = (
            "No Gmail account is configured for your login. Check DEFAULT_GMAIL_ACCOUNT_ID, "
            "GMAIL_USER_MAPPING in views.py, and configured accounts in gmail_service.py."
        )
        return render(request, "emailassistant/read_emails.html", context)

    try:
        if not is_gmail_connected(account_id):
            context["error"] = "The selected Gmail account is not connected. Check its Google OAuth authorization."
            return render(request, "emailassistant/read_emails.html", context)
        profile = get_gmail_profile(account_id=account_id) or {}
        context["selected_gmail_email"] = profile.get("emailAddress", "")
        emails = get_latest_emails(max_results=10, account_id=account_id)
        if emails is None:
            emails = []
        if not isinstance(emails, (list, tuple)):
            logger.error("get_latest_emails returned unexpected type %s for account %s.", type(emails).__name__, account_id)
            context["error"] = "Gmail returned an unexpected response. Check get_latest_emails() in gmail_service.py."
        elif not emails:
            logger.warning("No emails were returned for Gmail account %s.", account_id)
            context["error"] = "Gmail is connected, but no messages were returned. Check the inbox query in gmail_service.py."
        else:
            context["emails"] = emails
    except Exception:
        logger.exception("Failed to retrieve Gmail messages for account %s.", account_id)
        context["error"] = "Unable to retrieve emails from Gmail. Check authorization, API permissions, and server logs."
    return render(request, "emailassistant/read_emails.html", context)

# ==================================================
# ACCOUNT PAGE
# ==================================================
@login_required
def account(request):
    """Display Gmail account settings for the logged-in user."""
    try:
        configured_accounts = get_available_gmail_accounts()
        if isinstance(configured_accounts, dict):
            available_accounts = [
                {"id": account_id, **(details if isinstance(details, dict) else {})}
                for account_id, details in configured_accounts.items()
            ]
        else:
            available_accounts = [item for item in configured_accounts if isinstance(item, dict)]
    except Exception:
        logger.exception("Could not load available Gmail accounts.")
        available_accounts = []

    account_id = get_selected_gmail_account(request)
    selected_gmail_email = ""
    if account_id:
        try:
            if is_gmail_connected(account_id):
                profile = get_gmail_profile(account_id=account_id) or {}
                selected_gmail_email = profile.get("emailAddress", "")
        except Exception:
            logger.exception("Could not load profile for Gmail account %s.", account_id)

    return render(request, "emailassistant/account.html", {
        "available_accounts": available_accounts,
        "selected_account": account_id,
        "selected_gmail_email": selected_gmail_email,
        "error": None,
    })

# ==================================================
# SELECT GMAIL ACCOUNT
# ==================================================
@require_POST
@login_required
def select_gmail_account(request):
    """Select a Gmail account already configured by the application."""
    account_id = request.POST.get("account_id", "").strip()
    try:
        available_ids = get_configured_gmail_account_ids()
    except Exception:
        logger.exception("Could not validate the selected Gmail account.")
        messages.error(request, "Gmail accounts could not be loaded. Please try again.")
        return redirect("emailassistant:account")

    if not account_id or account_id not in available_ids:
        messages.error(request, "Please select a valid configured Gmail account.")
        return redirect("emailassistant:account")

    request.session["selected_gmail_account_id"] = account_id
    request.session["gmail_account_id"] = account_id
    request.session.pop("gmail_inbox_verified", None)
    request.session.pop("account_password_verified", None)
    messages.success(request, "Gmail account switched successfully.")
    return redirect("emailassistant:dashboard")
