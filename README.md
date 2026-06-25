# StudyTimer-Idea-Concept-Version
## Focus Timer

macOS 공부 타이머 — 타이머 실행 중 크롬 허용 탭 외 차단 및 앱 강제 종료

> **이 버전은 프로토타입 / 컨셉 버전입니다.**  
> Python으로 제작된 개발 검증용이며, 정식 버전은 C++로 재작성할 예정입니다.

---

## 로드맵

| 버전 | 상태 | 설명 |
|------|------|------|
| v0.x (현재) | 프로토타입 | Python — 기능 검증용 |
| v1.0 | 개발 예정 | C++ + Qt — 정식 버전 |

---

## 기능

- 타이머 실행 중 키오스크 모드 (Dock, 메뉴바 숨김 / Cmd+Tab, Spotlight 차단)
- 크롬 허용 사이트 외 탭 자동 닫기 (5초마다)
- 크롬 외 모든 앱 강제 종료 (3초마다)
- 비상 해제 — 비밀번호 입력 시 모든 차단 해제 + 타이머 일시정지
- 허용 사이트 추가/삭제 (비밀번호 필요)
- SHA-256 비밀번호 해싱
- 타이머 완료 시 시스템 알림 + 재부팅 옵션

---

## 요구사항

- macOS
- Python 3.10 이상
- pyobjc (키오스크 모드용)

```bash
pip install pyobjc-framework-Cocoa
```

---

## 실행

> 앱 차단 기능은 관리자 권한이 필요합니다.

```bash
sudo python3 study_timer.py
```

---

## 설정 파일 (config.json)

첫 실행 시 자동 생성됩니다. 아래 템플릿을 참고해 직접 만들 수도 있습니다.

```json
{
  "password_hash": "여기에_SHA-256_해시값",
  "allowed_sites": [
    "music.youtube.com",
    "docs.google.com",
    "mail.google.com",
    "gmail.com",
    "chatgpt.com",
    "chat.openai.com",
    "claude.ai"
  ]
}
```

`config.json`은 `.gitignore`에 의해 추적되지 않습니다.  
비밀번호 해시 생성 방법:

```bash
python3 -c "import hashlib; print(hashlib.sha256('YOUR_PASSWORD'.encode()).hexdigest())"
```

기본 비밀번호는 `1234`입니다. 반드시 변경하세요.

---

## 허용 앱

타이머 실행 중 아래 앱만 유지됩니다. 그 외는 강제 종료됩니다.

- Google Chrome
- Terminal / iTerm2
- 시스템 필수 프로세스 (WindowServer, Dock 등)

---

## 주의사항

- 타이머 시작 전 작업 중인 파일을 반드시 저장하세요.
- 비상 해제 비밀번호를 잊어버리면 `config.json`을 삭제 후 재실행하면 초기화됩니다.
- Finder는 macOS 필수 프로세스로 차단되지 않습니다.