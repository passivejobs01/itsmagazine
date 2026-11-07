# 원격 플레이 — Sunshine + Moonlight

미니PC에서 돌아가는 게임을 **폰·태블릿·노트북으로 끌어다 쓰는** 구성입니다.
영상에서 짧게 넘어간 **설치 커맨드와 순서**를 적어둡니다.

| 쪽 | 프로그램 | 설치 위치 |
|---|---|---|
| **호스트**(보내는 쪽) | **Sunshine** | 미니PC (Bazzite) |
| **클라이언트**(받는 쪽) | **Moonlight** | 폰 · 태블릿 · 노트북 |

> 🔑 **Sunshine은 "지금 켜져 있는 화면을 복사해서 보내는" 구조입니다.**
> 원격에서 새로 로그인하는 RDP 같은 방식이 아닙니다. 아래 전제조건이 여기서 나옵니다.

---

## 0. 🔴 시작 전에 — 2가지

### ① 미니PC에 **화면이 연결돼 있어야** 합니다

캡처할 화면이 없으면 Sunshine은 **기동 자체가 안 됩니다.** 포트도 열리지 않아서
Moonlight에는 *"호스트로부터 받은 영상이 없습니다"* 만 뜹니다.

```bash
grep -lx connected /sys/class/drm/*/status | wc -l   # 0이면 여기서 멈추세요
```

> 🔑 모니터 없이 구석에 둘 계획이라면 **HDMI 더미 플러그가 필수품**입니다. 몇천 원짜리
> 부품이고, 디스플레이 정보(EDID)를 내보내서 GPU가 모니터로 인식하게 합니다.
> (대안: `ujust setup-sunshine virtual-monitor`)

### ② 🔴 설치는 **미니PC 본체에서** — SSH로 하지 마세요

설치 중 **관리자 승인 창**이 뜨는데 원격 터미널에는 그 창을 띄울 수가 없습니다.
그런데 중단 처리가 없어서 **그 단계만 조용히 건너뛰고 끝까지 돌아갑니다.**

> ⚠️ 증상이 *"화면은 나오는데 게임패드가 안 먹는다"* 로 나타납니다. 본체에서 다시 하면 됩니다.

---

## 1. 설치 — 커맨드 한 줄

미니PC 본체에서 터미널을 열고:

```bash
ujust setup-sunshine enable-flatpak
```

비밀번호를 묻는 창이 뜨면 리눅스 계정 비밀번호를 입력합니다.

### 🔑 이 한 줄이 하는 일 — 딱 세 가지

| 하는 일 | 왜 필요한가 |
|---|---|
| `flatpak install --system dev.lizardbyte.app.Sunshine` | 앱 설치 |
| `additional-install.sh` 실행 | 🔴 **게임패드 권한** — udev 규칙 + `uhid` 모듈 |
| `systemctl --user enable --now` | **로그인할 때 자동 시작** |

> 🔑 **Flatpak으로 깔아야** KDE가 화면 캡처 권한을 **영구적으로** 줍니다. 다른 방식은
> 접속할 때마다 승인 창이 뜨거나, 권한을 못 받아 화면이 검게 나옵니다.

> ⚠️ 소프트웨어 센터(Discover)로 설치하면 **위 2·3번이 빠집니다.** 게임패드가 안 되고
> 자동 시작도 안 됩니다. **커맨드로 하는 이유가 이것입니다.**

인자 없이 `ujust setup-sunshine` 만 치면 메뉴가 뜹니다 — `status`(상태) ·
`portal`(설정 화면) · `virtual-monitor` · `update` · `disable` · `uninstall`.

> 🔴 **이미 설치한 기기에서 다시 실행할 때** — 이 명령은 시작할 때 서비스를 내립니다.
> **스트리밍 중이면 그 자리에서 끊깁니다.** (설정·페어링 정보는 건드리지 않습니다)

**게임패드 권한만 다시 넣으려면** 이 한 줄이면 됩니다. 중복 설치가 생기지 않습니다.

```bash
flatpak run --command=additional-install.sh dev.lizardbyte.app.Sunshine
```

---

## 2. 자동 로그인 (원격 전용으로 쓸 때)

Sunshine은 **데스크톱에 로그인된 상태**에서만 시작합니다. 재부팅 후 로그인 화면에
멈춰 있으면 Sunshine도 안 올라옵니다. → **시스템 설정 → 사용자 → 자동 로그인**

> ⚠️ 자동 로그인은 **그 기기에 손댈 수 있는 사람은 누구나 바로 들어온다**는 뜻입니다.
> 모니터·키보드를 늘 붙여 쓴다면 켤 필요가 없습니다.

---

## 3. 계정 만들기

미니PC 브라우저에서 **`https://localhost:47990`**

