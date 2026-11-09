# 계룡 초·중학생 혜택·행사 안내

서버와 빌드 도구 없이 HTML·CSS·JavaScript로 동작하는 정적 사이트입니다. 데이터 입력 규칙, 수록 범위, 조사한 기관은 [README.md](README.md)를 따릅니다.

## 이 저장소의 규칙

- **여러 PC에서 작업함:** 작업 전에 `git fetch`로 GitHub 상태부터 확인합니다. 커밋하지 않은 파일과 Claude 메모리는 다른 PC로 넘어가지 않습니다.
- **푸시 전에 먼저 받기:** `github-actions[bot]`이 매일 수집 결과를 커밋합니다. 작업 전과 푸시 전에 `git pull --rebase origin main`을 실행합니다.
- **자동 수집 파일은 직접 고치지 않기:** `data/notices.json`, `data/auto-benefits.json`, `data/auto-events.json`은 수집기가 다시 만듭니다. 공고 수집 기준은 `scripts/collect_notices.py`의 `SOURCES`, `INCLUDE`, `EXCLUDE`에서, 프로그램 카드는 `scripts/collect_programs.py`에서 바꿉니다.
- **`robots.txt` 지키기:** 수집기가 허용되지 않은 게시판은 읽지 않습니다. User-Agent 변경 등으로 우회하지 않으며, 새 기관을 시험할 때도 게시판을 읽기 전에 `robots.txt`부터 확인합니다.
- **모든 내용을 자동으로 유지하기:** 사람이 확인하거나 입력해야 하는 단계를 넣지 않습니다. 잘못된 항목은 공식 링크에서 확인하면 되므로 괜찮고, 누락이 더 큰 문제입니다. 애매한 공고는 빼지 말고 보여 줍니다.
- **`benefits.json`, `events.json`은 공식 출처로 확인한 내용만 넣기:** 확인하지 못한 값은 추측하지 않고 `"확인 필요"`로 적습니다.
- **인증키는 GitHub Secrets에만 두기:** `DATA_GO_KR_KEY`는 코드, 로그, 대화에 남기지 않습니다. 이 키로 하는 호출은 GitHub Actions에서 시험합니다.
- **외부 데이터 표시:** 화면에는 `textContent`로만 넣고, 링크는 `http://`, `https://`만 허용합니다.
- **`js/app.js` 코드 스타일:** 프레임워크와 빌드 없이 기존처럼 `var`와 `function`으로 작성합니다.

## 실행과 확인

이 PC에는 Node.js가 없습니다. Python과 Edge를 사용합니다.

```
python -m http.server 8000
```

- **미리 보기:** 위 명령을 실행하고 http://localhost:8000 을 엽니다. `index.html`을 직접 열면 데이터를 읽지 못합니다.
- **화면 확인:** `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`를 `--headless=new --dump-dom` 또는 `--screenshot`으로 실행합니다. 임시 `--user-data-dir`을 써야 이전 CSS 캐시의 영향을 받지 않습니다.
- **클릭 테스트:** 프로젝트 밖 임시 폴더에 복사본을 만들어 테스트 스크립트를 넣습니다. 프로젝트 파일에는 넣지 않습니다.
- **휴대폰 폭 확인:** 헤드리스 창은 약 500px보다 좁아지지 않습니다. 390px 폭은 다른 포트의 페이지에서 `iframe`으로 띄워 확인합니다.
- **수집기 실행:** 저장소 밖의 가상환경에 `pip install -r scripts/requirements.txt`로 설치한 뒤 `python scripts/collect_notices.py`와 `python scripts/collect_programs.py`를 실행합니다.
- **GitHub에서 수집 실행:** `gh workflow run collect-notices.yml --repo ssw0907/gyeryong-student-guide`
- **Git Bash 주의:** 한글이 많은 긴 인라인 스크립트는 해석에 실패할 수 있으니, 파일로 저장해 실행합니다.

## 배포

- `main`에 푸시하면 GitHub Pages가 루트 폴더를 배포합니다: https://ssw0907.github.io/gyeryong-student-guide/
- 이 저장소의 커밋 작성자는 로컬 설정(`ssw0907`, GitHub `noreply` 주소)을 사용합니다. 이메일을 공개 주소로 바꾸지 않습니다.
