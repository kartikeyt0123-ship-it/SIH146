"""Generate the demonstration video script as a PDF.

Every figure quoted in the script was measured from an actual run of
data/samples/demo_mixed_patterns.csv, not estimated. Re-run that file and check the
numbers against your own instance before recording - they depend on the file you use.

    backend/.venv/Scripts/python.exe scripts/make_demo_script_pdf.py
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "ChainLens_Demo_Video_Script.pdf"

NAVY = colors.HexColor("#0f2540")
SLATE = colors.HexColor("#475569")
MUTED = colors.HexColor("#64748b")
LINE = colors.HexColor("#dfe4ea")
AMBER = colors.HexColor("#b54708")
AMBER_BG = colors.HexColor("#fef4e6")
GREEN = colors.HexColor("#17694a")
PANEL = colors.HexColor("#f6f7f9")

styles = getSampleStyleSheet()

H1 = ParagraphStyle("H1", parent=styles["Title"], fontName="Helvetica-Bold",
                    fontSize=24, leading=28, textColor=NAVY, alignment=TA_LEFT,
                    spaceAfter=2)
SUB = ParagraphStyle("SUB", parent=styles["Normal"], fontSize=10.5, leading=15,
                     textColor=SLATE, spaceAfter=10)
SCENE = ParagraphStyle("SCENE", parent=styles["Heading2"], fontName="Helvetica-Bold",
                       fontSize=13, leading=16, textColor=NAVY,
                       spaceBefore=13, spaceAfter=1)
TIME = ParagraphStyle("TIME", parent=styles["Normal"], fontName="Helvetica-Bold",
                      fontSize=8.5, leading=11, textColor=AMBER, spaceAfter=4)
DO = ParagraphStyle("DO", parent=styles["Normal"], fontName="Helvetica-Oblique",
                    fontSize=9, leading=12.5, textColor=MUTED, spaceAfter=5,
                    leftIndent=8, borderPadding=0)
SAY = ParagraphStyle("SAY", parent=styles["Normal"], fontSize=11, leading=16.5,
                     textColor=colors.HexColor("#17212f"), spaceAfter=7,
                     leftIndent=8)
NOTE = ParagraphStyle("NOTE", parent=styles["Normal"], fontSize=8.5, leading=12,
                      textColor=MUTED, spaceAfter=4)
H2 = ParagraphStyle("H2", parent=styles["Heading2"], fontName="Helvetica-Bold",
                    fontSize=13, leading=16, textColor=NAVY, spaceBefore=12,
                    spaceAfter=5)
BODY = ParagraphStyle("BODY", parent=styles["Normal"], fontSize=9.5, leading=13.5,
                      textColor=colors.HexColor("#17212f"), spaceAfter=5)


def rule() -> HRFlowable:
    return HRFlowable(width="100%", thickness=0.6, color=LINE,
                      spaceBefore=4, spaceAfter=7)


def scene(number: str, title: str, timing: str, beats: list[tuple[str, str]]) -> list:
    """One scene: a heading, its timing, then alternating DO / SAY beats."""
    block = [Paragraph(f"{number}. {title}", SCENE), Paragraph(timing, TIME)]
    for kind, text in beats:
        if kind == "do":
            block.append(Paragraph(
                f"<b><font color='#0f2540'>ON SCREEN</font></b> &nbsp;{text}", DO))
        elif kind == "say":
            block.append(Paragraph(f"“{text}”", SAY))
        else:
            block.append(Paragraph(text, NOTE))
    return block


def callout(title: str, body: str, tone: str = "amber") -> Table:
    bg = AMBER_BG if tone == "amber" else PANEL
    fg = AMBER if tone == "amber" else SLATE
    para = Paragraph(f"<b><font color='{fg.hexval()}'>{title}</font></b> &nbsp;{body}",
                     ParagraphStyle("c", parent=BODY, fontSize=9, leading=12.5))
    table = Table([[para]], colWidths=[170 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


def data_table(rows: list[list[str]], widths: list[float], header: bool = True) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#17212f")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
    ]
    if header:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), PANEL),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("TEXTCOLOR", (0, 0), (-1, 0), SLATE),
        ]
    table.setStyle(TableStyle(style))
    return table


def build() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUT), pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title="ChainLens - Demonstration Video Script",
        author="ChainLens",
    )
    s: list = []

    # ----------------------------------------------------------------- cover
    s.append(Paragraph("ChainLens", H1))
    s.append(Paragraph(
        "Demonstration video script &nbsp;·&nbsp; approximately 5 minutes &nbsp;·&nbsp; "
        "SIH26146 Bitcoin Investigation Platform", SUB))
    s.append(rule())

    s.append(Paragraph(
        "Read the quoted lines aloud. The italic lines are what to do on screen, not "
        "things to say. Every number in this script was measured from a real run of "
        "<font face='Courier'>demo_mixed_patterns.csv</font> — check them against your "
        "own run before recording, because they change with the file you use.", BODY))

    s.append(Spacer(1, 4))
    s.append(callout(
        "The one thing to land.",
        "Not “we detect ransomware”. It is: <b>every finding can be traced to the row "
        "that produced it, and the system states plainly what it cannot prove.</b> "
        "Anyone can output a list of suspicious addresses. Almost nobody shows their "
        "working, offers the innocent explanation alongside the suspicious one, and "
        "refuses to promote a lead they cannot support."))

    s.append(Spacer(1, 8))
    s.append(Paragraph("Before you record", H2))
    s.append(data_table([
        ["Check", "Why it matters"],
        ["Fresh case, empty database",
         "A clean start makes the import counts on screen match this script."],
        ["Have demo_mixed_patterns.csv ready",
         "191 rows, imports instantly, and the addresses have readable names "
         "(COLLECTOR, EXCHANGE, VICTIM0) so viewers can follow what is happening."],
        ["Browser zoom around 110%",
         "Evidence-card text must be readable when the video is scaled down."],
        ["Close other tabs and notifications",
         "A pop-up mid-recording costs you a retake."],
        ["Do a silent dry run first",
         "Know where every click is before you start narrating."],
    ], [46 * mm, 124 * mm]))

    s.append(Spacer(1, 6))
    s.append(callout(
        "Which dataset?",
        "Use the small demo file for the walkthrough — readable names, instant import. "
        "Mention the 10,000-row result verbally (Scene 8) rather than importing it on "
        "camera: it adds a wait and the addresses are unreadable hashes.", tone="grey"))

    s.append(PageBreak())

    # ------------------------------------------------------------- the script
    s.append(Paragraph("The script", H1))
    s.append(rule())

    s.extend(scene("1", "What this is", "0:00 – 0:25", [
        ("do", "Application open on the case screen. No narration for the first second."),
        ("say", "A Bitcoin investigator gets a spreadsheet of transactions and has to "
                "decide which addresses are worth a closer look. The hard part is not "
                "spotting something unusual. It is explaining, later, why you looked at "
                "it at all."),
        ("say", "This is ChainLens. It runs entirely offline, it does not call a single "
                "external service, and every finding it produces is traceable back to "
                "the row it came from."),
    ]))

    s.extend(scene("2", "Import, and the answer key", "0:25 – 1:15", [
        ("do", "Drag demo_mixed_patterns.csv onto the drop zone."),
        ("say", "I am importing a transaction file. Notice the format is detected from "
                "the contents — the supplied dataset has no file extension at all."),
        ("do", "Click Import and validate. Let the result panel appear."),
        ("say", "191 rows in. 189 accepted clean, 2 with warnings, none quarantined — "
                "and those three numbers add back to 191. Every row is accounted for. "
                "Nothing is silently dropped."),
        ("do", "Point at the amber notice about the removed label column."),
        ("say", "This matters. The supplied file ships with a column called "
                "is_planted_suspicious — the answer key. It was removed at the ingestion "
                "boundary, before any analysable record existed. It survives only in the "
                "archive of the original upload. No detector and no model can reach it."),
        ("say", "We test that three ways: permuting that column, deleting it, and "
                "inverting it all produce byte-identical predictions. The system earns "
                "its findings."),
        ("do", "Point at the two fee-residual warnings."),
        ("say", "Two rows have amounts that do not reconcile with their stated fee. "
                "That is a data-quality finding, not a behavioural one. A fee residual "
                "never makes an address suspicious."),
    ]))

    s.extend(scene("3", "Run the analysis", "1:15 – 1:40", [
        ("do", "Click Run analysis. Let the stages advance."),
        ("say", "Structural detection runs first, because its output — which "
                "transactions are collaborative — is what the ownership heuristics have "
                "to exclude. Then features, the model, the behavioural rules, and "
                "scoring."),
        ("do", "Results appear: 30 alerts, with the per-detector breakdown."),
        ("say", "30 alerts from 191 transactions."),
        ("note", "Say your own observed timing here if you want to mention speed. On the "
                 "10,000-row dataset this stage takes about 3 seconds."),
    ]))

    s.extend(scene("4", "An evidence card — the four questions", "1:40 – 2:50", [
        ("do", "Open the alert inbox. Open the COLLECTOR alert "
               "(collection pattern, priority 78.7, evidence HIGH)."),
        ("say", "Every alert answers four questions, in order."),
        ("say", "What happened: twelve distinct payer addresses paid this one address "
                "inside a 72-hour window, and it then moved the funds on to just two "
                "destinations."),
        ("say", "Why it was flagged: two bars — the rule severity and the model's "
                "anomaly percentile — with the formula written underneath. The severity "
                "is graded, and the breakdown shows exactly how it was reached."),
        ("do", "Expand “How the severity was reached”."),
        ("say", "Sixty points for the payer count, plus ten because the money moved on "
                "to only two places. Nothing here is a magic number."),
        ("do", "Scroll to the supporting records. Click Source on any row."),
        ("say", "Which records support it: every contributing transaction. And from any "
                "one of them I am two clicks from the original row in the uploaded file."),
        ("do", "In the dialog, point at the amber-highlighted label column."),
        ("say", "There is the answer column, highlighted. The analyst can see it. The "
                "detectors never could."),
        ("do", "Close the dialog. Scroll to “What remains uncertain”."),
        ("say", "And the fourth question, which is the one most tools skip. A merchant, "
                "a payment processor and a donation address all produce this exact "
                "shape. The card says so itself, before anyone asks."),
    ]))

    s.extend(scene("5", "The graph, and what it refuses to draw", "2:50 – 3:30", [
        ("do", "Open the top peeling alert (PEEL4, priority 89.2). Click Open in graph."),
        ("say", "This is a candidate peeling sequence — each transaction carries most "
                "of its value forward while a small amount branches away."),
        ("say", "Addresses are circles, transactions are rectangles, endpoints are "
                "diamonds. Shape, not just colour. Solid edges are relationships the "
                "data supplied. Dashed edges are things we inferred."),
        ("do", "Point at the transaction node sitting between two addresses."),
        ("say", "There is no line drawn directly from one address to another anywhere "
                "in this graph, because the data cannot support one. The transaction "
                "node always stays in between. And the continuity is dashed, because "
                "this file has no previous-output references — so this is a candidate "
                "sequence, not proven movement of coins."),
        ("do", "Point at the node budget control."),
        ("say", "The full graph is never drawn. This is a bounded neighbourhood, and "
                "expanding it is a deliberate act."),
    ]))

    s.extend(scene("6", "The safeguards — the part that matters",
                   "3:30 – 4:20", [
        ("note", "This is the strongest section of the demonstration. Do not rush it."),
        ("do", "Back to the inbox. Filter the pattern to High activity requiring context."),
        ("say", "This address has by far the most activity in the dataset. It is "
                "medium priority, and it is described as high activity requiring "
                "context — not as a suspect."),
        ("say", "It looks exactly like a collection point: many payers converging on "
                "it. But value also flows back out to many distinct receivers. Money "
                "from many going to many is a service. Money from many going to few is "
                "a collection. So the collection finding was reduced before it was ever "
                "written, and the card explains why."),
        ("do", "Filter to Shared network observation."),
        ("say", "Several addresses were repeatedly seen at the same IP. The system "
                "shows the association and then states plainly that these addresses "
                "have not been grouped, and no priority has been transferred between "
                "them. A shared IP can never create an ownership link — a NAT gateway "
                "or a relaying node puts unrelated people behind one address."),
        ("do", "Optional: search PAYROLL in the inbox filter — no alert is returned."),
        ("say", "And here is a transaction with many identical outputs that is not "
                "flagged as a mixing structure, because it has a single payer. It is a "
                "payroll batch. The shape alone was not enough."),
    ]))

    s.extend(scene("7", "Test the ranking, then export", "4:20 – 4:50", [
        ("do", "Open any alert. Expand Evidence sensitivity. "
               "Click “Without the model score”."),
        ("say", "I can remove a component and see what the ranking would have been "
                "without it. And notice what it says: the ranking was recalculated, "
                "the model was not rescored, and the stored alert has not changed. It "
                "would be easy to call this retraining. It isn't, so we don't."),
        ("do", "Mark the alert Relevant with a reason. Add a note. Click Export."),
        ("say", "The export carries a PDF summary, the evidence as CSV, and a manifest "
                "with the source file's SHA-256, the detector version, the model "
                "version and the configuration — enough for someone else to reproduce "
                "this exact run."),
        ("say", "It lets a recipient detect that something changed. It is not a legal "
                "certification, and the package says so in its own limitations file."),
    ]))

    s.extend(scene("8", "Measured results, and the close", "4:50 – 5:20", [
        ("do", "Optional: cut to the terminal showing the evaluator output."),
        ("say", "Evaluation runs in a separate command-line tool — the only component "
                "allowed to open the answer key, and it reads predictions that were "
                "frozen before any label was available."),
        ("say", "On the full 10,000-row dataset: recall 1.00, precision 0.71, "
                "false-positive rate 0.041. Every planted pattern fully recovered. The "
                "legitimate exchange: not flagged. The six innocent shared-IP "
                "addresses: not flagged."),
        ("say", "Precision is below our own 0.85 target. We could have moved thresholds "
                "until it cleared — but we would have been tuning against the answer "
                "key, and the number would have meant nothing. So it stands as measured."),
        ("say", "Anyone can produce a list of suspicious addresses. This one tells you "
                "why, shows you the row it came from, offers the innocent explanation "
                "alongside the suspicious one, and refuses to promote a lead it cannot "
                "support."),
    ]))

    s.append(PageBreak())

    # ---------------------------------------------------------- verified numbers
    s.append(Paragraph("Numbers quoted in this script", H1))
    s.append(Paragraph(
        "Measured from an actual run of demo_mixed_patterns.csv. Re-run it and confirm "
        "before recording.", SUB))
    s.append(rule())

    s.append(Paragraph("Import", H2))
    s.append(data_table([
        ["Figure", "Value"],
        ["Transactions in file", "191"],
        ["Accepted clean / warning / quarantined", "189 / 2 / 0  (reconciles to 191)"],
        ["Label column removed", "is_planted_suspicious"],
        ["Fee-residual findings", "1 within tolerance, 1 above"],
    ], [80 * mm, 90 * mm]))

    s.append(Paragraph("Alerts produced", H2))
    s.append(data_table([
        ["Subject", "Priority", "Band", "Evidence", "Pattern"],
        ["PEEL4", "89.19", "high", "medium", "Peeling sequence (top alert)"],
        ["COLLECTOR", "78.74", "high", "high", "Collection pattern"],
        ["WALLET_A", "75.23", "high", "low", "Candidate common control"],
        ["EXCHANGE", "59.23", "medium", "low", "High activity requiring context"],
        ["NAT0", "58.87", "medium", "low", "Shared network observation"],
        ["VICTIM0", "57.06", "medium", "low", "Potential payment participant"],
        ["PAYROLL", "—", "—", "—", "No alert (benign equal-value batch)"],
    ], [26 * mm, 20 * mm, 18 * mm, 20 * mm, 86 * mm]))

    s.append(Spacer(1, 5))
    s.append(data_table([
        ["Totals", "Value"],
        ["Alerts", "30  (13 high, 15 medium, 2 low)"],
        ["By detector",
         "peeling 10 · participants 12 · common control 2 · shared IP 2 · "
         "CoinJoin 2 · collection 1 · high activity 1"],
    ], [34 * mm, 136 * mm]))

    s.append(Paragraph("From the 10,000-row dataset (quote verbally, Scene 8)", H2))
    s.append(data_table([
        ["Metric", "Value", "Denominator"],
        ["Recall", "1.0000", "290 labelled positive"],
        ["Precision", "0.7073", "410 predicted positive"],
        ["False-positive rate", "0.0413", "2,907 labelled negative"],
        ["Analysis time", "about 3.1 s", "10,000 rows, 3,197 addresses"],
        ["Test suite", "98 passing", "including label-permutation invariance"],
    ], [44 * mm, 36 * mm, 90 * mm]))

    s.append(Spacer(1, 8))
    s.append(callout(
        "Do not overstate.",
        "These results are <b>exploratory</b> — the model was fitted and scored on the "
        "same population. If a judge asks about generalisation, say exactly that. It is "
        "written on the model's own manifest, and saying it first is far stronger than "
        "being caught on it."))

    s.append(Spacer(1, 7))
    s.append(Paragraph("If something goes wrong on camera", H2))
    s.append(data_table([
        ["Symptom", "What to say and do"],
        ["Page will not load",
         "The backend is not running. Start it and re-record — do not narrate over a "
         "broken screen."],
        ["Analysis fails",
         "Show it. A failed run is displayed as a failure, with no previous run "
         "substituted in its place. That behaviour is deliberate and worth pointing out."],
        ["Numbers differ from this script",
         "Read what is actually on your screen. Never read a number that is not there."],
        ["Deployed instance is slow to wake",
         "The free tier sleeps when idle. Record against the local instance instead."],
    ], [44 * mm, 126 * mm]))

    s.append(Spacer(1, 8))
    s.append(rule())
    s.append(Paragraph(
        "Generated by scripts/make_demo_script_pdf.py · figures measured from "
        "data/samples/demo_mixed_patterns.csv", NOTE))

    doc.build(s)
    size = OUT.stat().st_size
    print(f"wrote {OUT.relative_to(ROOT)}  ({size / 1024:.0f} KB)")


if __name__ == "__main__":
    build()
