# TunaHeadCut

MR-MC240N PCIe 보드 + MR-J4-10B-RJ 서보앰프 + HG-KR13 모터의 **1축 절대 위치 제어**입니다.
이 PC에 설치된 Position Board Utility2의 API DLL을 `ctypes`로 호출하며 Python 표준 라이브러리만 사용합니다.
함수 인자, 상태 비트, 32바이트 포인트 구조체는 설치본 `mc2xxstd.h` Ver.2.40과 대조했습니다.

Python에서 받은 `0~1` 값을 Start·End 절대 위치 사이로 변환합니다.
설정 파일을 생략한 기본 시뮬레이션은 이전 소범위 Start `2794600`, End `2786203`을 사용합니다.
현재 실제 모터 설정은 **1:1 전자기어, 최소 -5598206 / 최대 11179010, 전체 4바퀴**입니다.
현재 설정을 모의 실행하려면 `python main.py --config axis_config.json 0 0.5 1`을 사용합니다.
아래 식은 설정 파일을 생략했을 때의 시뮬레이션 예입니다.

```text
목표 카운트 = round(Start + 입력값 × (End − Start))
           = round(2794600 − 입력값 × 8397)
```

- `0`: Start, 2794600
- `0.5`: 두 위치의 중간, 2790402 (Python의 짝수 반올림)
- `1`: End, 2786203
- 동일한 입력을 반복해도 같은 절대 위치를 목표로 합니다.
- 이미 목표의 허용 오차 안에서 위치결정 완료 상태이면 새 이동을 생략합니다.
- 범위 밖 값, NaN, 무한대, 잘못된 문자열은 거부합니다.

엔코더의 저장값이나 원점을 덮어쓰지 않습니다. 설정값은 **MR-MC240N의 현재 피드백
위치와 같은 좌표계·명령 단위**를 사용합니다. HG-KR13의 원시 엔코더 펄스 수와 보드의
명령 단위는 전자기어 설정에 따라 다르므로, 원시 카운트를 사용하려면 먼저 그 관계를
확인해야 합니다. CTR 장비의 4 mm 볼스크루, 196 mm 범위, 전자기어 값은 이식하지 않았습니다.

## 모의 실행

Python 3.10 이상에서 실행합니다. 별도 pip 패키지가 필요하지 않습니다.

```powershell
python main.py 0 0.5 1
python main.py
```

기본은 시뮬레이션입니다. 설정을 생략하면 `2794600 → 2786203` 카운트를 사용합니다.
첫 번째 명령은 세 위치를 순서대로 계산·모의 이동하고, 두 번째는 키보드 입력을 받습니다.
`status`는 현재 상태, `q` 또는 EOF는 종료, `Ctrl+C`는 중단 후 정리입니다.
시뮬레이터는 즉시 위치가 바뀌며 실제 모터의 시간·관성·정지 거리를 재현하지 않습니다.

## TCP/IP 수신과 더미 송신 검증

장비 PC에서 TCP 서버를 실행하면 수신한 값을 기존 `move_normalized()`에 전달합니다.
기본 주소는 `127.0.0.1:5000`이며 기본 동작은 시뮬레이션입니다.

### 현재 모터를 TCP로 제어

장비 PC에서 다음 명령을 실행합니다. 현재 `axis_config.json`의 **4바퀴 범위, 약 6 rpm**을 사용합니다.

```powershell
python main.py --motor-test --enable-motion --tcp-server --host 0.0.0.0 --port 5000
```

`TCP listening on 0.0.0.0:5000`이 출력되면 송신할 수 있습니다. 같은 PC의 다른 터미널에서는:

```powershell
python dummy_tcp_client.py 0.5 0.25 0.75 0.5
```

다른 PC에서는 `--host`에 장비 PC의 실제 IPv4 주소를 지정합니다.

```powershell
python dummy_tcp_client.py --host 192.168.0.10 --port 5000 0.5 0.25 0.75 0.5
```

