"""
KanjiVG SVG Parser → AI Training Dataset
KanjiVG SVG 파일들을 파싱하여 ML 학습용 데이터셋을 생성합니다.

출력 파일:
  1. kanji_dataset.json  — 전체 상세 데이터 (JSON)
  2. kanji_strokes.csv   — 획 단위 데이터 (CSV)
  3. kanji_summary.csv   — 문자 요약 (CSV)
"""

import os
import re
import json
import csv
import xml.etree.ElementTree as ET
from pathlib import Path

# KanjiVG namespace
KVG_NS = "http://kanjivg.tagaini.net"

def parse_path_to_points(d_attr):
    """SVG path의 d 속성에서 좌표값들을 추출한다.
    M/m (moveto), C/c (curveto), S/s (smooth curveto), 
    L/l (lineto), Q/q (quadratic) 등의 명령에서 숫자를 추출.
    """
    # 모든 숫자(소수점, 음수 포함)를 추출
    numbers = re.findall(r'-?\d+\.?\d*', d_attr)
    points = []
    for i in range(0, len(numbers) - 1, 2):
        try:
            x = float(numbers[i])
            y = float(numbers[i + 1])
            points.append([round(x, 2), round(y, 2)])
        except (ValueError, IndexError):
            continue
    return points


def extract_start_point(d_attr):
    """path d 속성에서 시작점 좌표를 추출한다."""
    match = re.match(r'M\s*(-?\d+\.?\d*)\s*[,\s]\s*(-?\d+\.?\d*)', d_attr)
    if match:
        return [float(match.group(1)), float(match.group(2))]
    return None


def parse_components(g_element):
    """<g> 요소에서 구성요소(component) 정보를 재귀적으로 추출한다."""
    components = []
    element = g_element.get(f'{{{KVG_NS}}}element')
    if element:
        comp = {"element": element}
        position = g_element.get(f'{{{KVG_NS}}}position')
        radical = g_element.get(f'{{{KVG_NS}}}radical')
        phon = g_element.get(f'{{{KVG_NS}}}phon')
        part = g_element.get(f'{{{KVG_NS}}}part')
        trad_form = g_element.get(f'{{{KVG_NS}}}tradForm')
        radical_form = g_element.get(f'{{{KVG_NS}}}radicalForm')
        
        if position: comp["position"] = position
        if radical: comp["radical"] = radical
        if phon: comp["phon"] = phon
        if part: comp["part"] = part
        if trad_form: comp["tradForm"] = trad_form
        if radical_form: comp["radicalForm"] = radical_form
        
        components.append(comp)
    
    # 하위 <g> 요소들도 재귀 탐색
    for child_g in g_element.findall('{http://www.w3.org/2000/svg}g'):
        components.extend(parse_components(child_g))
    for child_g in g_element.findall('g'):
        components.extend(parse_components(child_g))
    
    return components


