# -*- coding: utf-8 -*-
import csv
import urllib.request
import json
import time
import os
import sys

# Windows 콘솔 인코딩 에러 방지
sys.stdout.reconfigure(encoding='utf-8')

print("=" * 70)
print("[전처리] 청주시 전기차 충전소 위치 정규화 및 매핑 파이프라인")
print("=" * 70)

# ---------------------------------------------------------
# 1. 청주시 행정동 GeoJSON 로드 및 다각형 파싱
# ---------------------------------------------------------
print("\n[1] 행정동 GeoJSON 다운로드 및 파싱 중...")
geojson_url = "https://raw.githubusercontent.com/vuski/admdongkor/master/ver20230701/HangJeongDong_ver20230701.geojson"
req = urllib.request.Request(geojson_url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    with urllib.request.urlopen(req) as response:
        geo_data = json.loads(response.read().decode('utf-8'))
except Exception as e:
    print(f"❌ GeoJSON 다운로드 실패: {e}")
    sys.exit(1)

cj_polygons = {} # {'행정동명': [ [ [lng, lat], ... ], ... ]}

for feature in geo_data['features']:
    if '청주시' in feature['properties'].get('sggnm', ''):
        adm_nm = feature['properties']['adm_nm']
        dong_name = adm_nm.split(' ')[-1].replace('·', '').replace('.', '')
        
        geom = feature['geometry']
        polys = []
        if geom['type'] == 'Polygon':
            polys.append(geom['coordinates'][0]) # 외곽선만 취급
        elif geom['type'] == 'MultiPolygon':
            for poly in geom['coordinates']:
                polys.append(poly[0])
                
        cj_polygons[dong_name] = polys

print(f"✅ 총 {len(cj_polygons)}개 행정동 다각형 추출 완료")

# ---------------------------------------------------------
# 2. Point-in-Polygon (Ray Casting Algorithm) 구현
# ---------------------------------------------------------
def is_point_in_polygon(x, y, poly):
    """(x=lng, y=lat)가 다각형 poly 내부에 있는지 판별"""
    n = len(poly)
    inside = False
    p1x, p1y = poly[0]
    for i in range(n + 1):
        p2x, p2y = poly[i % n]
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xinters:
                        inside = not inside
        p1x, p1y = p2x, p2y
    return inside

def find_dong_by_coords(lat, lng):
    if lat == 0 or lng == 0: return None
    for dong, polys in cj_polygons.items():
        for poly in polys:
            if is_point_in_polygon(lng, lat, poly):
                return dong
    return None

# ---------------------------------------------------------
# 3. 공공데이터 API에서 충전소 원본 데이터 수집
# ---------------------------------------------------------
print("\n[2] 공공데이터 API 충전기 정보 수집 중...")
service_key = '778914bb5400a3f59aeee690052a3667b198964a47b476c14fa56fc686c2eed1'
info_url = f'http://apis.data.go.kr/B552584/EvCharger/getChargerInfo?serviceKey={service_key}&pageNo=1&numOfRows=9999&zcode=43&zscode=43110&dataType=JSON'
status_url = f'http://apis.data.go.kr/B552584/EvCharger/getChargerStatus?serviceKey={service_key}&pageNo=1&numOfRows=9999&zcode=43&zscode=43110&dataType=JSON&period=5'

info_items = []
try:
    req = urllib.request.Request(info_url, headers={'User-Agent': 'Mozilla/5.0'})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req) as response:
                info_data = json.loads(response.read().decode('utf-8'))
                info_items = info_data.get('items', {}).get('item', [])
                if info_items:
                    print(f"✅ 충전기 정보 {len(info_items)}건 수집 완료 (시도 횟수: {attempt+1})")
                    break
        except Exception as e:
            if attempt < 4:
                print(f"⚠️ API 호출 지연({e}), 3초 후 재시도... ({attempt+1}/5)")
                time.sleep(3)
            else:
                raise e
except Exception as e:
    print(f"❌ API 호출 에러: {e}")
    sys.exit(1)

