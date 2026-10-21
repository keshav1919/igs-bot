import re
import html
import time
import urllib.parse
from typing import Optional, Dict, Any, List
import aiohttp

UUID_REGEX = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE
)
EMAIL_REGEX = re.compile(r"^[^\s@|]+@[^\s@|]+\.[^\s@|]+$", re.IGNORECASE)

DEFAULT_API_ENDPOINT = "https://code.yamin.bd/api.php?action=fetch"
MS_OAUTH_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
MS_GRAPH_MESSAGES_URL = (
    "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages"
    "?$top=10&$select=id,subject,body,bodyPreview,from,receivedDateTime&$orderby=receivedDateTime desc"
)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

import datetime
from dateutil import parser as date_parser

def format_relative_time(date_val) -> str:
    """Formats timestamp into relative time like '9s', '15m 58s', '1hr 45m', '2d ago'."""
    if not date_val:
        return "now"
    try:
        if isinstance(date_val, str):
            dt = date_parser.parse(date_val)
        elif isinstance(date_val, (int, float)):
            dt = datetime.datetime.fromtimestamp(date_val, datetime.timezone.utc)
        else:
            dt = date_val
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        now = datetime.datetime.now(datetime.timezone.utc)
        diff = int((now - dt).total_seconds())
        if diff < 0:
            diff = 0
        if diff < 60:
            return f"{diff}s"
        elif diff < 3600:
            m = diff // 60
            s = diff % 60
            return f"{m}m {s}s" if s else f"{m}m"
        elif diff < 86400:
            h = diff // 3600
            m = (diff % 3600) // 60
            return f"{h}hr {m}m" if m else f"{h}hr"
        else:
            d = diff // 86400
            return f"{d}d ago"
    except Exception:
        return "recent"


def format_email_date(date_val) -> str:
    """Formats date like '21 Oct 07:43'."""
    if not date_val:
        return datetime.datetime.now().strftime("%d %b %H:%M")
    try:
        if isinstance(date_val, str):
            dt = date_parser.parse(date_val)
        elif isinstance(date_val, (int, float)):
            dt = datetime.datetime.fromtimestamp(date_val, datetime.timezone.utc)
        else:
            dt = date_val
        return dt.strftime("%d %b %H:%M")
    except Exception:
        return str(date_val)[:16]


