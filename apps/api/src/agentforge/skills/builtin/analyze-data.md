---
name: analyze-data
description: Analyze tabular data (CSV, JSON, exported spreadsheets) with code: clean it, compute statistics, find patterns and report findings. Use when the person attaches or pastes data and asks for analysis, summaries, trends or comparisons.
---
# Analyze data

## 1. Find the data
- Attachments appear in the workspace after the first workspace call, at the paths listed under "Attached files" in the environment. Pasted data should be written to a file first. For a small text file, read_task_input is enough.

## 2. Look before you compute
- Check the row count, column names and types, missing values, duplicates, ranges and odd values. Tell the person about data quality problems and how you handled them.
- Use Python with pandas when it is installed (install it with pip if not; pass the quiet and yes flags). Otherwise the standard library's csv, json and statistics modules are enough.

## 3. Answer the question that was asked
- Define each metric precisely, for example "revenue = sum of amount where status is paid".
- Compute with code, never in your head, and show the numbers.

## 4. Be careful
- Correlation is not causation. Watch for small samples, outliers, mixed units or currencies, and time zones. State your assumptions.

## 5. Present
- Lead with the two to four key findings, with numbers. Follow with a compact results table, then two or three lines on the method (what you filtered or cleaned) and the caveats.
- Keep the scripts you ran in the workspace so the work is reproducible. If the person wants an output file (a cleaned csv, a summary), save it with create_task_artifact as csv or markdown.
- Charts cannot be shown in chat, so give a table or describe the shape in words. Never claim a chart was shown.

## Rules
- Keep the data in the workspace. Do not send it to outside services.