def parse_svg_file(filepath):
    """단일 SVG 파일을 파싱하여 데이터를 추출한다."""
    filename = os.path.basename(filepath)
    name_no_ext = os.path.splitext(filename)[0]
    
    # 변형(variant) 정보 추출: 04e14-Kaisho.svg → variant="Kaisho"
    variant = None
    unicode_hex = name_no_ext
    if '-' in name_no_ext:
        parts = name_no_ext.split('-', 1)
        unicode_hex = parts[0]
        variant = parts[1]
    
    # 유니코드 코드포인트를 실제 문자로 변환
    try:
        codepoint = int(unicode_hex, 16)
        character = chr(codepoint)
    except (ValueError, OverflowError):
        character = None
    
    # SVG 파싱 (DTD 선언 제거 - Python xml.etree가 처리 못함)
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # DOCTYPE 제거 (Python XML 파서가 외부 DTD를 로드할 수 없으므로)
        content = re.sub(r'<!DOCTYPE[^>]*\[.*?\]>', '', content, flags=re.DOTALL)
        
        root = ET.fromstring(content)
    except ET.ParseError as e:
        print(f"  ⚠ XML 파싱 오류: {filepath}: {e}")
        return None
    
    ns = {'svg': 'http://www.w3.org/2000/svg'}
    
    # StrokePaths 그룹 찾기
    stroke_paths_g = None
    for g in root.findall('.//svg:g', ns):
        gid = g.get('id', '')
        if gid.startswith('kvg:StrokePaths_'):
            stroke_paths_g = g
            break
    # namespace 없이도 시도
    if stroke_paths_g is None:
        for g in root.iter('g'):
            gid = g.get('id', '')
            if gid.startswith('kvg:StrokePaths_'):
                stroke_paths_g = g
                break
    
    if stroke_paths_g is None:
        print(f"  ⚠ StrokePaths 그룹 없음: {filepath}")
        return None
    
    # 메인 캐릭터 그룹 (StrokePaths 바로 아래의 첫 번째 g)
    main_g = None
    for child in stroke_paths_g:
        tag = child.tag.replace('{http://www.w3.org/2000/svg}', '')
        if tag == 'g':
            main_g = child
            break
    
    if main_g is None:
        print(f"  ⚠ 메인 그룹 없음: {filepath}")
        return None
    
    # 메인 문자 요소
    main_element = main_g.get(f'{{{KVG_NS}}}element') or main_g.get('kvg:element')
    main_radical = main_g.get(f'{{{KVG_NS}}}radical') or main_g.get('kvg:radical')
    
    # 모든 path(획) 추출
    strokes = []
    all_paths = list(main_g.iter('{http://www.w3.org/2000/svg}path'))
    if not all_paths:
        all_paths = list(main_g.iter('path'))
    
    for path in all_paths:
        path_id = path.get('id', '')
        d_attr = path.get('d', '')
        stroke_type = path.get(f'{{{KVG_NS}}}type') or path.get('kvg:type', '')
        
        # 획 순서 번호 추출: kvg:04e00-s1 → 1
        order_match = re.search(r'-s(\d+)$', path_id)
        order = int(order_match.group(1)) if order_match else len(strokes) + 1
        
        start_point = extract_start_point(d_attr)
        all_points = parse_path_to_points(d_attr)
        
        stroke = {
            "order": order,
            "path": d_attr,
            "type": stroke_type if stroke_type else None,
            "start_point": start_point,
            "points": all_points
        }
        strokes.append(stroke)
    
    # 획 순서대로 정렬
    strokes.sort(key=lambda s: s["order"])
    
    # 구성요소 추출
    components = parse_components(main_g)
    # 첫 번째는 메인 문자 자체이므로, 하위 구성요소만 필요하면 [1:]
    sub_components = components[1:] if len(components) > 1 else []
    
    # 부수 정보 수집
    radicals = []
    for comp in components:
        if "radical" in comp:
            radicals.append(f"{comp['radical']}:{comp['element']}")
    
    result = {
        "unicode": unicode_hex,
        "character": main_element or character,
        "codepoint": codepoint if character else None,
        "variant": variant,
        "stroke_count": len(strokes),
        "strokes": strokes,
        "components": sub_components,
        "radicals": radicals,
        "main_radical": main_radical,
    }
    
    return result


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    kanji_dir = os.path.join(script_dir, "kanji")
    output_dir = script_dir
    
    if not os.path.isdir(kanji_dir):
        print(f"❌ kanji 디렉토리를 찾을 수 없습니다: {kanji_dir}")
        return
    
    # SVG 파일 목록
    svg_files = sorted([
        os.path.join(kanji_dir, f)
        for f in os.listdir(kanji_dir)
        if f.endswith('.svg')
    ])
    
    total = len(svg_files)
    print(f"📂 SVG 파일 {total}개 발견. 파싱 시작...\n")
    
    all_records = []
    errors = 0
    
    for i, filepath in enumerate(svg_files, 1):
        if i % 500 == 0 or i == total:
            print(f"  진행: {i}/{total} ({i*100//total}%)")
        
        record = parse_svg_file(filepath)
        if record:
            all_records.append(record)
        else:
            errors += 1
    
    print(f"\n✅ 파싱 완료: {len(all_records)}개 성공, {errors}개 실패\n")
    
    # ── 1. JSON 출력 ──
    json_path = os.path.join(output_dir, "kanji_dataset.json")
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(all_records, f, ensure_ascii=False, indent=2)
    print(f"📄 JSON 저장: {json_path} ({len(all_records)} records)")
    
    # ── 2. Strokes CSV 출력 ──
    strokes_csv_path = os.path.join(output_dir, "kanji_strokes.csv")
    with open(strokes_csv_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            "unicode", "character", "variant", "total_strokes",
            "stroke_order", "stroke_type", "start_x", "start_y", "path_data"
        ])
        for rec in all_records:
            for stroke in rec["strokes"]:
                sp = stroke.get("start_point") or [None, None]
                writer.writerow([
                    rec["unicode"],
                    rec["character"],
                    rec["variant"] or "",
                    rec["stroke_count"],
                    stroke["order"],
                    stroke["type"] or "",
                    sp[0] if sp[0] is not None else "",
                    sp[1] if sp[1] is not None else "",
                    stroke["path"],
                ])
    stroke_rows = sum(rec["stroke_count"] for rec in all_records)
    print(f"📄 Strokes CSV 저장: {strokes_csv_path} ({stroke_rows} rows)")
    
    # ── 3. Summary CSV 출력 ──
    summary_csv_path = os.path.join(output_dir, "kanji_summary.csv")
    with open(summary_csv_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            "unicode", "character", "codepoint", "variant", "stroke_count",
            "radicals", "components", "main_radical"
        ])
        for rec in all_records:
            comp_str = "|".join(c["element"] for c in rec["components"])
            rad_str = "|".join(rec["radicals"])
            writer.writerow([
                rec["unicode"],
                rec["character"],
                rec["codepoint"] or "",
                rec["variant"] or "",
                rec["stroke_count"],
                rad_str,
                comp_str,
                rec["main_radical"] or "",
            ])
    print(f"📄 Summary CSV 저장: {summary_csv_path} ({len(all_records)} rows)")
    
    # ── 통계 출력 ──
    print(f"\n{'='*50}")
    print(f"📊 데이터셋 통계")
    print(f"{'='*50}")
    print(f"  총 문자 수:     {len(all_records)}")
    
    base_chars = [r for r in all_records if r["variant"] is None]
    variant_chars = [r for r in all_records if r["variant"] is not None]
    print(f"  기본 문자:      {len(base_chars)}")
    print(f"  변형 문자:      {len(variant_chars)}")
    
    total_strokes = sum(r["stroke_count"] for r in all_records)
    avg_strokes = total_strokes / len(all_records) if all_records else 0
    max_strokes = max(r["stroke_count"] for r in all_records) if all_records else 0
    print(f"  총 획 수:       {total_strokes}")
    print(f"  평균 획 수:     {avg_strokes:.1f}")
    print(f"  최대 획 수:     {max_strokes}")
    
    # 문자 유형 분류
    kanji_count = sum(1 for r in all_records if r["codepoint"] and r["codepoint"] >= 0x4E00 and r["codepoint"] <= 0x9FFF)
    hiragana_count = sum(1 for r in all_records if r["codepoint"] and 0x3040 <= r["codepoint"] <= 0x309F)
    katakana_count = sum(1 for r in all_records if r["codepoint"] and 0x30A0 <= r["codepoint"] <= 0x30FF)
    print(f"  한자:           {kanji_count}")
    print(f"  히라가나:       {hiragana_count}")
    print(f"  가타카나:       {katakana_count}")
    
    # 샘플 출력
    print(f"\n📝 샘플 데이터 (처음 3개):")
    for rec in all_records[:3]:
        print(f"  {rec['character']} (U+{rec['unicode']}) — {rec['stroke_count']}획, 구성요소: {[c['element'] for c in rec['components']]}")
    
    print(f"\n🎉 완료! 생성된 파일:")
    print(f"  • {json_path}")
    print(f"  • {strokes_csv_path}")
    print(f"  • {summary_csv_path}")


if __name__ == "__main__":
    main()
