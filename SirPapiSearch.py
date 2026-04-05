#!/usr/bin/python3
import argparse
import csv
import os
import re
import time
import hashlib
import unicodedata
from dataclasses import dataclass, asdict
from io import BytesIO
from urllib.parse import urlparse, unquote, parse_qs

import requests
from serpapi import search

def print_banner():
    banner = r"""
 ____  _      ____             _ ____                      _     
/ ___|(_)_ __|  _ \ __ _ _ __ (_) ___|  ___  __ _ _ __ ___| |__  
\___ \| | '__| |_) / _` | '_ \| \___ \ / _ \/ _` | '__/ __| '_ \ 
 ___) | | |  |  __/ (_| | |_) | |___) |  __/ (_| | | | (__| | | |
|____/|_|_|  |_|   \__,_| .__/|_|____/ \___|\__,_|_|  \___|_| |_|
                        |_|                                      

        SirPapiSearch v3.0 | by clayhax
"""
    print(banner)

# ---------------- SerpAPI Key Configuration ----------------
# API key resolution priority:
#   1) --api-key argument
#   2) SERPAPI_KEY environment variable
#   3) HARDCODED_SERPAPI_KEY (convenient fallback; leave "" to disable)
HARDCODED_SERPAPI_KEY = "<key>"  # e.g. "your_serpapi_key_here"

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

try:
    from docx import Document
except Exception:
    Document = None

try:
    import openpyxl
except Exception:
    openpyxl = None

try:
    from pptx import Presentation
except Exception:
    Presentation = None

try:
    import olefile
except Exception:
    olefile = None


# ---- Heuristics / regex ----
INTERNAL_PATH_PATTERNS = [
    r"[A-Za-z]:\\",            # C:\...
    r"\\\\[A-Za-z0-9_.-]+\\",  # \\server\share...
    r"/Users/",                # macOS
    r"/home/",                 # Linux
]
_internal_path_re = re.compile("|".join(INTERNAL_PATH_PATTERNS))

_email_re = re.compile(r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[A-Za-z]{2,}\b")
_user_re = re.compile(r"\b(?:[A-Za-z0-9_.-]{2,}\\[A-Za-z0-9_.-]{2,}|[A-Za-z0-9_.-]{3,})\b")

KEYWORDS = [
    "password", "passwd", "pwd",
    "token", "apikey", "api_key", "secret", "client_secret",
    "authorization", "bearer",
    "private key", "ssh-rsa", "BEGIN PRIVATE KEY", "BEGIN RSA PRIVATE KEY",
    "connectionstring", "jdbc:", "odbc", "ldap", "saml",
]


# ---------------- LinkedIn (SerpAPI Google results only) ----------------
HONORIFICS = {
    "mr", "mrs", "ms", "miss", "mx", "dr", "prof", "sir", "madam", "dame",
}
SUFFIXES = {
    "jr", "sr", "ii", "iii", "iv", "v", "md", "phd", "dds", "dvm", "esq", "mba", "pe",
}
LASTNAME_PARTICLES = {
    "da", "de", "del", "della", "der", "di", "du", "la", "le", "los", "las",
    "van", "von", "st", "st.", "san", "santa",
}


def strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s)
        if not unicodedata.combining(c)
    )


def normalize_name_token(s: str) -> str:
    s = strip_accents(s)
    s = s.replace("’", "'").replace("`", "'")
    s = re.sub(r"[^\w\-\'.]", "", s, flags=re.UNICODE)
    return s


def clean_linkedin_title_to_name(title: str) -> str:
    """
    Handles common Google result title shapes, e.g.:
      'John Doe - CompanyName'
      'John Doe - Board Chair, CompanyName'
      'LinkedIn - John Doe'
      'LinkedIn · John Doe'
      'John Doe - CompanyName | LinkedIn'
    """
    if not title:
        return ""

    t = title.strip()

    # Strip trailing branding
    t = re.sub(r"\s*\|\s*LinkedIn\s*$", "", t, flags=re.IGNORECASE)

    # Strip leading "LinkedIn - " or "LinkedIn · " or "LinkedIn:"
    t = re.sub(r"^\s*LinkedIn\s*[-·:]\s*", "", t, flags=re.IGNORECASE)

    # Split on dash variants and take first segment as candidate name
    t = re.split(r"\s+[-–—]\s+", t, maxsplit=1)[0].strip()

    # Collapse whitespace
    t = re.sub(r"\s{2,}", " ", t).strip()

    return t


