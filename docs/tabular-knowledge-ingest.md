# CSV/XLSX knowledge ingestion: USE-003 increment

Implemented 25 October 2026. Knowledge upload accepts CSV (`text/csv`) and XLSX
(`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`) through the
existing file upload/preview/index/reindex path. Original bytes and SHA-256 are
retained; retrieval keeps the original file, document and chunk hashes. Existing
PDF, DOCX and direct text documents retain their behavior.

The file picker sends CSV/XLSX bytes to the server rather than parsing them in
the browser. Extraction runs in the existing bounded worker: 20 seconds, 256 MiB,
at most two concurrent parses, at most 5 MiB uploaded bytes. Failure preserves
the original and exposes the parse error with zero indexed chunks.

CSV accepts UTF-8 (including BOM), comma/semicolon/tab delimiters, quoted fields,
escaped quotes and embedded newlines. Delimiter detection uses the first nonempty
logical record outside quotes; ambiguous counts prefer comma. Column counts must
match except for entirely empty lines. Logical row numbers preserve empty rows
and the header. Values remain strings, including leading zeroes and decimal
commas. Bad encodings/quoting/widths fail explicitly.

XLSX reads the declared worksheets, shared/rich/inline strings, stored numeric
text, boolean and ISO date cell text. Workbook order, sheet names and hidden-sheet
state are shown. Formula cells are rejected even when a cached value exists:
the cache is the last calculated result, whose freshness this extractor cannot
establish. Macro books, error cells, missing/unsupported/external sheets, invalid
shared-string references, duplicate cell addresses and unsupported phonetic
annotations fail explicitly. XML DTDs are rejected. ZIP/XML parts have count and
expanded-size limits. No relationship, macro, formula or external URL is executed
or fetched.

Limits are 20 worksheets, 10,000 rows, 256 columns, 100,000 extracted cells,
32,000 characters per cell, 64,000 per CSV record, two million extracted text
characters, 2,000 ZIP parts, eight MiB per selected XML part and 32 MiB of selected
expanded XML. Oversized data is rejected rather than silently truncated.

Each nonempty value is emitted with an exact source label, for example
`"Revenue"!B7 [n] = "120.50"`. These labels stay in preview, indexed chunks and
retrieval snapshots; existing citations open the document/chunk containing them.
There is no invented PDF page for a spreadsheet and no new clickable spreadsheet
grid. Long values are emitted as numbered parts, each retaining its exact cell
address. Table chunking preserves complete labeled lines with bounded overlap and
exact character offsets; it never cuts a value away from its address. Numeric
dates and number formatting are deliberately not interpreted;
style indices and this limitation are visible. Charts, images, comments and
layout are outside this values-only extractor. This is not OCR or an Excel
calculation engine. Save a values-only copy before importing a formula workbook.

Verification covers parser failures/limits, exact addresses/values, sheet order,
original bytes, provenance, project isolation, duplicate names, embedding retry
and failed reindex. Browser checks exercise actual CSV/XLSX uploads, previews and
formula rejection at desktop and mobile widths, with no app console errors,
framework overlay or horizontal overflow. Browser plugin was not available;
the repository Playwright workflow was used. Its smoke worker uses no real model
or embeddings, so these checks do not qualify model quality or EVAL-101.

Design sources reviewed 25 October 2026:
[CSV Parse options](https://csv.js.org/parse/options/),
[UTF-8 BOM handling](https://csv.js.org/parse/options/bom/),
[Open XML cell values and formula caches](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.spreadsheet.cellvalue),
[Open XML cell locations/types](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.spreadsheet.cell).

Remaining USE-003 scope: common pack-source selection, extraction completeness
indicators (including mixed text/scan PDFs), and end-to-end user-document pack
qualification. Existing SHA-based records and duplicate-name rejection do not
claim global content deduplication across renamed uploads. Full USE-003 remains
open until those acceptance requirements are verified.
