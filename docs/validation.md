# 실제 검증 기록

검증일: 2026-11-17 (Asia/Seoul). Windows 11, CPython 3.12.16.

## 최종 결과

- `python -m pytest -q -W error`: **41 passed in 16.53s**, 실패 0, 경고 0.
- 요구사항 18절의 23개 항목에 대한 테스트 매핑: test-matrix.md.
- 실제 Uvicorn loopback + HTTPX 통합 테스트이며 HTTP를 모의 응답으로 대체하지 않았다.
- 같은 seed의 별도 HTTP 실행에서 응답 body bytes, ID·레이블·제공 집합·사유, 특징 프레임 및 분할의 일치를 확인했다. wall-clock 수신 시각과 실행시간은 비교 대상에서 제외했다.
- 기본, 감사 누락, 지연, 제한 연계의 4개 조건을 실제로 학습·평가했다.
- 빈 검출 모집단 및 한 클래스 평가 불가 상태를 확인했다. 숫자 0으로 조작하거나 U를 정상 음성으로 변환하지 않았다.

## 실행한 주요 명령

현재 저장소의 .venv/Scripts/python.exe를 사용했다.

```powershell
# 설치: 네트워크가 차단된 기본 실행 실패 후 승인된 설치에서 패키지 다운로드
uv --cache-dir .uv-cache pip install --python .venv\Scripts\python.exe -e '.[test]'
# SciPy 호환 범위 수정 후 캐시를 이용한 오프라인 재설치
uv --cache-dir .uv-cache pip install --offline --python .venv\Scripts\python.exe -e '.[test]'
$env:MPLCONFIGDIR='C:\code\AIPdatalab\.runtime\matplotlib'
.venv\Scripts\python.exe scripts\check_environment.py
.venv\Scripts\python.exe -m pytest -q -W error
.venv\Scripts\python.exe scripts\validate_stages.py --run-dir artifacts\manual-verified
.venv\Scripts\python.exe -m pipeline.cli run --run-dir artifacts\smoke-verified --mode smoke --conditions exact missing delayed restricted --bootstrap 100
.venv\Scripts\python.exe -m compileall -q app detector scenarios ground_truth pipeline models evaluation tests scripts
.venv\Scripts\python.exe -m pipeline.cli --help
```

validate_stages.py는 CLI의 init, 별도 serve 프로세스, scenarios, truth, features, train, evaluate, report를 순서대로 호출했다. 모든 호출은 성공했고 서버 프로세스는 종료됐다. smoke-verified는 자동 시작/정상 종료 경로를 사용했다.

## 실제 산출물

- artifacts/smoke-verified/report.md: 4개 조건 종합 보고서.
- artifacts/smoke-verified/versions/{exact,missing,delayed,restricted}/metrics.csv: 조건별 6개 비교군 test 지표.
- 같은 디렉터리의 bootstrap.json, scores.jsonl, figures/*.png: paired 사건 CI, 예측 점수 및 PR/confusion 그림.
- artifacts/smoke-verified/private/: 실제 수신 응답·합성 SQLite·독립 정답·모집단.
- artifacts/smoke-verified/environment.json: 실제 버전과 실행 시점 소스 SHA-256.
- artifacts/manual-verified/report.md: 단계별 CLI 실행 산출물 (exact 조건).

smoke-verified는 예정 사건 36개, 예정/실제 응답 51개, 검출 이벤트 응답 44개, U 6개다. 주 모집단 test에서는 U 2개를 제외한 알려진 레이블 9개를 평가했다. 이 표본 수로 통계적 충분성이나 일반화된 성능 개선을 주장하지 않는다. 그림 중 M2 PR curve를 실제 열어 축·내용을 확인했다.

## 중간 실패와 수정

- 기본 네트워크 권한에서는 Python/패키지 다운로드가 실패했다. 승인된 설치 이후 실험은 외부 API 없이 로컬에서 수행했다.
- 최초 SciPy 1.18 조합에서 sklearn 최적화 옵션 경고가 발생해 SciPy 범위를 1.14 이상 1.16 미만으로 제한했다. 실제 검증은 1.15.3이다.
- Windows PowerShell이 TOML에 붙인 UTF-8 BOM으로 재설치가 실패한 적이 있으며 BOM을 제거해 수정했다.
- 추가 재현성 테스트가 메모리 tuple과 JSON에 직렬화된 list를 직접 비교해 실패한 적이 있다. 양쪽 모두 실제 저장 JSON을 읽어 비교하도록 수정했다. 이후 전체 41개 테스트가 경고를 오류로 처리하는 설정에서 통과했다.
- 수신 증거가 없는 U 사례를 검출기 오탐으로 집계하던 항목을 수정하고 별도 unknown-evidence 항목으로 기록했다.

## 미실행 및 한계

- Docker 실행 도구가 없어 Dockerfile/Compose의 실제 build/up을 실행하지 않았다.
- Linux/macOS, Python 3.11/3.13, 대규모 experiment, 다중 seed 연구 실험은 실행하지 않았다.
- 합성 JSON 스키마·가상 event clock·가공된 누락/지연 조건이다. 실제 기관 로그, 실제 개인정보, 운영망 지연·처리량이나 상용 제품을 평가하지 않았다.
- 조건별 재학습 실험이며 깨끗한 모델을 고정한 배포 후 견고성 실험은 별도 범위다.