| 수신값 | 목표 절대 좌표 | 중간 위치 기준 회전량 |
|---|---:|---:|
| `0` | 11179010 | +2바퀴 |
| `0.25` | 6984706 | +1바퀴 |
| `0.5` | 2790402 | 0 |
| `0.75` | -1403902 | -1바퀴 |
| `1` | -5598206 | -2바퀴 |

`0 → 1`은 4바퀴 이동이며 약 41초가 걸립니다. 같은 값을 반복하면 같은 절대 위치를 유지합니다.
송신기는 이동 완료 응답을 받은 뒤 다음 값을 전송합니다. 서버는 `Ctrl+C`로 정지·종료합니다.

### 모터 없이 통신 확인

같은 PC의 터미널 두 개에서 다음 명령을 실행하세요.

```powershell
# 터미널 1: 수신기 (모의 축)
python main.py --tcp-server --config axis_config.json

# 터미널 2: 더미 송신기
python dummy_tcp_client.py --include-invalid --fragment-bytes 1
```

더미 송신기는 기본으로 `0, 0.25, 0.5, 0.75, 1`을 순서대로 전송하고 각 응답을 기다립니다.
`--include-invalid`는 `-0.1, 1.1, nan, inf, abc, 빈 줄`의 거부 응답도 확인합니다.
`--fragment-bytes 1`은 요청을 1바이트씩 나누어 쓰며, 송신기가 11개 응답을 예상대로 받으면
`PASS: 11 responses matched expectations.`를 출력합니다. 소켓 계층은 이 쓰기를 합칠 수도
있으므로 테스트에서는 줄바꿈을 나중에 보내는 경우도 별도로 확인합니다.

수신기는 계속 연결을 기다립니다. `Ctrl+C`로 종료하거나, 검증용으로
`python main.py --tcp-server --once`를 사용하면 한 연결 종료 후 정리하고 끝납니다.

수신기 터미널(터미널 1)에는 연결·해제, 이동 요청, 이동 중 1초 간격의 피드백·잔여 거리,
완료 시 피드백과 소요 시간이 표시됩니다.
잘못된 입력과 이동 오류도 같은 터미널에 기록합니다. 출력 예:

```text
[14:30:00] MOVE request: input=0.5 target=2790402
[14:30:00] MOVE done: input=0.5 target=2790402 feedback=2790402 elapsed=0.001s
```

`request`는 위치 명령 요청, `done`은 위치결정 완료 확인입니다. 이미 목표의 허용 오차
안에 있어 실제 이동을 생략한 경우에도 완료 로그를 표시합니다.

### 다른 PC에서 전송

```powershell
# 수신 PC: LAN 연결을 받는 모의 실행
python main.py --tcp-server --config axis_config.json --host 0.0.0.0 --port 5000

# 송신 PC: 192.168.0.10을 실제 수신 PC의 IP로 변경
python dummy_tcp_client.py --host 192.168.0.10 --port 5000 0 0.5 1
```

`0.0.0.0` 대신 수신 PC의 특정 IPv4 주소를 지정할 수도 있습니다. 수신 PC 방화벽에서
해당 TCP 포트의 접속을 허용해야 합니다. 이 프로토콜에는 인증·암호화가 없으므로 신뢰할 수
있는 장비 내부망에서 사용합니다. 프로그램이 방화벽 설정을 자동 변경하지는 않습니다.

현재 등록된 MotorBench 설정은 위의 `--motor-test` 명령으로 시작합니다.
별도로 준비된 RUNNING 축을 사용할 때는 일반 하드웨어 옵션도 지원합니다.

```powershell
python main.py --tcp-server --host 0.0.0.0 --port 5000 `
  --hardware --config axis_config.json --enable-motion
