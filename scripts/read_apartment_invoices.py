#!/usr/bin/env python3
"""Read-only: list unread (= awaiting payment) apartment invoices from a SECOND mailbox.

Authorizes a separate Google account (its own token file, leaving the itcardigan
token untouched), finds a chosen label, reads unread messages + their PDFs, and
extracts: property (Zágráb / Somfa), amount, payment due date.

Writes NOTHING (no Sheets, no Dropbox, no label changes). Read-only Gmail scope.

Usage:
    source venv/bin/activate
    python scripts/read_apartment_invoices.py                 # interactive label pick
    python scripts/read_apartment_invoices.py --label "Számlák"
    python scripts/read_apartment_invoices.py --label "Számlák" --include-read
"""

import sys
import os
import re
import asyncio
import argparse
import tempfile
from pathlib import Path

# PDF text
try:
    import PyPDF2
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

# OCR fallback (optional)
try:
    from pdf2image import convert_from_path
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from config import get_settings
from gmail.auth import GmailAuth
from gmail.client import GmailClient
from invoice_processor import create_rules_engine

READONLY_SCOPE = 'https://www.googleapis.com/auth/gmail.readonly'
APARTMENT_TOKEN = 'gmail_token_apartment.json'

# Property detection: lowercase substring match. Includes street addresses AND
# deterministic account/customer identifiers (extend as new invoices are learned).
PROPERTY_KEYWORDS = {
    'Zágráb': ['zágráb', 'zagráb', 'zagreb', 'zágrábi utca', 'díjnet', 'dijnet'],
    'Somfa': ['somfa', '2007547090'],  # 2007547090 = One Somfa számlafizető azonosító
}

# Default when no keyword matches (user: unknown → Zágráb). Somfa stays explicit.
DEFAULT_PROPERTY = 'Zágráb'


def parse_huf(s: str):
    """Normalize a Hungarian-formatted amount string to int (drop decimals)."""
    if not s:
        return None
    s = s.replace(' ', ' ').strip()
    if ',' in s:           # decimal comma -> keep integer part
        s = s.split(',')[0]
    digits = re.sub(r'[^\d]', '', s)   # strip space/dot thousand seps
    return int(digits) if digits else None


# Space/dot/nbsp-thousand-separated Hungarian amount, optional decimals
_NUM = r'(\d{1,3}(?:[ .' + ' ' + r']\d{3})+(?:,\d{2})?|\d+(?:,\d{2})?)'

# Strong "total payable" anchors, highest priority first
_TOTAL_ANCHORS = [
    'Fizetendő összeg', 'Fizetendő végösszeg', 'Fizetni kell', 'Fizetendő',
    'Mindösszesen', 'Bruttó végösszeg', 'Végösszeg',
]


def extract_amount(text: str, patterns):
    """Prefer 'total payable' anchors (space-aware number), then known patterns,
    then loose fallback. Returns (amount, source)."""
    for kw in _TOTAL_ANCHORS:
        m = re.search(re.escape(kw) + r'\s*:?\s*' + _NUM + r'\s*(?:Ft|HUF|forint)?',
                      text, re.IGNORECASE)
        if m:
            amt = parse_huf(m.group(1))
            if amt and amt >= 100:
                return amt, 'anchor: ' + kw
    for pat in patterns:
        try:
            m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
        except re.error:
            continue
        if m:
            val = m.group(1) if m.groups() else m.group(0)
            amt = parse_huf(val)
            if amt and amt >= 100:
                return amt, pat
    for kw in ['Fizetendő összeg', 'Fizetendő', 'Összesen', 'Végösszeg', 'Bruttó', 'Mindösszesen']:
        m = re.search(re.escape(kw) + r'[^0-9]{0,25}(\d[\d . ]{1,})', text, re.IGNORECASE)
        if m:
            amt = parse_huf(m.group(1))
            if amt and amt >= 100:
                return amt, 'fallback: ' + kw
    return None, None


def extract_due_date(text: str):
    """Prefer dates near payment-deadline keywords, else first date. Returns YYYY-MM-DD."""
    for kw in ['fizetési határidő', 'fiz. határidő', 'fiz.határidő', 'esedékesség',
               'befizetési határidő', 'határidő', 'due date', 'fizetendő']:
        m = re.search(re.escape(kw) + r'[^0-9]{0,15}(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})',
                      text, re.IGNORECASE)
        if m:
            y, mo, d = m.groups()
            return f"{y}-{int(mo):02d}-{int(d):02d}"
    m = re.search(r'(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})', text)
    if m:
        y, mo, d = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    return None


