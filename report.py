import pandas as pd


RESULT_COLUMNS = [
    "업체명",
    "키워드",
    "식별값",
    "API순위",
    "화면순위",
    "매칭텍스트",
    "API매칭필드",
    "API제목",
    "캡처파일",
    "사용브라우저",
    "비고",
]


def _build_df(rows, columns):
    df = pd.DataFrame(rows)
    return df.reindex(columns=columns)


def save_report(rows, output_path: str, error_rows=None, undetected_rows=None):
    error_rows = error_rows or []
    undetected_rows = undetected_rows or []

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        _build_df(rows, RESULT_COLUMNS).to_excel(writer, sheet_name="전체결과", index=False)
        _build_df(error_rows, RESULT_COLUMNS).to_excel(writer, sheet_name="오류행", index=False)
        _build_df(undetected_rows, RESULT_COLUMNS).to_excel(writer, sheet_name="미노출행", index=False)
