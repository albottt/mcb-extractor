# MCB Schedule Extractor (React + FastAPI)

This version connects the React upload interface to the OCR functions in `backend/extractor.py`. The API renders PDF pages, reads the MCB rating column and DB reference number, returns a count matrix, review rows, and an Excel workbook.

## Requirements
- Python 3.10+
- Node.js 18+
- Tesseract OCR installed on the computer and available on PATH

### Install Tesseract OCR on Windows
Install Tesseract OCR (for example, the UB Mannheim Windows build), then verify in a new terminal:

```powershell
tesseract --version
```

If Tesseract is installed but not on PATH, add its installation directory to PATH or set `pytesseract.pytesseract.tesseract_cmd` near the top of `backend/extractor.py` to the full `tesseract.exe` path.

## Start the backend
Open a terminal in the project folder:

```powershell
cd backend
py -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Check `http://127.0.0.1:8000/api/health` or open `http://127.0.0.1:8000/docs`.

## Start the frontend
In another terminal, from the project folder:

```powershell
cd frontend
npm install
npm run dev
```

Open the local URL printed by Vite (normally `http://localhost:5173`). The frontend posts the selected PDF to `POST /api/extract`.

## Page selection
The current frontend sends `pages=386`, so the backend processes up to 386 pages from PDF page 1. If the document contains fewer pages, it processes the pages available. You can change that value in `frontend/src/App.jsx`. The endpoint also accepts `start_page` and `pages` as multipart form fields.

## OCR tuning and accuracy
The API uses the crop coordinates and allowed ratings currently configured in `backend/extractor.py` (`MCB_X1_FRAC`, `MCB_X2_FRAC`, `DATA_Y1_FRAC`, `DATA_Y2_FRAC`, and `KNOWN_RATINGS`). Validate the crop on your actual drawings. The current allowed ratings are `10`, `20`, `32`, and `63`; add other ratings used in the schedule if necessary. OCR counts should be checked in the Review tab and against the source PDF before being used for engineering decisions.

## API response
The endpoint returns `columns`, `rows`, `review_columns`, `review`, and `xlsx_base64` (base64 encoded `.xlsx` containing `Counts` and `Review` sheets).