> ⚠️ **"연결이 비공개로 설정되어 있지 않습니다"** 경고는 자체 서명 인증서라 정상입니다
> → **고급 → 계속 진행**. 여기서 사용자명·비밀번호를 새로 만듭니다.

> 🔑 이건 **설정 화면 전용 계정**입니다. 리눅스 로그인 계정과 무관하고, Moonlight로
> 접속할 때도 쓰지 않습니다(그쪽은 PIN). **세 개가 다 다릅니다.**

---

## 4. Moonlight 설치 · 페어링

| 기기 | 방법 |
|---|---|
| **안드로이드 · iOS** | 앱스토어에서 **Moonlight Game Streaming** |
| **Windows** | `winget install MoonlightGameStreamingProject.Moonlight` |
| macOS · Linux | [공식 사이트](https://moonlight-stream.org/) |

1. Moonlight를 실행하면 **미니PC 이름이 목록에 자동으로** 뜹니다
   (안 보이면 **+** 로 `<미니PC_IP>` 직접 추가)
2. 선택하면 **4자리 PIN** → 설정 화면의 **PIN** 탭에 입력
3. 목록에 **Desktop** 이 나타납니다 → 선택하면 화면이 뜹니다

> ⚠️ **기동에 20~30초 걸립니다.** 재부팅 직후 목록에 안 보여도 조금 기다리세요.

---

## 5. 설치 확인

```bash
flatpak list | grep -i sunshine
systemctl --user is-active app-dev.lizardbyte.app.Sunshine.service
ls /etc/udev/rules.d/60-sunshine.rules /dev/uhid    # 게임패드 권한 (둘 다 있어야 함)
ss -tln | grep -E ':(47984|47989|47990|48010)'      # 정상이면 4개
```

| 결과 | 할 일 |
|---|---|
| `60-sunshine.rules` 또는 `/dev/uhid` 없음 | 🔴 **본체에서** `additional-install.sh` 다시 실행 (1번 끝) → 재부팅 |
| 포트가 하나도 없음 | 기동 실패 → **9번** |

---

## 6. 🔴 게임패드 — 순서를 틀리면 안 먹습니다

**Sunshine은 연결하는 그 순간** 클라이언트가 보고한 컨트롤러를 보고 호스트에 가상 패드를
만듭니다. 연결할 때 컨트롤러가 없었으면 **아예 안 만듭니다.**

| 순서 | 할 일 | 왜 |
|---|---|---|
| ① | **컨트롤러를 먼저 연결** | 클라이언트가 먼저 인식해야 보고됩니다 |
| ② | **Moonlight 연결** (연결돼 있으면 끊고 다시) | 🔴 스트리밍 중에 꽂으면 안 잡힙니다 |
| ③ | **그 다음** 게임·에뮬 실행 | 🔑 에뮬은 시작할 때 장치를 훑습니다 |
| ④ | 에뮬에서 **바인딩** | `Sunshine (libvirtualhid) ... Controller` 라는 **새 장치**로 잡힙니다 |

```bash
grep '^N: Name' /proc/bus/input/devices | grep -i controller
```

> 🔑 여기에 아무것도 안 나오면 **에뮬 설정을 아무리 만져도 안 됩니다.** ①②부터 다시.
> *"단축키는 되는데 게임 입력만 안 되는"* 상태가 딱 이 경우입니다.

---

## 7. 화질 · 지연 권장값

Moonlight 설정에서 조정합니다.

| 항목 | 권장 |
|---|---|
| 해상도·프레임 | **1080p / 60fps** |
| 비트레이트 | **20~30 Mbps** (1080p60 기준) |
| 코덱 | **H.264** — HEVC는 화질이 낫지만 디코딩 지연이 큽니다 |
| 창 모드(PC) | **Borderless windowed** — Alt+Tab과 가장 잘 맞습니다 |

### 🔴 블루투스 게임패드를 쓴다면 — Wi-Fi는 5GHz로

블루투스와 **2.4GHz Wi-Fi는 같은 주파수 대역**을 씁니다. 폰이 2.4GHz로 영상을 받으면서
동시에 블루투스로 패드 입력까지 받으면 **서로 간섭해서 끊기거나 입력이 밀립니다.**

> 🔑 **받는 기기는 5GHz(또는 6GHz)에 연결하세요.** 이것 하나로 대부분 해결됩니다.
> 호스트(미니PC)는 가능하면 **유선 랜**을 쓰세요.

---

## 8. (선택) Steam 빅 픽처로 바로 들어가기

접속하자마자 콘솔 같은 화면이 뜨게 하는 설정입니다.
설정 화면 → **Applications → Add New** 에서:

| 항목 | 값 |
|---|---|
| Application Name | `Steam Big Picture` |
| Command | `flatpak-spawn --host steam steam://open/bigpicture` |
| **Auto Detach** | 켜기 |

> ⚠️ Bazzite의 **게임 모드(Game Mode)** 는 별도 세션(gamescope)이라 Flatpak Sunshine으로는
> **캡처되지 않습니다.** 원격 플레이에는 **빅 픽처**를 쓰세요.

---

## 9. 문제 해결

```bash
tail -30 ~/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine/sunshine.log
```

| 로그에 보이는 것 | 의미 |
|---|---|
| `Couldn't load cuda` | ⚠️ **정상** — NVIDIA가 없어서 그렇습니다. 무시 |
| `AppImage and Flatpak do not support KMS capture` | ⚠️ **정상** — 다른 방식으로 넘어갑니다 |
| `KMS monitor list` 사이가 **비어 있음** | 🔴 **화면이 없습니다** → 0번 ① |
| `Loaded portal restore token` 에서 **멈춤** | 화면 캡처 권한 요청이 응답을 못 받는 중 ↓ |
| `Trying encoder [vulkan]` 이후 진행 없음 | 인코더 탐색이 멈춘 상태 ↓ |

**권한 요청에서 멈출 때** — ① 미니PC 화면에 승인 창이 떠 있으면 허용
② 🔑 **짧은 시간에 여러 번 재시작하지 마세요.** 알림이 몰리면 요청이 묶여서 멈춥니다
(재시작 사이에 **30초 이상**) ③ 그래도 안 되면 **재부팅**이 가장 빠릅니다.

**인코더 탐색이 멈출 때** — AMD 기기인데 NVIDIA 인코더부터 뒤지는 경우입니다.
하드웨어 인코딩이 되는지 보고, 된다면 고정해서 탐색을 건너뜁니다.

```bash
vainfo | grep EncSlice      # VAProfileH264High : VAEntrypointEncSlice 가 보이면 가능
echo 'encoder = vaapi' >> ~/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine/sunshine.conf
systemctl --user restart app-dev.lizardbyte.app.Sunshine.service
```

> ⚠️ **모든 기기에 필요한 설정이 아닙니다.** 보통은 자동 선택이 잘 됩니다. 위 증상이
> 있을 때만 적용하세요. (NVIDIA 기기라면 `nvenc`)

**다른 PC에서 설정 화면이 안 열릴 때** — `https://<미니PC_IP>:47990` 으로 들어가면
**CSRF 보호에 막혀** 실패합니다(`Internal Server Error` / `CSRF Protection Error`).
**비밀번호 문제가 아닙니다.** 기본적으로 `localhost` 접근만 허용하기 때문입니다.

```bash
echo 'csrf_allowed_origins = https://<미니PC_IP>,https://<미니PC_IP>:47990' \
  >> ~/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine/sunshine.conf
systemctl --user restart app-dev.lizardbyte.app.Sunshine.service
```

> 💡 **대부분은 필요 없습니다.** 처음 한 번 본체에서 설정해두면 들어갈 일이 거의 없습니다.

---

## Moonlight 단축키 (PC 클라이언트)

전부 **`Ctrl + Alt + Shift` + 아래 키**입니다.

| 키 | 동작 |
|---|---|
| **`Z`** | 마우스·키보드 캡처 해제/복귀 (스트림은 유지) |
| `Q` | 스트림 종료 |
| `X` | 전체화면 ↔ 창 모드 |
| **`S`** | **성능 통계 표시** (프레임·지연·유실) |
| `V` | 클립보드 텍스트를 호스트에 붙여넣기 |

> 💡 "시스템 단축키 캡처"가 꺼져 있으면(기본값) **그냥 Alt+Tab** 으로도 빠져나올 수 있습니다.

---

## ⚠️ 집 밖에서 쓸 때

Sunshine 포트를 **공유기에서 인터넷으로 열지 마세요.** VPN(WireGuard · Tailscale)으로
집 네트워크에 들어온 다음 접속하면 추가 설정 없이 그대로 됩니다.

> 🔴 **공유기에서 미니PC의 IP를 예약해두세요.** VPN으로 들어올 때는 이름으로 호스트를
> 자동으로 찾지 못하고 **저장된 IP로 바로 접속**합니다. IP가 바뀌면 밖에서 막히고,
> 복구하려면 그 기기가 있는 곳으로 가야 합니다.

---

## 참고

- [Sunshine 공식 문서](https://docs.lizardbyte.dev/projects/sunshine/latest/)
- [Moonlight 공식 사이트](https://moonlight-stream.org/)
- [Bazzite — Sunshine 설정 문서](https://docs.bazzite.gg/Advanced/sunshine/)
