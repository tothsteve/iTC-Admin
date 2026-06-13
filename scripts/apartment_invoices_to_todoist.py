#!/usr/bin/env python3
"""One command: unread apartment invoices (IMAP) → Todoist tasks + reminders.

For each UNREAD invoice in the apartment mailbox:
  - extracts property (Zágráb/Somfa), amount, payment due date, Gmail deep link
  - creates a Todoist task in the 🏡 Otthon project with the email link
  - adds reminders 3/2/1 days before the due date (at 09:00); overdue invoices get
    priority p1 and no past reminders
  - DEDUPES via a local state file, so re-running never creates duplicates

Read-only on Gmail. Writes only to Todoist.

Setup (.env):
  APARTMENT_EMAIL=...           # already set
  APARTMENT_APP_PASSWORD=...    # already set
  TODOIST_API_TOKEN=...         # Todoist → Settings → Integrations → Developer → API token
  TODOIST_PROJECT_ID=6f7qmf8FWc9F4Rxm   # optional; default = 🏡 Otthon

Usage:
  source venv/bin/activate
  python scripts/apartment_invoices_to_todoist.py --folder "Szamlak"
  python scripts/apartment_invoices_to_todoist.py --folder "Szamlak" --dry-run
"""

import sys
import os
import json
import email
import imaplib
import argparse
import getpass
import uuid
import urllib.parse
from datetime import datetime, timedelta, date

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
except ImportError:
    pass

import requests

sys.path.insert(0, os.path.dirname(__file__))
from read_apartment_invoices_imap import (
    IMAP_HOST, IMAP_PORT, decode_subject, get_body_and_pdfs,
)
from read_apartment_invoices import (
    create_rules_engine, extract_amount, extract_due_date, detect_property,
    extract_pdf_text,
)

DEFAULT_PROJECT_ID = '6f7qmf8FWc9F4Rxm'  # 🏡 Otthon
STATE_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'processed_apartment_invoices.json')
REST = 'https://api.todoist.com/rest/v2'
SYNC = 'https://api.todoist.com/sync/v9/sync'

# Sender substring -> (emoji, type label). Extend as new vendors appear.
VENDOR_TYPES = [
    ('dijnet', ('💧', 'víz')),
    ('mvmee', ('⚡', 'villany')),
    ('mvmnext', ('🔥', 'gáz')),
    ('one-d.hu', ('🌐', 'internet')),
    ('one.hu', ('📱', 'telekom')),
]


def vendor_type(sender: str):
    s = (sender or '').lower()
    for key, val in VENDOR_TYPES:
        if key in s:
            return val
    return ('🧾', 'számla')


