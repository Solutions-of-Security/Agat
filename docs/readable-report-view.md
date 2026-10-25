# Readable report view: USE-004 increment

Implemented 25 October 2026. Run result offers **Исходный текст** and
**Форматированный** views. The original view remains the default; switching
preserves the stored response and existing download bytes. The formatted view
renders headings, lists, code, quotations and GFM tables. Tables scroll within
their own keyboard-accessible region on narrow screens.

Known citations open exactly the current run's source document/chunk/hash.
The plain and formatted views share one citation resolver, preserving grouped
`[K1, K2/K3]` punctuation and leaving unknown, ambiguous or malformed references
inert. Code snippets retain literal markers. Model-supplied links become active
only when their destination exactly matches an unambiguous source reference from
this run; remote or invented destinations remain text. Raw HTML is ignored and
images are represented by their labels, without making image requests.

The Markdown renderer is loaded only when the user chooses the formatted view.
The production build creates a separate renderer chunk, keeping its parser out
of the initial app path. A load/render failure retains the full source result and
download instead of replacing the page with a blank state. Results above 200,000
UTF-8 bytes explicitly fall back to the complete original text; nothing is
silently truncated. Formatting itself does not claim validated facts or human
acceptance. Completion copy now asks the user to check the result and sources.

Verification includes 66 web tests, existing Temporal/process schedule tests,
workspace typechecks and production builds. Browser checks use the real app at
`http://127.0.0.1:8896` in desktop Chromium (1440×1000) and mobile Chromium
emulation (Pixel 7). The UI fixture projects a known response/source snapshot
over a real ingested CSV document; it does not claim actual model inference.
Checks cover page identity, meaningful content, table rendering and keyboard
scroll, no framework overlay, exact source navigation, unchanged original/download,
no external image requests, and recovery when the deferred renderer is deliberately
unavailable. Normal flows produce no app console errors; the failure test
intentionally induces a failed module request and verifies its contained fallback.
The Browser plugin was unavailable, so the repository Playwright workflow was used.
The cold-load failure case blocks service workers only in that test so a cache
cannot mask the injected failed module request, following
[Playwright's network interception guidance](https://playwright.dev/docs/network).

Adding Markdown parser dependencies makes Temporal's existing `ms` Duration types
available to TypeScript. The schedule spec now has the SDK's explicit `ScheduleSpec`
context, and its catch-up duration retains a literal type. This is a compile-time
compatibility correction: duration values, schedule commands and workflow code
are unchanged. Existing calendar/hardening tests and the shared build validate it.

Design sources reviewed 25 October 2026:
[react-markdown security and HTML handling](https://github.com/remarkjs/react-markdown),
[remark-gfm tables](https://github.com/remarkjs/remark-gfm).
The implementation does not enable raw HTML plugins or weaken the default URL
transform; scoped citation checks add another restriction on active links.

[DOCX/PDF export](./business-report-office-export.md) now uses the saved canonical
Markdown and appears directly in the run result, including runs saved to history.
Remaining USE-004 scope is a persisted accept/rework decision tied to exact
artifact version and run. Real
model/business acceptance, physical mobile devices and screen-reader usability
remain separate qualification work.
