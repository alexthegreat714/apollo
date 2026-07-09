# OCR97 Nightly Test Plan — Document Expansion

## Why this exists

The May 2025 benchmarks ran 12 times against **the same 3 documents**. Score locked at 82.9 / recommended 86 for 10 consecutive identical runs. This tells us how OCR97 performs on three specific PDFs — a Federal Reserve FSR, a BIS Quarterly, and an IRS W-4 — and nothing else.

The BIS Quarterly scored 72.07 because GB10 was unavailable and rapidocr (token_score 50) won the routing. That's a hardware availability failure masquerading as a capability ceiling. The score also can't be trusted to generalize because the sample is too small and too narrow (all financial-text PDFs, no forms beyond W-4, no instruction booklets, no chart-heavy annual reports).

This plan expands to 16 documents across 4 night-rotation manifests. The rotation cycles daily based on `day_of_year % 4`.

---

## Night 1 — IRS Forms Gauntlet
**Manifest:** `config/ocr97_night_manifests/night_1_irs_forms_gauntlet.json`
**Target routing:** `forms_or_checkboxes` → `gb10_qwen_ocr`, `rapidocr`, `tesseract`

| Document | Challenge |
|---|---|
| IRS Form 1040 (2024) | Numbered lines, filing status checkboxes, dollar fields, multi-section |
| IRS Schedule D | Multi-column table, short/long-term sections, date + amount columns |
| IRS Form W-9 | Entity-type checkboxes, TIN field, certification block |
| IRS Form 1099-B | Dense multi-box financial form, FATCA checkbox, small-print |
| IRS Form 8949 | Tabular disposition rows, Part I / Part II, adjustment columns |

**What this tests:** Whether the forms routing consistently lands on qwen3-VL when GB10 is up. The W-4 (original) missed "Employee's Withholding Certificate" and "Internal Revenue Service" when tesseract won. These 5 docs are progressively harder. 8949 and Schedule D require reading dense column headers correctly.

**Expected score range:** 80–92 depending on GB10 availability. If GB10 is down, expect 70–78 (rapidocr fallback). If GB10 is up and routing is correct, should push toward 90.

---

## Night 2 — Charts & Annual Reports
**Manifest:** `config/ocr97_night_manifests/night_2_charts_annual_reports.json`
**Target routing:** `chart_or_figure` → `gb10_deplot_chart`, `gb10_qwen_ocr`, `gb10_paddleocr_vl`, `rapidocr`, `tesseract`

| Document | Challenge |
|---|---|
| BIS Annual Economic Report 2024 | Very chart-dense, chapter headings, mixed text/figure |
| Fed FSR May 2024 | Charts + risk language; different from existing Nov 2024 baseline |
| BIS Quarterly March 2025 | Chart-figure mix (same doc class as the 72-scoring Q4 2024) |
| Fed Monetary Policy Report Feb 2025 | FOMC policy text + dense economic charts |

**What this tests:** Whether `gb10_deplot_chart` or `gb10_qwen_ocr` fires on chart-heavy PDFs. The BIS Q4 2024 collapsed to rapidocr in May. BIS Q1 2025 is structurally identical — if it still scores below 80 here, the routing gap is in document classification (not being detected as `chart_or_figure`), not in GB10 availability.

**Expected score range:** 82–92 with GB10 up. The deplot and qwen3-VL combination should handle chart captions well. The failure mode is the same as Q4 2024: if qwen3 is evicted from VRAM between pages, the per-page fallback chain degrades.

---

## Night 3 — Dense Text & Multi-Column
**Manifest:** `config/ocr97_night_manifests/night_3_dense_text_multicolumn.json`
**Target routing:** `digital_pdf` or `table_dense` → `native_pdf_text`, `gb10_paddleocr_vl`, `mineru2_5`, `gb10_qwen_ocr`, `rapidocr`, `tesseract`

| Document | Challenge |
|---|---|
| Fed Beige Book Nov 2024 | 12-district narrative, dense sectioned text, ~100 pages |
| Fed Beige Book Apr 2025 | Same structure, different content — consistency check |
| IRS Publication 550 | 100+ page investment guide, tables + examples, cross-references |
| IRS Form 1040 Instructions | Two-column layout, tax bracket tables, line-by-line instruction blocks |

**What this tests:** Surya column detection on the 1040 instructions (genuine two-column PDF). Native PDF text extraction vs MinerU on the Beige Book (which should be digitally generated, not scanned). IRS Pub 550 stress-tests very long documents where the 5-page cap means we only see the intro — good for checking whether the opening pages of a dense guide extract cleanly.