def parse_first_last(full_name: str) -> tuple[str, str]:
    """
    Heuristics:
    - Remove honorifics at start (Dr, Mr, etc.)
    - Remove suffixes at end (Jr, Sr, II, etc.)
    - First token => first name
    - Last token => last name (+ attach particles like 'de la', 'van', etc. if present)
    - Middle names ignored
    """
    if not full_name:
        return ("", "")

    s = full_name.strip()
    s = re.sub(r"[,\u00A0]+", " ", s)
    s = re.sub(r"\s{2,}", " ", s).strip()

    raw_parts = [p for p in s.split(" ") if p]
    parts = [normalize_name_token(p) for p in raw_parts]
    parts = [p for p in parts if p]

    if len(parts) < 2:
        return ("", "")

    while parts and parts[0].rstrip(".").lower() in HONORIFICS:
        parts.pop(0)
    if len(parts) < 2:
        return ("", "")

    while parts and parts[-1].rstrip(".").lower() in SUFFIXES:
        parts.pop()
    if len(parts) < 2:
        return ("", "")

    first = parts[0]
    last = parts[-1]

    # Attach particles immediately before last token (can chain)
    i = len(parts) - 2
    particle_chain = []
    while i >= 1:
        token = parts[i].rstrip(".").lower()
        if token in LASTNAME_PARTICLES:
            particle_chain.insert(0, parts[i])
            i -= 1
            continue
        break

    if particle_chain:
        last = " ".join(particle_chain + [last])

    return (first, last)


def normalize_for_email(s: str) -> str:
    s = strip_accents(s).lower()
    s = s.replace(" ", "")
    s = s.replace("'", "")
    s = s.replace("-", "")
    s = re.sub(r"[^a-z0-9.]", "", s)
    return s


def render_email(fmt: str, first: str, last: str, email_domain: str | None = None) -> str:
    first_n = normalize_for_email(first)
    last_n = normalize_for_email(last)

    mapping = {
        "first": first_n,
        "last": last_n,
        "f": first_n[:1],
        "l": last_n[:1],
    }

    out = fmt
    for k, v in mapping.items():
        out = out.replace("{" + k + "}", v)

    if "@" not in out:
        if not email_domain:
            raise ValueError("email format does not contain '@' and no email domain was provided")
        out = out + "@" + email_domain.strip()

    return out


def linkedin_search_names(company: str, api_key: str, max_results: int, sleep_s: float) -> list[tuple[str, str, str]]:
    query = f'site:linkedin.com/in "{company}"'
    urls_seen = set()
    results_out = []

    for start in range(0, max_results, 10):
        print(f"[+] (linkedin) Fetching results from offset {start}")
        params = {"engine": "google", "q": query, "api_key": api_key, "start": start, "num": 10}

        results = search(params)
        organic = results.get("organic_results", [])
        if not organic:
            print("[-] (linkedin) No more results.")
            break

        for r in organic:
            link = (r.get("link") or "").strip()
            title = (r.get("title") or "").strip()

            if "linkedin.com/in/" not in link:
                continue
            if link in urls_seen:
                continue
            urls_seen.add(link)

            name_chunk = clean_linkedin_title_to_name(title)
            if not name_chunk:
                continue

            # Filter obvious non-person titles
            if re.search(r"\b(linkedin|profiles?|people)\b", name_chunk, re.IGNORECASE):
                continue
            if "member" in name_chunk.lower() and "linkedin" in name_chunk.lower():
                continue

            first, last = parse_first_last(name_chunk)
            if not first or not last:
                continue

            results_out.append((link, first, last))

        time.sleep(sleep_s)

    return results_out


# ---------------- File Enumeration Helpers ----------------
def safe_filename_from_url(url: str) -> str:
    path = urlparse(url).path
    name = os.path.basename(path) or "unknown"
    name = unquote(name)
    name = re.sub(r"[^\w.\-() ]+", "_", name).strip()
    return name or "unknown"


#def guess_ext(url: str) -> str:
#    fn = safe_filename_from_url(url)
#    _, ext = os.path.splitext(fn)
#    return ext.lower().lstrip(".")

