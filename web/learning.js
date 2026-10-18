// Learning-menu metadata only. These levels never become recognition labels.
export const JLPT_NOTICE = "N5~N1 대비는 편집자가 추정한 소규모 학습 예시입니다. JLPT는 공식 급수별 어휘·한자 전체 목록을 공개하지 않습니다. 구성 한자의 개별 급수를 뜻하지 않습니다.";
export const LEARNING_SOURCES = Object.freeze({
  jlpt: { label: "JLPT 공식 급수 설명", url: "https://www.jlpt.jp/e/about/levelsummary.html" },
  faq: { label: "JLPT 공식 FAQ", url: "https://www.jlpt.jp/faq/" },
  handwriting: { label: "문화청 손글씨·자형 지침", url: "https://www.bunka.go.jp/seisaku/kokugo_nihongo/kokugo_shisaku/joyokanjihyo_sakuin/index.html" },
});

const kanaRows = [
  ["あいうえお", "アイウエオ", "a i u e o"],
  ["かきくけこ", "カキクケコ", "ka ki ku ke ko"],
  ["さしすせそ", "サシスセソ", "sa shi su se so"],
  ["たちつてと", "タチツテト", "ta chi tsu te to"],
  ["なにぬねの", "ナニヌネノ", "na ni nu ne no"],
  ["はひふへほ", "ハヒフヘホ", "ha hi fu he ho"],
  ["まみむめも", "マミムメモ", "ma mi mu me mo"],
  ["やゆよ", "ヤユヨ", "ya yu yo"],
  ["らりるれろ", "ラリルレロ", "ra ri ru re ro"],
  ["わをん", "ワヲン", "wa wo n"],
];

export const LEARNING_GROUPS = Object.freeze([
  { id: "hiragana", track: "kana", label: "히라가나 46자" },
  { id: "katakana", track: "kana", label: "가타카나 46자" },
  { id: "greetings", track: "daily", label: "인사·감사" },
  { id: "dining", track: "daily", label: "식당·주문" },
  { id: "travel", track: "daily", label: "길 찾기" },
  { id: "shopping", track: "daily", label: "쇼핑" },
  ...[5, 4, 3, 2, 1].map((level) => ({ id: `N${level}`, track: "jlpt", label: `N${level} 대비 · 추정` })),
]);

function daily(id, group, text, reading, meaning, usage) {
  return { id, group, text, reading, meaning, usage, kind: "daily", source: "editorial", basis: "일상 상황용 자체 작성 예시. 시험 급수는 배정하지 않았습니다." };
}
function jlpt(id, group, text, reading, meaning, basis) {
  return { id, group, text, reading, meaning, basis, kind: "jlpt", source: "jlpt", estimated: true };
}

export const LEARNING_ENTRIES = Object.freeze([
  ...kanaRows.flatMap(([hiragana, katakana, readings]) => [hiragana, katakana].flatMap((row, script) => Array.from(row, (text, index) => ({
    id: `kana-${text.codePointAt(0).toString(16)}`, group: script ? "katakana" : "hiragana", text,
    reading: readings.split(" ")[index], meaning: text === "を" || text === "ヲ" ? "wo · 조사에서는 보통 o" : "기초 가나",
    kind: "kana", source: "editorial", basis: "현대 기본 가나. 탁음·반탁음·요음·촉음은 이 첫 메뉴 범위에서 제외했습니다.",
  })))),
  daily("hello", "greetings", "おはようございます", "おはようございます", "안녕하세요 (아침 인사)", "아침에 정중하게 인사할 때"),
  daily("thanks", "greetings", "ありがとうございます", "ありがとうございます", "감사합니다", "도움을 받았을 때"),
  daily("excuse", "greetings", "すみません", "すみません", "실례합니다 / 미안합니다", "말을 걸거나 사과할 때"),
  daily("water", "dining", "水", "みず", "물", "식당에서 자주 쓰는 단어"),
  daily("water-please", "dining", "水をください", "みずをください", "물을 주세요", "정중하게 물을 부탁할 때"),
  daily("delicious", "dining", "おいしいです", "おいしいです", "맛있어요", "음식의 맛을 말할 때"),
  daily("station", "travel", "駅", "えき", "역", "이동할 곳을 찾을 때"),
  daily("station-where", "travel", "駅はどこですか", "えきはどこですか", "역은 어디인가요?", "길을 물을 때"),
  daily("right", "travel", "右", "みぎ", "오른쪽", "방향 안내를 이해할 때"),
  daily("price", "shopping", "いくらですか", "いくらですか", "얼마인가요?", "가격을 물을 때"),
  daily("this-please", "shopping", "これをください", "これをください", "이것을 주세요", "물건을 가리켜 주문할 때"),
  daily("yen", "shopping", "円", "えん", "엔 (일본 화폐 단위)", "가격표를 읽을 때"),
  jlpt("n5-student", "N5", "学生", "がくせい", "학생", "기초 교실·자기소개 맥락의 명사로, N5의 기본 표현 읽기 목표를 참고한 편집자 추정입니다."),
  jlpt("n5-water", "N5", "水", "みず", "물", "구체적인 일상 사물 명사로, N5의 기초 어휘 읽기 목표를 참고한 편집자 추정입니다."),
  jlpt("n4-plan", "N4", "予定", "よてい", "예정", "친숙한 일상의 일정 설명에 쓰는 명사로, N4의 일상 주제 읽기 목표를 참고한 편집자 추정입니다."),
  jlpt("n4-promise", "N4", "約束", "やくそく", "약속", "일상 관계·약속 설명용 어휘로, N4의 기본적인 일상 문장 이해 목표를 참고한 편집자 추정입니다."),
  jlpt("n3-prepare", "N3", "準備", "じゅんび", "준비", "일상 절차를 설명하는 한자어로, N3의 구체적인 일상 내용 이해 목표를 참고한 편집자 추정입니다."),
  jlpt("n3-confirm", "N3", "確認", "かくにん", "확인", "안내·업무 절차의 내용을 파악하는 어휘로, N3의 일상 정보 읽기 목표를 참고한 편집자 추정입니다."),
  jlpt("n2-environment", "N2", "環境", "かんきょう", "환경", "사회·생활의 폭넓은 주제를 읽는 연습용 한자어로, N2의 기사·해설 이해 목표를 참고한 편집자 추정입니다."),
  jlpt("n2-judgment", "N2", "判断", "はんだん", "판단", "의견과 이유를 설명하는 글의 어휘로, N2의 일반 주제 논지 이해 목표를 참고한 편집자 추정입니다."),
  jlpt("n1-concept", "N1", "概念", "がいねん", "개념", "추상적인 개념을 논하는 글의 어휘로, N1의 추상적·논리적인 글 이해 목표를 참고한 편집자 추정입니다."),
  jlpt("n1-contradiction", "N1", "矛盾", "むじゅん", "모순", "논거 사이 관계를 논하는 어휘로, N1의 논리 구조 이해 목표를 참고한 편집자 추정입니다."),
]);

export function entriesForGroup(group) {
  return LEARNING_ENTRIES.filter((entry) => entry.group === group);
}

export function practiceCharacters(entry) {
  return [...new Set(Array.from(entry?.text ?? "").filter((char) => /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}]/u.test(char)))];
}

export function allowsRemoteScoring(variantId) {
  return variantId === "default";
}

export function practiceForm(pack, char, variantId = "default") {
  const form = pack?.characters?.[char]?.forms?.find((candidate) => candidate.id === variantId);
  return form?.usage === "practice" ? form : null;
}
