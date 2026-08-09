# Smart Factory Flask API + MCP Server 실행 가이드

## 1. 전제 조건

프로젝트 루트에서 실행한다.

```bash
cd /Users/insu/Desktop/smartfactory_mcp
source .venv/bin/activate
```

의존성을 설치한다.

```bash
python -m pip install -r requirements-part3.txt
```

TimescaleDB를 실행한다.

```bash
docker compose up -d
docker compose ps
```

`smart_factory_timescaledb`가 `healthy`인지 확인한다. 서버는 프로젝트 루트의 `.env`를 자동으로 읽으며 비밀번호를 로그나 MCP 응답에 포함하지 않는다.

## 2. Flask 서버 실행

```bash
python -m web_app.app
```

기본 주소:

```text
대시보드       http://127.0.0.1:5000/
Health Check   http://127.0.0.1:5000/health
센서 목록       http://127.0.0.1:5000/api/sensors
```

Flask 개발 서버는 로컬 데모용이며 기본적으로 `127.0.0.1`에만 바인딩한다. 외부에 공개할 때는 인증, HTTPS와 운영용 WSGI 서버를 별도로 구성해야 한다.

## 3. REST API

| Method | 경로 | 기능 |
|---|---|---|
| GET | `/health` | DB 연결과 모델 확인 |
| GET | `/api/model` | 모델 실행 요약 |
| GET | `/api/sensors` | 센서 목록 |
| GET | `/api/sensors/<id>/status` | 마지막 센서 상태 |
| GET | `/api/sensors/<id>/history?hours=24&limit=200` | 센서 이력 |
| GET | `/api/abnormal-sensors?minimum_status=DEGRADING` | 이상 센서 |
| GET | `/api/anomaly-detail?sensor_id=92&window_start=...` | 특정 Window 상세 |
| GET | `/api/factory-summary` | 전체 상태 요약 |

예시:

```bash
curl http://127.0.0.1:5000/api/sensors/92/status
curl 'http://127.0.0.1:5000/api/sensors/92/history?hours=1&limit=10'
curl 'http://127.0.0.1:5000/api/abnormal-sensors?minimum_status=DEGRADING'
```

## 4. Flask 테스트

실제 네트워크 포트를 열지 않고 Flask Test Client로 전체 API를 확인한다.

```bash
python scripts/flask_smoke_test.py
```

정상 API 9개가 200을 반환하고, 잘못된 센서 ID와 숫자 파라미터가 400을 반환해야 한다.

## 5. MCP 서버 실행

```bash
python -m mcp_server.server
```

기본 전송 방식은 `stdio`다. 정상 실행 시 터미널 입력을 기다리는 것처럼 보이며, MCP Client가 JSON-RPC 메시지를 주고받는다. 일반 텍스트를 직접 입력하는 프로그램이 아니다.

기본 모델 실행은 다음 값으로 고정했다.

```text
iforest_2019_02_2019_07_v1
```

다른 실행을 사용하려면 환경변수로 지정할 수 있다.

```bash
MCP_MODEL_RUN_ID=<model_run_id> python -m mcp_server.server
```

## 6. MCP 종단 간 테스트

별도 MCP Client가 서버를 실행하고 Tool 목록과 센서 84의 상태를 조회한다.

```bash
python scripts/mcp_smoke_test.py
```

완료 조건:

- Tool 7개가 모두 조회된다.
- `get_sensor_status(sensor_id="84")`가 구조화된 응답을 반환한다.
- `model_run_id`가 `iforest_2019_02_2019_07_v1`이다.
- `as_of`와 `is_historical_data=true`가 포함된다.

## 7. MCP Client 설정 예시

MCP Client의 서버 설정에 다음 형태로 등록한다. 실제 설정 파일 위치와 최상위 키 이름은 사용하는 Client 문서를 따른다.

```json
{
  "mcpServers": {
    "smart-factory": {
      "command": "/Users/insu/Desktop/smartfactory_mcp/.venv/bin/python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/Users/insu/Desktop/smartfactory_mcp"
    }
  }
}
```

## 8. MCP Tool 계약

### `list_monitored_sensors`

모델에 포함된 센서와 각 센서의 마지막 저장 상태를 반환한다.

### `get_sensor_status`

입력:

```json
{"sensor_id": "92"}
```

반환 항목:

```text
window_start, window_end, unit
risk_score, status
isolation_anomaly, sigma_anomaly
sigma_detected_features
sample_count
mean, std, min_value, max_value, rms, peak_to_peak, slope
previous_window, gap_minutes
```

### `get_abnormal_sensors`

입력:

```json
{"minimum_status": "DEGRADING", "limit": 20}
```

허용 상태는 `NORMAL`, `ATTENTION`, `DEGRADING`, `WARNING`이다.

### `get_sensor_history`

입력:

```json
{"sensor_id": "92", "hours": 24, "limit": 200}
```

`hours`는 현재 시간이 아니라 해당 센서의 마지막 저장 시각을 기준으로 계산한다. 최대 744시간, 최대 1,000개 Window로 제한한다.

### `get_anomaly_detail`

입력:

```json
{"sensor_id": "92", "window_start": "2019-07-30 20:38:00"}
```

특정 Window의 모델 점수, 3-Sigma 탐지 Feature, 1분 Feature와 직전 수집 공백을 반환한다.

### `get_factory_summary`

모니터링 센서의 마지막 상태별 개수를 반환한다.

### `get_model_summary`

Feature, 모델 파라미터, 센서 수, 결과 행 수와 데이터 기간을 반환한다.

## 9. 공통 안전장치

- 임의 SQL Tool을 제공하지 않는다.
- 모든 입력은 파라미터화된 SQL로 전달한다.
- DB 트랜잭션을 `READ ONLY`로 설정한다.
- 센서 ID는 숫자만 허용한다.
- 조회 기간과 결과 개수에 상한을 둔다.
- 원본 DB 비밀번호와 연결 문자열을 응답하지 않는다.
- 모든 응답에 과거 데이터 여부와 기준 시각을 포함한다.

## 10. 현재 데이터 확인 결과

마지막 저장 시각 `2019-07-30 20:42:00` 기준:

| 센서 | 상태 | Risk Score |
|---|---|---:|
| 84 | NORMAL | 57.77 |
| 92 | DEGRADING | 81.83 |
| 109 | NORMAL | 16.92 |

이 값은 실시간 공장 상태가 아니라 공개 데이터셋의 마지막 기록이다.