def guess_ext(url: str) -> str:
    parsed = urlparse(url)

    # Check query parameters first (NetSuite style)
    qs = parse_qs(parsed.query)

    if "_xt" in qs:
        ext = qs["_xt"][0]
        return ext.lower().lstrip(".")

    # Fallback to filename parsing
    fn = safe_filename_from_url(url)
    _, ext = os.path.splitext(fn)
    return ext.lower().lstrip(".")
    
PLATFORM_PATTERNS = [
    ("NetSuite", [
        r"/core/media/media\.nl\b",
        r"[?&]_xt=\.",
        r"[?&]c=\d+",
    ]),
    ("SharePoint/OneDrive", [
        r"sharepoint\.com",
        r"sharepoint-df\.com",
        r"-my\.sharepoint\.com",
        r"/_layouts/15/download\.aspx",
        r"/_layouts/15/Doc\.aspx",
        r"/:b:/s/",
        r"/personal/",
    ]),
    ("AWS S3/CDN", [
        r"\.s3\.amazonaws\.com",
        r"s3\.amazonaws\.com",
        r"\.cloudfront\.net",
    ]),
    ("Google Drive/Docs", [
        r"drive\.google\.com/file/d/",
        r"docs\.google\.com/document/d/",
        r"docs\.google\.com/spreadsheets/d/",
        r"docs\.google\.com/presentation/d/",
    ]),
    ("Salesforce", [
        r"content\.force\.com",
        r"/servlet/servlet\.FileDownload",
        r"/file-asset/",
        r"\.my\.salesforce\.com",
    ]),
]

def detect_platform(url: str) -> str:
    u = url.lower()
    for platform, patterns in PLATFORM_PATTERNS:
        for pat in patterns:
            if re.search(pat, u, re.IGNORECASE):
                return platform
    return ""

def normalize_dt(dt) -> str:
    if not dt:
        return ""
    try:
        return dt.isoformat()
    except Exception:
        return str(dt)


def sha256_bytes(b: bytes) -> str:
    h = hashlib.sha256()
    h.update(b)
    return h.hexdigest()


def extract_internal_paths(meta_dict: dict) -> str:
    if not meta_dict:
        return ""
    hits = []
    for k, v in meta_dict.items():
        if v is None:
            continue
        s = str(v)
        if _internal_path_re.search(s):
            hits.append(f"{k}={s}")
    return "; ".join(hits)


def detect_text_encoding(sample: bytes) -> str:
    try:
        sample.decode("utf-8")
        return "utf-8"
    except Exception:
        return "latin-1"


def findings_from_text(content: bytes, sample_limit: int = 300_000) -> dict:
    sample = content[:sample_limit]
    enc = detect_text_encoding(sample)
    text = sample.decode(enc, errors="replace")

    emails = _email_re.findall(text)
    users = [u for u in _user_re.findall(text) if len(u) <= 64]
    paths = _internal_path_re.findall(text)

    kw_hits = []
    lower = text.lower()
    for kw in KEYWORDS:
        if kw.lower() in lower:
            kw_hits.append(kw)

    def summarize(items, max_samples=5):
        uniq = []
        seen = set()
        for i in items:
            if i in seen:
                continue
            seen.add(i)
            uniq.append(i)
            if len(uniq) >= max_samples:
                break
        return len(seen), uniq

    email_count, email_samples = summarize(emails)
    user_count, user_samples = summarize(users)
    path_count, path_samples = summarize(paths)

    findings_parts = []
    if email_count:
        findings_parts.append(f"emails={email_count} samples={email_samples}")
    if user_count:
        findings_parts.append(f"user_tokens={user_count} samples={user_samples}")
    if path_count:
        findings_parts.append(f"internal_paths={path_count} samples={path_samples}")
    if kw_hits:
        findings_parts.append(f"keywords={sorted(set(kw_hits))}")

    return {
        "Encoding": enc,
        "Findings": "; ".join(findings_parts) if findings_parts else "",
        "InternalPathIndicators": extract_internal_paths({"ContentSample": text[:5000]}),
    }


@dataclass
class MetaRow:
    URL: str
    FileType: str
    FileName: str
    Platform: str
    SizeBytes: str
    ContentType: str
    SHA256: str

    Title: str
    Author: str
    Creator: str
    Producer: str
    Application: str
    Company: str
    LastModifiedBy: str
    Created: str
    Modified: str

    HttpLastModified: str
    HttpETag: str

    Encoding: str
    Findings: str

    InternalPathIndicators: str
    Error: str


