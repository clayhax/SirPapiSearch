## Overview

SirPapiSearch is a SerpAPI-powered OSINT tool designed to automate:

- Public file enumeration from Google indexing
- Document metadata extraction (without saving files to disk)
- LinkedIn-based name harvesting and email generation

Built specifically for **penetration testing and OSINT workflows**.

---

## Features

### File Enumeration (Default Mode)

- Searches Google via SerpAPI using domain-based dorks
- Supports file types:
  - `pdf`, `docx`, `xlsx`, `pptx`, `doc`, `xls`
  - Optional: `txt`, `csv`
- Extracts:
  - Author, Title, Creator, Producer
  - Application, Company, LastModifiedBy
  - Creation/Modification timestamps
  - Internal path indicators
  - File hashes (SHA256)
  - High-signal findings (emails, usernames, keywords)

---

### Smart File Handling

Handles real-world enterprise document hosting patterns:

- NetSuite (`media.nl?...&_xt=.pdf`)
- SharePoint / OneDrive
- AWS S3 / CloudFront
- Google Drive / Docs
- Salesforce

Includes:
- Query parameter extension detection (`_xt=.pdf`)
- HTTP `Content-Type` fallback
- Streamed downloads (no disk writes)

---

### LinkedIn Email Enumeration Mode

- Uses Google-indexed LinkedIn results (no scraping)
- Extracts **FirstName + LastName**
- Normalizes:
  - Capitalization
  - Accents (`José → Jose`)
  - Prefixes / suffixes (`Dr`, `Jr`, etc.)
- Generates email formats

Supported placeholders:
- `{first}`
- `{last}`
- `{f}`
- `{l}`

---

## Installation

```bash
git clone https://github.com/clayhax/SirPapiSearch.git
cd SirPapiSearch
```
Install dependencies:
```bash
python3 -m pip install requests google-search-results pypdf python-docx openpyxl python-pptx olefile
```

## API Key Configuration
* register and grab your SerpAPI key https://serpapi.com/
* SirPapiSearch supports three methods (priority order):

  - `--api-key`
  - `SERPAPI_KEY` environment variable 
  - Hardcoded fallback in script

---

### Usage

## File Enumeration (Default)

```bash
python3 SirPapiSearch.py example.com
```
## LinkedIn Mode

```bash
python3 SirPapiSearch.py company.com --linkedin --company "Company Name" --email-format "{f}{last}"
```

---

## ⚠ Disclaimer

This tool is intended for **authorized security testing only**.

## 👤 Author

clayhax

---

## ⭐ Support

If you find this tool useful:

* Star the repo
* Report issues
* Contribute or tag me @0xclayhax on twitter with your ideas/suggestions to add
