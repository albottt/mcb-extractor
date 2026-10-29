from __future__ import annotations

import base64
import io
import re
from collections import Counter
from pathlib import Path

import fitz
import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from extractor import extract_db_name, extract_page_ratings, KNOWN_RATINGS, ELCB_RATINGS, ISOLATOR_RATINGS

app = FastAPI(title="MCB Schedule Extractor API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://mcb-extractor-frontend.onrender.com",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "mcb-schedule-extractor"}


@app.post("/api/extract")
async def extract(
    file: UploadFile = File(...),
    pages: int | None = Form(default=None),
    start_page: int = Form(default=1),
):
    """Process all pages (or up to `pages`) starting at `start_page`.
    Frontend currently sends `pages=386`, matching the original UI. If the PDF
    has fewer pages, all available pages in the requested range are processed.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")
    if start_page < 1:
        raise HTTPException(status_code=400, detail="start_page must be 1 or greater.")
    if pages is not None and pages < 1:
        raise HTTPException(status_code=400, detail="pages must be 1 or greater.")

    pdf_bytes = await file.read(MAX_UPLOAD_BYTES + 1)
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="The uploaded PDF is empty.")
    if len(pdf_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="PDF is too large. Maximum upload size is 100 MB.")

    try:
        payload = extract_pdf_to_result(pdf_bytes, page_limit=pages, start_page=start_page)
        return JSONResponse(payload)
    except (fitz.FileDataError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=f"Could not read/process this PDF: {exc}") from exc
    except Exception as exc:
        # Keep details in server logs; send a concise error to the browser.
        import logging
        logging.exception("MCB extraction failed")
        raise HTTPException(status_code=500, detail=f"Extraction failed: {exc}") from exc


def unique_db_headers(page_db_names: dict[int, str]) -> dict[int, str]:
    """Ensure duplicate DB reference numbers become unique Excel columns."""
    seen: Counter[str] = Counter()
    result: dict[int, str] = {}
    for page_number in sorted(page_db_names):
        name = page_db_names[page_number]
        seen[name] += 1
        result[page_number] = name if seen[name] == 1 else f"{name} (PDF p.{page_number})"
    return result


def make_excel(counts_df: pd.DataFrame, review_df: pd.DataFrame) -> str:
    """Return the Counts and Review workbook encoded as base64 for the UI."""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        counts_df.to_excel(writer, sheet_name="Counts")
        review_df.to_excel(writer, sheet_name="Review", index=False)

        ws = writer.sheets["Counts"]
        ws.freeze_panes = "B2"
        ws.column_dimensions["A"].width = 18
        for col in range(2, ws.max_column + 1):
            ws.column_dimensions[ws.cell(1, col).column_letter].width = 20

        ws_review = writer.sheets["Review"]
        ws_review.freeze_panes = "A2"
        for col, width in {1: 12, 2: 28, 3: 22, 4: 70, 5: 24, 6: 55}.items():
            ws_review.column_dimensions[ws_review.cell(1, col).column_letter].width = width

    return base64.b64encode(buffer.getvalue()).decode("ascii")



def extract_pdf_to_result(
    pdf_bytes: bytes,
    page_limit: int | None = None,
    start_page: int = 1,
) -> dict:
    """Extract MCB and ELCB ratings and combine them in one DB matrix."""

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError("The uploaded file is not a readable PDF.") from exc

    board_types = ["MCB", "ELCB", "ISOLATOR"]

    with doc:
        total_pdf_pages = len(doc)

        if total_pdf_pages == 0:
            raise ValueError("The uploaded PDF contains no pages.")

        if start_page > total_pdf_pages:
            raise ValueError(
                f"start_page ({start_page}) exceeds the PDF's "
                f"{total_pdf_pages} pages."
            )

        end_page = (
            total_pdf_pages
            if page_limit is None
            else min(total_pdf_pages, start_page + page_limit - 1)
        )

        # Store counts separately for every page and rating type.
        page_counts: dict[tuple[int, str], Counter] = {}
        page_db_names: dict[int, str] = {}
        reviews: list[dict] = []

        # Keep all detected ratings for each type.
        rating_totals = {
            board_type: Counter()
            for board_type in board_types
        }

        for page_number in range(start_page, end_page + 1):
            page = doc[page_number - 1]

            # The DB reference is shared by both MCB and ELCB results.
            db_name = extract_db_name(page, page_number)
            page_db_names[page_number] = db_name

            for board_type in board_types:
                counts, review = extract_page_ratings(
                    page,
                    page_number,
                    board_type,
                )

                page_counts[(page_number, board_type)] = counts
                rating_totals[board_type].update(counts)

                # Record the type so MCB and ELCB reviews are distinguishable.
                review["DB name"] = db_name
                review["Detected object type"] = board_type

                if db_name.startswith("Page "):
                    existing = review.get("Review note", "")
                    warning = "DB reference not recognized by OCR"
                    review["Review note"] = (
                        f"{existing}; {warning}"
                        if existing
                        else warning
                    )

                if page_number == 1 and not review.get("Review note"):
                    review["Review note"] = (
                        "Cover page; verify whether to exclude"
                    )

                if review.get("Review note"):
                    reviews.append(review)

    # Ensure DB reference names are unique across Excel columns.
    db_headers = unique_db_headers(page_db_names)

    # Build a stable row list: MCB first, ELCB second.
    # Include configured ratings even when their count is zero.
    rating_rows: list[tuple[str, str, str]] = []

    for rating in sorted(set(KNOWN_RATINGS), key=int):
        rating_rows.append((f"{rating} A MCB", "MCB", rating))

    for rating in sorted(set(ELCB_RATINGS), key=int):
        rating_rows.append((f"{rating} mA ELCB", "ELCB", rating))

    for rating in sorted(set(ISOLATOR_RATINGS), key=int):
            rating_rows.append((f"{rating} A ISOLATOR", "ISOLATOR", rating))

    # Also include any valid rating detected by OCR that was not
    # present in the configured rating lists.
    existing_ratings = {
        (board_type, rating)
        for _, board_type, rating in rating_rows
    }

    for board_type in board_types:
        for rating in rating_totals[board_type]:
            if (board_type, rating) not in existing_ratings:
                unit = "A" if board_type == "MCB" else "mA"
                rating_rows.append(
                    (f"{rating} {unit} {board_type}", board_type, rating)
                )
                existing_ratings.add((board_type, rating))

    # Build the matrix with DB reference numbers as columns.
    matrix = {}

    for page_number in sorted(page_counts_by_page := {
        page_num
        for page_num, _ in page_counts
    }):
        db_header = db_headers[page_number]
        column_values = []

        for _, board_type, rating in rating_rows:
            counts = page_counts.get(
                (page_number, board_type),
                Counter(),
            )
            column_values.append(counts.get(rating, 0))

        matrix[db_header] = column_values

    rating_labels = [row_label for row_label, _, _ in rating_rows]
    row_header = "MCB / ELCB Rating"

    counts_df = pd.DataFrame(matrix, index=rating_labels)
    counts_df.index.name = row_header

    review_columns = [
        "PDF page",
        "DB name",
        "Detected object type",
        "Detected MCB entries",
        "Detected ratings",
        "Average OCR confidence",
        "Review note",
    ]

    review_df = pd.DataFrame(reviews, columns=review_columns)

    # Match the response format expected by the React frontend.
    counts_rows = [
        {
            row_header: row_label,
            **{
                header: int(counts_df.loc[row_label, header])
                for header in counts_df.columns
            },
        }
        for row_label in rating_labels
    ]

    return {
        "row_header": row_header,
        "columns": [row_header, *list(counts_df.columns)],
        "rows": counts_rows,
        "review_columns": review_columns,
        "review": review_df.fillna("").to_dict(orient="records"),
        "xlsx_base64": make_excel(counts_df, review_df),
        "meta": {
            "total_pdf_pages": total_pdf_pages,
            "processed_pages": end_page - start_page + 1,
            "start_page": start_page,
            "end_page": end_page,
            "mcb_ratings_found": len(rating_totals["MCB"]),
            "elcb_ratings_found": len(rating_totals["ELCB"]),
            "db_columns": len(counts_df.columns),
        },
    }
