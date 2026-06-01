# 네이버 블로그 노출·순위 모니터링 툴

네이버 검색에서 블로그 글의 노출 여부와 순위를 자동으로 점검하고, 결과를 엑셀 리포트로 정리하는 데스크탑 툴입니다.

여러 운영 블로그의 발행 글이 실제 검색 결과에 노출되는지 수작업으로 확인하던 과정을 자동화하기 위해 제작했습니다.

## 주요 기능

- 키워드별 네이버 검색 결과에서 대상 블로그 글의 노출 여부 확인
- 네이버 검색 API 순위와 브라우저 화면 기준 순위 비교
- 다수 업체 / 다수 키워드 일괄 점검
- 검색 결과 캡처 저장
- 점검 결과 엑셀 리포트 자동 생성
- 블로그 글 누락 여부 확인
- PyInstaller 기반 exe 패키징 지원

## 기술 스택

- Python
- customtkinter
- Playwright
- 네이버 검색 API
- requests
- pandas
- openpyxl
- Pillow
- PyInstaller

## 프로젝트 구조

| 파일 | 역할 |
| --- | --- |
| `app.py` | 데스크탑 UI 진입점 |
| `main.py` | 키워드 순위 점검 실행 로직 |
| `browser.py` | Playwright 기반 브라우저 제어 |
| `exposure_core.py` | 블로그 글 노출·누락 판정 로직 |
| `naver_api.py` | 네이버 블로그 검색 API 연동 |
| `excel_writer.py` | 엑셀 보고서 작성 및 캡처 이미지 삽입 |
| `report.py` | 점검 결과 리포트 저장 |
| `selectors.json` | 검색 결과 파싱용 셀렉터 정의 |
| `blog_tool.spec` | PyInstaller 빌드 설정 |

## 실행

```powershell
pip install customtkinter playwright requests pandas openpyxl pillow pyinstaller
python -m playwright install chromium
python app.py
```

## 설정

- 앱 화면에서 네이버 API Client ID / Secret을 입력하면 로컬 `config.json`에 저장됩니다.
- `config.json`과 `input.csv`는 `.gitignore`에 포함되어 있으며, 저장소에는 실제 API 키와 운영 데이터가 포함되지 않습니다.
- 실행 시 앱이 저장된 API 값을 `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` 환경변수로 주입해 네이버 검색 API를 호출합니다.
- 검색 대상은 앱 화면에서 직접 추가하거나 로컬 `input.csv`에 저장된 값을 불러와 사용할 수 있습니다.

## 출력

- 검색 결과 캡처: `output/captures/`
- 순위 점검 리포트: `output/rank_report.xlsx`
- 업체별 엑셀 보고서: 선택한 보고서 템플릿 기준으로 생성

## 비고

- 개인 실무 자동화 목적으로 직접 기획하고, 생성형 AI를 활용해 개발한 프로젝트입니다.
- 요구사항 정의, 기능 설계, 출력 검증을 직접 수행했습니다.