def load_state():
    try:
        with open(STATE_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def collect_unread(email_addr, app_pw, folder, max_n=50):
    """Connect read-only and return a list of invoice dicts."""
    imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    imap.login(email_addr, app_pw.replace(' ', ''))
    typ, _ = imap.select(f'"{folder}"', readonly=True)
    if typ != 'OK':
        imap.logout()
        raise RuntimeError(f"Cannot select folder: {folder}")
    typ, data = imap.search(None, 'UNSEEN')
    ids = (data[0].split() if data and data[0] else [])[-max_n:]

    rules = create_rules_engine()
    amount_patterns = rules.collect_amount_patterns()

    out = []
    for mid in ids:
        typ, mdata = imap.fetch(mid, '(RFC822)')
        if typ != 'OK' or not mdata or not mdata[0]:
            continue
        msg = email.message_from_bytes(mdata[0][1])
        subject = decode_subject(msg.get('Subject', ''))
        sender = decode_subject(msg.get('From', ''))
        rfc_id = (msg.get('Message-ID') or '').strip()
        link = ('https://mail.google.com/mail/u/0/#search/'
                + urllib.parse.quote('rfc822msgid:' + rfc_id)) if rfc_id else ''
        body, pdfs = get_body_and_pdfs(msg)
        text = body
        for _, payload in pdfs:
            text += '\n' + extract_pdf_text(payload)
        out.append({
            'msgid': rfc_id or subject,
            'subject': subject,
            'sender': sender,
            'property': detect_property(text + ' ' + subject),
            'amount': extract_amount(text, amount_patterns)[0],
            'due': extract_due_date(text),
            'link': link,
        })
    imap.logout()
    return out


def todoist_headers(token):
    return {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}


def create_task(token, project_id, content, description, due_date, priority):
    """priority: REST 1..4 (4=urgent). Returns task id."""
    payload = {'content': content, 'description': description,
               'project_id': project_id, 'priority': priority}
    if due_date:
        payload['due_date'] = due_date
    r = requests.post(f'{REST}/tasks', headers=todoist_headers(token), json=payload, timeout=30)
    r.raise_for_status()
    return r.json()['id']


def add_absolute_reminders(token, task_id, datetimes):
    """datetimes: list of 'YYYY-MM-DDTHH:MM:SS' strings (floating local time)."""
    commands = []
    for dt in datetimes:
        commands.append({
            'type': 'reminder_add',
            'temp_id': str(uuid.uuid4()),
            'uuid': str(uuid.uuid4()),
            'args': {'item_id': task_id, 'type': 'absolute', 'due': {'date': dt}},
        })
    if not commands:
        return
    r = requests.post(SYNC, headers=todoist_headers(token),
                      json={'commands': commands}, timeout=30)
    r.raise_for_status()


def main():
    parser = argparse.ArgumentParser(description="Unread apartment invoices → Todoist")
    parser.add_argument("--email", default=os.environ.get('APARTMENT_EMAIL'))
    parser.add_argument("--folder", default="Szamlak", help="Mailbox/label name")
    parser.add_argument("--project-id", default=os.environ.get('TODOIST_PROJECT_ID', DEFAULT_PROJECT_ID))
    parser.add_argument("--max", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true", help="Show what would be created, write nothing")
    args = parser.parse_args()

    token = os.environ.get('TODOIST_API_TOKEN')
    if not token and not args.dry_run:
        print("❌ Hiányzik a TODOIST_API_TOKEN a .env-ből.")
        print("   Todoist → Settings → Integrations → Developer → API token → másold a .env-be:")
        print("   TODOIST_API_TOKEN=...")
        return
    if not args.email:
        print("❌ Adj meg email címet (--email vagy APARTMENT_EMAIL a .env-ben)")
        return

    app_pw = os.environ.get('APARTMENT_APP_PASSWORD') or getpass.getpass(
        f"App password for {args.email} (hidden): ")

    print(f"🔌 IMAP {args.email} / {args.folder} ...")
    try:
        invoices = collect_unread(args.email, app_pw, args.folder, args.max)
    except imaplib.IMAP4.error as e:
        print(f"❌ IMAP login/select failed: {e}")
        return
    print(f"   {len(invoices)} olvasatlan levél\n")

    state = load_state()
    today = datetime.now().date()
    created, skipped = 0, 0

    for inv in invoices:
        key = inv['msgid']
        if key in state:
            print(f"⏭️  Már feldolgozva: {inv['subject'][:50]}")
            skipped += 1
            continue
        if not inv['amount'] or not inv['due']:
            print(f"⚠️  Hiányos (összeg/dátum) – kihagyva: {inv['subject'][:50]}")
            continue

        emoji, label = vendor_type(inv['sender'])
        amt_str = f"{inv['amount']:,} Ft".replace(',', ' ')
        content = f"{emoji} {inv['property']} – {label} {amt_str}"
        due_d = date.fromisoformat(inv['due'])
        overdue = due_d <= today

        desc = (f"{'⚠️ LEJÁRT ' if overdue else ''}Határidő: {inv['due']} · {amt_str}\n\n"
                f"[📧 Számla megnyitása Gmailben]({inv['link']})")
        priority = 4 if overdue else 3  # REST: 4=urgent(p1), 3=p2

        # Reminders: 3/2/1 days before at 09:00, only future ones
        reminder_dts = []
        if not overdue:
            for days in (3, 2, 1):
                rd = due_d - timedelta(days=days)
                if rd >= today:
                    reminder_dts.append(f"{rd.isoformat()}T09:00:00")

        rem_note = f"  ⏰ {len(reminder_dts)} emlékeztető" if reminder_dts else ("  ⚠️ lejárt" if overdue else "")
        print(f"➕ {content}  (due {inv['due']}){rem_note}")

        if args.dry_run:
            continue

        task_id = create_task(token, args.project_id, content, desc, inv['due'], priority)
        add_absolute_reminders(token, task_id, reminder_dts)
        state[key] = {'task_id': task_id, 'created_at': today.isoformat(),
                      'subject': inv['subject'], 'amount': inv['amount'], 'due': inv['due']}
        created += 1

    if not args.dry_run:
        save_state(state)

    print(f"\n✅ Kész — {created} új feladat, {skipped} kihagyva (már megvolt)."
          + (" [DRY-RUN: semmi nem jött létre]" if args.dry_run else ""))


if __name__ == "__main__":
    main()