```

TCP 바인딩 성공 후 축을 준비하고 서보를 활성화한 다음 `TCP listening on ...`을 출력합니다.
`--motor-test`에서는 READY 상태의 Flash 복원·System Start, ABS 기준점과 전자기어 검증,
JSON 속도 상한 적용을 로컬 입력 모드와 동일하게 수행합니다. 포트가 이미 사용 중이면 이 준비를 수행하지 않습니다.
네트워크 메시지로 서보 활성화, 파라미터 변경, 원점 재설정 등을 할 수는 없습니다.
수신기가 켠 서보는 클라이언트 연결 사이에도 ON을 유지하며, 수신기 종료 시 기존 정리 절차를 따릅니다.

### 전송 형식과 응답

ASCII 숫자 하나 뒤에 LF(`\n`)를 붙입니다. CRLF도 지원하며 한 줄은 최대 128바이트입니다.
예를 들어 `0.5\n`을 전송하면 위치결정 완료 후 아래 형태의 UTF-8 JSON 한 줄을 돌려줍니다.

```json
{"ok":true,"input":0.5,"target_counts":2790402,"feedback_counts":2790402}
```

위 카운트는 현재 4바퀴 범위의 중간 위치 예입니다. 실제 응답은 설정된 범위와 피드백을 사용합니다.
성공 응답은 목표 허용 오차 안에서 위치결정 완료를 확인했다는 의미입니다.
송신기는 **응답 한 줄을 받은 후 다음 값**을 보내세요. 여러 줄이 합쳐져 수신되어도
도착 순서대로 처리하며, 가장 최신 값으로 이전 명령을 덮어쓰지는 않습니다.
한 번에 한 클라이언트 연결만 처리하므로 복수 송신기를 동시에 사용하지 마세요.

| 오류 코드 | 동작 |
|---|---|
| `invalid_value` | 범위 밖·숫자 아님·비 ASCII·빈 줄; 이동하지 않고 다음 줄 수신 |
| `line_too_long` | 128바이트 초과; 해당 연결 종료 |
| `incomplete_line` | 줄바꿈 없이 송신 종료; 미완성 값 폐기 |
| `client_timeout` | 제한 시간 내 완성된 줄이 없음; 연결 종료 |
| `motion_error` | 축 이동 실패; 오류 응답 시도 후 서버 종료 및 축 정리 |

오류 응답은 `{"ok":false,"error":"invalid_value","message":"..."}` 형태입니다.
`--client-timeout`은 입력 대기 제한이며 기본 30초입니다. 부분 데이터만 계속 보내도 제한 시간을
연장하지 않습니다. 이동 중에는 기존 `move_timeout_s`를 적용하고 이동 응답 후 입력 대기를 다시 시작합니다.
더미 송신기의 응답 대기는 `--timeout`으로 조정하며 기본 75초입니다.
현재 `move_timeout_s=60`과 정리 시간을 고려한 값이며, 모터 속도를 낮춰 이동 제한을 늘리면
송신기의 `--timeout`도 그보다 충분히 길게 지정해야 합니다.

연결 끊김은 원격 정지 명령이 아닙니다. 이미 시작한 이동은 완료 또는 이동 시간 제한까지 처리합니다.
응답을 받지 못했어도 이동이 실행됐을 수 있으므로 더미 송신기는 자동 재전송하지 않습니다.
미완성 데이터는 다음 연결에 이어 붙이지 않으며, 정상 연결 종료 후에는 다시 접속할 수 있습니다.

### 자동 검증 범위

```powershell
python -m unittest discover -s tests -v
```

실제 루프백 TCP 소켓으로 정상·잘못된 값, 분할·병합 수신, 길이 초과, 미완성 입력,
수신 시간 초과, 재접속, 이동 실패 시 정리, 이동 중 연결 끊김을 검증합니다.
통합 테스트는 `main.py` 수신기와 `dummy_tcp_client.py` 송신기를 **별도 프로세스**로 실행하고,
현재 4바퀴 설정에 대한 정상값 5개의 모의 피드백 카운트와 잘못된 값 6개의 거부 응답을 확인합니다.
모터 시험 모드와 TCP를 함께 사용하는 경로도 모의 백엔드로 검증하며,
포트 바인딩 실패 시 준비·서보 ON 금지, 준비 실패 시 소켓 정리까지 확인합니다.
물리적으로 다른 두 PC 사이의 LAN 연결과 실제 모터 동작은 이 검증에 포함되지 않습니다.

## 최소·최대 위치 설정

```powershell
# 로컬 설정 파일이 없을 때만 모터 시험용 설정을 복사합니다.
if (-not (Test-Path axis_config.json)) {
    Copy-Item position_board/axis_config.utility2.json axis_config.json
}
```

`axis_config.json`의 다음 값을 실제 기구에 맞춰 수정하세요. 파일은 Git에서 제외됩니다.
동봉한 설정에는 최소 `-5598206`, 최대 `11179010`, `reverse_direction: true`,
속도 `419430 count/s`(약 6 rpm), 이동 제한 시간 `60초`를 반영했습니다. 기구에서 분리한 모터 시험용이며,
기존 설정 파일은 자동으로 변경되지 않습니다.

| 설정 | 의미 |
|---|---|
| `min_position_counts` | 위치 범위의 숫자상 최솟값; 현재 End -5598206 |
| `max_position_counts` | 위치 범위의 숫자상 최댓값; 현재 Start 11179010 |
| `reverse_direction` | `true`: 입력 0=최댓값, 1=최솟값. 생략 또는 `false`: 기존 0=최솟값, 1=최댓값 |
| `speed` | 보드에 설정된 속도 단위의 양의 정수; rpm으로 자동 환산하지 않음 |
| `acceleration_ms`, `deceleration_ms` | 가속·감속 시간 상수, 1~20000 ms |
| `position_tolerance_counts` | 도착 판정 허용 오차; 기본 10 카운트 |
| `move_timeout_s` | 한 번의 이동 완료 대기 시간 |
| `poll_interval_s` | 이동 상태 조회 간격; 기본 0.02초 |

카운트는 음수를 포함하는 signed 32-bit 범위를 지원합니다. 목표값을 한 회전으로
나누거나 순환시키지 않습니다. 설정은 실행 시 읽으며 변경 후 프로그램을 다시 시작합니다.
이 파일은 보드의 전자기어나 기계 원점을 변경하지 않습니다.

소프트웨어는 설정 범위 안의 목표만 보내며, 피드백이 설정 범위를 허용 오차보다 더 벗어나면
정지를 요청합니다. 이 폴링 검사는 보드의 스트로크 리미트나 기구의 정지 여유를 대체하지 않습니다.
현재 위치가 범위 밖이면 자동으로 안쪽으로 복귀하지 않고 이동을 차단합니다.

## MR-MC240N PCIe 사용

기구에서 분리한 현재 모터를 시험하려면 Utility2 등 다른 프로그램의 축 제어를 종료하고 서보 OFF 상태에서 실행합니다.

```powershell
# 읽기 전용 상태 확인
python main.py --motor-test --status

