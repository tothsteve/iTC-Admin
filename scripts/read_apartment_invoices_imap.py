#!/usr/bin/env python3
"""Read-only: list unread (= awaiting payment) apartment invoices from a Gmail
mailbox via IMAP + App Password. No OAuth / no GCP project / no verification.

Reuses the extraction helpers from read_apartment_invoices.py (amount, due date,
property detection, PDF text + OCR). Writes NOTHING.

Setup (once, in the apartment Gmail account):
  1. Account has 2-Step Verification ON.
  2. Create an App Password:  https://myaccount.google.com/apppasswords
  3. (IMAP is on by default for Gmail.)

Usage:
  source venv/bin/activate
  python scripts/read_apartment_invoices_imap.py --email tistvan87@gmail.com
  # app password is prompted (hidden), or set APARTMENT_APP_PASSWORD env var
  python scripts/read_apartment_invoices_imap.py --email x@gmail.com --folder "Számlák"
  python scripts/read_apartment_invoices_imap.py --email x@gmail.com --include-read
"""

import sys
import os
import imaplib
import email
import argparse
import getpass
from email.header import decode_header

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
except ImportError:
    pass

sys.path.insert(0, os.path.dirname(__file__))
from read_apartment_invoices import (  # reuse extraction helpers
    parse_huf, extract_amount, extract_due_date, detect_property,
    extract_pdf_text, create_rules_engine, PROPERTY_KEYWORDS,
)

IMAP_HOST = 'imap.gmail.com'
IMAP_PORT = 993

# Which Gmail account index the deep links open in the browser (tistvan87 = u/2).
GMAIL_USER_INDEX = os.environ.get('APARTMENT_GMAIL_INDEX', '2')


def build_gmail_link(rfc_id: str) -> str:
    """Gmail deep link to a message by RFC822 Message-ID, in the right account index."""
    import urllib.parse
    if not rfc_id:
        return ''
    return (f'https://mail.google.com/mail/u/{GMAIL_USER_INDEX}/#search/'
            + urllib.parse.quote('rfc822msgid:' + rfc_id.strip()))


def decode_mutf7(name: str) -> str:
    """Decode IMAP modified UTF-7 mailbox name to unicode (best effort)."""
    try:
        # IMAP mUTF-7 uses '&' for shift and ',' instead of '/' inside base64
        out = []
        i = 0
        while i < len(name):
            c = name[i]
            if c == '&':
                j = name.find('-', i)
                if j == -1:
                    j = len(name)
                chunk = name[i + 1:j]
                if chunk == '':
                    out.append('&')
                else:
                    b64 = chunk.replace(',', '/')
                    pad = '=' * (-len(b64) % 4)
                    import base64
                    out.append(base64.b64decode(b64 + pad).decode('utf-16-be'))
                i = j + 1
            else:
                out.append(c)
                i += 1
        return ''.join(out)
    except Exception:
        return name


def decode_subject(raw: str) -> str:
    if not raw:
        return ''
    parts = decode_header(raw)
    out = ''
    for text, enc in parts:
        if isinstance(text, bytes):
            try:
                out += text.decode(enc or 'utf-8', errors='ignore')
            except Exception:
                out += text.decode('utf-8', errors='ignore')
        else:
            out += text
    return out


def list_folders(imap):
    """Return list of (display_name, raw_name)."""
    typ, data = imap.list()
    folders = []
    for line in data:
        if isinstance(line, bytes):
            line = line.decode('utf-8', errors='ignore')
        # format: (flags) "/" "Mailbox Name"
        # take last quoted token, else last whitespace token
        if '"' in line:
            raw = line.split('"')[-2]
        else:
            raw = line.split()[-1]
        folders.append((decode_mutf7(raw), raw))
    return folders


def pick_folder(imap):
    folders = list_folders(imap)
    folders.sort(key=lambda f: f[0])
    print("\n📂 IMAP mappák (= Gmail címkék):")
    for i, (disp, raw) in enumerate(folders, 1):
        print(f"   {i}. {disp}")
    choice = input("\n   Melyik a számla-mappa? (szám): ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(folders):
        return folders[int(choice) - 1][1]
    return choice


def html_to_text(html: str) -> str:
    """Strip HTML to plain text (BeautifulSoup if available, else regex)."""
    import re as _re
    try:
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, 'html.parser').get_text(' ')
    except Exception:
        html = _re.sub(r'(?is)<(script|style).*?</\1>', ' ', html)
        return _re.sub(r'<[^>]+>', ' ', html)


def get_body_and_pdfs(msg):
    """Return (body_text, [(filename, bytes), ...]). Falls back to HTML body if no plain."""
    plain = ''
    html = ''
    pdfs = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get('Content-Disposition') or '')
            fname = part.get_filename()
            if fname:
                fname = decode_subject(fname)
            if ctype == 'application/pdf' or (fname and fname.lower().endswith('.pdf')):
                payload = part.get_payload(decode=True)
                if payload:
                    pdfs.append((fname or 'attachment.pdf', payload))
            elif 'attachment' not in disp and ctype in ('text/plain', 'text/html'):
                payload = part.get_payload(decode=True)
                if payload:
                    decoded = payload.decode(part.get_content_charset() or 'utf-8', errors='ignore')
                    if ctype == 'text/plain':
                        plain += decoded + '\n'
                    else:
                        html += decoded + '\n'
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            decoded = payload.decode(msg.get_content_charset() or 'utf-8', errors='ignore')
            if (msg.get_content_type() or '') == 'text/html':
                html = decoded
            else:
                plain = decoded

    body = plain if plain.strip() else html_to_text(html)
    return body, pdfs


