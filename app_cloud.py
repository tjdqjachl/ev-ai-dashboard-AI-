from flask import Flask, send_from_directory, jsonify, make_response
import subprocess
import threading
import sys
import os

app = Flask(__name__)

# 동시 다발적인 데이터 갱신 요청을 막기 위한 락(Lock)
refresh_lock = threading.Lock()

@app.route('/')
def index():
    # dashboard_cloud.html 서빙
    try:
        with open('dashboard_cloud.html', 'r', encoding='utf-8') as f:
            html = f.read()
        response = make_response(html)
        response.headers['Content-Type'] = 'text/html; charset=utf-8'
        return response
    except Exception as e:
        return f"Error loading dashboard: {e}", 500

@app.route('/map_cloud.html')
def serve_map():
    # 생성된 map_cloud.html 서빙 (캐시 무시 설정 추가)
    if not os.path.exists('map_cloud.html'):
        return "지도 데이터가 아직 생성되지 않았습니다. 실시간 갱신을 먼저 실행해주세요.", 404
        
    try:
        with open('map_cloud.html', 'r', encoding='utf-8') as f:
            html = f.read()
        response = make_response(html)
        response.headers['Content-Type'] = 'text/html; charset=utf-8'
        # 캐시 방지 헤더
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response
    except Exception as e:
        return f"Error loading map: {e}", 500

@app.route('/api/refresh', methods=['POST'])
def refresh_data():
    # Rate Limiting & DoS 방지
    if not refresh_lock.acquire(blocking=False):
        return jsonify({
            'status': 'error', 
            'message': '현재 다른 사용자의 업데이트가 진행 중입니다. 잠시 후 다시 시도해주세요.'
        }), 429

    try:
        # 1. API 수집 실행
        subprocess.run([sys.executable, 'preprocess_chargers_cloud.py'], check=True)
        # 2. AI 분석 실행
        subprocess.run([sys.executable, 'ev_analysis_cloud.py'], check=True)
        
        return jsonify({'status': 'success'})
    except subprocess.CalledProcessError as e:
        return jsonify({'status': 'error', 'message': f'스크립트 실행 오류: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        refresh_lock.release()

if __name__ == '__main__':
    # 로컬 테스트 시 실행 (실제 상용 배포시에는 gunicorn 등의 WSGI 서버가 진입점이 됩니다)
    app.run(host='0.0.0.0', port=5000, debug=False)
