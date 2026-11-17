# Third-party licences that need a decision

Most of Prometheus's dependencies use permissive licences (MIT, BSD, Apache-2.0). These use a "copyleft" licence.
Prometheus uses each one **unmodified, as a separate library installed with pip** (never copied into Prometheus's own
files, never changed). Under those licences that is allowed, with the duties below. If Prometheus ever modifies one of
them, or bundles it inside one file with Prometheus's code, this needs a fresh look.

| Library | Licence | Where it is used | What the licence asks | Status |
|---|---|---|---|---|
| fpdf2 | LGPL-3.0 | `prometheus/actions/pdf.py` (Markdown to PDF); two tests make test PDFs | Keep it a separate, replaceable library; keep its licence text; share any changes made to fpdf2 itself | **Planned to be replaced** by ReportLab (BSD) if the PDF tests pass with it (M3 audit plan D5). Not done yet: see below |
| pywebpush | MPL-2.0 | `prometheus/phone.py` (phone notifications) | File-level copyleft: changes to pywebpush's own files must be shared; using it unmodified asks nothing more | Keep, unmodified |
| pystray | LGPL-3.0 | `prometheus/tray.py` (the tray icon) | Same as fpdf2 | Keep, unmodified |

## Sign-off (owner)
I have read this and accept using the libraries above unmodified, as separate pip packages.

Owner: ____________________   Date: ______________

(Left blank on purpose: only the owner can sign.)

## Why fpdf2 is not replaced yet
The plan (docs/m3/M3_AUDIT_FIX_PLAN.md, D5) says to swap fpdf2 for ReportLab only if the PDF tests pass with it.
The session that wrote this could not install ReportLab (the install was blocked), so nothing was swapped or tested.
Until it is, fpdf2 stays and is listed here.
