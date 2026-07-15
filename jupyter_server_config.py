# /workspace/.jupyter/jupyter_server_config.py 로 배치.
# JUPYTER_CONFIG_DIR=/workspace/.jupyter 환경변수 덕분에 pod가 켜질 때마다
# Jupyter가 이 파일을 실행 -> 채점 서버 자동 기동.
import subprocess

subprocess.Popen(['bash', '/workspace/lingo/serve.sh'],
                 stdout=open('/workspace/logs/boot.log', 'a'),
                 stderr=subprocess.STDOUT)
