# Third-party licences that need a decision

Most of Prometheus's dependencies use permissive licences (MIT, BSD, Apache-2.0). These use a "copyleft" licence.
Prometheus uses each one **unmodified, as a separate library installed with pip** (never copied into Prometheus's own
files, never changed). Under those licences that is allowed, with the duties below. If Prometheus ever modifies one of
them, or bundles it inside one file with Prometheus's code, this needs a fresh look.

| Library | Licence | Where it is used | What the licence asks | Status |
|---|---|---|---|---|
| ~~fpdf2~~ | ~~LGPL-3.0~~ | was `prometheus/actions/pdf.py` | - | **Replaced (17 Nov) by ReportLab (BSD licence)**: no longer a dependency |
| pywebpush | MPL-2.0 | `prometheus/phone.py` (phone notifications) | File-level copyleft: changes to pywebpush's own files must be shared; using it unmodified asks nothing more | Keep, unmodified |
| pystray | LGPL-3.0 | `prometheus/tray.py` (the tray icon) | Same as fpdf2 | Keep, unmodified |

## Sign-off (owner)
I have read this and accept using the libraries above unmodified, as separate pip packages.

Owner: ____________________   Date: ______________

(Left blank on purpose: only the owner can sign.)

## What changed for PDFs (M3 audit plan D5, done 17 Nov 2026)
fpdf2 (LGPL) was replaced by ReportLab (BSD, permissive: no duties beyond keeping its licence notice). New tests in
`tests/test_v3_pdf_writer.py` were written against fpdf2 first (text, headings, lists, code, clickable links, accents,
page breaks), passed, and passed unchanged on ReportLab. The full suite then passed with fpdf2 uninstalled.
Tested on Linux here and by GitHub's runs; not checked on your laptop.