def http_fetch(url: str, timeout: int, max_bytes: int, user_agent: str):
    headers = {"User-Agent": user_agent}
    with requests.get(url, headers=headers, timeout=timeout, stream=True, allow_redirects=True) as r:
        r.raise_for_status()

        ct = r.headers.get("Content-Type", "").split(";")[0].strip()
        lm = r.headers.get("Last-Modified", "") or ""
        etag = r.headers.get("ETag", "") or ""

        cl = r.headers.get("Content-Length")
        if cl and cl.isdigit() and int(cl) > max_bytes:
            raise ValueError(f"Content-Length {cl} exceeds max_bytes {max_bytes}")

        buf = BytesIO()
        total = 0
        for chunk in r.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"Downloaded bytes exceeded max_bytes {max_bytes}")
            buf.write(chunk)

        content = buf.getvalue()
        return content, ct, str(total), lm, etag

CONTENT_TYPE_MAP = {
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.ms-excel": "xls",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "text/csv": "csv",
    "text/plain": "txt",
}

# ---------------- Extractors ----------------
def extract_pdf(content: bytes) -> dict:
    if PdfReader is None:
        raise RuntimeError("pypdf not installed (python3 -m pip install pypdf)")
    reader = PdfReader(BytesIO(content))
    meta = reader.metadata
    md = {}
    if meta:
        md = {str(k): "" if v is None else str(v) for k, v in dict(meta).items()}

    return {
        "Title": md.get("/Title", ""),
        "Author": md.get("/Author", ""),
        "Creator": md.get("/Creator", ""),
        "Producer": md.get("/Producer", ""),
        "Application": "",
        "Company": "",
        "LastModifiedBy": "",
        "Created": md.get("/CreationDate", ""),
        "Modified": md.get("/ModDate", ""),
        "InternalPathIndicators": extract_internal_paths(md),
    }


def extract_docx(content: bytes) -> dict:
    if Document is None:
        raise RuntimeError("python-docx not installed (python3 -m pip install python-docx)")
    doc = Document(BytesIO(content))
    cp = doc.core_properties
    md = {
        "Title": cp.title or "",
        "Author": cp.author or "",
        "Creator": "",
        "Producer": "",
        "Application": cp.application or "",
        "Company": cp.company or "",
        "LastModifiedBy": cp.last_modified_by or "",
        "Created": normalize_dt(cp.created),
        "Modified": normalize_dt(cp.modified),
    }
    md["InternalPathIndicators"] = extract_internal_paths(md)
    return md


def extract_xlsx(content: bytes) -> dict:
    if openpyxl is None:
        raise RuntimeError("openpyxl not installed (python3 -m pip install openpyxl)")
    wb = openpyxl.load_workbook(filename=BytesIO(content), read_only=True, data_only=True)
    p = wb.properties
    md = {
        "Title": p.title or "",
        "Author": p.creator or "",
        "Creator": p.creator or "",
        "Producer": "",
        "Application": p.application or "",
        "Company": p.company or "",
        "LastModifiedBy": p.lastModifiedBy or "",
        "Created": normalize_dt(p.created),
        "Modified": normalize_dt(p.modified),
    }
    md["InternalPathIndicators"] = extract_internal_paths(md)
    return md


def extract_pptx(content: bytes) -> dict:
    if Presentation is None:
        raise RuntimeError("python-pptx not installed (python3 -m pip install python-pptx)")
    pres = Presentation(BytesIO(content))
    cp = pres.core_properties
    md = {
        "Title": cp.title or "",
        "Author": cp.author or "",
        "Creator": "",
        "Producer": "",
        "Application": cp.application or "",
        "Company": cp.company or "",
        "LastModifiedBy": cp.last_modified_by or "",
        "Created": normalize_dt(cp.created),
        "Modified": normalize_dt(cp.modified),
    }
    md["InternalPathIndicators"] = extract_internal_paths(md)
    return md