# 중간 → 1바퀴 이동 → 반대쪽으로 2바퀴 이동 → 중간 복귀, 서보 OFF
python main.py --motor-test --enable-motion 0.5 0.25 0.75 0.5

# 전체 범위 확인: 중간 → 최대 → 최소(4바퀴) → 중간 복귀
python main.py --motor-test --enable-motion 0.5 0 1 0.5

# 직접 입력: 0~1, status, q (이동 중에는 Ctrl+C로 정지·종료)
python main.py --motor-test --enable-motion
```

`--motor-test`는 보드 0 / 축 1 / 포인트 0과 로컬 `axis_config.json`을 사용합니다.
로컬 파일이 없으면 `position_board/axis_config.utility2.json`을 사용합니다.
READY 상태이면 Flash에서 저장된 파라미터를 읽고 MotorBench 프로젝트의 단위·전자기어·리미트·ABS 등록값을
검증한 뒤 서보 OFF 상태로 System Start합니다. 이미 RUNNING이면 설정을 읽어 검증합니다.
시험 속도는 `axis_config.json`의 `speed`로 지정하며 모터 시험 모드의 상한은 `4194304 count/s`(60 rpm)입니다.
기본 시험 속도는 `419430 count/s`(약 6 rpm)입니다. 속도를 낮추면 전체 범위 이동 시간에 맞춰 `move_timeout_s`도 늘려야 합니다.
서보 OFF 상태에서 보드 속도 상한 `0222/0223`도 지정한 속도에 맞춰 함께 기록하고 읽어서 검증합니다.
이는 실행 시 RAM에 적용되며 종료 후에도 재설정/재기동 전까지 유지됩니다. 현재 Flash의 기본 상한은 419430이고,
다음 시험 실행에서 다시 JSON 속도를 적용합니다. `speed`는 rpm이 아닙니다.
그 후 `--enable-motion`으로 서보를 켜고 피드백이 0.25초 이상 안정되면 이동합니다.
좌표 범위는 유지하며, 이동 중 1초마다 피드백과 남은 거리를 출력합니다.
`--status`는 Flash 읽기나 System Start도 실행하지 않습니다. 다른 축/서보가 동작 중이거나,
설정 불일치·ABS 오류·강제정지가 있으면 진행하지 않습니다. E400 등 장애 상태는 Utility2에서 원인을 확인해야 합니다.
기준점을 다시 등록하거나 Flash에 덮어쓰지 않습니다.

HG-KR13의 [엔코더 분해능은 한 회전당 4194304 pulse](https://www.mitsubishielectric.com/fa/products/faspec/point.page?formNm=HG-KR3_HG-KR13_6575&kisyu=%2Fservo&lang=2)입니다.
현재 전자기어 1:1에서 최대−최소는 **16777216 count = 4바퀴(1440도)**입니다.
중간 기준점 `2790402`에서 양쪽 끝까지 각각 2바퀴이며, `0.25 → 0.75`는 2바퀴입니다.
기본 속도 약 6 rpm에서 전체 구간 이동은 가감속을 포함해 약 41초입니다.
`0.5 → 0 → 1 → 0.5` 전체 명령은 총 8바퀴를 이동하므로 약 82초가 걸립니다. 제한 시간은 각 이동별로 적용됩니다.
`--motor-test`는 시작할 때 전체 각도와 환산 rpm을 표시하고, 각 이동에 목표/실측 이동량을 count와 도 단위로 표시합니다.
JSON의 최소·최대는 등록된 MotorBench 프로젝트의 소프트웨어 리미트와 일치해야 합니다.
첫 시험은 위의 중간 구간 왕복 명령을 권장합니다. 끝점에서 서보를 끄면 무부하 축이 조금 돌아
다음 실행 시 범위 검사에 걸릴 수 있습니다. 종료 시 좌표를 임의로 재등록하지는 않습니다.

현재 모터 단독 시험용 프로젝트는 [position_board/TunaHeadCut-MotorBench.pbp2](position_board/TunaHeadCut-MotorBench.pbp2)입니다.
기구에서 분리된 모터의 현재 위치를 시험용 중간 좌표 `2790402`로 등록하고,
Flash 저장·보드 소프트웨어 재시작 후 ABS 좌표 복원을 검증했습니다. 이후 손으로 축을 돌린 것을 사용자가 확인하여
현재 위치를 중간값으로 다시 등록하고 MotorBench 프로젝트와 Flash를 갱신했습니다.
[재등록 및 속도 적용 기록](position_board/backups/motor_bench_manual_rotation_20261013_134356.json).
이후 사용자 요청에 따라 기준점과 전자기어를 유지하며 범위를 4바퀴로 확장했습니다.
보드 리미트·속도·포인트 3개를 읽어 검증하고 Flash에 저장했습니다.
[4바퀴 설정 적용 기록](position_board/backups/four_turns_20261013_135505/apply_result.json).
새 범위의 실제 전원 OFF/ON 복원과 4바퀴 이동은 아직 시험하지 않았습니다.
최종 확인 상태는 RUNNING, 서보 OFF, 기준점 요청·ABS 오류·알람·강제정지 없음입니다.
기구 연결 후에는 실제 기구 기준으로 재보정해야 합니다.

Utility2 신규 기준점 등록용 초기 프로젝트는 [position_board/TunaHeadCut.pbp2](position_board/TunaHeadCut.pbp2)입니다.
파라미터·포인트 파일을 포함하며, 홈 센서 없이 최초 1회 Data set 방식으로 기준점을 등록하도록 구성했습니다.
[설정 순서](position_board/README.md)에 따라 ABS 배터리·배선과 최초 등록을 준비하고,
등록 후 보드에서 읽어 저장한 운용 파라미터로 전원 재투입 시 좌표를 복원합니다.
이 프로젝트의 속도 단위는 count/s이며 전용 Python 설정은 `position_board/axis_config.utility2.json`입니다.
CTR ClampRing의 PCIe 연결과 미연결 입력 설정을 반영했고, 현재 4바퀴 범위·1:1 전자기어·ABS 설정을
보드에 적용했습니다. 기준점 등록 후에는 MotorBench 운용 파일을 사용하며 초기 파일을 다시 전송하지 않습니다.
변경값과 실제 읽기 검증 결과는 [CTR 설정 비교](position_board/CTR_SETTINGS.md)에 기록했습니다.

1. Windows에 서로 호환되는 Mitsubishi Position Board Utility2 드라이버와 API DLL을 설치합니다.
   64-bit Python은 `mc2xxstd_x64.dll`, 32-bit Python은 해당 설치본의 32-bit API를 사용합니다.
2. Utility2에서 TunaHeadCut용 **1축 프로젝트**를 준비하고 앰프 축 번호, 전자기어, 속도 단위,
   리미트, 위치 기준을 설정합니다. CTR 원본의 전자기어·ABS·정지 입력 설정은 장비 조건에 맞춰 확인합니다.
3. System Start와 필요한 원점 설정을 완료합니다. 프로그램은 `RUNNING`, 원점 재설정 불필요,
   엔코더·서보 오류 없음, 강제정지·인터록 해제 상태를 요구합니다.
4. 이 앱에서 사용할 축의 포인트 번호 하나를 예약합니다. 기본은 축 1, 포인트 0입니다.
   포인트 오프셋은 0이어야 합니다. 실행 중 같은 축에 다른 프로그램이 명령을 보내면 안 됩니다.
5. 현재 위치를 읽고, 같은 좌표계의 최소·최대 카운트와 속도를 `axis_config.json`에 기록합니다.

`--hardware`는 Python의 비트 수에 맞는 정식 API Library를 자동으로 찾습니다.
`ProgramFiles(x86)`, `ProgramW6432`, `ProgramFiles` 아래의
`Position Board\MR-MC2XX\API Library\Library`를 확인하며 테스트 도구·샘플의 DLL은 선택하지 않습니다.
둘 이상의 설치본이 발견되거나 다른 경로에 설치했다면 `--dll`로 사용할 파일을 지정하세요.
명시한 DLL의 로딩이 실패해도 다른 DLL로 전환하지 않습니다. DLL을 프로젝트에 복사할 필요는 없습니다.

이 PC에서 확인한 설치본:

- Position Board Utility2 (for MR-MC2xx): 3.50
- API DLL: `C:\Program Files (x86)\Position Board\MR-MC2XX\API Library\Library\mc2xxstd_x64.dll`
- DLL 파일 버전: 2.4.0.0, Python: 64-bit

먼저 DLL 연결과 현재 보드 상태를 각각 확인할 수 있습니다.

```powershell
# DLL 로딩 및 필수 함수 확인: 보드에 연결하지 않음
python main.py --hardware --check-api