def main():
    parser = argparse.ArgumentParser(description="Read unread apartment invoices via IMAP")
    parser.add_argument("--email", default=os.environ.get('APARTMENT_EMAIL'),
                        help="Apartment Gmail address (default: APARTMENT_EMAIL env)")
    parser.add_argument("--folder", help="Mailbox/label name (skip interactive pick)")
    parser.add_argument("--include-read", action="store_true",
                        help="Include read messages too (default: unread only)")
    parser.add_argument("--max", type=int, default=50, help="Max messages to scan")
    parser.add_argument("--debug", action="store_true",
                        help="Dump amount candidates + keyword context per email")
    args = parser.parse_args()

    if not args.email:
        print("❌ Adj meg email címet (--email vagy APARTMENT_EMAIL a .env-ben)")
        return
    app_pw = os.environ.get('APARTMENT_APP_PASSWORD') or getpass.getpass(
        f"App password for {args.email} (hidden): ")

    print(f"🔌 Connecting {IMAP_HOST}:{IMAP_PORT} as {args.email} ...")
    imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    try:
        imap.login(args.email, app_pw.replace(' ', ''))
    except imaplib.IMAP4.error as e:
        print(f"❌ Login failed: {e}")
        print("   Ellenőrizd: 2FA bekapcsolva + App Password (16 karakter), nem a sima jelszó.")
        return
    print("✅ Logged in")

    folder = args.folder or pick_folder(imap)
    print(f"\n🔎 Mappa: {folder}")

    # Select read-only so nothing is marked as read
    typ, _ = imap.select(f'"{folder}"', readonly=True)
    if typ != 'OK':
        print(f"❌ Cannot select folder: {folder}")
        imap.logout()
        return

    criteria = 'ALL' if args.include_read else 'UNSEEN'
    typ, data = imap.search(None, criteria)
    ids = data[0].split() if data and data[0] else []
    ids = ids[-args.max:]
    print(f"   {len(ids)} levél ({criteria})\n")

    rules = create_rules_engine()
    amount_patterns = rules.collect_amount_patterns()

    results = []
    for mid in ids:
        typ, mdata = imap.fetch(mid, '(RFC822)')
        if typ != 'OK' or not mdata or not mdata[0]:
            continue
        msg = email.message_from_bytes(mdata[0][1])
        subject = decode_subject(msg.get('Subject', ''))
        sender = decode_subject(msg.get('From', ''))
        rfc_id = (msg.get('Message-ID') or '').strip()
        gmail_link = build_gmail_link(rfc_id)
        print(f"📧 {subject[:60]}  ({sender})")

        body, pdfs = get_body_and_pdfs(msg)
        text = body
        for fname, payload in pdfs:
            text += '\n' + extract_pdf_text(payload)

        prop = detect_property(text + ' ' + subject)
        amount, amt_src = extract_amount(text, amount_patterns)
        due = extract_due_date(text)

        if args.debug:
            import re as _re
            print("      ── DEBUG ──")
            print(f"      matched amount source: {amt_src}")
            cand = _re.findall(r'(\d[\d . ]{1,})\s*(?:Ft|HUF|forint)', text, _re.IGNORECASE)
            uniq = []
            for c in cand:
                v = parse_huf(c)
                if v and v not in uniq:
                    uniq.append(v)
            print(f"      összeg-jelöltek (Ft/HUF): {sorted(uniq, reverse=True)[:15]}")
            for kw in ['fizetend', 'összesen', 'végösszeg', 'mindössze', 'bruttó', 'számla összeg', 'határid', 'esedék']:
                for mm in _re.finditer(_re.escape(kw), text, _re.IGNORECASE):
                    s = max(0, mm.start() - 10)
                    snippet = text[s:mm.start() + 60].replace('\n', ' ⏎ ')
                    print(f"        [{kw}] …{snippet}…")
                    break
            print(f"      text length: {len(text)} chars")

        results.append({'subject': subject, 'sender': sender, 'property': prop,
                        'amount': amount, 'due': due, 'has_pdf': bool(pdfs),
                        'link': gmail_link})
        amt_str = f"{amount:,} Ft".replace(',', ' ') if amount else "—"
        print(f"      🏠 {prop}   💰 {amt_str}   📅 {due or '—'}   {'📎' if pdfs else '(no pdf)'}")
        if gmail_link:
            print(f"      🔗 {gmail_link}")

    imap.logout()

    # Summary
    print("\n" + "=" * 78)
    print("ÖSSZESÍTŐ — olvasatlan (fizetésre váró) számlák" if not args.include_read
          else "ÖSSZESÍTŐ — összes számla")
    print("=" * 78)
    print(f"{'Ingatlan':<14}{'Összeg':>14}{'Határidő':>14}   Tárgy")
    print("-" * 78)
    total = 0
    by_prop = {}
    for r in results:
        amt_str = f"{r['amount']:,} Ft".replace(',', ' ') if r['amount'] else "—"
        if r['amount']:
            total += r['amount']
            by_prop[r['property']] = by_prop.get(r['property'], 0) + r['amount']
        print(f"{r['property']:<14}{amt_str:>14}{(r['due'] or '—'):>14}   {r['subject'][:40]}")
    print("-" * 78)
    for prop, s in by_prop.items():
        print(f"   {prop}: {s:,} Ft".replace(',', ' '))
    print(f"   ÖSSZ: {total:,} Ft".replace(',', ' '))
    print("=" * 78)

    import json
    print("\n===JSON_START===")
    print(json.dumps(results, ensure_ascii=False))
    print("===JSON_END===")


if __name__ == "__main__":
    main()
