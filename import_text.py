"""Import: tidies text from .txt and .md files, and fetches Google Docs shared by link.
Standard library only. Messages are plain words, safe to show in the studio."""

import http.client
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

IMPORT_EXTENSIONS = (".txt", ".md")
MAX_IMPORT_BYTES = 5 * 1024 * 1024

NOT_TXT_OR_MD = "Import opens .txt and .md files."
EMPTY = "There's no text to import."


class ImportProblem(ValueError):
    """A problem with an import that is safe to show to the user."""


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(text: str) -> str:
    """Removes a byte-order mark, Windows line endings and invisible control characters."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    return _CONTROL.sub("", text)


# Markdown escapes are hidden as private-use characters while tidying, so "\*" never starts italics.
_ESCAPABLE = "\\`*_{}[]()#+-.!>|~"
_ESCAPE = re.compile("\\\\([" + re.escape(_ESCAPABLE) + "])")
_HIDDEN = re.compile("[-]")

_FENCE = re.compile(r"^\s*(```|~~~)")
# Every pattern stops at its own next marker (bodies exclude the marker), so odd long lines
# stay fast: a slow pattern would hold up the whole studio while it runs.
_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.*)$")
_QUOTE = re.compile(r"^\s*(?:>\s?)+")
_LIST = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+")
_IMAGE = re.compile(r"!\[[^\[\]]*\]\([^()]*\)")
# A link's target looks like an address, so "[Tom](whispers)" stays a speaker tag and a cue.
_LINK = re.compile(r"\[([^\[\]]+)\]\((?=[^()\s]*[/.:#?])[^()]*\)")
_AUTOLINK = re.compile(r"<(?:https?://|mailto:)[^<>\s]+>")
_HTML_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>")
_CODE = re.compile(r"`+([^`]+?)`+")
_STRONG_STAR = re.compile(r"\*\*(?=[^\s*])([^*]+?)(?<=[^\s*])\*\*")
_STRONG_UNDER = re.compile(r"(?<!\w)__(?=[^\s_])([^_]+?)(?<=[^\s_])__(?!\w)")
_EM_STAR = re.compile(r"(?<![\w*])\*(?=[^\s*])([^*]+?)(?<=[^\s*])\*(?![\w*])")
_EM_UNDER = re.compile(r"(?<![\w_])_(?=[^\s_])([^_]+?)(?<=[^\s_])_(?![\w_])")
_SEP_CELL = re.compile(r":?-{3,}:?")


def _is_rule(line: str) -> bool:
    """A dividing line: three or more of the same "-", "*" or "_", spaces allowed between."""
    marks = line.replace(" ", "").replace("\t", "")
    return len(marks) >= 3 and marks[0] in "-*_" and marks == marks[0] * len(marks)


def _is_table_separator(line: str) -> bool:
    """The |---|:---:| row under a table's header."""
    s = line.strip()
    if "-" not in s or set(s) - set("|:- \t"):
        return False
    return all(_SEP_CELL.fullmatch(cell.strip()) for cell in s.strip("|").split("|"))


def _heading_text(body: str) -> str:
    """The heading's words, without closing marks ("Chapter 2 ##" -> "Chapter 2")."""
    body = body.rstrip()
    unmarked = body.rstrip("#")
    if unmarked != body and (not unmarked or unmarked[-1] in " \t"):
        body = unmarked.rstrip()
    return body


_LEADING_HASH = re.compile(r"(?m)^[ \t]*#+[ \t]*")


def _tidy_inline(line: str) -> str:
    line = _IMAGE.sub("", line)
    line = _LINK.sub(r"\1", line)
    line = _AUTOLINK.sub("", line)
    line = _HTML_TAG.sub("", line)
    line = _CODE.sub(r"\1", line)
    for pattern in (_STRONG_STAR, _STRONG_UNDER, _EM_STAR, _EM_UNDER):
        line = pattern.sub(r"\1", line)
    return line


def tidy_markdown(text: str) -> str:
    """Turns Markdown into plain lines VoiceCast can read (rules in the import spec, section 2)."""
    text = _ESCAPE.sub(lambda m: chr(0xE000 + ord(m.group(1))), text)
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":  # front matter at the very start
        for i in range(1, len(lines)):
            if lines[i].strip() in ("---", "..."):
                lines = lines[i + 1:]
                break
    out, in_code = [], False
    for line in lines:
        if _FENCE.match(line):
            in_code = not in_code
            continue
        if in_code or _is_rule(line) or _is_table_separator(line):
            continue
        heading = _HEADING.match(line)
        if heading:
            line = _heading_text(heading.group(1))
        elif line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            line = ", ".join(c for c in cells if c)
        else:
            line = _LIST.sub("", _QUOTE.sub("", line))
        out.append(_tidy_inline(line).strip())
    return _HIDDEN.sub(lambda m: chr(ord(m.group(0)) - 0xE000), "\n".join(out))