def detect_property(text: str):
    # The CONSUMPTION address ("felhasználási hely") decides the property, NOT the
    # billing/customer address. MVM bills list both — e.g. customer at Zágrábi utca
    # but the metered usage point at Somfa utca — so matching the whole text would
    # mis-detect. Match the usage-point lines first; fall back to the full text.
    usage = '\n'.join(ln for ln in text.split('\n')
                      if re.search(r'felhaszn[aá]l[aá]si\s+hely', ln, re.I))
    for scope in (usage.lower(), text.lower()):
        if not scope:
            continue
        hits = [name for name, kws in PROPERTY_KEYWORDS.items() if any(k in scope for k in kws)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return '+'.join(hits) + ' (?)'
    return DEFAULT_PROPERTY


def extract_pdf_text(data: bytes) -> str:
    """PyPDF2 text; OCR fallback if too little text."""
    if not PDF_AVAILABLE:
        return ''
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tf:
        tf.write(data)
        tmp = Path(tf.name)
    text = ''
    try:
        with open(tmp, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                text += (page.extract_text() or '') + '\n'
    except Exception as e:
        print(f"      ⚠️  PyPDF2 failed: {e}")
    if len(text.strip()) < 100 and OCR_AVAILABLE:
        try:
            print("      🔍 Little text — OCR fallback...")
            images = convert_from_path(str(tmp))
            for img in images:
                text += pytesseract.image_to_string(img, lang='hun+eng') + '\n'
        except Exception as e:
            print(f"      ⚠️  OCR failed: {e}")
    try:
        tmp.unlink()
    except Exception:
        pass
    # MVM PDFs embed UTF-16 runs that PyPDF2 leaves as NUL bytes interspersed
    # between characters, breaking substring matches (e.g. "S\x00o\x00m\x00f\x00a").
    return text.replace('\x00', '')


async def build_apartment_client() -> GmailClient:
    """Authorize the second account with a separate read-only token file."""
    settings = get_settings()
    auth = GmailAuth()
    auth.token_file = os.path.join(settings.credentials_dir, APARTMENT_TOKEN)
    auth.scopes = [READONLY_SCOPE]
    print(f"🔑 Using separate token: {auth.token_file}")
    print("   (First run opens a browser/URL — log in with the APARTMENT mailbox account)")
    client = GmailClient(auth)
    ok = await client.initialize()
    if not ok:
        return None
    return client


def pick_label(client: GmailClient) -> str:
    """List user labels and let the user choose one."""
    res = client.service.users().labels().list(userId='me').execute()
    labels = [l for l in res.get('labels', []) if l.get('type') != 'system']
    labels.sort(key=lambda l: l['name'])
    print("\n📂 Címkék a fiókban:")
    for i, l in enumerate(labels, 1):
        print(f"   {i}. {l['name']}")
    choice = input("\n   Melyik a számla-címke? (szám): ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(labels):
        return labels[int(choice) - 1]['name']
    return choice  # treat as raw label name


async def main():
    parser = argparse.ArgumentParser(description="Read unread apartment invoices from a second mailbox")
    parser.add_argument("--label", help="Invoice label name (skip interactive pick)")
    parser.add_argument("--include-read", action="store_true",
                        help="Include read messages too (default: unread only = awaiting payment)")
    parser.add_argument("--max", type=int, default=50, help="Max threads to scan")
    args = parser.parse_args()

    client = await build_apartment_client()
    if not client:
        print("❌ Auth failed")
        return

    profile = client.service.users().getProfile(userId='me').execute()
    print(f"✅ Connected mailbox: {profile.get('emailAddress')}\n")

    label = args.label or pick_label(client)
    print(f"\n🔎 Label: {label}")

    query = f'label:"{label}"'
    if not args.include_read:
        query += ' is:unread'
    print(f"   Query: {query}")

    emails = await client.search_emails(query, max_results=args.max, require_attachments=False)
    print(f"   {len(emails)} levél\n")

    rules = create_rules_engine()
    amount_patterns = rules.collect_amount_patterns()

    results = []
    for email in emails:
        subject = email.get('subject', '')
        sender = email.get('sender', '')
        print(f"📧 {subject[:60]}  ({sender})")

        # Aggregate text from PDFs + email body
        text = email.get('body', '') or ''
        pdfs = email.get('pdf_attachments', [])
        for att in pdfs:
            data = await client.download_attachment(email['id'], att['attachment_id'], att['filename'])
            if data:
                text += '\n' + extract_pdf_text(data)

        prop = detect_property(text + ' ' + subject)
        amount, amt_src = extract_amount(text, amount_patterns)
        due = extract_due_date(text)

        results.append({
            'subject': subject,
            'property': prop,
            'amount': amount,
            'due': due,
            'has_pdf': bool(pdfs),
        })
        amt_str = f"{amount:,} Ft".replace(',', ' ') if amount else "—"
        print(f"      🏠 {prop}   💰 {amt_str}   📅 {due or '—'}   {'📎' if pdfs else '(no pdf)'}")

    # Summary table
    print("\n" + "=" * 78)
    print("ÖSSZESÍTŐ — olvasatlan (fizetésre váró) számlák" if not args.include_read
          else "ÖSSZESÍTŐ — összes számla")
    print("=" * 78)
    print(f"{'Ingatlan':<14}{'Összeg':>14}{'Határidő':>14}   Tárgy")
    print("-" * 78)
    total = 0
    for r in results:
        amt_str = f"{r['amount']:,} Ft".replace(',', ' ') if r['amount'] else "—"
        if r['amount']:
            total += r['amount']
        print(f"{r['property']:<14}{amt_str:>14}{(r['due'] or '—'):>14}   {r['subject'][:40]}")
    print("-" * 78)
    by_prop = {}
    for r in results:
        if r['amount']:
            by_prop[r['property']] = by_prop.get(r['property'], 0) + r['amount']
    for prop, s in by_prop.items():
        print(f"   {prop}: {s:,} Ft".replace(',', ' '))
    print(f"   ÖSSZ: {total:,} Ft".replace(',', ' '))
    print("=" * 78)


if __name__ == "__main__":
    asyncio.run(main())
