from collections import Counter
from pathlib import Path
import re
import time

import fitz
import cv2
import numpy as np
import pandas as pd
import pytesseract

PDF_PATH = "Load Schedule.pdf"
OUTPUT_XLSX = "mcb_counts.xlsx"
PAGES_TO_PROCESS = 386  # first 386 PDF pages, 1-based
DPI = 300

# The schedule starts on PDF page 2 in the uploaded file; page 1 is a cover.
# Coordinates below are fractions of rendered page dimensions, based on the
# uploaded schedule layout. If the PDF layout changes, adjust these.
# Crop the MCB Rating (A) column, excluding its header.
MCB_X1_FRAC = 0.197
MCB_X2_FRAC = 0.235
DATA_Y1_FRAC = 0.230
DATA_Y2_FRAC = 0.645

ELCB_X1_FRAC = 0.118
ELCB_X2_FRAC = 0.155

ISOLATOR_X1_FRAC = 0.082
ISOLATOR_X2_FRAC = 0.117

# Common ratings. Expand this list if your drawings use other ratings.
KNOWN_RATINGS = ["10", "20", "32", "63" ]
ELCB_RATINGS = ["30", "100"]
ISOLATOR_RATINGS = ["40", "63", "80", "100", "125", "160", "200"]

def clean_ocr_token(token, objectType):
    """Normalize common OCR confusions in MCB ratings."""
    s = token.upper().replace(" ", "").replace(".", "")
    s = s.replace("O", "0").replace("I", "1").replace("L", "1")
    s = re.sub(r"[^0-9A]", "", s)
    if s.endswith("A"):
        s = s[:-1]
    # Handle OCR variants such as 1OA -> 10A after O->0.
    if objectType == "MCB" and s in KNOWN_RATINGS:
        return s
    if objectType == "ELCB" and s in ELCB_RATINGS:
        return s
    if objectType == "Isolator" and s in ISOLATOR_RATINGS:
        return s
    return None

def preprocess_column(gray):
    """Remove horizontal grid lines to make the rating text easier to OCR."""
    _, binary = cv2.threshold(
        gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )
    h_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(20, gray.shape[1] // 2), 1)
    )
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel)
    cleaned = cv2.subtract(binary, horizontal)
    return cv2.bitwise_not(cleaned)

def extract_page_ratings(page, page_number, boardType):
    """OCR the MCB column and return rating counts plus a review record."""
    pix = page.get_pixmap(dpi=DPI, alpha=False)
    image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.width, pix.n
    )
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)

    x1 = int(pix.width * MCB_X1_FRAC)
    x2 = int(pix.width * MCB_X2_FRAC)
    y1 = int(pix.height * DATA_Y1_FRAC)
    y2 = int(pix.height * DATA_Y2_FRAC)

    crop = gray[y1:y2, x1:x2]
    if boardType == "ELCB":
        temp_x1 = int(pix.width * ELCB_X1_FRAC)
        temp_x2 = int(pix.width * ELCB_X2_FRAC)
        crop = gray[y1:y2, temp_x1:temp_x2]

    if boardType == "Isolator":
        isolator_x1 = int(pix.width * ISOLATOR_X1_FRAC)
        isolator_x2 = int(pix.width * ISOLATOR_X2_FRAC)
        crop = gray[y1:y2, isolator_x1:isolator_x2]
    # cv2.imwrite(f"isolator_{page_number}.png", gray[y1:y2, isolator_x1:isolator_x2])

    crop = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    cleaned = preprocess_column(crop)

    config = "--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789Aa"
    data = pytesseract.image_to_data(
        cleaned, config=config, output_type=pytesseract.Output.DICT
    )

    counts = Counter()
    observed = []
    for i, raw in enumerate(data["text"]):
        raw = raw.strip()
        if not raw:
            continue
        rating = clean_ocr_token(raw, boardType)
        if rating:
            conf = float(data["conf"][i]) if data["conf"][i] not in ("", "-1") else -1
            observed.append((rating, conf, data["top"][i], raw))
            counts[rating] += 1

    # This diagnostic is not a proof of accuracy; it flags unusually low/high
    # detected rating counts for manual review.
    note = ""
    if not observed:
        note = "No MCB ratings detected"
    elif len(observed) > 42:
        note = f"Detected {len(observed)} ratings; more than 42 circuit rows"
    elif any(conf >= 0 and conf < 35 for _, conf, _, _ in observed):
        note = "One or more low-confidence OCR tokens"
    return counts, {
        "PDF page": page_number,
        "Detected MCB entries": len(observed),
        "Detected ratings": ", ".join(r for r, _, _, _ in observed),
        "Average OCR confidence": round(
            sum(c for _, c, _, _ in observed if c >= 0) /
            max(1, sum(1 for _, c, _, _ in observed if c >= 0)), 1
        ),
        "Review note": note,
    }

