# 웹캠 하나로 IP 카메라 만들기 — 홈캠 시스템 세팅 가이드

2만 원짜리 USB 웹캠을 **IP 카메라로 바꾸고**, 문이 열리면 **텔레그램으로 사진**이 오게 만드는 전체 과정입니다.
윈도 PC 한 대에서 전부 돌아갑니다.

> 영상: **기컴 IT13 MAX 리뷰 — 웹캠 홈캠 시스템 만들기** · [잇츠매거진](https://www.youtube.com/@itsmagazine)

---

## 완성 구조

```
┌─ Windows 11 PC ─────────────────────────────────┐
│                                                  │
│   [웹캠] ──→ [go2rtc] ────────────────────────────→ 외부 인터넷
│                 :1984 (스냅샷·웹UI)                │
│                 :8554 (RTSP)                      │
│                    │ 스냅샷                        │
│   ┌─ VirtualBox ───┼──────────────────────────┐   │
│   │  ┌─ UmbrelOS ──┼───────────────────────┐  │   │
│   │  │   [Home Assistant] ←────────────────┼──┼───┼── Tuya 게이트웨이
│   │  │        │                            │  │   │   (문 열림 센서)
│   │  └────────┼────────────────────────────┘  │   │
│   └───────────┼───────────────────────────────┘   │
└───────────────┼───────────────────────────────────┘
                └──→ 텔레그램 (스마트폰)
```

| 구성요소 | 역할 |
|---|---|
| **go2rtc** | USB 웹캠을 IP 카메라(RTSP·HTTP 스냅샷)로 바꾼다 |
| **FFmpeg** | go2rtc 가 호출하는 인코더. **없으면 조용히 실패한다** |
| **VirtualBox** | 윈도 위에서 홈서버 OS를 돌린다 |
| **UmbrelOS** | 홈서버 OS. 앱스토어로 Home Assistant 설치 |
| **Home Assistant** | 문 센서 감지 → 스냅샷 → 텔레그램 |

---

## 세팅 순서

**순서가 중요합니다.** 특히 4번을 건너뛰면 뒤에서 원인 찾기가 어렵습니다.

| # | 단계 | 비고 |
|---|---|---|
| 1 | FFmpeg 설치 | go2rtc 보다 **먼저** |
| 2 | go2rtc 내려받기 | 실행파일 1개 |
| 3 | **웹캠 장치 이름 확인** | [⬇ 명령어](#1-웹캠-장치-이름-확인) |
| 4 | 🔴 **지원 해상도·코덱 조합 확인** | [⬇ 명령어](#2-지원-조합-확인--가장-중요한-단계) · **여기서 안 보고 가면 헤맵니다** |
| 5 | `go2rtc.yaml` 작성 → 실행 | [⬇ 설정](#3-go2rtcyaml) |
| 6 | 방화벽 규칙 등록 | [⬇](#방화벽) |
| 7 | IP 고정 (공유기 DHCP 예약) | 호스트 · VM **둘 다** |
| 8 | VirtualBox + UmbrelOS 설치 | |
| 9 | Home Assistant 설치 → 센서 연결 | |
| 10 | Generic Camera 등록 | [⬇ 주소](#home-assistant-에-넣을-주소) |
| 11 | 자동화 1개 작성 | [⬇ 예시](#자동화-문-열림--스냅샷--텔레그램) |

---

## go2rtc 세팅 — 실제로 썼던 명령어

### 1. 웹캠 장치 이름 확인

go2rtc 설정에 **정확한 장치 이름**을 넣어야 합니다. 먼저 이름부터 확인합니다.

```bat
ffmpeg -list_devices true -f dshow -i dummy
```

출력에서 `(video)` 로 끝나는 줄이 웹캠입니다.

```
[dshow @ ...] "Cox cpc1080" (video)
[dshow @ ...] "마이크 (Realtek Audio)" (audio)
```

> ⚠️ `-list_devices` 뒤의 **`true` 를 빼먹으면** 안 됩니다. 빼면 ffmpeg 이 다음 인자(`-f`)를
> 값으로 먹어버려 엉뚱한 오류가 납니다.

### 2. 지원 조합 확인 — 🔴 가장 중요한 단계

**웹캠마다 지원하는 해상도·프레임·코덱 조합이 다릅니다.** 이걸 안 보고 설정을 쓰면
*"설정은 맞는 것 같은데 화면이 안 나온다"* 로 한참 헤맵니다.

```bat
ffmpeg -list_options true -f dshow -i video="Cox cpc1080"
```

실제 출력(요약):

| 코덱 | 1920x1080 | 1280x960 | 1280x720 | 800x600 | 640x480 |
|---|---|---|---|---|---|
| **mjpeg** | 30 | 30 | 30 | 30 | 30 |
| yuyv422 | 5 | 7.5 | **10** | 20 | 30 |

여기서 두 가지를 읽어야 합니다.

**① `input_format=mjpeg` 을 반드시 넣는다**
안 넣으면 ffmpeg 이 기본값으로 **yuyv422(무압축)** 를 고릅니다.
이 카메라는 720p 가 **30fps → 10fps** 로 떨어집니다.

**② 프레임레이트를 표에 있는 값으로 쓴다**
이 카메라는 **전 해상도 30fps 고정**이었습니다. `framerate=15` 로 썼더니 실패했습니다.

```
[dshow] Could not set video options
```

### 3. `go2rtc.yaml`

`go2rtc.exe` 와 **같은 폴더**에 둡니다.

```yaml
api:  { listen: ":1984" }     # 웹 UI · 스냅샷 API
rtsp: { listen: ":8554" }     # RTSP 스트림
log:  { level: info }

ffmpeg:
  bin: C:/ffmpeg/bin/ffmpeg.exe      # ← FFmpeg 경로 (아래 참고)

streams:
  webcam: ffmpeg:device?video=Cox cpc1080&input_format=mjpeg&video_size=1280x720&framerate=30#video=h264
```

`video=` 뒤에는 **1번에서 확인한 장치 이름을 그대로** 넣습니다.

### 4. 실행

```bat
cd /d C:\go2rtc
go2rtc.exe
```

> 🔴 **`cd /d` 가 중요합니다.** go2rtc 는 **현재 작업 디렉터리에서 `go2rtc.yaml` 을 찾습니다.**
> 다른 위치에서 실행하면 설정을 못 읽고 **기본값으로 조용히 뜹니다.**

### 5. 확인

브라우저에서 열어봅니다.

```
http://localhost:1984                       웹 UI (스트림 목록)
http://localhost:1984/api/frame.jpeg?src=webcam    스냅샷 1장
```

---

## 🔴 가장 많이 막히는 지점 — 스냅샷이 0바이트

**HTTP 200 인데 이미지가 비어 있는** 증상입니다. 성공처럼 보여서 원인을 엉뚱한 데서 찾게 됩니다.

| `ffmpeg.bin` 경로 | 결과 |
|---|---|
| 정상 | 200 · 22,396 바이트 |
| 잘못됨 | **200 · 0 바이트** |

원인은 로그에만 나옵니다.

```
fork/exec ... : cannot find the path specified
```

> 🔑 **go2rtc 윈도 바이너리에는 FFmpeg 이 들어 있지 않습니다.**
> (도커 이미지에는 포함돼 있어 혼동하기 쉽습니다)
> `ffmpeg:device` 소스는 **외부 FFmpeg 실행파일을 불러서** 돌립니다.
> 반드시 따로 받아서 `ffmpeg.bin` 에 경로를 적거나 PATH 에 넣어야 합니다.

점검:
```bat
ffmpeg -version
```

---

## 해상도 선택

| | 체감 |
|---|---|
| 1920x1080 @30 | 동작하지만 **약간 끊김** |
| **1280x720 @30** | ✅ **부드러움 — 이걸로 확정** |

픽셀 수가 2.25배 줄어 h264 인코딩 부하도 그만큼 내려갑니다.
문 열림 스냅샷·짧은 확인 영상 용도에는 720p 로 충분합니다.

---

## 방화벽

**손으로 실행하면 윈도가 알아서 허용 규칙을 만들어 줍니다.** 문제는 **자동 실행**입니다.
작업 스케줄러는 비대화형이라 허용 프롬프트가 뜨지 않고, HA 에서는 *"카메라 연결 안 됨"* 으로만 보입니다.

미리 박아둡니다 (관리자 PowerShell):

```powershell
New-NetFirewallRule -DisplayName "go2rtc (LAN only)" -Direction Inbound `
  -Protocol TCP -LocalPort 1984,8554 -Action Allow -Profile Any `
  -RemoteAddress LocalSubnet
```

`-RemoteAddress LocalSubnet` 으로 **같은 공유기 안에서만** 열립니다.

---

## 부팅 시 자동 실행 (선택)

`C:\go2rtc\start-go2rtc.cmd`
```bat
@echo off
cd /d C:\go2rtc
go2rtc.exe >> logs\go2rtc.log 2>&1
```

```powershell
schtasks /Create /TN "go2rtc" /TR "C:\go2rtc\start-go2rtc.cmd" /SC ONLOGON /RL HIGHEST /F
```

> 여기서도 **`cd /d` 를 빼면** 설정 파일을 못 찾습니다.

---

## Home Assistant 에 넣을 주소

**설정 ▸ 기기 및 서비스 ▸ 통합 추가 ▸ Generic Camera**

```
Still Image URL : http://<PC의 LAN IP>:1984/api/frame.jpeg?src=webcam
Stream Source   : rtsp://<PC의 LAN IP>:8554/webcam
```

> `localhost` 가 아니라 **LAN IP** 를 씁니다. HA 는 가상 머신 안에 있어서
> 호스트 PC 입장에서는 **외부 기기**입니다.
>
> 🔴 **IP 를 고정하세요.** 공유기 DHCP 예약으로 **호스트 PC 와 VM 둘 다** 묶어둡니다.
> IP 가 바뀌면 카메라도 자동화도 조용히 끊깁니다.

---

## 자동화: 문 열림 → 스냅샷 → 텔레그램

**설정 ▸ 자동화 및 장면 ▸ 자동화 만들기** · YAML 모드

```yaml
alias: 사무실 문 열림 — 웹캠 사진 알림
triggers:
  - trigger: state
    entity_id: binary_sensor.<문_센서_엔티티>
    from: "off"
    to: "on"
conditions: []
actions:
  - action: telegram_bot.send_photo
    data:
      entity_id: notify.<텔레그램_봇_엔티티>
      url: http://<PC의 LAN IP>:1984/api/frame.jpeg?src=webcam
      caption: "🚪 문이 열렸습니다 {{ now().strftime('%H:%M:%S') }}"
  - delay:
      minutes: 1
mode: single
```

**포인트 두 가지**

- `telegram_bot.send_photo` 는 **`url` 을 직접 받습니다.** 파일로 저장했다가 보낼 필요가 없어,
  Generic Camera 없이도 go2rtc 주소만으로 동작합니다
- 끝의 `delay: 1분` + `mode: single` 이 **알림 폭주를 막습니다.**
  1분 안에 문이 다시 열려도 건너뜁니다

> ⚠️ HA **2026.9** 부터 `target:` 이 `entity_id:` 로 바뀌었습니다. 옛 예제를 그대로 쓰면 실패합니다.

---

## 내려받을 곳

| | |
|---|---|
| go2rtc | https://github.com/AlexxIT/go2rtc/releases — `go2rtc_win64.zip` |
| FFmpeg | https://www.gyan.dev/ffmpeg/builds/ — `release-essentials` |
| VirtualBox | https://www.virtualbox.org/wiki/Downloads — 개인 사용 무료 |
| UmbrelOS | https://umbrel.com/umbrelos — **가상 머신용 이미지** |

---

## 정리 — 처음 하면 꼭 걸리는 것 4가지

1. **FFmpeg 을 안 깔았다** → 스냅샷이 200/0바이트. 로그에만 원인이 나온다
2. **`-list_options` 를 안 봤다** → 지원하지 않는 해상도·fps 를 적어 실패
3. **`input_format=mjpeg` 을 뺐다** → 프레임이 30 에서 10 으로 떨어진다
4. **다른 폴더에서 실행했다** → `go2rtc.yaml` 을 못 찾고 기본값으로 뜬다

네 가지 모두 **"에러는 안 나는데 안 되는"** 유형이라, 알고 시작하면 시간을 크게 아낍니다.
