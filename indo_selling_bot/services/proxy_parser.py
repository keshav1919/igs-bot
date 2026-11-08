"""
Proxy Credential Parser.
Intelligently extracts username and password from diverse user inputs while strictly preserving
special characters in passwords and preventing false-positive matches on label names.
"""
import re
from typing import Tuple, Optional


class ProxyCredentialParser:
    LABEL_USERNAME_PATTERNS = [
        r"(?i)^(?:username|user\s*name|user|usr)\b",
    ]
    LABEL_PASSWORD_PATTERNS = [
        r"(?i)^(?:password|pass|pwd)\b",
    ]

    KNOWN_PREFIXES = (
        "mobile_", "mobile-", "mobile",
        "mob_", "mob-", "mob",
        "res_", "res-", "res",
        "resi_", "resi-", "resi",
        "residential_", "residential-", "residential"
    )

    @classmethod
    def is_label_word(cls, text: str) -> bool:
        """Check if a word is merely a label keyword like 'Username' or 'Password'."""
        clean = text.strip().lower().rstrip(":")
        return clean in {"username", "user", "usr", "user name", "password", "pass", "pwd"}

    @classmethod
    def identify_single_field(cls, text: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Identify if a single message provides ONLY a username or ONLY a password.
        Returns: ('username' | 'password' | None, extracted_value | None)
        """
        raw = text.strip()
        if not raw:
            return None, None

        # Check explicit label format like "Username: mobile_123" or "Pass: mypass"
        m_user = re.match(r"(?i)^(?:username|user\s*name|user|usr)\s*[:=\-]\s*(.+)$", raw)
        if m_user:
            val = m_user.group(1).strip()
            if val and not cls.is_label_word(val):
                return "username", val

        m_pass = re.match(r"(?i)^(?:password|pass|pwd)\s*[:=\-]\s*(.+)$", raw)
        if m_pass:
            val = m_pass.group(1).strip()
            if val and not cls.is_label_word(val):
                return "password", val

        # If text is single token/line with known proxy username prefix
        lower = raw.lower()
        for prefix in cls.KNOWN_PREFIXES:
            if lower.startswith(prefix) and not cls.is_label_word(raw):
                # Ensure it's not a full colon-separated string
                if ":" not in raw and "\n" not in raw and " " not in raw:
                    return "username", raw

        return None, None

    @classmethod
    def parse(cls, text: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Extract username and password from input string.
        Returns: (username, password, error_reason)
        """
        if not text or not text.strip():
            return None, None, "Empty credentials provided."

        raw = text.strip()

        # ---------------------------------------------------------
        # Strategy 1: Four-field format (server:port:username:password)
        # ---------------------------------------------------------
        if ":" in raw and "\n" not in raw:
            parts = raw.split(":")
            if len(parts) >= 4 and parts[1].isdigit():
                # server:port:username:password (password may contain colons)
                uname = parts[2].strip()
                pword = ":".join(parts[3:]).strip()
                if uname and pword and not cls.is_label_word(uname) and not cls.is_label_word(pword):
                    return uname, pword, None

        # ---------------------------------------------------------
        # Strategy 2: Labeled lines (Format B & D)
        # e.g.:
        # Username: mobile_97d8c330
        # Password: 1Ly+mSt2JEQS
        # ---------------------------------------------------------
        user_match = re.search(r"(?i)(?:^|[\r\n])\s*(?:username|user\s*name|user|usr)\s*[:=\-]\s*([^\r\n]+)", raw)
        pass_match = re.search(r"(?i)(?:^|[\r\n])\s*(?:password|pass|pwd)\s*[:=\-]\s*([^\r\n]+)", raw)

        if user_match and pass_match:
            uname = user_match.group(1).strip()
            pword = pass_match.group(1).strip()
            if uname and pword and not cls.is_label_word(uname) and not cls.is_label_word(pword):
                return uname, pword, None

        # ---------------------------------------------------------
        # Strategy 3: Multi-line separated label and value (Format C)
        # e.g.:
        # Username
        # mobile_97d8c330
        # Password
        # 1Ly+mSt2JEQS
        # ---------------------------------------------------------
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if len(lines) >= 4:
            found_user = None
            found_pass = None
            i = 0
            while i < len(lines):
                line_lower = lines[i].lower().rstrip(":")
                if line_lower in {"username", "user", "usr", "user name"} and i + 1 < len(lines):
                    next_val = lines[i + 1]
                    if not cls.is_label_word(next_val):
                        found_user = next_val
                        i += 2
                        continue
                elif line_lower in {"password", "pass", "pwd"} and i + 1 < len(lines):
                    next_val = lines[i + 1]
                    if not cls.is_label_word(next_val):
                        found_pass = next_val
                        i += 2
                        continue
                i += 1

            if found_user and found_pass:
                return found_user, found_pass, None

        # ---------------------------------------------------------
        # Strategy 4: Exactly two non-empty lines without labels
        # e.g. Format 2:
        # username
        # password
        # Or reversed
        # ---------------------------------------------------------
        if len(lines) == 2:
            line0, line1 = lines[0], lines[1]
            if not cls.is_label_word(line0) and not cls.is_label_word(line1):
                # Check if one of them matches a known username prefix
                l0_lower = line0.lower()
                l1_lower = line1.lower()
                l0_is_prefixed = any(l0_lower.startswith(p) for p in cls.KNOWN_PREFIXES)
                l1_is_prefixed = any(l1_lower.startswith(p) for p in cls.KNOWN_PREFIXES)

                if l1_is_prefixed and not l0_is_prefixed:
                    # Password first, username second
                    return line1, line0, None
                else:
                    # Default line0 = username, line1 = password
                    return line0, line1, None

        # ---------------------------------------------------------
        # Strategy 5: Single line 'username:password' (Format A & Format 1)
        # ---------------------------------------------------------
        if ":" in raw and "\n" not in raw:
            uname, _, pword = raw.partition(":")
            uname = uname.strip()
            pword = pword.strip()

            if uname and pword and not cls.is_label_word(uname) and not cls.is_label_word(pword):
                # Ensure username is clean (no spaces)
                if " " not in uname:
                    return uname, pword, None

        return None, None, "Could not identify valid proxy credentials. Please verify your format."
