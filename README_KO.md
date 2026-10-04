# v3.4.25 Streamlit compatibility hotfix

원인:
v3.4.25 KBO safety patch가 런타임에서
BASEBALL_ADVANCED_BUILD / LIVE_BASEBALL_BUILD를 3.4.25로 바꿨는데,
기존 app.py는 각각 3.4.23 / 3.4.20인지 검사합니다.
그래서 패치 자체는 정상 설치됐지만 app.py가 '구버전'으로 오판하고 st.stop() 했습니다.

적용:
1. 이 ZIP을 풉니다.
2. sports_ev_engine/__init__.py 한 파일만 기존 저장소의 같은 경로에 덮어씁니다.
3. GitHub에서 Commit changes.
4. Streamlit Community Cloud는 커밋을 자동 감지합니다.
5. 필요하면 share.streamlit.io > 앱 오른쪽 ⋮ > Reboot.

이 핫픽스는 v3.4.25 기능을 제거하지 않습니다.
패치 함수는 그대로 설치하고, 기존 app.py의 base-build 검사를 통과하도록
build identifier만 호환 처리합니다.
