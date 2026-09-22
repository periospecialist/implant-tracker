# Desktop interface — version 2.8.0

Compact rectangular controls, 12-point text, and light/dark appearance remain.
Manufacturer and Component Type use visible multi-select toggle buttons on one row.
Any clears that group; selections within each group are inclusive.
Implant Type and Export shown rows share a second row. Implant Type uses a
persistent checkbox popup that remains open during multi-selection and closes
when the user clicks elsewhere or presses Escape.

Inventory rows and headings use bold text. Straumann rows are blue and Nobel
rows purple. The pinned Reorder column uses solid red YES and green NO cells
with bold white labels and a contrasting dark header. Selection, sorting, and
vertical scrolling stay synchronized between the table and pinned column.
The manual Inventory filter bar and color guide remain removed.

End users install the ready-to-run desktop ZIP and update from **Setup → Application updates**. Developers can rebuild using `BUILD_WINDOWS.cmd`; updates replace the app folder while retaining the separate LocalAppData settings and queue.
No host or database changes are required.