**Expected score range:** 82–90. The Beige Book is digital text and should score well. The 1040 instructions column detection is the wildcard — if Surya column split fires correctly, score improves; if it splits at the wrong boundary, text order scrambles.

---

## Night 4 — Stress / Regression
**Manifest:** `config/ocr97_night_manifests/night_4_stress_mixed.json`

| Document | Purpose |
|---|---|
| USCIS Form I-9 | Multi-page government form, legal attestation, List A/B/C checkboxes |
| BIS Quarterly Dec 2024 | **Regression check** — re-run the 72-scorer; expect >85 with GB10 up |
| IRS Schedule B | Narrow dividend/interest table, Part III foreign account questions |
| Fed FSR Nov 2024 | **Baseline rerun** — expect ~94; confirms no regression in the champion doc |

**What this tests:** The BIS Q4 2024 re-run is the most important signal. If it scores >85 with GB10 available, the May 2025 failure was definitively a hardware availability issue. If it still scores <80, there's a routing or quality bug in how chart-heavy BIS documents are classified.

**Expected score range:** 85–93. The baseline FSR should hold near 94. BIS Q4 rerun is the swing factor.

---

---

## Night 5 — Two-Column Academic Papers
**Manifest:** `config/ocr97_night_manifests/night_5_academic_twocolumn.json`
**Target routing:** Surya column split → `digital_pdf` → `native_pdf_text`, `gb10_paddleocr_vl`, `mineru2_5`

| Document | Challenge |
|---|---|
| arXiv 1706.03762 (Transformer) | Two-column, attention diagrams, BLEU score tables |
| arXiv 1810.04805 (BERT) | Two-column, GLUE benchmark result tables |
| arXiv 2005.14165 (GPT-3) | 70+ pages two-column, scaling charts, few-shot tables |
| NIST FIPS 197 (AES) | Algorithm spec, hex value tables, byte array diagrams |

**Expected score range:** 75–90. The arXiv papers are the first true test of Surya column detection on non-financial content. If column split fires correctly, score improves. The FIPS 197 hex tables are the hardest — values like `{63, 7c, 77, 7b}` must be read correctly.

---

## Night 6 — NIST Technical Standards
**Manifest:** `config/ocr97_night_manifests/night_6_nist_tech_standards.json`

| Document | Challenge |
|---|---|
| NIST SP 800-63B | Digital identity, authenticator assurance level tables |
| NIST SP 800-171r2 | Numbered requirements 3.X.X, CUI control tables |
| NIST SP 800-53r5 | 600-page catalog, dense control family text |
| NIST CSWP 29 (CSF 2.0) | Hierarchical Function→Category→Subcategory tables |

**Expected score range:** 80–92. These are digital-native PDFs, so `native_pdf_text` should fire and score well. The scoring challenge is term accuracy: NIST documents have precise identifiers (3.1.1, AC-2, IA-5) that must survive extraction exactly.

---

## Night 7 — Long IRS Tax Publications
**Manifest:** `config/ocr97_night_manifests/night_7_irs_long_publications.json`

| Document | Challenge |
|---|---|
| IRS Pub 17 (300+ pages) | Opening chapters, two-column, examples + tax tables |
| IRS Pub 525 | Income classification, definition-heavy two-column |
| IRS Pub 590-A | IRA contribution limits, phase-out tables, worksheets |
| IRS Pub 590-B | RMD life expectancy tables, large numeric grids |

**Expected score range:** 82–92. The life expectancy tables in Pub 590-B are the specific challenge — a multi-page numeric grid where column alignment determines correct RMD calculation. If OCR scrambles columns, the extracted numbers are meaningless.

---

## Night 8 — Statistical Data Releases
**Manifest:** `config/ocr97_night_manifests/night_8_statistical_data_releases.json`

| Document | Challenge |
|---|---|
| BLS Employment Situation (Apr 2025) | Nonfarm payroll tables, demographic unemployment breakdown |
| BLS CPI (Apr 2025) | Index level + percent change side-by-side columns |
| BLS JOLTS (Apr 2025) | Multi-series industry table (openings, hires, quits, layoffs) |
| Fed H.6 Money Stock | M1/M2 monetary aggregate table, footnote revision markers |

**Expected score range:** 70–88. This is the hardest column-alignment test in the corpus. BLS releases pack 6–8 numeric columns into tables that are ~90 characters wide. A one-column shift makes every number wrong. If `native_pdf_text` fires correctly, these should score well. If the PDF is rendered as image (some releases are), the fallback to tesseract will struggle.

---

