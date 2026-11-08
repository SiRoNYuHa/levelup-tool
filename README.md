# LevelUp Tool

성장 테이블을 보고 순위 조건을 판정하고, 경로를 시뮬레이션하는 데스크톱 도구입니다.

## 실행 오류 안내

**이 프로젝트는 환경에 따라 실행 오류가 발생할 확률이 있습니다.**

아래와 같은 경우에 바로 실행되지 않거나 중간에 실패할 수 있습니다.

- Python 버전이 맞지 않거나 PATH에 등록되지 않은 경우
- `tkinter`가 설치되지 않은 Python을 쓰는 경우
- 의존 모듈(`evaluator`, `models`, `simulator`, `storage` 등)을 프로젝트 루트가 아닌 다른 위치에서 실행하는 경우
- PyInstaller로 exe를 빌드할 때 패키지 설치 또는 빌드가 실패한 경우
- Windows 외 환경에서 GUI/`build.bat`을 그대로 실행하는 경우

오류가 나면 정상 동작으로 단정하지 말고, Python 설치와 실행 경로부터 확인하는 것을 권장합니다.

## 실행 방법

프로젝트 폴더에서:

```bash
python main.py
```

Windows에서는 `py -3 main.py`로 실행할 수도 있습니다.

## exe 빌드

Windows에서 `build.bat`을 실행하면 `dist\LevelUpTool.exe`를 만들려고 시도합니다. 빌드 과정에서도 오류가 날 수 있습니다.

## 테스트

```bash
python -m unittest tests.test_core
```