# 위치 조회: 설정 파일 없이 가능. System Start/Servo ON/이동을 보내지 않음.
python main.py --hardware --status

# 설정값과 실제 위치를 확인한 뒤 실행하는 이동 명령
python main.py --hardware --config axis_config.json --enable-motion 0.5

# 숫자를 생략하면 키보드 입력으로 연속 사용
python main.py --hardware --config axis_config.json --enable-motion

# 설치 경로를 직접 지정하는 경우
python main.py --hardware --dll 'C:\Program Files (x86)\Position Board\MR-MC2XX\API Library\Library\mc2xxstd_x64.dll' --status
```

`--check-api`는 선택한 DLL 경로, Python 비트 수, 포인트 구조체 크기를 JSON으로 출력합니다.
`--status`는 현재 상태를 JSON으로 출력하고 사용한 DLL 경로를 표준 오류에 표시합니다.
`system_running: false`이면 Utility2에서 프로젝트와 System Start를 먼저 준비하세요.
시스템 시작 전 읽은 위치는 이동 범위 보정값으로 사용하지 마세요.

기본 보드 ID는 0, 축은 1입니다. 필요하면 `--board-id`, `--axis`, `--point`를 지정합니다.
한 번에 선택한 한 축만 제어합니다. `--enable-motion`은 명시적으로 서보를 활성화합니다.
앱이 켠 서보는 종료 시 정지 확인 후 OFF합니다. 실행 전에 이미 ON이었던 서보는 ON을 유지합니다.
기구가 토크 해제 시 움직일 수 있다면 이 종료 동작을 고려해 지지·브레이크 구성을 준비해야 합니다.

일반 `--hardware` 실행은 원점 설정, 알람 리셋, 파라미터 전송, 강제정지 해제를 자동 수행하지 않습니다.
`--motor-test`의 예외는 위에서 설명한 Flash 복원·System Start·속도 상한 적용입니다.
이동 오류·시간 초과·Ctrl+C에는 정지를 요청하며, 급정지 확인이 실패하면 보드 전체의
소프트웨어 강제정지를 요청합니다. 정지 실패는 오류로 보고하고 정상 완료로 처리하지 않습니다.
통신·프로세스 장애 시 Python만으로 정지를 보장할 수 없으므로 하드웨어 정지 회로가 필요합니다.

## Python 코드에서 입력 연결

```python
from servo_axis import AxisConfig, NormalizedAxis
from mr_mc240n import MrMc240nBackend