## Night 9 — International Finance / Chart-Dense
**Manifest:** `config/ocr97_night_manifests/night_9_international_chartdense.json`

| Document | Challenge |
|---|---|
| IMF GFSR Oct 2024 | Multi-panel charts, heatmaps, sovereign spread plots |
| IMF WEO Apr 2025 | 2–4 charts per page, GDP bars, fan charts, projection tables |
| BIS Working Paper 1197 | Two-column academic, IRF plots, econometric tables |
| OECD Economic Outlook | Country comparison charts, dot plots, forecast fan charts |

**Expected score range:** 72–88. The IMF WEO is the supreme chart density test — pages routinely have a 4-panel figure grid with country labels inside chart areas. `gb10_deplot_chart` was designed for this but DePlot's accuracy on multi-panel economic charts is unknown. This run will reveal whether DePlot adds value or qwen3-VL on the full page does better.

---

## Night 10 — Legal & Regulatory Text
**Manifest:** `config/ocr97_night_manifests/night_10_legal_regulatory.json`

| Document | Challenge |
|---|---|
| Federal Rules of Civil Procedure | Rule hierarchy, cross-references, dense prose |
| Federal Rules of Evidence | Article/Rule/Subsection hierarchy, exception lists |
| SEC Regulation S-K (17 CFR 229) | Item numbering, form cross-references, legal definitions |
| CFTC Part 4 Regulations | CTA/CPO requirements, section numbering, trading law |

**Expected score range:** 82–93. Legal documents are digital-native text PDFs with no charts. `native_pdf_text` should dominate. The challenge is: legal text has very specific term requirements ("summons", "pleading", "registrant") that must appear verbatim. Score will be high if extraction is clean and low if the PDF has any unusual encoding.

---

## Rotation Schedule

Selection is `day_of_year % 10 → night 1–10`. Fallback is the original 3-doc baseline if selector or manifest is missing.

| slot | Night | Key test |
|---|---|---|
| 0 | Night 1 | IRS Forms Gauntlet |
| 1 | Night 2 | Charts & Annual Reports |
| 2 | Night 3 | Dense Text / Multi-column |
| 3 | Night 4 | Stress / Regression |
| 4 | Night 5 | Academic Two-Column (arXiv) |
| 5 | Night 6 | NIST Tech Standards |
| 6 | Night 7 | Long IRS Publications |
| 7 | Night 8 | Statistical Data Releases |
| 8 | Night 9 | International / Chart-Dense |
| 9 | Night 10 | Legal & Regulatory |

**Upcoming schedule from Jun 26:**

| Date | Slot | Night |
|---|---|---|
| Jun 26 | 7 | Night 8 — Statistical Data Releases |
| Jun 27 | 8 | Night 9 — International / Chart-Dense |
| Jun 28 | 9 | Night 10 — Legal & Regulatory |
| Jun 29 | 0 | Night 1 — IRS Forms Gauntlet |
| Jun 30 | 1 | Night 2 — Charts & Annual Reports |
| Jul 1  | 2 | Night 3 — Dense Text |
| Jul 2  | 3 | Night 4 — Stress / Regression |
| Jul 3  | 4 | Night 5 — Academic (arXiv) |
| Jul 4  | 5 | Night 6 — NIST Standards |
| Jul 5  | 6 | Night 7 — Long IRS Pubs |

---

## Grading Criteria

After running all 4 nights, grade OCR97 as follows:

| Scenario | Grade |
|---|---|
| Avg ≥ 90 across all 4 nights with GB10 up | Earns the "97" name conditionally — expand doc set before claiming |
| Avg 85–90, BIS Q4 rerun ≥ 85 | Grade = 87, routing gap is availability-only |
| Avg 85–90, BIS Q4 rerun < 80 | Grade = 83, routing bug in chart-heavy classification |
| Avg < 85 with GB10 up | Grade = 80, fundamental gap — investigate qwen3-VL quality on forms/tables |
| Avg < 85 with GB10 consistently down | Availability-gated, not capability-gated — fix GB10 uptime first |

---

## What Would Justify the Name "OCR97"

The name claims ≥ 97% accuracy. To legitimately hold that:

1. 20+ documents across all layout classes (forms, charts, tables, handwriting, scanned, two-column)
2. Average ≥ 95, minimum ≥ 90 on the expanded set
3. GB10 up for ≥ 90% of benchmark runs (reliability gate, not just capability)
4. At least one handwritten document tested and scoring ≥ 85 (TrOCR path)

None of those conditions are currently met. Current honest grade: **86** on the existing 3-doc set, **unknown** on anything else.