def extract_ole_office(content: bytes) -> dict:
    if olefile is None:
        raise RuntimeError("olefile not installed (python3 -m pip install olefile)")

    ole = olefile.OleFileIO(BytesIO(content))
    meta = olefile.OleMetadata()
    meta.parse(ole)
    ole.close()

    md = {
        "Title": meta.title or "",
        "Author": meta.author or "",
        "Creator": "",
        "Producer": "",
        "Application": meta.creating_application or "",
        "Company": meta.company or "",
        "LastModifiedBy": meta.last_saved_by or "",
        "Created": normalize_dt(getattr(meta, "create_time", None)),
        "Modified": normalize_dt(getattr(meta, "last_saved_time", None)),
    }
    md["InternalPathIndicators"] = extract_internal_paths(md)
    return md


def extract_txt(content: bytes) -> dict:
    return findings_from_text(content)


def extract_csv(content: bytes) -> dict:
    return findings_from_text(content)


EXTRACTORS = {
    "pdf": extract_pdf,
    "docx": extract_docx,
    "xlsx": extract_xlsx,
    "pptx": extract_pptx,
    "doc": extract_ole_office,
    "xls": extract_ole_office,
    # opt-in
    "txt": extract_txt,
    "csv": extract_csv,
}


def serp_search_filetype(domain: str, ext: str, api_key: str, max_results: int, sleep_s: float) -> set[str]:
    q = f"site:{domain} filetype:{ext}"
    urls = set()

    for start in range(0, max_results, 10):
        print(f"[+] ({ext}) Fetching results from offset {start}")
        params = {"engine": "google", "q": q, "api_key": api_key, "start": start, "num": 10}
        results = search(params)
        organic = results.get("organic_results", [])
        if not organic:
            print(f"[-] ({ext}) No more results.")
            break

        for r in organic:
            link = r.get("link")
            if link and domain in link:
                urls.add(link)

        time.sleep(sleep_s)

    return urls


