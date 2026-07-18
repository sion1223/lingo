"""
StrokeEvaluator — DTW 기반 획 정확도 평가기

사용 흐름:
    1. evaluator = StrokeEvaluator("kanji_dataset.json")
    2. result = evaluator.evaluate(character="一", user_strokes=[[...], [...]])
    3. print(result["summary"])
"""
import json
import numpy as np
from typing import List, Dict, Any, Optional

from .dtw import dtw_distance
from .features import preprocess_stroke, DEFAULT_RESAMPLE_N
from .feedback import (
    dtw_dist_to_score,
    analyze_stroke_diff,
    generate_overall_feedback,
)


class StrokeEvaluator:
    """
    참조 데이터(KanjiVG 파싱 결과)를 로드하고
    사용자가 입력한 획 시퀀스의 정확도를 평가한다.
    """

    def __init__(
        self,
        dataset_path: str,
        resample_n: int = DEFAULT_RESAMPLE_N,
        window_ratio: float = 0.2,
        use_features: bool = False,
    ):
        """
        Args:
            dataset_path: parse_kanji.py가 생성한 kanji_dataset.json 경로
            resample_n:   DTW 비교용 리샘플링 포인트 수
            window_ratio: Sakoe-Chiba 밴드 비율 (0~1)
            use_features: True면 5차원 피처, False면 2D 좌표만 사용
        """
        self.resample_n = resample_n
        self.window = max(1, int(resample_n * window_ratio))
        self.use_features = use_features
        self._db: Dict[str, List[Dict]] = {}  # character → [template, ...]
        self._load_dataset(dataset_path)

    # ──────────────────────────────────────────────
    # 데이터 로드
    # ──────────────────────────────────────────────

    def _load_dataset(self, path: str) -> None:
        """JSON 데이터셋을 로드하고 참조 템플릿 DB를 구축한다."""
        with open(path, "r", encoding="utf-8") as f:
            records = json.load(f)

        loaded = 0
        for rec in records:
            char = rec.get("character")
            strokes = rec.get("strokes", [])
            if not char or not strokes:
                continue

            templates = []
            for s in strokes:
                pts = s.get("points", [])
                if len(pts) < 2:
                    continue
                processed = preprocess_stroke(pts, self.resample_n, self.use_features)
                templates.append({
                    "order":     s.get("order", 0),
                    "type":      s.get("type"),
                    "processed": processed,
                    "raw":       pts,
                })

            if templates:
                if char not in self._db:
                    self._db[char] = []
                self._db[char].append(templates)
                loaded += 1

        print(f"[StrokeEvaluator] {loaded}개 문자 로드 완료 (총 {len(self._db)}종)")

    # ──────────────────────────────────────────────
    # 평가 메인
    # ──────────────────────────────────────────────

    def evaluate(
        self,
        character: str,
        user_strokes: List[List[List[float]]],
        verbose: bool = True,
    ) -> Dict[str, Any]:
        """
        사용자가 입력한 문자 획의 정확도를 평가한다.

        Args:
            character:    평가할 문자 (예: "一", "あ")
            user_strokes: 사용자가 그린 획 목록.
                          각 획은 [[x, y], [x, y], ...] 형태의 좌표 목록.
            verbose:      True면 콘솔 출력

        Returns:
            {
              "character": str,
              "overall_score": float,       # 0~100
              "grade": str,                 # "완벽"/"우수"/"양호"/"보통"/"미흡"
              "summary": str,               # 요약 메시지
              "stroke_count_match": bool,
              "stroke_results": [           # 획별 세부 결과
                {
                  "stroke_index": int,      # 1-based
                  "score": float,
                  "grade": str,
                  "dtw_distance": float,
                  "issues": [str, ...],
                  "suggestions": [str, ...],
                  "per_point_errors": [float, ...],
                }
              ],
              "top_suggestions": [str, ...],
              "worst_strokes": [int, ...],
            }
        """
        if character not in self._db:
            return self._not_found_result(character)

        # 가장 획수가 근접한 템플릿 선택
        ref_templates_list = self._db[character]
        n_user = len(user_strokes)
        ref_templates = min(
            ref_templates_list,
            key=lambda t: abs(len(t) - n_user)
        )
        n_ref = len(ref_templates)

        stroke_count_match = (n_user == n_ref)

        # 전처리: 사용자 획
        user_processed = []
        for s in user_strokes:
            if len(s) < 2:
                # 점으로만 이루어진 획 처리
                s = s + [s[0]] if s else [[0, 0], [0, 0]]
            user_processed.append(
                preprocess_stroke(s, self.resample_n, self.use_features)
            )

        # 획별 DTW 비교
        stroke_results = []
        n_compare = min(n_user, n_ref)

        for idx in range(n_compare):
            u_seq = user_processed[idx]
            r_seq = ref_templates[idx]["processed"]

            dist, path = dtw_distance(u_seq, r_seq, window=self.window)
            score = dtw_dist_to_score(dist)

            # 원시 정규화 포인트 (피드백용 — 항상 2D)
            u_norm = preprocess_stroke(user_strokes[idx], self.resample_n, use_features=False)
            r_norm = ref_templates[idx]["processed"][:, :2] if self.use_features else r_seq

            detail = analyze_stroke_diff(u_norm, r_norm, path, stroke_idx=idx + 1)
            detail["score"] = score
            detail["grade"] = _score_to_grade(score)
            detail["dtw_distance"] = round(dist, 4)

            stroke_results.append(detail)

        # 획 수 불일치 패널티
        missing = max(0, n_ref - n_user)
        extra   = max(0, n_user - n_ref)
        stroke_penalty = (missing + extra) * 5.0  # 획당 5점 감점

        avg_score = (
            float(np.mean([r["score"] for r in stroke_results]))
            if stroke_results else 0.0
        )
        overall_score = max(0.0, round(avg_score - stroke_penalty, 1))

        # 피드백 생성
        feedback = generate_overall_feedback(stroke_results, overall_score)

        # 획 수 불일치 안내 추가
        if missing > 0:
            feedback["top_suggestions"].insert(
                0, f"획이 {missing}개 부족합니다. {n_ref}획으로 쓰세요."
            )
        if extra > 0:
            feedback["top_suggestions"].insert(
                0, f"획이 {extra}개 더 많습니다. {n_ref}획으로 쓰세요."
            )

        result = {
            "character": character,
            "overall_score": overall_score,
            "grade": feedback["grade"],
            "summary": feedback["summary"],
            "stroke_count_match": stroke_count_match,
            "expected_stroke_count": n_ref,
            "user_stroke_count": n_user,
            "stroke_results": stroke_results,
            "top_suggestions": feedback["top_suggestions"],
            "worst_strokes": feedback["worst_strokes"],
        }

        if verbose:
            self._print_result(result)

        return result

    # ──────────────────────────────────────────────
    # 헬퍼
    # ──────────────────────────────────────────────

    def get_reference(self, character: str) -> Optional[List[Dict]]:
        """참조 데이터에서 특정 문자의 획 목록을 반환한다."""
        templates_list = self._db.get(character)
        if templates_list is None:
            return None
        return templates_list[0]  # 첫 번째 템플릿

    def available_characters(self) -> List[str]:
        """평가 가능한 문자 목록을 반환한다."""
        return list(self._db.keys())

    def _not_found_result(self, character: str) -> Dict:
        return {
            "character": character,
            "overall_score": 0.0,
            "grade": "오류",
            "summary": f"'{character}' 문자를 데이터셋에서 찾을 수 없습니다.",
            "stroke_count_match": False,
            "expected_stroke_count": 0,
            "user_stroke_count": 0,
            "stroke_results": [],
            "top_suggestions": ["지원되지 않는 문자입니다."],
            "worst_strokes": [],
        }

    @staticmethod
    def _print_result(result: Dict) -> None:
        char = result["character"]
        score = result["overall_score"]
        grade = result["grade"]
        print(f"\n{'='*50}")
        print(f"  문자: {char}  |  점수: {score:.1f}점  |  등급: {grade}")
        print(f"{'='*50}")
        print(f"  {result['summary']}")
        if not result["stroke_count_match"]:
            print(f"  ⚠  획 수: 입력 {result['user_stroke_count']}획 / 기준 {result['expected_stroke_count']}획")
        print()
        for r in result["stroke_results"]:
            bar = _score_bar(r["score"])
            print(f"  [{r['stroke_index']}획] {r['score']:.1f}점 ({r['grade']})  {bar}")
            for issue in r.get("issues", []):
                print(f"       ✗ {issue}")
        print()
        if result["top_suggestions"]:
            print("  [개선 제안]")
            for s in result["top_suggestions"]:
                print(f"    • {s}")
        print(f"{'='*50}\n")


def _score_to_grade(score: float) -> str:
    from .feedback import score_to_grade
    return score_to_grade(score)


def _score_bar(score: float, width: int = 20) -> str:
    filled = int(score / 100 * width)
    return "[" + "█" * filled + "░" * (width - filled) + "]"