# 상태 데이터 수집 (빠른 업데이트를 위해 병합)
status_dict = {}
try:
    req = urllib.request.Request(status_url, headers={'User-Agent': 'Mozilla/5.0'})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req) as response:
                st_data = json.loads(response.read().decode('utf-8'))
                for it in st_data.get('items', {}).get('item', []):
                    # chgerId + statId 가 유니크 키
                    uid = it.get('statId', '') + "_" + it.get('chgerId', '')
                    status_dict[uid] = it.get('stat', '9')
                if status_dict:
                    break
        except Exception:
            if attempt < 4: time.sleep(3)
except Exception:
    pass # 상태 데이터 실패해도 기존 정보는 활용 가능

# ---------------------------------------------------------
# 4. 좌표 기반 공간 맵핑 (Point in Polygon) & 주소 Fallback
# ---------------------------------------------------------
print("\n[3] 좌표 기반 정밀 매핑 (Point in Polygon) 진행 중...")
dong_synonyms = {
    '봉명2송정동': ['봉명2', '송정'],
    '성화개신죽림동': ['성화', '개신', '죽림'],
    '운천신봉동': ['운천', '신봉'],
    '율량사천동': ['율량', '사천'],
    '탑대성동': ['탑동', '대성'],
    '용담명암산성동': ['용담', '명암', '산성']
}

all_dongs = list(cj_polygons.keys())

mapped_chargers = []
success_count = 0
fallback_count = 0
fail_count = 0

for item in info_items:
    statId = item.get('statId', '')
    chgerId = item.get('chgerId', '')
    uid = statId + "_" + chgerId
    
    item['stat_realtime'] = status_dict.get(uid, item.get('stat', '9'))
    
    lat = float(item.get('lat', 0)) if item.get('lat') else 0.0
    lng = float(item.get('lng', 0)) if item.get('lng') else 0.0
    addr = str(item.get('addr', ''))
    
    dong_matched = 'Unknown'
    match_method = 'None'
    
    # 1순위: 좌표 기반 매핑 (가장 정확함)
    found_dong = find_dong_by_coords(lat, lng)
    if found_dong:
        dong_matched = found_dong
        match_method = 'GeoJSON(Point-in-Polygon)'
        success_count += 1
    else:
        # 2순위: 주소 텍스트 기반 Fallback 매핑
        addr_clean = addr.replace(' ', '')
        # 단순 매칭
        for d in all_dongs:
            d_clean = d.replace(' ', '')
            if d_clean in addr_clean or d_clean[:-1] in addr_clean:
                dong_matched = d
                match_method = 'Address(Text)'
                break
        
        # 동의어 매칭 (복합 행정동)
        if dong_matched == 'Unknown':
            for key, syns in dong_synonyms.items():
                if any(syn in addr_clean for syn in syns):
                    dong_matched = key
                    match_method = 'Address(Synonyms)'
                    break
                    
        if dong_matched != 'Unknown':
            fallback_count += 1
        else:
            fail_count += 1
            
    # 정제된 데이터 객체 구성
    mapped_chargers.append({
        'statId': statId,
        'chgerId': chgerId,
        'statNm': item.get('statNm', ''),
        'addr': addr,
        'lat': lat,
        'lng': lng,
        'busiNm': item.get('busiNm', ''),
        'chgerType': item.get('chgerType', ''),
        'output': item.get('output', ''),
        'stat_realtime': item['stat_realtime'],
        'dong_matched': dong_matched,
        'match_method': match_method
    })

print(f"  - 좌표 매핑 성공: {success_count}건")
print(f"  - 주소 텍스트 매핑 (Fallback): {fallback_count}건")
print(f"  - 매핑 실패 (Unknown): {fail_count}건")

# ---------------------------------------------------------
# 5. CSV 결과물 저장
# ---------------------------------------------------------
output_file = 'cleaned_chargers_cloud.csv'
print(f"\n[4] 결과를 '{output_file}'에 저장 중...")
with open(output_file, 'w', encoding='utf-8-sig', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['statId', 'chgerId', 'statNm', 'addr', 'lat', 'lng', 'busiNm', 'chgerType', 'output', 'stat_realtime', 'dong_matched', 'match_method'])
    for ch in mapped_chargers:
        writer.writerow([
            ch['statId'], ch['chgerId'], ch['statNm'], ch['addr'], 
            ch['lat'], ch['lng'], ch['busiNm'], ch['chgerType'], 
            ch['output'], ch['stat_realtime'], ch['dong_matched'], ch['match_method']
        ])
        
print("✅ 전처리 완료!")