def main():
    parser = argparse.ArgumentParser(
        description="Enumerate publicly indexed files via SerpAPI (Google) and extract high-value metadata."
    )
    parser.add_argument("domain", help="Target domain for file enumeration OR email domain for --linkedin mode (e.g. example.com)")

    parser.add_argument(
        "--api-key",
        default=None,
        help="SerpAPI key (overrides SERPAPI_KEY env var and HARDCODED_SERPAPI_KEY)"
    )

    # LinkedIn mode: OFF by default. When enabled, tool will ONLY run LinkedIn mode and exit.
    parser.add_argument(
        "--linkedin",
        action="store_true",
        help="LinkedIn email enumeration mode (SerpAPI Google results only). Requires --company and --email-format. Exits after writing emails."
    )
    parser.add_argument("--company", default=None,
                        help="Company name to search in LinkedIn results (required with --linkedin)")
    parser.add_argument("--email-format", default=None,
                        help="REQUIRED with --linkedin. Template supports {first},{last},{f},{l}. "
                             "Examples: '{f}{last}@domain.com', '{first}{last}@domain.com', '{first}.{last}' (appends @<domain>).")
    # Optional override; by default we use the positional domain argument as the email domain.
    parser.add_argument("--email-domain", default=None,
                        help="Optional override: email domain to use for --linkedin mode. If omitted, uses positional <domain> argument.")
    parser.add_argument("--out-emails", default="linkedin-emails.txt",
                        help="Output file for generated emails (default: linkedin-emails.txt)")

    parser.add_argument(
        "--types",
        default="pdf,docx,xlsx,pptx,doc,xls",
        help="Comma-separated file extensions (default: pdf,docx,xlsx,pptx,doc,xls). Add csv,txt if desired."
    )
    parser.add_argument("--max", type=int, default=700, help="Max SerpAPI results per type (default: 700)")
    parser.add_argument("--sleep", type=float, default=1.0, help="Sleep between SerpAPI requests (default: 1.0)")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout seconds (default: 20)")
    parser.add_argument("--max-bytes", type=int, default=20_000_000, help="Max download size per file (default: 20MB)")
    parser.add_argument("--user-agent", default="Mozilla/5.0 (compatible; FileEnum/3.1)",
                        help="User-Agent for HTTP fetches")
    parser.add_argument("--out-urls", default="URLs.txt", help="Output file for URLs (default: URLs.txt)")
    parser.add_argument("--out-csv", default=None, help="Output CSV file (default: <domain>-Metadata.csv)")
    args = parser.parse_args()
    print_banner()

    # Resolve API key priority:
    api_key = args.api_key or os.getenv("SERPAPI_KEY") or HARDCODED_SERPAPI_KEY
    if not api_key:
        raise SystemExit(
            "[-] Missing SerpAPI key. Provide --api-key, set SERPAPI_KEY env var, "
            "or hardcode HARDCODED_SERPAPI_KEY in the script."
        )

    # ---------------- LinkedIn Mode (only if explicitly requested) ----------------
    if args.linkedin:
        if not args.company:
            raise SystemExit("[-] --company is required when using --linkedin")
        if not args.email_format:
            raise SystemExit("[-] --email-format is required when using --linkedin")

        # Email domain defaults to positional <domain> unless overridden
        effective_email_domain = args.email_domain or args.domain
        
        if "." not in effective_email_domain:
            raise SystemExit(
                f"[-] Email domain looks invalid: '{effective_email_domain}'. "
                f"Use a full domain like '{effective_email_domain}.com' or pass --email-domain."
            )
            
        contacts = linkedin_search_names(
            company=args.company,
            api_key=api_key,
            max_results=args.max,
            sleep_s=args.sleep
        )

        print(f"[✓] (linkedin) Parsed {len(contacts)} LinkedIn name hits (first+last).")

        emails = set()
        for (url, first, last) in contacts:
            try:
                emails.add(render_email(args.email_format, first, last, effective_email_domain))
            except Exception as e:
                print(f"[-] (linkedin) Failed rendering email for {first} {last} ({url}): {e}")

        sorted_emails = sorted(emails)
        with open(args.out_emails, "w", encoding="utf-8") as f:
            for e in sorted_emails:
                f.write(e + "\n")

        print(f"[✓] Emails saved to {args.out_emails} ({len(sorted_emails)} unique).")
        return  # will not proceed automatically with file enumeration afterward in linkedin mode

    # ---------------- File Enumeration Mode (default) ----------------
    out_csv = args.out_csv or f"{args.domain}-Metadata.csv"
    types = [t.strip().lower().lstrip(".") for t in args.types.split(",") if t.strip()]
    all_urls: set[str] = set()

    for ext in types:
        all_urls |= serp_search_filetype(args.domain, ext, api_key, args.max, args.sleep)

    sorted_urls = sorted(all_urls)
    print(f"\n[✓] Found {len(sorted_urls)} unique URLs across types: {', '.join(types)}")

    with open(args.out_urls, "w", encoding="utf-8") as f:
        for u in sorted_urls:
            f.write(u + "\n")
    print(f"[✓] URLs saved to {args.out_urls}")

    fieldnames = list(MetaRow.__annotations__.keys())
    with open(out_csv, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        for idx, url in enumerate(sorted_urls, 1):
            ext = guess_ext(url) or "unknown"
            filename = safe_filename_from_url(url)

            row = MetaRow(
                URL=url,
                FileType=ext,
                FileName=filename,
                Platform=detect_platform(url),
                SizeBytes="",
                ContentType="",
                SHA256="",

                Title="",
                Author="",
                Creator="",
                Producer="",
                Application="",
                Company="",
                LastModifiedBy="",
                Created="",
                Modified="",

                HttpLastModified="",
                HttpETag="",

                Encoding="",
                Findings="",

                InternalPathIndicators="",
                Error="",
            )

            print(f"[+] ({idx}/{len(sorted_urls)}) Processing: {url}")

            try:
                content, ct, size_bytes, lm, etag = http_fetch(
                    url=url, timeout=args.timeout, max_bytes=args.max_bytes, user_agent=args.user_agent
                )
                ct = ct.split(";")[0].strip()
                row.ContentType = ct
                # If extension unknown, try to infer from Content-Type
                if ext not in EXTRACTORS and ct in CONTENT_TYPE_MAP:
                    ext = CONTENT_TYPE_MAP[ct]
                    row.FileType = ext
                row.SizeBytes = size_bytes
                row.HttpLastModified = lm
                row.HttpETag = etag
                row.SHA256 = sha256_bytes(content)

                extractor = EXTRACTORS.get(ext)
                if not extractor:
                    row.Error = f"No extractor for extension: {ext}"
                else:
                    md = extractor(content)
                    for k, v in md.items():
                        if hasattr(row, k) and v is not None:
                            setattr(row, k, str(v))

            except Exception as e:
                row.Error = str(e)

            writer.writerow(asdict(row))

    print(f"[✓] Report saved to {out_csv}")


if __name__ == "__main__":
    main()