config = AxisConfig.from_file("axis_config.json")
backend = MrMc240nBackend(axis_number=1)  # 설치된 API DLL 자동 선택

with NormalizedAxis(backend, config) as axis:
    axis.arm()
    received_value = 0.5  # 다른 Python 코드에서 받은 값
    feedback = axis.move_normalized(received_value)
    print(feedback)
```

`move_normalized()`는 목표의 실제 피드백 위치와 완료 상태를 확인할 때까지 기다립니다.
동작 중 매 순간 목표를 갱신하는 추종 제어가 아니라, 한 이동을 완료한 뒤 다음 입력을 처리하는
위치결정 방식입니다. 한 인스턴스를 한 제어 스레드에서 사용하세요. 중복 이동 요청은 거부합니다.
오류 후에는 원인을 해결하고 세션을 새로 생성해야 합니다.

## 구현과 검증

- `servo_axis.py`: 설정 검증, 0~1 변환, 이동 완료 확인, 모의 장치
- `mr_mc240n.py`: 설치 DLL 자동 탐색·로딩, 1축 PCIe API, 절대 포인트 설정·읽기 검증·시작
- `main.py`: 명령행 및 키보드 입력
- `tcp_input.py`: 줄바꿈 기반 TCP 수신, 입력 검증, 이동 완료 응답
- `dummy_tcp_client.py`: 다른 PC에서도 실행 가능한 더미 송신기
- `tests/`: 모의 장치/DLL 테스트; 실제 장비에 연결하지 않음

```powershell
python -m unittest discover -s tests -v
```

이 PC에서 설치 DLL의 로딩·함수 연결과 보드 0 / 축 1의 열기·상태 조회·닫기에 성공했습니다.
조회 당시 `system_running: false`, `servo_ready: false`, `position_counts: 0`이었습니다.
System Start, Servo ON, 실제 이동 명령은 이 검증에서 실행하지 않았습니다.
실제 이동에는 Utility2의 축 준비와 `axis_config.json`의 속도 설정이 필요합니다.

API 및 좌표 단위 참고:
[Mitsubishi MR-MC200/MR-MC300 API Library 매뉴얼](https://dl.mitsubishielectric.com/dl/fa/document/manual/ssc/ib0300225/ib0300225engl.pdf).
절대 위치는 `sscSetPointDataEx` / `sscAutoStart`, 피드백은 `sscGetCurrentFbPositionFast`를 사용합니다.