def clean_html_to_readable_text(raw_html: str) -> str:
    """Converts HTML email to clean, readable plain text for Telegram."""
    if not raw_html:
        return ""
    text = re.sub(r"<(style|script)\b[^>]*>[\s\S]*?</\1>", "", raw_html, flags=re.IGNORECASE)
    text = re.sub(r"<!--[\s\S]*?-->", "", text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(p|div|tr|h[1-6]|li)\b[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    lines = [line.strip() for line in text.split("\n")]
    cleaned = []
    for line in lines:
        if line:
            cleaned.append(line)
        elif cleaned and cleaned[-1] != "":
            cleaned.append("")
    result = "\n".join(cleaned).strip()
    if len(result) > 2800:
        result = result[:2800] + "\n\n... (Message truncated)"
    return result


def clean_text_for_code(body: str) -> str:
    """Removes HTML tags, style, script, and comments."""
    if not body:
        return ""
    text = re.sub(r"<(style|script)\b[^>]*>[\s\S]*?</\1>", "", body, flags=re.IGNORECASE)
    text = re.sub(r"<!--[\s\S]*?-->", "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return text


def extract_code_from_text(subject: str, body: str) -> Optional[str]:
    """
    Extracts OTP/Verification code from subject or body text, matching
    the exact regex precedence logic used by MailCode (yamin.bd).
    """
    # 1. Subject match: 4 to 8 digits, not a year (19xx or 20xx)
    subj_match = re.search(r"\b(\d{4,8})\b", subject or "")
    if subj_match and not re.match(r"^(19|20)\d{2}$", subj_match.group(1)):
        return subj_match.group(1)

    # 2. Clean body
    clean = clean_text_for_code(body or "")

    # 3. Strip false-positive numbers
    clean = re.sub(r"\b\d{4}[-/]\d{2}[-/]\d{2}\b", "", clean)  # dates
    clean = re.sub(r"\b(19|20)\d{2}\b", "", clean)  # years
    clean = re.sub(r"\b94025\b", "", clean)  # Menlo Park zip code
    clean = re.sub(r"\b\d{10,}\b", "", clean)  # phone numbers / long IDs

    # 4. Context keyword match: e.g. "code is 123456", "verification: 1234"
    context_match = re.search(
        r"(?:code|otp|pin|password|verification)[^\d]{0,20}(\d{4,8})",
        clean,
        re.IGNORECASE
    )
    if context_match:
        return context_match.group(1)

    # 5. Isolated 5-8 digit number
    match_58 = re.search(r"(?:^|\s|>|:|-)(\d{5,8})(?:$|\s|<|\.|,|-)", clean) or re.search(r"\b(\d{5,8})\b", clean)
    if match_58:
        return match_58.group(1)

    # 6. Isolated 4-digit number (excluding 19xx / 20xx years)
    match_4 = re.search(r"\b(?!(?:19|20)\d{2}\b)(\d{4})\b", clean)
    if match_4:
        return match_4.group(1)

    return None


def parse_credential_line(line: str) -> Optional[Dict[str, Any]]:
    """
    Parses a single credential line.
    Format:
      email|password|refresh_token|client_id
      or
      email|password|refresh_token|client_id|recovery_email
    Handles tokens with embedded pipes, zero-width chars, and url-encoding.
    """
    clean = re.sub(r"[\u200B-\u200D\uFEFF\u00A0]", "", str(line or "")).strip()
    if not clean or "|" not in clean:
        return None

    email_match = re.match(r"^([^\s|]+@[^\s|]+)\|(.+)$", clean)
    if not email_match:
        return None

    email = email_match.group(1).strip()
    if not EMAIL_REGEX.match(email):
        return None

    rest = email_match.group(2)

    # Check 5-part (UUID + recovery_email at the end)
    five = re.search(r"\|([0-9a-f-]{36})\|([^\s|]+@[^\s|]+)\s*$", rest, re.IGNORECASE)
    recovery_email = None
    if five and UUID_REGEX.match(five.group(1)) and EMAIL_REGEX.match(five.group(2).strip()):
        client_id = five.group(1).lower()
        recovery_email = five.group(2).strip()
        middle = rest[:-len(five.group(0))].rstrip("|")
    else:
        # Check 4-part (UUID at the end)
        four = re.search(r"\|([0-9a-f-]{36})\s*$", rest, re.IGNORECASE)
        if four and UUID_REGEX.match(four.group(1)):
            client_id = four.group(1).lower()
            middle = rest[:-len(four.group(0))].rstrip("|")
        else:
            # Fallback simple pipe split
            parts = [p.strip() for p in clean.split("|")]
            if len(parts) >= 4 and UUID_REGEX.match(parts[3]):
                token = re.sub(r"[\r\n\t ]", "", parts[2])
                if "%" in token:
                    token = urllib.parse.unquote(token)
                return {
                    "email": parts[0],
                    "password": parts[1],
                    "refresh_token": token,
                    "client_id": parts[3].lower(),
                    "recovery_email": parts[4] if len(parts) >= 5 and EMAIL_REGEX.match(parts[4]) else None
                }
            return None

    mid_parts = middle.split("|")
    if len(mid_parts) < 2:
        return None

    password = mid_parts[0].strip()
    refresh_token = re.sub(r"[\r\n\t ]", "", "|".join(mid_parts[1:])).strip()
    if "%" in refresh_token:
        refresh_token = urllib.parse.unquote(refresh_token)

    if not password or not refresh_token:
        return None

    return {
        "email": email,
        "password": password,
        "refresh_token": refresh_token,
        "client_id": client_id,
        "recovery_email": recovery_email
    }


def parse_all_credentials(text: str) -> List[Dict[str, Any]]:
    """Parses multiple lines of credentials."""
    results = []
    seen_emails = set()
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        cred = parse_credential_line(line)
        if cred and cred["email"].lower() not in seen_emails:
            seen_emails.add(cred["email"].lower())
            results.append(cred)
    return results


class MailService:
    def __init__(self, endpoint: str = DEFAULT_API_ENDPOINT):
        self.endpoint = endpoint
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=25, connect=8)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers={"User-Agent": USER_AGENT}
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def fetch_via_yamin_api(self, cred: Dict[str, Any]) -> Dict[str, Any]:
        """Calls code.yamin.bd/api.php?action=fetch."""
        session = await self._get_session()
        payload = {
            "email": cred["email"],
            "password": cred.get("password", ""),
            "refresh_token": cred["refresh_token"],
            "client_id": cred["client_id"],
            "recovery_email": cred.get("recovery_email"),
            "source": "bot"
        }
        t0 = time.time()
        try:
            async with session.post(self.endpoint, json=payload) as resp:
                data = await resp.json(content_type=None)
                duration_ms = int((time.time() - t0) * 1000)

                if isinstance(data, dict):
                    data["duration_ms"] = duration_ms
                    data["source_method"] = "yamin_api"
                    return data
                return {
                    "status": False,
                    "error": f"Invalid server response: {str(data)[:100]}",
                    "duration_ms": duration_ms
                }
        except Exception as e:
            duration_ms = int((time.time() - t0) * 1000)
            return {
                "status": False,
                "error": f"API request error: {str(e)}",
                "duration_ms": duration_ms
            }

    async def fetch_via_direct_graph(self, cred: Dict[str, Any]) -> Dict[str, Any]:
        """
        Directly exchanges refresh token with Microsoft Identity and fetches
        messages via Microsoft Graph API. Used as high-reliability fallback.
        """
        session = await self._get_session()
        t0 = time.time()

        token_data = {
            "client_id": cred["client_id"],
            "grant_type": "refresh_token",
            "refresh_token": cred["refresh_token"],
            "scope": "https://graph.microsoft.com/Mail.Read offline_access"
        }

        try:
            async with session.post(
                MS_OAUTH_TOKEN_URL,
                data=token_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            ) as t_resp:
                t_json = await t_resp.json(content_type=None)

            if "access_token" not in t_json:
                err_desc = t_json.get("error_description", t_json.get("error", "Failed to refresh token"))
                return {
                    "status": False,
                    "error": f"Microsoft OAuth error: {err_desc}",
                    "error_code": "token_error",
                    "duration_ms": int((time.time() - t0) * 1000)
                }

            access_token = t_json["access_token"]
            new_refresh_token = t_json.get("refresh_token")

            graph_headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json"
            }
            async with session.get(MS_GRAPH_MESSAGES_URL, headers=graph_headers) as g_resp:
                if g_resp.status != 200:
                    err_text = await g_resp.text()
                    return {
                        "status": False,
                        "error": f"Graph API error ({g_resp.status}): {err_text[:120]}",
                        "duration_ms": int((time.time() - t0) * 1000)
                    }
                g_json = await g_resp.json(content_type=None)

            raw_messages = g_json.get("value", [])
            parsed_messages = []
            extracted_code = None

            for m in raw_messages:
                subject = m.get("subject", "")
                sender_info = m.get("from", {}).get("emailAddress", {})
                sender_name = sender_info.get("name", "")
                sender_email = sender_info.get("address", "")
                body_content = m.get("body", {}).get("content", "") or m.get("bodyPreview", "")
                received_dt = m.get("receivedDateTime", "")

                code = extract_code_from_text(subject, body_content)
                if code and not extracted_code:
                    extracted_code = code

                parsed_messages.append({
                    "subject": subject,
                    "senderName": sender_name,
                    "senderEmail": sender_email,
                    "from": sender_email,
                    "date": received_dt,
                    "code": code or "------",
                    "preview": m.get("bodyPreview", ""),
                    "message": body_content,
                    "snippet": m.get("bodyPreview", "")
                })

            duration_ms = int((time.time() - t0) * 1000)
            return {
                "status": True,
                "code": extracted_code or "------",
                "email": cred["email"],
                "messages": parsed_messages,
                "new_refresh_token": new_refresh_token,
                "duration_ms": duration_ms,
                "source_method": "direct_graph"
            }

        except Exception as e:
            return {
                "status": False,
                "error": f"Direct Graph error: {str(e)}",
                "duration_ms": int((time.time() - t0) * 1000)
            }

    async def get_latest_code(self, cred: Dict[str, Any]) -> Dict[str, Any]:
        """
        Unified fast fetcher:
        1. Queries code.yamin.bd API.
        2. If yamin.bd fails, seamlessly falls back to Direct Microsoft Graph API.
        """
        res = await self.fetch_via_yamin_api(cred)
        if res.get("status"):
            return res

        direct_res = await self.fetch_via_direct_graph(cred)
        if direct_res.get("status"):
            return direct_res

        return direct_res if direct_res.get("error") else res

mail_service = MailService()
