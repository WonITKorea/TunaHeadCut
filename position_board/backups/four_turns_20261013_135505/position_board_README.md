# TunaHeadCut: 홈 센서 없는 절대좌표 설정

## 현재 모터 단독 시험 설정

**현재는 [TunaHeadCut-MotorBench.pbp2](TunaHeadCut-MotorBench.pbp2)를 사용합니다.**
사용자가 모터가 기구에서 분리되어 있고 임의 기준점을 등록해도 된다고 확인하여,
Data set 방식으로 현재 모터 위치를 `2790402`에 등록했습니다. 실제 기구의 Start/End 보정값은 아닙니다.
서보 OFF 후 분리된 모터의 위치가 조금 변하므로, 양끝 대신 범위 중간을 기준 좌표로 사용했습니다.
입력 0의 목표 `2794600`, 입력 1의 목표 `2786203`, 전자기어 1:1은 유지합니다.
프로젝트/Flash의 기본 속도 상한은 200 count/s입니다. Python 모터 시험은 실행 시 JSON의 속도를
보드 상한에 적용하며, 현재 로컬 설정은 20000 count/s입니다.

2026-10-13 13:43, 사용자가 축을 손으로 돌렸다고 확인하여 현재 위치를 `2790402`로 다시 등록했습니다.
MotorBench 프로젝트와 Flash의 ABS 기준값을 갱신했고, 속도 상한 20000의 RAM 적용·읽기를 검증했습니다.
등록 직후 서보 OFF 피드백은 `2790426`, 알람·원점 요청·ABS 오류 없음입니다.
[현재 기준점과 속도 적용 기록](backups/motor_bench_manual_rotation_20261013_134356.json).
아래 복원 기록은 이번 재등록 전 최초 설정에 대한 것입니다.

- 보드가 생성한 ABS 기준 데이터와 `0241=0001`을 실제 읽어서 MotorBench 프로젝트에 저장했습니다.
- 현재 기준 좌표 `0246/7=0x002A9402`, 기준 데이터 `024D=4313`, `024E=77C3`, `024F=003F`(16진수).
- Flash ROM 저장 후 보드 소프트웨어 재시작 → Flash 읽기 → System Start로 복원을 검증했습니다.
- 검증 전후 피드백 `2791185 → 2791187`, 차이 2 count; 파라미터 1054개 일치.
- 최종 상태: RUNNING, `home_required=false`, `encoder_error=false`, 알람·경고·강제정지 없음, 서보 OFF.
- 기록: [등록 데이터](backups/motor_bench_registered_parameters.json), [Flash 복원 검증](backups/motor_bench_flash_restore.json).

새 기준점에서 실제 앰프·PC 전원을 끄고 켜는 시험과 20000 count/s 왕복 이동은 아직 수행하지 않았습니다.
재기동 시 System Start 전에 MotorBench 파라미터를 전송하거나 Flash 파라미터를 읽어야 합니다.
Flash 파라미터 저장은 포인트 테이블 저장과 별개입니다. Utility2 시험에는 동봉한 MotorBench 포인트 파일도 전송합니다.
Python은 이동 직전 사용할 포인트를 직접 기록합니다.

**기준점 등록은 매번 다시 실행하지 않습니다. `TunaHeadCut.prm2` 초기 파일을 다시 전송하면 등록 데이터가 사라집니다.**
기구에 모터를 연결할 때는 실제 기구 기준으로 다시 보정하고 외부 정지·리미트 입력을 구성합니다.
현재 입력 미연결 설정은 모터 단독 시험 조건입니다.

## 기구 연결 후 새 기준점을 등록할 초기 프로젝트