def extract_db_name(page, page_number):
    """Read the DB REF NO from the top-left metadata block."""
    pix = page.get_pixmap(dpi=300, alpha=False)
    image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.width, pix.n
    )
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)

    # Top-left project metadata block; excludes most of the schedule grid.
    crop = gray[
        int(pix.height * 0.07):int(pix.height * 0.27),
        0:int(pix.width * 0.43)
    ]
    crop = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    crop = cv2.threshold(
        crop, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )[1]

    # cv2.imwrite(f"db_{page_number}.png", crop)
    text = pytesseract.image_to_string(crop, config="--oem 3 --psm 6")
    # Match DB reference labels such as DB-E-06-FLS-B-01.
    match = re.search(
        r"DB\s*REF\s*NO\s*[:.]?\s*([A-Z0-9][A-Z0-9./_-]{3,})",
        text, flags=re.IGNORECASE
    )
    if match:
        db_name = re.sub(r"\s+", "", match.group(1)).strip(":-.")
        if len(db_name) >= 5:
            return db_name.upper()

    # OCR sometimes misses the label, so look for a DB-like token anywhere.
    match = re.search(
        r"\bDB[-_ ]?[A-Z0-9][A-Z0-9./_-]{4,}\b",
        text, flags=re.IGNORECASE
    )
    if match:
        return re.sub(r"\s+", "", match.group(0)).upper()

    return f"Page {page_number}"  # fallback, flagged for review

def main():
    pdf_path = Path(PDF_PATH)
    if not pdf_path.exists():
        raise FileNotFoundError(
            f"Cannot find {PDF_PATH!r}. Put it in the same folder "
            "as this script or update PDF_PATH."
        )

    # Process both rating types from every PDF page.
    board_types = ["MCB", "ELCB", "Isolator"]

    # Store counts separately for each type and PDF page.
    page_counts = {}
    page_db_names = {}
    rating_totals = {
        board_type: Counter()
        for board_type in board_types
    }
    review_rows = []

    start_time = time.time()

    with fitz.open(pdf_path) as doc:
        total_pages = min(PAGES_TO_PROCESS, len(doc))

        print(
            f"PDF has {len(doc)} pages; "
            f"processing pages 1–{total_pages}."
        )

        for page_index in range(total_pages):
            page_number = page_index + 1
            page = doc[page_index]

            # Extract the DB reference once per page.
            db_name = extract_db_name(page, page_number)
            page_db_names[page_number] = db_name

            # Extract MCB and ELCB ratings independently.
            for board_type in board_types:
                counts, review = extract_page_ratings(
                    page,
                    page_number,
                    board_type
                )

                page_counts[(page_number, board_type)] = counts
                rating_totals[board_type].update(counts)

                # Identify which type of OCR produced this review record.
                review["DB name"] = db_name
                review["Detected object type"] = board_type

                if db_name.startswith("Page "):
                    existing_note = review["Review note"]
                    warning = "DB reference not recognized by OCR"

                    review["Review note"] = (
                        f"{existing_note}; {warning}"
                        if existing_note
                        else warning
                    )

                # Keep records that require attention.
                if review["Review note"] or page_number == 1:
                    if page_number == 1 and not review["Review note"]:
                        review["Review note"] = (
                            "Cover page; verify whether to exclude"
                        )

                    review_rows.append(review)

            if page_number % 10 == 0 or page_number == total_pages:
                print(
                    f"Processed {page_number}/{total_pages} pages"
                )

    # Make DB reference column names unique if duplicates occur.
    used_names = Counter()
    for page_num in sorted(page_db_names):
        name = page_db_names[page_num]
        used_names[name] += 1

        if used_names[name] > 1:
            page_db_names[page_num] = (
                f"{name} (PDF p.{page_num})"
            )

    # Build the output rating rows.
    # MCB rows appear first, followed by ELCB rows.
    rating_rows = []

    for rating in sorted(KNOWN_RATINGS, key=int):
        rating_rows.append(
            (f"{rating} A MCB", "MCB", rating)
        )

    for rating in sorted(ELCB_RATINGS, key=int):
        rating_rows.append(
            (f"{rating} mA ELCB", "ELCB", rating)
        )

    for rating in sorted(ISOLATOR_RATINGS, key=int):
            rating_rows.append(
                (f"{rating} A Isolator", "Isolator", rating)
            )

    # Create the matrix: rating rows × DB reference columns.
    output_data = {}

    for page_num in range(1, total_pages + 1):
        db_name = page_db_names[page_num]
        column_values = []

        for row_label, board_type, rating in rating_rows:
            counts = page_counts.get(
                (page_num, board_type),
                Counter()
            )
            column_values.append(counts.get(rating, 0))

        output_data[db_name] = column_values

    row_labels = [row[0] for row in rating_rows]

    output = pd.DataFrame(
        output_data,
        index=row_labels
    )
    output.index.name = "Rating"

    # Export both worksheets.
    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
        output.to_excel(writer, sheet_name="Counts")

        review_columns = [
            "PDF page",
            "DB name",
            "Detected object type",
            "Detected MCB entries",
            "Detected ratings",
            "Average OCR confidence",
            "Review note",
        ]

        pd.DataFrame(
            review_rows,
            columns=review_columns
        ).to_excel(
            writer,
            sheet_name="Review",
            index=False
        )

        # Format the Counts worksheet.
        ws = writer.sheets["Counts"]
        ws.freeze_panes = "B2"
        ws.column_dimensions["A"].width = 20

        for col in range(2, ws.max_column + 1):
            ws.column_dimensions[
                ws.cell(1, col).column_letter
            ].width = 22

        # Format the Review worksheet.
        ws_review = writer.sheets["Review"]
        ws_review.freeze_panes = "A2"
        ws_review.column_dimensions["B"].width = 28
        ws_review.column_dimensions["C"].width = 20
        ws_review.column_dimensions["E"].width = 45
        ws_review.column_dimensions["G"].width = 45

    print(f"Total time taken: {time.time() - start_time:.2f} seconds")
    print(f"Saved {OUTPUT_XLSX}")
    print("Verify MCB, ELCB and Isolator counts in the Counts worksheet.")
    print("Review the Review worksheet for OCR warnings.")

if __name__ == "__main__":
    main()