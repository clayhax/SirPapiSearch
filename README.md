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
  - `pdf`, `docx`, `xlsx`, `pptx`, `doc`, `xls`, `txt`, `csv`
- Extracts:
  - Author, Title, Creator, Producer
  - Application, Company, LastModifiedBy
  - Creation/Modification timestamps
  - Internal path indicators
  - File hashes (SHA256)
  - High-signal findings (emails, usernames, keywords)
- Streamed downloads (no disk writes)
- Outputs file URLs to `URLs.txt` and extracted metadata to `{companyname}-Metadata.csv` by default

---

### LinkedIn Email Enumeration Mode

- Uses Google-indexed LinkedIn results (no scraping)
- Extracts **FirstName + LastName**
- Generates email formats

Supported placeholders:
- `{first}`, `{last}`, `{f}`, `{l}`

---

## Installation

```bash
git clone https://github.com/clayhax/SirPapiSearch.git
cd SirPapiSearch
```
Install dependencies:
```bash
python3 -m pip install serpapi requests pypdf python-docx openpyxl python-pptx olefile
```

## API Key Configuration
* Register and grab your SerpAPI key https://serpapi.com/
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

Comments, suggestions, and improvements are always welcome. Be sure to follow @0xclayhax on Twitter for the latest updates.
