# YT Player

Windows 10/11 x64용 YouTube 다운로드·재생기. Python 3.12 + PySide6 + mpv.

## 실행

프로젝트 루트의 `YTPlayer.exe`를 더블 클릭하면 실행됩니다.
이 파일은 `dist/YTPlayer/YTPlayer.exe`를 여는 실행기이므로 `dist` 폴더를 함께 유지하세요.
실행기만 다시 만들려면 `powershell -ExecutionPolicy Bypass -File scripts/build_launcher.ps1`을 실행합니다.

완성된 포터블 배포본은 `dist/YTPlayer/YTPlayer.exe`를 실행합니다.
공유용 압축 파일은 `dist/YTPlayer-0.1.0-win-x64.zip`입니다.
`YTPlayer` 폴더 전체를 함께 이동하세요. Python 설치는 필요하지 않습니다.
앱 폴더는 쓰기 가능한 위치에 두세요.

개발 환경에서는 PowerShell에서 다음을 실행합니다. [uv](https://docs.astral.sh/uv/)가 필요합니다.

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.tmp\uv-cache'
uv sync --frozen --group dev
uv run python scripts/bootstrap_tools.py
uv run python -m yt_player
```

도구 준비 스크립트는 공식 최신 안정 릴리스의 yt-dlp·mpv·Deno와 Gyan의
FFmpeg 안정 빌드를 다운로드하고 게시된 SHA-256을 검증합니다.
다운로드 캐시는 `.tmp/tool-cache`, 실행 파일은 `tools`에 둡니다.
PySide6-Essentials는 필요한 QtCore·QtGui·QtWidgets·QtNetwork 모듈을 제공하며,
사용하지 않는 WebEngine 등의 설치를 피합니다.

## 사용 방법

- 공개 단일 영상 또는 Shorts 주소를 입력하고 Enter 또는 **다운로드 및 재생**을 누릅니다.
- 스트리밍과 다운로드는 별도 연결입니다. 다운로드 완료 전부터 시청할 수 있지만
  네트워크 사용량이 증가할 수 있습니다. 완료 시 현재 재생을 끊지 않습니다.
- 완료된 같은 영상·화질을 다시 열면 보관된 로컬 파일을 재생합니다.
- 왼쪽 사이드바에서 영상 제목·재생 상태와 최근 영상 최대 20개를 확인합니다.
  새로 추가하거나 다시 연 영상이 최상단에 표시되며 목록 순서와 너비는 저장됩니다.
- 영상 목록을 선택한 뒤 사이드바의 취소·재시도·폴더 열기를 사용할 수 있습니다.
  목록을 두 번 클릭하면 현재 화질 설정으로 해당 주소를 엽니다.
- **리스트 비우기**는 목록 밖의 작업까지 모든 다운로드·주소 분석을 취소하고
  표시 목록을 비웁니다. 현재 재생과 완성·부분 파일은 보존하며 재시작해도 빈 목록을 유지합니다.
  20개 제한으로 숨겨진 작업은 목록 비우기를 누르기 전까지 계속 진행합니다.
- Space: 재생/일시정지, F11: 전체화면, Esc: 전체화면 종료.
- 영상 영역을 한 번 클릭하면 재생·일시정지를 전환합니다. 더블클릭 판정 후 적용됩니다.
- 영상 영역 더블클릭으로 전체화면과 기존 창 크기·최대화 상태를 전환합니다.
- 타임바 클릭·드래그로 해당 시점에 이동합니다. 호버하면 해당 구간 이미지와 시간이 표시됩니다.
- ← / →: 기본 5초, Shift + ← / →: 기본 30초 이동합니다. 설정에서 각 간격을
  변경할 수 있으며 즉시 적용·자동 저장됩니다. 입력창과 설정 편집 중에는 탐색하지 않습니다.
- 썸네일 방식은 **빠른 미리보기**와 **정밀 미리보기** 중 선택합니다. 빠른 방식은
  YouTube 구간별 이미지를 우선 사용하고, 없으면 약 1초 간격으로 프레임을 추출합니다.
  정밀 방식은 해당 시점의 프레임을 추출하며 다운로드 중에는 별도 저해상도 연결을 사용합니다.
  원격 미리보기는 추가 트래픽·지연이 생길 수 있고 실패 시 시간과 미리보기 불가 안내를 표시합니다.
- **■ 재생 중지** 아이콘은 재생만 중단합니다. 다운로드는 목록에서 별도로 취소합니다.
- 화질은 해상도 상한입니다. 제공되지 않는 화질은 그 이하에서 선택합니다.
- 재생목록 주소만 입력하는 기능·라이브·로그인 영상은 지원하지 않습니다.
  `watch?v=...&list=...`는 해당 영상 하나만 처리합니다.

## 설정과 보관

설정은 변경 후 자동 저장되며 음량·음소거·속도·창 크기도 복원됩니다.
기본 설정은 1080p, 자동 삭제 OFF, 시작 시 업데이트 확인 ON입니다.

- `data/settings.json`: 설정 / `data/tasks.json`: 작업 기록 / `data/app.log`: 오류 로그.
- `downloads/ytp-<작업ID>/`: 기본 영상 저장 폴더. 설정에서 위치를 바꿀 수 있습니다.
- 자동 삭제 OFF: 이전 다운로드를 계속합니다. 동시 2개, 나머지는 순서대로 대기합니다.
- 자동 삭제 ON: 새 영상이 실제 재생되면 직전 재생 작업만 중단하고 그 전용 폴더를 삭제합니다.
  새 영상이 실패하면 직전 파일은 보존합니다. 기존 보관 파일에 소급 적용하지 않습니다.
- 종료 시 진행 중인 작업은 중단하고 부분 파일을 남깁니다. 재실행 후 **재시도**하면
  yt-dlp가 가능한 범위에서 이어받습니다. 자동 재개는 하지 않습니다.
- 파일 잠금 등으로 삭제하지 못한 작업은 정리 대기로 기록하고 다음 실행에서 재시도합니다.

## yt-dlp 업데이트

설정에서 현재·최신 버전과 마지막 확인 시각을 확인합니다. 자동 확인은 성공 후 24시간
캐시를 사용하고 수동 확인은 즉시 요청합니다. 업데이트는 공식 안정 릴리스의 SHA-256과
실행 버전을 검증한 뒤 적용합니다. 다운로드·주소 분석·스트리밍 중에는 적용을 대기합니다.
재생 중지를 누르고 다운로드도 모두 완료/취소하면 적용할 수 있습니다.
실패 시 이전 실행 파일로 복구하고, 중간에 종료된 교체 작업도 다음 실행에서 복구합니다.

## 테스트·빌드

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.tmp\uv-cache'
uv run pytest --basetemp=.tmp/pytest-temp
$env:QT_QPA_PLATFORM = 'windows'
uv run pytest -m runtime --basetemp=.tmp/runtime-test
uv run ruff check --cache-dir .tmp/ruff-cache .
powershell -ExecutionPolicy Bypass -File scripts/build.ps1 -SkipTools
```

첫 빌드에서 도구도 받으려면 `-SkipTools`를 생략합니다. PyInstaller 폴더형 배포본을 생성합니다.
`-NoArchive`를 추가하면 ZIP 생성을 생략합니다. 실제 YouTube 통합 검증은
`uv run python scripts/smoke_youtube.py <공개영상URL>`로 실행할 수 있습니다.
썸네일 두 방식의 실제 원격 검증은 `uv run python scripts/smoke_previews.py <공개영상URL>`입니다.
빌드는 `.tmp/portable-stage`에서 만든 뒤 프로그램 파일만 교체하므로 기존 설정·다운로드를 보존합니다.
빌드 전 실행 중인 YT Player를 종료하세요.
배포 실행 파일은 `YTPlayer.exe --smoke-test <로컬영상경로>`로 Qt/mpv 자체 검증 후
종료할 수 있습니다. 사용자 설정과 별도로 `.tmp/smoke-test/data/smoke-result.json`에 결과를 기록합니다.
실제 YouTube 연결은 네트워크·지역·YouTube 정책 변경의 영향을 받습니다. 오류가 나면
yt-dlp를 최신 안정 버전으로 업데이트하고 재시도하세요.

다운로드 권한이 있는 콘텐츠에 사용하세요. 외부 구성요소의 고지는
`THIRD_PARTY_NOTICES.md`와 배포본의 `tools`를 참조하세요.
