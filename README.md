# CD Ripper Studio

오디오 CD를 정확하게 리핑하고, 웹에서 메타데이터와 커버를 가져와 태그까지 처리하는 Windows 프로그램.

## 주요 기능

| # | 기능 | 구현 |
|---|------|------|
| 1 | CD 리핑 | Windows IOCTL로 직접 읽음 (외부 프로그램 불필요). **보안 모드**(재독취 후 비교·다수결), **읽기 오프셋 보정**, **AccurateRip 검증** |
| 2 | 커버 이미지 | iTunes(최대 3000px), Deezer(1000px), Cover Art Archive 검색 + **Google 이미지** 브라우저 연동(드래그&드롭 / URL / 클립보드) |
| 3 | 파일 이름 규칙 | `{albumartist}/[{year} - ]{album}/{track:02}. {title}` 형식, 실시간 미리보기, `[ ]` 선택 블록, 컴필레이션용 별도 규칙 |
| 4 | 메타데이터 자동 검색 | CD 삽입 시 **MusicBrainz 디스크 ID** 자동 조회 → CD-Text → 앨범명 검색(MusicBrainz/iTunes/Deezer) → 수동 입력 |
| 5 | 웹캠 트랙 인식 | 웹캠 촬영 또는 사진 파일 → 영역 드래그 선택 → **Windows 내장 OCR**(한국어) / Tesseract → 텍스트·표에서 수정 후 적용 |
| 6 | 출력 포맷 | MP3 (CBR 320 / VBR V0), FLAC, ALAC, AAC, Opus, WAV + "보관용 FLAC 사본 동시 생성" 옵션 |
| 7 | 앨범/트랙 메타데이터 | 앨범: 아티스트·앨범·연도·장르·디스크 번호·레이블·카탈로그·바코드·코멘트·컴필레이션 / 트랙: 제목·아티스트·작곡·작사·장르·ISRC·코멘트·가사 |
| 8 | 기타 | 클립보드 트랙 목록 붙여넣기(멜론·벅스 페이지 복사), 일괄 편집, feat. 분리, Title Case, 디스크별 정보 기억, .cue/.m3u8/.log 생성, folder.jpg 저장 |

## exe 만들기

### 방법 A — 내 PC에서 빌드 (가장 간단)
1. [Python 3.12](https://www.python.org/downloads/) 설치 (설치 시 **"Add python.exe to PATH" 체크**)
2. 이 폴더에서 `build.bat` 더블클릭
3. 몇 분 후 `dist\CDRipperStudio.exe` 생성 (단일 파일, 약 150MB)

### 방법 B — GitHub에서 자동 빌드
이 폴더를 GitHub 저장소로 올리면 `.github/workflows/build-windows.yml`이 Windows에서 exe를 빌드해
Actions → Artifacts에 올려줍니다. `v1.0.0` 같은 태그를 푸시하면 Release에도 첨부됩니다.

### 선택 구성요소
- **AAC / ALAC 출력**: [ffmpeg.exe](https://www.gyan.dev/ffmpeg/builds/)를 exe와 같은 폴더에 두거나 설정에서 경로 지정
  (빌드 전에 프로젝트 폴더에 `ffmpeg.exe`를 두면 exe 안에 같이 포함됩니다)
- **한국어 OCR**: Windows 설정 → 시간 및 언어 → 언어 및 지역 → 한국어 → 언어 옵션 → *광학 문자 인식* 설치
  (한국어 Windows는 보통 이미 설치되어 있습니다)

## 사용 순서

1. CD 넣기 → 자동으로 디스크 ID 조회. 찾으면 바로 채워지고, 후보가 여러 개면 선택 창이 뜹니다.
2. 못 찾으면: **🌐 웹에서 정보 찾기**(앨범명 검색) / **📷 웹캠으로 트랙 인식** / 직접 입력 / 멜론 페이지에서 트랙 목록 복사 후 `Ctrl+Shift+V`
3. **🖼 커버 찾기** → 원하는 이미지 더블클릭 (또는 Google 이미지에서 끌어다 놓기)
4. 포맷·파일 이름 규칙 확인 → **● 리핑 시작**
5. 상태 칸에 `✅ 정확함 (신뢰도 N)`이 뜨면 전 세계 다른 사용자의 리핑과 비트 단위로 일치한다는 뜻입니다.

> **처음 한 번만**: 설정 → 리핑 → *읽기 오프셋*을 내 드라이브 값으로 설정하세요
> ([AccurateRip 드라이브 오프셋 목록](http://www.accuraterip.com/driveoffsets.htm)). 대부분의 LG·ASUS 드라이브는 `+6`.

## 포맷 추천

| 용도 | 추천 |
|------|------|
| 휴대폰·차량·어디서나 재생 | **MP3 CBR 320** (기본값) |
| MP3인데 용량은 줄이고 싶다 | **MP3 VBR V0** — 귀로 구분 불가, 약 20% 작음 |
| 다시 리핑하지 않을 원본 보관 | **FLAC** — CD와 비트 단위 동일. *MP3 + 보관용 FLAC 동시 생성* 옵션 추천 |
| iPhone / Apple Music | **ALAC**(무손실) 또는 **AAC 256** |
| 최소 용량·최신 기기 | **Opus** |

## 벤치마크한 프로그램
- **EAC / dBpoweramp**: 보안 리핑, AccurateRip, 오프셋 보정, 로그 → AccurateRip 일치 시 재독취 생략(dBpoweramp 방식)으로 빠르면서 정확하게
- **foobar2000 / Mp3tag**: `[ ]` 조건부 블록이 있는 파일 이름 규칙, 일괄 태그 편집
- **MusicBrainz Picard**: 디스크 ID 조회, 작곡/작사(work relation) 정보, MusicBrainz ID 태그
- **iTunes**: CD 넣으면 바로 정보가 채워지는 흐름

## 개발

```bat
run.bat            :: 소스로 바로 실행
```
```bash
pip install -r requirements.txt pytest
CDRIPPER_FAKE=1 python main.py     # CD 드라이브 없이 가상 CD로 UI 테스트
pytest
```

```
cdripper/
  core/   drive.py (Windows IOCTL CD 읽기), ripper.py (보안 모드), accuraterip.py, toc.py (디스크 ID),
          encoders.py, xing.py (VBR 헤더), tagger.py, naming.py, ocr.py, job.py, settings.py
  sources/web.py   MusicBrainz · Cover Art Archive · iTunes · Deezer
  gui/    main_window.py, search_dialog.py, cover_dialog.py, ocr_dialog.py, settings_dialog.py
```

설정 및 디스크별 저장 정보: `%APPDATA%\CDRipperStudio\`