Utility2에서 **`TunaHeadCut.pbp2`**를 엽니다. `.prm2`, `.pnt2`, `.adr2`가 같은 폴더에 있어야 합니다.
설치된 Utility2 3.50의 정식 템플릿으로 만든 **최초 기준점 등록용 프로젝트**입니다.
홈 센서 탐색 대신 확인된 위치를 한 번 등록하고, 절대엔코더와 저장한 기준 데이터로 재기동 시 좌표를 복원합니다.
파일 구조와 설정값을 검증했고, 설치된 API DLL로 보드 RAM에 전송한 뒤 System Start와 읽기 검증을 완료했습니다.
Utility2 화면에서 열기와 실제 이동은 아직 검증하지 않았습니다.
CTR ClampRing의 PCIe 연결·Test 모드·파일 연결 구성을 반영했습니다.
사용자가 기존 카운트·1:1 전자기어 유지 및 외부 정지·리미트 입력 미연결을 확인했습니다.
이 초기 파일에는 유효한 기준점 복원 데이터가 없습니다. 위 MotorBench 파일은 등록을 마친 별도 프로젝트입니다.
최초 ABS 활성화 때 발생했던 `AL.25.1`은 이후 앰프 재기동·보드 재설정과 모터 기준점 등록을 거쳐 해제되었습니다.
CTR 원본과의 차이는 [CTR_SETTINGS.md](CTR_SETTINGS.md)를 참조하세요.

## 반영한 구성

| 항목 | 설정 |
|---|---|
| 보드 / ID / 채널 | MR-MC240N / 0 / 1 |
| Utility2 연결 / 모드 | CTR과 동일한 PCIe (`CONNECTION=0`) / Test (`TOOL_MODE=1`) |
| 제어 축 | 축 1만 사용, SSCNET Line 1의 앰프 축 d1 |
| 앰프 | MR-J4-B 계열 (`021D=0000`, `021E=1000`) |
| 제어 옵션 | `0200=1001`: 실제 축 제어, count/s, 기준점 유효성 확인 |
| 절대위치 검출 | `1102(PA03)=0001`: ABS 사용 |
| 최초 기준점 등록 방식 | `0240=0002`: Home position return의 Data set 방식, DOG 탐색 없음 |
| 등록할 기준 좌표 | `0246=A468`, `0247=002A`: Start 2794600 |
| 원점 이동량 | `0248/0249=0` |
| 최초 복원 데이터 | `0241=0000`, `024D~024F=0`: 아직 등록되지 않음 |
| 전자기어 | `020A/B=1`, `020C/D=1`: 1:1, 설치 템플릿·현재 보드와 동일 |
| 속도 배율 | `020E/F=1` |
| 속도 / 속도 상한 | 200 count/s (`0222/0223=200`) |
| 소프트웨어 스트로크 리미트 | 하한 2786203 / 상한 2794600 (`0228~022B`); 기준점 등록 완료 후 유효 |
| 센서 입력 | `0219=0303`: 미연결, LSP·LSN·DOG 감지하지 않음 |
| 외부 강제정지 입력 | `000E=5AE1`: 보드 EMI 입력 미사용; 소프트웨어 SEMI는 유효 |
| 앰프 강제정지 입력 | `1103(PA04)=2100`: EM2/EM1 입력 미사용 |
| 포인트 0 | Start 2794600 |
| 포인트 1 | 중간 2790402 |
| 포인트 2 | End 2786203 |
| 포인트 속도 / 가속 / 감속 | 200 / 500 ms / 500 ms |
| 축 1 포인트 오프셋 | 0 |