def _finish(text: str) -> str:
    """For every import: lines starting with "#" stay readable (the player skips them),
    trailing spaces go, and runs of blank lines become one."""
    text = _LEADING_HASH.sub("", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip("\n")


def tidy_file(filename: str, text: str) -> str:
    """Tidies an imported file's text; the extension decides whether the Markdown rules run."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in IMPORT_EXTENSIONS:
        raise ImportProblem(NOT_TXT_OR_MD)
    text = clean_text(text)
    if ext == ".md":
        text = tidy_markdown(text)
    return _finish(text)


def decode_bytes(data: bytes) -> str:
    """UTF-8 first; bytes that aren't valid UTF-8 are read as Windows-1252 (as the page does for files),
    so a .md file kept in Drive in the older Windows format keeps its dashes and quotes."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def count_words(text: str) -> int:
    return len(text.split())


# --- Google Docs shared as "Anyone with the link" ---------------------------------------------
# Only VoiceCast's server fetches, and only from Google's own hosts. Tests point these at a fake.
EXPORT_URL = "https://docs.google.com/document/d/{id}/export?format=md"
EXPORT_KIND = ".md"  # how the answer is tidied (Task 1 may switch both to format=txt / ".txt")
ALLOWED_SCHEMES = ("https",)
ALLOWED_HOSTS = ("docs.google.com",)
ALLOWED_HOST_SUFFIXES = (".googleusercontent.com",)
ALLOWED_PORTS = (443,)  # or no port at all
SIGN_IN_HOSTS = ("accounts.google.com",)
FETCH_TIMEOUT = 30  # seconds for the whole download, redirects included
MAX_REDIRECTS = 5

NOT_A_DOC_LINK = "That isn't a Google Docs link. It should start with https://docs.google.com/document/d/"
NOT_SHARED = ("This Doc isn't shared. In Google Docs, click Share and set General access to "
              "Anyone with the link.")
NOT_FOUND = "Google couldn't find that Doc. Check the link."
NO_CONNECTION = "Couldn't reach Google. Check your internet connection."
UNEXPECTED = "Google sent an unexpected answer. Try again later."
TOO_BIG = "That is too big to import (the limit is 5 MB)."

_DOC_LINK = re.compile(r"^https://docs\.google\.com/document/(?:u/\d+/)?d/([A-Za-z0-9_-]{20,})(?:[/?#].*)?$")


def parse_doc_id(link: str) -> str:
    match = _DOC_LINK.match((link or "").strip())
    if not match:
        raise ImportProblem(NOT_A_DOC_LINK)
    return match.group(1)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Every redirect comes back as an HTTPError, so fetch_google_doc can check where it points."""
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)  # no cookie handler: nothing of the user's is sent


def _host(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def _allowed(url: str) -> bool:
    """Only Google's own hosts, over https, on the usual port, with no account part ("user@")."""
    parts = urllib.parse.urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        return False
    if parts.username is not None or parts.password is not None or "\\" in parts.netloc:
        return False
    if port is not None and port not in ALLOWED_PORTS:
        return False
    host = _host(url)
    return parts.scheme in ALLOWED_SCHEMES and (host in ALLOWED_HOSTS or host.endswith(ALLOWED_HOST_SUFFIXES))


def _read_until(response, deadline: float) -> bytes:
    """Reads at most MAX_IMPORT_BYTES + 1 bytes in pieces, giving up when the overall time is up."""
    data = b""
    while len(data) <= MAX_IMPORT_BYTES:
        if time.monotonic() > deadline:
            raise TimeoutError
        piece = response.read1(min(64 * 1024, MAX_IMPORT_BYTES + 1 - len(data)))  # what has arrived so far
        if not piece:
            break
        data += piece
    return data


def fetch_google_doc(link: str) -> str:
    """Downloads a Doc shared as "Anyone with the link" and returns its tidied text."""
    url = EXPORT_URL.format(id=parse_doc_id(link))
    deadline = time.monotonic() + FETCH_TIMEOUT
    for _ in range(MAX_REDIRECTS + 1):
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            request = urllib.request.Request(url, headers={"User-Agent": "VoiceCast"})
            with _OPENER.open(request, timeout=remaining) as response:
                data = _read_until(response, deadline)
                content_type = response.headers.get_content_type()
        except urllib.error.HTTPError as e:
            location = e.headers.get("Location", "") if e.headers else ""
            e.close()
            if e.code in (301, 302, 303, 307, 308) and location:
                try:
                    url = urllib.parse.urljoin(url, location)
                    host, allowed = _host(url), _allowed(url)
                except ValueError:  # an address that can't be read
                    raise ImportProblem(UNEXPECTED) from None
                if host in SIGN_IN_HOSTS:
                    raise ImportProblem(NOT_SHARED) from None
                if not allowed:
                    raise ImportProblem(UNEXPECTED) from None
                continue
            if e.code in (401, 403):
                raise ImportProblem(NOT_SHARED) from None
            if e.code == 404:
                raise ImportProblem(NOT_FOUND) from None
            raise ImportProblem(UNEXPECTED) from None
        except ValueError:  # a redirect address that can't be read
            raise ImportProblem(UNEXPECTED) from None
        except (urllib.error.URLError, OSError, http.client.HTTPException):  # includes timeouts
            raise ImportProblem(NO_CONNECTION) from None
        if len(data) > MAX_IMPORT_BYTES:
            raise ImportProblem(TOO_BIG)
        if content_type == "text/html":  # Google's sign-in page instead of the Doc
            raise ImportProblem(NOT_SHARED)
        if not content_type.startswith("text/"):  # e.g. a PDF: not something to read aloud
            raise ImportProblem(UNEXPECTED)
        return tidy_file("doc" + EXPORT_KIND, decode_bytes(data))
    raise ImportProblem(UNEXPECTED)
