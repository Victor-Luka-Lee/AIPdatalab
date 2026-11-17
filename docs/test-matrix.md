# 필수 검증 항목 대응

각 번호는 첨부 요구사항 18절에 대응한다. 실제 실행 결과는 validation.md를 참조한다.

| 번호 | 테스트 |
| --- | --- |
| 1~6 | test_01_own_response_zero ~ test_06_denied_is_not_disclosure |
| 7 | test_07_missing_evidence_unknown (응답·정책·관계 3종) |
| 8 | test_08_failed_attempt_not_positive |
| 9 | test_09_exact_join_uses_key |
| 10 | test_10_unmatched_rows_retained |
| 11 | test_11_future_events_not_used |
| 12 | test_12_unarrived_audit_not_used |
| 13 | test_13_label_defect_and_scenario_excluded |
| 14 | test_14_no_raw_identifiers |
| 15 | test_15_all_comparison_populations_equal |
| 16 | test_16_incident_splits_no_overlap |
| 17 | test_17_preprocessing_fit_train_only + test_model_fit_manifest_and_missing_audit |
| 18 | test_18_unknown_excluded_not_negative |
| 19 | test_19_one_class_unavailable + test_no_detector_population_report |
| 20 | test_20_detector_miss_independent_oracle + test_unknown_evidence_not_called_detector_false_event |
| 21 | test_21_real_http_smoke_report (실제 loopback Uvicorn + HTTPX, 4개 조건) |
| 22 | test_22_same_seed_data_and_plan + test_same_seed_http_replay_consistent |
| 23 | test_23_external_target_blocked (8개 주소) |

추가로 제한 연계의 다중 후보 처리, 전체 결측 범주형 입력, 정책 마스킹, 승인 업무 범위, 동점 경보 예산, 공유 개인정보 값의 객체 문맥을 검증한다. 모델 전처리의 미지 test 범주가 train 인코딩에 들어오지 않는지, 실제 train fit ID가 train 분할에만 속하는지 확인한다.