파라미터 의미는 [Mitsubishi Details 매뉴얼](https://www.mitsubishielectric.com/dl/fa/document/manual/ssc/ib0300223/ib0300223engl.pdf)의 4.5, 5.8, 6.21절을 확인했습니다.
다른 축과 미사용 포인트는 설치 템플릿 값입니다. Python은 기본 포인트 0을 매 이동마다 덮어씁니다.
Utility2에서 포인트 0~2 전체를 연속 실행하면 세 위치를 순서대로 이동하므로, 최초 확인은 단일 포인트로 합니다.

## 최초 1회 설정

절대위치 유지용 배터리가 MR-J4-B에 정상 연결되어 있어야 합니다. 소프트웨어 설정만으로 전원 OFF 중 엔코더 데이터를 보존할 수는 없습니다.

1. `TunaHeadCut.pbp2`를 열고 보드 0 / 채널 1 / 축 1, MR-J4-B / Line 1 / d1을 확인합니다. 현재 EMI·EM2·LSP·LSN은 미연결 입력을 무시하는 시험 설정입니다. 실제 운전 전 독립적인 비상정지 회로를 갖추고, 배선에 맞춰 입력 설정과 스트로크 리미트를 검증합니다.
2. 파라미터를 전송하고 System Start를 실행합니다. PA03을 처음 활성화할 때 AL.25가 발생하면 매뉴얼의 최초 ABS 설정 절차에 따라 앰프 전원을 재투입하고 시스템을 다시 시작합니다.
3. 기구를 **실제로 알고 있는 Start 위치**에 맞춰 정지시킵니다. 이 초기 파일은 그 위치에 `2794600`을 부여하도록 준비한 것입니다. 현재 위치가 Start인지 확인되지 않았다면 등록을 실행하지 마세요. 소프트웨어 강제정지가 켜져 있다면 현장 정지 수단과 위치 확인을 마친 뒤 명시적으로 해제해야 합니다. Python은 이를 자동 해제하지 않습니다.
4. Utility2에서 **Home position return**을 실행합니다. 선택된 방식 `0240=0002`가 현재 위치를 기준으로 등록합니다. 별도 기능인 **Home position reset / Data set function**은 초기 등록 전에 실행하면 90H 오류가 날 수 있으므로 최초 등록에 사용하지 않습니다.
5. 등록 완료 후 `ZP=ON`, `ZREQ=OFF`, `ABSE=OFF`와 현재 피드백 `2794600`을 확인합니다. 보드가 생성한 `024D~024F` 및 `0241`의 복원 유효 비트도 확인합니다.
6. **보드에서 실제 파라미터를 읽어** 별도 운용 프로젝트로 저장합니다. 예: `TunaHeadCut-Calibrated.pbp2`. 로컬 초기 파일을 그대로 저장하는 것만으로는 새 기준 데이터가 들어가지 않습니다. 선택적으로 이 상태의 파라미터를 보드 Flash ROM에도 백업합니다.

기준점 등록 전에는 `0241=0000`이 정상입니다. 임의로 `1`로 바꾸거나 `024D~024F`를 추측해서 넣지 않습니다.
기존 숫자가 엔코더 원시값이었다면, 이 등록 과정은 그 값과 기계 위치의 관계를 새로 확정하는 작업입니다. Start와 End의 대응도 실제 기구에서 확인해야 합니다.

## 이후 전원 재투입

1. System Start **전에** 등록 후 저장한 운용 파라미터를 전송하거나 보드의 Flash ROM 파라미터 읽기를 실행합니다. 기준 좌표 `0246/0247`, 기준 엔코더 데이터 `024D~024F`, 유효한 `0241`이 함께 복원되어야 합니다.
2. System Start 후 `system_running: true`, `home_required: false`, `encoder_error: false` 및 현재 피드백을 확인합니다. 기구가 움직이지 않은 상태의 전원 재투입 전후 좌표가 허용 오차 안에서 같은지 검증합니다.
3. 정상 복원이 확인되면 Python에서 절대 위치 명령만 사용합니다. 기준점 등록은 매번 실행하지 않습니다.

**등록 후에는 이 초기 프로젝트를 다시 전송하지 마세요.** 아직 유효한 기준 데이터가 없는 파일입니다. 재기동에는 보드에서 읽어 저장한 운용 프로젝트를 사용합니다.
배터리 소실 등으로 기준이 유효하지 않으면 `home_required`/`encoder_error`로 이동이 차단됩니다. 이는 홈 센서를 요구한다는 뜻이 아니라 좌표 기준을 확인·재등록해야 한다는 뜻입니다.
일반 `--hardware` 실행은 Flash 읽기나 System Start를 하지 않습니다.
명시적인 `--motor-test --enable-motion` 실행은 READY 상태에서 저장된 Flash 파라미터를 읽고
MotorBench의 단위·전자기어·범위·ABS 기준 데이터와 대조한 후 System Start합니다.
RUNNING이면 단위·전자기어·범위·ABS 기준을 읽어 검증합니다.
두 상태 모두 서보 OFF 상태에서 `speed`를 보드 속도 상한 `0222/0223`에 함께 기록하고 읽어 확인합니다.
속도는 `1~20000 count/s`이며, 이 적용은 RAM에 남고 Flash에는 저장하지 않습니다.
기준점 등록·알람 리셋·강제정지 해제는 프로그램 실행 때 자동 수행하지 않습니다.
이 순서는 [Mitsubishi API 매뉴얼의 Flash 읽기 절차](https://www.mitsubishielectric.com/dl/fa/document/manual/ssc/ib0300225/ib0300225engj.pdf#page=50)를 따릅니다.

## Python 실행

프로젝트 루트 `C:\Users\user\TunaHeadCut-1`에서 실행합니다.

```powershell
# 읽기 전용 상태 확인 (복원/서보 ON 없음)
python main.py --motor-test --status

# 모터 단독 왕복 시험: 중간 → 25% → 75% → 중간, 종료 시 서보 OFF
python main.py --motor-test --enable-motion 0.5 0.25 0.75 0.5

# 대화형 시험: 0~1을 입력, q로 종료, 이동 중 Ctrl+C로 정지·종료
python main.py --motor-test --enable-motion

# TCP 입력으로 제어
python main.py --hardware --config position_board/axis_config.utility2.json --enable-motion --tcp-server
```

`--motor-test`는 기본적으로 루트의 `axis_config.json`을 읽고, 없으면 동봉한 설정을 읽습니다.
전체 서보 OFF 및 정지 상태가 필요합니다. 다른 프로그램이 같은 축을 제어하지 않도록 합니다.
이동 중 피드백이 1초마다 표시됩니다. 첫 시험은 끝점 대신 위의 중간 구간 왕복을 사용하면
서보 OFF 후 무부하 축의 작은 이완 때문에 범위를 벗어나는 것을 줄일 수 있습니다.

동봉한 `axis_config.utility2.json`은 200 count/s, 현재 루트 `axis_config.json`은 20000 count/s입니다.
위치 범위와 이동 제한 시간 60초는 같습니다. 200 count/s에서는 8397 count의 등속 이동만 약 42초가 필요합니다.
JSON 속도만 올리면 보드 상한에 제한되므로, 모터 시험 모드는 두 값이 일치하도록 적용합니다.
[Mitsubishi 속도 제한 및 가감속 설명](https://www.mitsubishielectric.com/dl/fa/document/manual/ssc/ib0300223/ib0300223engl.pdf#page=153)에 따라
상한 두 워드를 한 번에 기록합니다. 짧은 이동에서는 가감속 때문에 지정 속도에 도달하지 않을 수 있습니다.
Utility2의 속도 단위나 배율을 변경하면 이 속도 해석도 바뀝니다.

## 현재 오류와의 관계

- `system is not RUNNING`: 파라미터 전송 및 System Start 후 `system_running: true`인지 확인합니다.
- `feedback position is outside ...`: 복원한 피드백과 Start/End의 좌표 기준을 확인합니다. System Start 전의 위치 0에 맞춰 범위를 넓히지 않습니다.
- `home_required`: 최초 Data set 방식 등록 또는 저장된 절대위치 기준 데이터 복원이 필요합니다.
- `servo_alarm` / `encoder_error`: 알람 번호를 확인합니다. `AL.25.1`이면 최초 ABS 설정 절차에 따라 배터리·앰프 전원 재투입을 확인하고 기준점을 등록해야 합니다. 현재 모터 시험 설정에서는 해제되어 있습니다.
- `forced_stop`: 외부 또는 소프트웨어 강제정지 상태를 확인합니다. MotorBench 설정 완료 시에는 OFF이며, Python은 켜진 강제정지를 자동 해제하지 않습니다.

원본 템플릿: `C:\Program Files (x86)\Position Board\MR-MC2XX\PbTest\Template\MR-MC2XX\ENU`.
